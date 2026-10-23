"""One durable daily FY27 draft, using the existing refresh and immutable ledger.

The existing worker is the scheduler; no browser, cron service or second engine
is required. Only today's missed run is caught up. Job rows are operational state;
successful source/result snapshots remain append-only in forecast_ledger_event.
"""

import asyncio
import importlib
import time as clock
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.config.settings import Settings
from packages.domain.identity.models import Actor, Role
from packages.forecast.daily_diagnostics import (
    attempt_history,
    diagnostic_scope,
    mark_stage,
    safe_diagnostic,
    safe_error_code,
)
from packages.forecast.refresh_lock import refresh_slot
from packages.forecast.store import fingerprint
from packages.persistence.database import get_session_factory
from packages.persistence.jobs import JobRepository
from packages.persistence.models import AsyncJob
from packages.source_snapshot.adapter import SourceAdapterError
from packages.source_snapshot.historical_archive import read_database_review

JOB_TYPE = "REFRESH_DAILY_FY27_FORECAST"
TIMEZONE = ZoneInfo("Asia/Shanghai")
RUN_TIME = time(9, 30)
SERVICE_ACTOR = "forecast-daily-scheduler"
Json = dict[str, Any]
logger = structlog.get_logger(__name__)


def prepare_runtime(settings: Settings) -> None:
    """Load one coherent refresh dependency graph before a long-lived worker waits.

    Previously store.py was cached at startup but the HTTP refresh was imported
    the next morning. A deployment between those times mixed old loaded modules
    and new files, causing ImportError before any source query. Workers still
    require a restart when deployed; no hot-reload or second engine is introduced.
    """
    if _active(settings, settings.local_trial_department_id):
        importlib.import_module("apps.api.routes.forecast")


