import argparse
import asyncio
import signal
import uuid

import structlog

from packages.calculation_pipeline import run_full_calculation_job
from packages.config.settings import get_settings
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.observability.logging import configure_logging
from packages.persistence.audit_archive import AUDIT_ARCHIVE_JOB_TYPE, build_audit_archive
from packages.persistence.database import dispose_engine, get_session_factory
from packages.persistence.jobs import JobRepository
from packages.source_snapshot.adapter import GuanCliSourceAdapter, SourceAdapterError
from packages.source_snapshot.service import build_source_snapshot

logger = structlog.get_logger(__name__)


async def handle_one(repository: JobRepository, *, job_id: uuid.UUID | None = None) -> bool:
    job = await repository.claim_next(job_id=job_id)
    if job is None:
        return False
    structlog.contextvars.bind_contextvars(job_id=str(job.job_id), job_type=job.job_type)
    try:
        if job.job_type == "NOOP":
            result: dict[str, object] = {"status": "ok"}
        elif job.job_type == "BUILD_SOURCE_SNAPSHOT":
            result = await _build_source_snapshot_job(job.job_id, job.payload)
        elif job.job_type == "CALCULATE_BUDGET_REVISION":
            result = await run_full_calculation_job(job.job_id, job.payload)
        elif job.job_type == AUDIT_ARCHIVE_JOB_TYPE:
            result = await _build_audit_archive_job(job.job_id, job.payload)
        else:
            raise SourceAdapterError("UNSUPPORTED_JOB", "Worker 不支持该作业类型")
        await repository.mark_succeeded(job.job_id, result)
        logger.info("job_succeeded")
    except SourceAdapterError as exc:
        await repository.mark_failed(job.job_id, exc.code, exc.safe_message)
        logger.warning("job_failed", error_code=exc.code)
    except DomainError as exc:
        await repository.mark_incomplete(
            job.job_id,
            exc.code,
            exc.message,
            {
                "schema_version": "1.0",
                "status": "INCOMPLETE",
                "blocking_code": exc.code,
            },
        )
        logger.warning("job_incomplete", error_code=exc.code)
    except Exception:
        await repository.mark_failed(job.job_id, "INTERNAL_ERROR", "作业执行失败，请根据请求编号排查")
        logger.exception("job_failed", error_code="INTERNAL_ERROR")
    finally:
        structlog.contextvars.clear_contextvars()
    return True


async def _build_source_snapshot_job(job_id: uuid.UUID, payload: dict[str, object]) -> dict[str, object]:
    try:
        raw_roles = payload["roles"]
        if not isinstance(raw_roles, list):
            raise TypeError
        actor = Actor(
            user_id=str(payload["actor_id"]),
            department_id=str(payload["department_id"]),
            roles=frozenset(Role(str(value)) for value in raw_roles),
        )
        fiscal_year = int(str(payload["fiscal_year"]))
        request_id = str(payload["request_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceAdapterError("JOB_PAYLOAD_INVALID", "作业载荷无效") from exc
    if not actor.has_role(Role.DATA_MAINTAINER):
        raise SourceAdapterError("JOB_ROLE_INVALID", "作业缺少数据维护人员角色")
    settings = get_settings()
    adapter = GuanCliSourceAdapter(settings)
    async with get_session_factory()() as session:
        return await build_source_snapshot(
            session,
            adapter=adapter,
            settings=settings,
            actor=actor,
            fiscal_year=fiscal_year,
            build_job_id=job_id,
            request_id=request_id,
        )


async def _build_audit_archive_job(job_id: uuid.UUID, payload: dict[str, object]) -> dict[str, object]:
    async with get_session_factory()() as session:
        return await build_audit_archive(
            session,
            settings=get_settings(),
            job_id=job_id,
            payload=payload,
        )


async def worker_loop(*, once: bool) -> None:
    settings = get_settings()
    configure_logging(settings)
    repository = JobRepository(worker_id=settings.worker_id)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        while not stop.is_set():
            handled = await handle_one(repository)
            if once:
                return
            if not handled:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await dispose_engine()


def run() -> None:
    parser = argparse.ArgumentParser(description="预算智解异步 Worker")
    parser.add_argument("--once", action="store_true", help="领取至多一个作业后退出")
    args = parser.parse_args()
    asyncio.run(worker_loop(once=args.once))


if __name__ == "__main__":
    run()