def due_time(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("daily schedule requires a timezone-aware clock")
    return datetime.combine(now.astimezone(TIMEZONE).date(), RUN_TIME, TIMEZONE)


def run_identity(department: str, day: date) -> tuple[str, str]:
    identity = uuid.uuid5(uuid.NAMESPACE_URL, f"budget:forecast:{department}:2027:{day.isoformat()}")
    return f"forecast-daily:{identity}", f"daily-fy27-{identity}"


def _active(settings: Settings, department: str) -> bool:
    return (
        settings.forecast_daily_enabled
        and settings.environment == "local"
        and settings.auth_mode == "dev_headers"
        and settings.local_trial_enabled
        and department == settings.local_trial_department_id
        and department.startswith("opening-api-")
        and (make_url(settings.database_url).database or "").startswith("budget_acceptance_")
    )


async def enqueue_due(
    settings: Settings,
    *,
    now: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Idempotent across workers/restarts, including a crash after ledger commit."""
    department = settings.local_trial_department_id
    if not _active(settings, department):
        return
    now = now or datetime.now(UTC)
    scheduled = due_time(now)
    day = scheduled.date().isoformat()
    factory = session_factory or get_session_factory()
    timeout = settings.forecast_refresh_timeout_seconds + 30
    async with factory() as session, session.begin():
        # Recover only expired leases. A still running extraction is also guarded
        # by the independent session advisory lock, not merely the job lease.
        jobs = (
            await session.scalars(
                select(AsyncJob)
                .where(
                    AsyncJob.job_type == JOB_TYPE,
                    AsyncJob.payload["department_id"].astext == department,
                    AsyncJob.status == "RUNNING",
                    or_(
                        AsyncJob.timeout_at <= now,
                        (AsyncJob.timeout_at.is_(None))
                        & (AsyncJob.heartbeat_at <= now - timedelta(seconds=timeout)),
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for job in jobs:
            retry = job.payload.get("day") == day and job.attempt_no < settings.forecast_daily_max_attempts
            interrupted = {
                "attempt": job.attempt_no,
                "stage": "unknown",
                "outcome": "QUEUED" if retry else "FAILED",
                "error_code": "FORECAST_DAILY_WORKER_INTERRUPTED",
                "retry": retry,
                "finished_at": now.isoformat(),
            }
            history = attempt_history(job.result)
            if not any(item.get("attempt") == job.attempt_no for item in history):
                history.append(interrupted)
            job.result = {"diagnostics": interrupted, "attempts": history[-5:]}
            job.status = "QUEUED" if retry else "FAILED"
            job.available_at = now
            job.finished_at = None if retry else now
            job.locked_by = None
            job.timeout_at = None
            job.last_error_code = "FORECAST_DAILY_WORKER_INTERRUPTED"
            job.last_error_safe = (
                "自动更新中断；已保存版本保留，将按有限次数重试" if retry else "自动更新中断；已保留上次结果"
            )
        # Do not manufacture a backlog of artificial historical forecasts.
        await session.execute(
            update(AsyncJob)
            .where(
                AsyncJob.job_type == JOB_TYPE,
                AsyncJob.payload["department_id"].astext == department,
                AsyncJob.payload["day"].astext < day,
                AsyncJob.status == "QUEUED",
            )
            .values(
                status="CANCELLED",
                finished_at=now,
                last_error_code="FORECAST_DAILY_SUPERSEDED",
                last_error_safe="已错过的历史日期不补造预测，仅执行今天的更新",
            )
        )
        if now < scheduled:
            return
        dedupe_key, request_id = run_identity(department, scheduled.date())
        await session.execute(
            insert(AsyncJob)
            .values(
                job_id=uuid.uuid4(),
                job_type=JOB_TYPE,
                dedupe_key=dedupe_key,
                payload={
                    "department_id": department,
                    "fiscal_year": 2027,
                    "day": day,
                    "scheduled_for": scheduled.isoformat(),
                    "request_id": request_id,
                },
                status="QUEUED",
                priority=100,
                attempt_no=0,
                available_at=now,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[AsyncJob.dedupe_key])
        )


async def _generate(job: AsyncJob, settings: Settings) -> Json:
    # Runtime import avoids moving established HTTP guard/calculation behavior or
    # creating a second forecast implementation. No HTTP identity is fabricated.
    from apps.api.routes import forecast as route

    mark_stage("validate_job")
    department = str(job.payload.get("department_id", ""))
    if not _active(settings, department):
        raise SourceAdapterError("FORECAST_DAILY_DISABLED", "本部门自动更新未启用，不会查询观远")
    today = datetime.now(UTC).astimezone(TIMEZONE).date()
    if job.payload.get("day") != today.isoformat() or job.payload.get("fiscal_year") != 2027:
        raise SourceAdapterError("FORECAST_DAILY_EXPIRED", "仅允许补跑今天的预测，不会补造历史版本")
    dedupe_key, request_id = run_identity(department, today)
    if job.dedupe_key != dedupe_key or job.payload.get("request_id") != request_id:
        raise SourceAdapterError("JOB_PAYLOAD_INVALID", "自动更新任务身份无效")
    actor = Actor(SERVICE_ACTOR, department, frozenset({Role.DATA_MAINTAINER}))
    digest = fingerprint({"command": "daily-refresh", "department_id": department, "day": today.isoformat()})
    mark_stage("refresh_lock")
    async with refresh_slot(department):
        # A crash may happen after saving the append-only version but before
        # acknowledging the job. Recover that exact version without querying BI.
        mark_stage("request_replay")
        replay = await route._store_call("request_result", department, request_id, digest)
        if replay:
            result = replay
        else:
            mark_stage("baseline_read")
            baseline = await read_database_review(department)
            mark_stage("parent_read")
            parent = (await route._current(actor))["version"]
            if parent is None:
                raise SourceAdapterError(
                    "FORECAST_DAILY_PARENT_REQUIRED",
                    "先由人工生成首版预测，自动更新才能沿用已确定的季度状态和参数",
                )
            if parent["baseline_hash"] != baseline["sha256"]:
                raise SourceAdapterError(
                    "FORECAST_DAILY_BASELINE_CHANGED", "定版预算与预测基准不一致，自动更新已停止"
                )
            body = route.Refresh(
                states=parent["assumptions"]["states"],
                reason=f"每日09:30自动更新 · {today.isoformat()} · 未人工确认",
            )
            result = await route._refresh_from_source(
                baseline, body, actor, request_id, digest, automatic=True
            )
        mark_stage("result_projection")
        version = result["version"]
        return {
            "version_id": version["id"],
            "sequence": version["sequence"],
            "as_of": version["as_of"],
            "scheduled_for": job.payload["scheduled_for"],
            "status": "DRAFT",
        }


def _safe_failure(exc: Exception) -> tuple[str, str, bool]:
    """Only typed source errors or proven transient storage errors may retry."""
    if isinstance(exc, TimeoutError):
        return "FORECAST_DAILY_TIMEOUT", "自动更新超时；已保留上次结果", True
    if isinstance(exc, ImportError):
        return (
            "FORECAST_DAILY_RUNTIME_INVALID",
            "自动更新服务代码尚未同步，请重启更新服务；已保留上次结果",
            False,
        )
    if isinstance(exc, SourceAdapterError):
        # Error messages are not taken from arbitrary exceptions or SQL drivers.
        return safe_error_code(exc.code), "自动更新来源检查未完成；已保留上次结果", exc.retryable
    if isinstance(exc, HTTPException):
        if isinstance(exc.detail, dict):
            retry = exc.detail.get("retryable") is True
            if isinstance(exc.__cause__, SQLAlchemyError):
                retry = _transient_storage(exc.__cause__)
            return (
                safe_error_code(exc.detail.get("code")),
                "自动更新未完成；已保留上次结果，请根据诊断编号检查更新服务",
                retry,
            )
        # This is the existing optimistic-concurrency failure, not an arbitrary
        # 409 rule/validation rejection. Other rule errors require review.
        changed = exc.status_code == 409 and exc.detail == "预测版本已变化，请重新读取后操作"
        return (
            "FORECAST_VERSION_CHANGED" if changed else "FORECAST_DAILY_CONFLICT",
            "预测版本或规则状态变化，本次未生成新版本；已保留上次结果",
            changed,
        )
    if isinstance(exc, (SQLAlchemyError, OSError)):
        retry = _transient_storage(exc)
        return (
            "FORECAST_STORAGE_UNAVAILABLE" if retry else "FORECAST_STORAGE_ERROR",
            "预测存储操作未完成；已保留上次结果",
            retry,
        )
    return (
        "FORECAST_DAILY_FAILED",
        "自动更新未完成；已保留上次结果，请根据诊断编号检查更新服务",
        False,
    )


def _transient_storage(exc: BaseException) -> bool:
    # Programming/constraint/invalid-payload errors must not be blindly retried.
    # Driver connection invalidation and documented transient SQLSTATEs are safe.
    if isinstance(exc, OSError):
        return True
    if getattr(exc, "connection_invalidated", False) is True:
        return True
    state = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return isinstance(state, str) and (
        state.startswith("08") or state in {"40001", "40P01", "53300", "57P01", "57P02", "57P03"}
    )


async def execute_job(job: AsyncJob, repository: JobRepository, settings: Settings) -> Json:
    """Lease + bounded attempt + safe durable outcome; no confirmations or FY28 writes."""
    factory = repository.session_factory
    now = datetime.now(UTC)
    timeout = settings.forecast_refresh_timeout_seconds + 30
    owns = (
        AsyncJob.job_id == job.job_id,
        AsyncJob.status == "RUNNING",
        AsyncJob.locked_by == repository.worker_id,
        AsyncJob.attempt_no == job.attempt_no,
    )
    async with factory() as session, session.begin():
        lease = await session.scalar(
            update(AsyncJob)
            .where(*owns)
            .values(timeout_at=now + timedelta(seconds=timeout))
            .returning(AsyncJob.job_id)
        )
        if lease is None:
            return {"outcome": "LEASE_LOST", "retry": False}
    result: Json | None = None
    error: tuple[str, str, bool] | None = None
    started = clock.monotonic()
    diagnostic: Json = {"diagnostic_id": uuid.uuid4().hex[:16], "attempt": job.attempt_no}
    with diagnostic_scope() as progress:
        try:
            async with asyncio.timeout(timeout):
                result = await _generate(job, settings)
        except asyncio.CancelledError:
            logger.warning(
                "forecast_daily_cancelled",
                **diagnostic,
                stage=progress.stage,
                exception_type="CancelledError",
                elapsed_ms=int((clock.monotonic() - started) * 1000),
                outcome="CANCELLED",
                retry=False,
            )
            raise
        except Exception as exc:
            error = _safe_failure(exc)
            cause = exc.__cause__ or exc
            diagnostic["exception_type"] = type(cause).__name__
            source_id = (
                exc.detail.get("diagnostic_id")
                if isinstance(exc, HTTPException) and isinstance(exc.detail, dict)
                else getattr(exc, "diagnostic_id", None)
            )
            diagnostic.update(safe_diagnostic({"diagnostic_id": source_id}))
        diagnostic["stage"] = progress.stage
    finished = datetime.now(UTC)
    retry = bool(
        error
        and error[2]
        and job.attempt_no < settings.forecast_daily_max_attempts
        and job.payload.get("day") == finished.astimezone(TIMEZONE).date().isoformat()
    )
    outcome = "SUCCEEDED" if error is None else ("QUEUED" if retry else "FAILED")
    diagnostic.update(
        elapsed_ms=int((clock.monotonic() - started) * 1000),
        outcome=outcome,
        retry=retry,
        finished_at=finished.isoformat(),
    )
    if error:
        diagnostic["error_code"] = error[0]
    diagnostic = safe_diagnostic(diagnostic)
    history = [*attempt_history(getattr(job, "result", None)), diagnostic]
    values: Json = {
        "result": {**(result or {}), "diagnostics": diagnostic, "attempts": history[-5:]},
        "status": outcome,
        "finished_at": None if retry else finished,
        "timeout_at": None,
        "locked_by": None,
        "last_error_code": error[0] if error else None,
        "last_error_safe": error[1][:500] if error else None,
    }
    if retry:
        values["available_at"] = finished + timedelta(
            seconds=min(900, settings.forecast_daily_retry_seconds * 2 ** (job.attempt_no - 1))
        )
    async with factory() as session, session.begin():
        acknowledged = await session.scalar(
            update(AsyncJob).where(*owns).values(**values).returning(AsyncJob.job_id)
        )
    if acknowledged is None:
        diagnostic = {**diagnostic, "outcome": "LEASE_LOST", "retry": False}
    log = logger.info if diagnostic["outcome"] == "SUCCEEDED" else logger.warning
    log("forecast_daily_attempt_completed", **diagnostic)
    return diagnostic


async def retry_failed_today(
    settings: Settings,
    *,
    job_id: uuid.UUID,
    expected_code: str,
    now: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """Explicit operator retry after repair; retain the failed attempt on this job.

    This is not an automatic retry of unknown errors, a backfill, or a new daily
    identity. It cannot reset successful, expired, exhausted, or another job.
    """
    department = settings.local_trial_department_id
    if not _active(settings, department):
        return False
    now = now or datetime.now(UTC)
    day = now.astimezone(TIMEZONE).date()
    dedupe_key, request_id = run_identity(department, day)
    factory = session_factory or get_session_factory()
    async with factory() as session, session.begin():
        job = await session.scalar(select(AsyncJob).where(AsyncJob.job_id == job_id).with_for_update())
        if (
            job is None
            or job.job_type != JOB_TYPE
            or job.payload.get("department_id") != department
            or job.payload.get("day") != day.isoformat()
            or job.dedupe_key != dedupe_key
            or job.payload.get("request_id") != request_id
            or job.status != "FAILED"
            or job.last_error_code != expected_code
            or job.attempt_no >= settings.forecast_daily_max_attempts
        ):
            return False
        history = attempt_history(job.result)
        if not any(item.get("attempt") == job.attempt_no for item in history):
            history.append(
                {
                    "attempt": job.attempt_no,
                    "stage": "unknown",
                    "outcome": "FAILED",
                    "error_code": safe_error_code(job.last_error_code),
                    "retry": False,
                    **({"finished_at": job.finished_at.isoformat()} if job.finished_at else {}),
                }
            )
        job.result = {"attempts": history[-5:]}
        job.status, job.available_at, job.finished_at = "QUEUED", now, None
        job.locked_by, job.timeout_at = None, None
        return True


async def schedule_status(
    settings: Settings,
    department: str,
    *,
    now: datetime | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> Json:
    """Read only same-department operations metadata; never enqueue from a GET."""
    now = now or datetime.now(UTC)
    scheduled = due_time(now)
    factory = session_factory or get_session_factory()
    async with factory() as session:
        scope = (AsyncJob.job_type == JOB_TYPE, AsyncJob.payload["department_id"].astext == department)
        last = await session.scalar(
            select(AsyncJob).where(*scope).order_by(AsyncJob.created_at.desc()).limit(1)
        )
        success = await session.scalar(
            select(AsyncJob)
            .where(*scope, AsyncJob.status == "SUCCEEDED")
            .order_by(AsyncJob.finished_at.desc())
            .limit(1)
        )
    ran_today = bool(last and last.payload.get("day") == scheduled.date().isoformat())
    next_run = scheduled if now < scheduled or not ran_today else scheduled + timedelta(days=1)
    enabled = _active(settings, department)
    last_run = None
    if last:
        state = last.status if last.status in {"SUCCEEDED", "RUNNING", "QUEUED"} else "FAILED"
        diagnostic = safe_diagnostic((last.result or {}).get("diagnostics"))
        last_run = {
            "state": state,
            "scheduled_for": last.payload["scheduled_for"],
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "version_id": (last.result or {}).get("version_id"),
            "sequence": (last.result or {}).get("sequence"),
            "error": {
                "code": last.last_error_code,
                "message": last.last_error_safe,
                **{
                    key: diagnostic[key]
                    for key in ("stage", "diagnostic_id", "exception_type")
                    if key in diagnostic
                },
            }
            if last.last_error_code
            else None,
        }
    return {
        "enabled": enabled,
        "time": "09:30",
        "timezone": "Asia/Shanghai",
        "next_run_at": next_run.isoformat() if enabled else None,
        "last_run": last_run,
        "last_success_at": success.finished_at.isoformat() if success and success.finished_at else None,
        "catch_up": True,
    }
