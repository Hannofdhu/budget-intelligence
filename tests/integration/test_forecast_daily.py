"""Synthetic scheduled refresh contracts in an exact temporary PostgreSQL schema.

No existing budget, archive, forecast or worker job is changed; no BI is queried.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Self, cast

import pytest
from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.routes import forecast as route
from packages.forecast import daily, refresh_lock
from packages.forecast.store import fingerprint
from packages.persistence import jobs
from packages.persistence.jobs import JobRepository
from packages.persistence.models import AsyncJob
from packages.source_snapshot.adapter import SourceAdapterError
from tests.integration.test_forecast_database_store import inputs, isolated_store
from tests.unit.test_forecast_daily import configured

pytestmark = pytest.mark.skipif(
    os.getenv("BUDGET_RUN_DB_TESTS") != "1", reason="requires isolated PostgreSQL"
)


@pytest.fixture
def scheduled_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    # Enqueue, claim and execute must share one clock, including when CI runs
    # before 09:30 Shanghai time. Never enqueue a future job against a real clock.
    now = datetime(2026, 10, 16, 1, 30, 1, tzinfo=UTC)

    class ScheduledClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(now.timestamp(), tz)

    monkeypatch.setattr(daily, "datetime", ScheduledClock)
    monkeypatch.setattr(jobs, "datetime", ScheduledClock)
    return now


async def test_postgresql_daily_enqueue_concurrent_dedupe_leases_and_read_only_status() -> None:
    async with isolated_store() as (_store, factory):
        async with factory.begin() as session:
            table = AsyncJob.__table__
            assert isinstance(table, Table)
            await session.run_sync(lambda sync: table.create(sync.connection()))
        settings = configured()
        before = datetime(2026, 10, 16, 1, 29, tzinfo=UTC)
        await daily.enqueue_due(settings, now=before, session_factory=factory)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(AsyncJob)) == 0
        now = before + timedelta(minutes=1)
        await asyncio.gather(
            *[daily.enqueue_due(settings, now=now, session_factory=factory) for _ in range(6)]
        )
        async with factory.begin() as session:
            assert await session.scalar(select(func.count()).select_from(AsyncJob)) == 1
            job = await session.scalar(select(AsyncJob))
            assert job is not None and job.status == "QUEUED"
            job.status, job.attempt_no, job.locked_by = "RUNNING", 1, "crashed-worker"
            job.timeout_at = now - timedelta(seconds=1)
        await daily.enqueue_due(settings, now=now, session_factory=factory)
        async with factory.begin() as session:
            job = await session.scalar(select(AsyncJob))
            assert job is not None and job.status == "QUEUED" and job.locked_by is None
            job.status, job.attempt_no = "RUNNING", settings.forecast_daily_max_attempts
            job.timeout_at = now - timedelta(seconds=1)
        await daily.enqueue_due(settings, now=now, session_factory=factory)
        status = await daily.schedule_status(
            settings, settings.local_trial_department_id, now=now, session_factory=factory
        )
        assert status["last_run"]["state"] == "FAILED"
        assert status["last_run"]["error"]["code"] == "FORECAST_DAILY_WORKER_INTERRUPTED"
        assert status["next_run_at"].startswith("2026-10-17T09:30")
        other = await daily.schedule_status(settings, "other", now=now, session_factory=factory)
        assert not other["enabled"] and other["last_run"] is None
        tomorrow = now + timedelta(days=1)
        await daily.enqueue_due(settings, now=tomorrow, session_factory=factory)
        # A later wake only adds that current day, never 10/18/19 missed versions.
        await daily.enqueue_due(settings, now=tomorrow + timedelta(days=3), session_factory=factory)
        async with factory() as session:
            jobs = (await session.scalars(select(AsyncJob).order_by(AsyncJob.created_at))).all()
            assert len(jobs) == 3 and jobs[1].status == "CANCELLED" and jobs[2].status == "QUEUED"


async def test_postgresql_refresh_lock_excludes_second_connection_and_releases_after_exception() -> None:
    async with isolated_store() as (_store, factory):
        engine = cast(AsyncEngine, factory.kw["bind"])
        async with refresh_lock.refresh_slot("synthetic-lock", engine=engine):
            with pytest.raises(SourceAdapterError) as exc:
                async with refresh_lock.refresh_slot("synthetic-lock", engine=engine):
                    raise AssertionError("must not acquire same department from another connection")
            assert exc.value.code == "FORECAST_REFRESH_IN_PROGRESS"
            async with refresh_lock.refresh_slot("different-department", engine=engine):
                pass
        with pytest.raises(RuntimeError):
            async with refresh_lock.refresh_slot("synthetic-lock", engine=engine):
                raise RuntimeError("synthetic failure")
        async with refresh_lock.refresh_slot("synthetic-lock", engine=engine):
            pass
        entered = asyncio.Event()

        async def interrupted_refresh() -> None:
            async with refresh_lock.refresh_slot("synthetic-lock", engine=engine):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(interrupted_refresh())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with refresh_lock.refresh_slot("synthetic-lock", engine=engine):
            pass


async def test_postgresql_consumer_filters_reserve_daily_queue_without_changing_default_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with isolated_store() as (_store, factory):
        async with factory.begin() as session:
            table = AsyncJob.__table__
            assert isinstance(table, Table)
            await session.run_sync(lambda sync: table.create(sync.connection()))
            now = datetime.now(UTC)
            for job_type in (daily.JOB_TYPE, "NOOP"):
                await session.execute(
                    insert(AsyncJob).values(
                        job_type=job_type,
                        dedupe_key=job_type,
                        payload={},
                        status="QUEUED",
                        priority=100,
                        attempt_no=0,
                        available_at=now,
                        created_at=now,
                    )
                )
        repository = JobRepository("isolated-consumers")
        repository.session_factory = factory
        general = await repository.claim_next(exclude_job_type=daily.JOB_TYPE)
        assert general is not None and general.job_type == "NOOP"
        forecast = await repository.claim_next(job_type=daily.JOB_TYPE)
        assert forecast is not None and forecast.job_type == daily.JOB_TYPE
        assert await repository.claim_next() is None
        async with factory.begin() as session:
            await session.execute(
                update(AsyncJob)
                .where(AsyncJob.job_id == forecast.job_id)
                .values(attempt_no=forecast.attempt_no + 1, locked_by="recovered-worker")
            )

        async def forbidden(*_args: Any) -> Any:
            raise AssertionError("stale lease must not query BI or save a version")

        monkeypatch.setattr(daily, "_generate", forbidden)
        await daily.execute_job(forecast, repository, configured())
        async with factory() as session:
            actual = await session.get(AsyncJob, forecast.job_id)
            assert (
                actual is not None and actual.status == "RUNNING" and actual.locked_by == "recovered-worker"
            )


async def test_postgresql_scheduled_version_replays_after_job_ack_crash_without_querying_bi(
    monkeypatch: pytest.MonkeyPatch,
    scheduled_now: datetime,
) -> None:
    async with isolated_store() as (store, factory):
        async with factory.begin() as session:
            table = AsyncJob.__table__
            assert isinstance(table, Table)
            await session.run_sync(lambda sync: table.create(sync.connection()))
        settings = configured()
        department = settings.local_trial_department_id
        parent = await store.append_version(**inputs(department, "parent"))
        await store.confirm(
            department=department,
            actor="synthetic",
            version_id=parent["version"]["id"],
            request_id="parent-confirm",
            request_hash=fingerprint("parent-confirm"),
            reason="synthetic",
        )
        source_calls: list[str] = []
        engine = cast(AsyncEngine, factory.kw["bind"])
        monkeypatch.setattr(refresh_lock, "get_engine", lambda: engine)
        monkeypatch.setattr(route, "store", store)

        async def baseline(_department: str) -> dict[str, Any]:
            return {"sha256": "b" * 64}

        async def refresh(
            _baseline: Any, body: Any, actor: Any, request_id: str, digest: str, **kwargs: Any
        ) -> Any:
            assert kwargs == {"automatic": True}
            source_calls.append(request_id)
            current = (await store.current(department))["version"]
            values = inputs(department, request_id, current["id"])
            values["request_hash"] = digest
            return await store.append_version(**values)

        monkeypatch.setattr(daily, "read_database_review", baseline)
        monkeypatch.setattr(route, "_refresh_from_source", refresh)
        # Synthetic parent's minimal assumptions are replaced with a valid model
        # only in the isolated test read, never in the immutable ledger itself.
        original_current = route._current

        async def current(actor: Any) -> Any:
            view = await original_current(actor)
            view["version"]["assumptions"] = route.Assumptions().model_dump(mode="json")
            return view

        monkeypatch.setattr(route, "_current", current)
        now = scheduled_now
        await daily.enqueue_due(settings, now=now, session_factory=factory)
        repository = JobRepository("isolated-daily-worker")
        repository.session_factory = factory
        job = await repository.claim_next()
        assert job is not None
        generated = await daily._generate(job, settings)  # simulate committed ledger, crashed before job ACK
        assert generated["status"] == "DRAFT"
        await daily.execute_job(job, repository, settings)
        assert len(source_calls) == 1
        actual = await store.current(department)
        assert len(actual["history"]) == 2 and actual["version"]["status"] == "DRAFT"
        prior = await store.version(department, parent["version"]["id"])
        assert prior is not None and prior["version"]["status"] == "CONFIRMED"
        status = await daily.schedule_status(settings, department, now=now, session_factory=factory)
        assert status["last_run"]["state"] == "SUCCEEDED"
        assert status["last_run"]["version_id"] == generated["version_id"]
        assert status["last_success_at"]


async def test_postgresql_explicit_today_retry_preserves_failed_attempt_and_never_resets_success(
    monkeypatch: pytest.MonkeyPatch,
    scheduled_now: datetime,
) -> None:
    async with isolated_store() as (_store, factory):
        async with factory.begin() as session:
            table = AsyncJob.__table__
            assert isinstance(table, Table)
            await session.run_sync(lambda sync: table.create(sync.connection()))
        settings = configured()
        now = scheduled_now
        await daily.enqueue_due(settings, now=now, session_factory=factory)
        async with factory.begin() as session:
            job = await session.scalar(select(AsyncJob))
            assert job is not None
            identity = job.job_id
            job.status, job.attempt_no = "FAILED", 1
            job.last_error_code = "FORECAST_DAILY_FAILED"
            job.finished_at = now
        assert not await daily.retry_failed_today(
            settings, job_id=identity, expected_code="DIFFERENT", now=now, session_factory=factory
        )
        assert not await daily.retry_failed_today(
            settings,
            job_id=identity,
            expected_code="FORECAST_DAILY_FAILED",
            now=now + timedelta(days=1),
            session_factory=factory,
        )
        assert await daily.retry_failed_today(
            settings,
            job_id=identity,
            expected_code="FORECAST_DAILY_FAILED",
            now=now,
            session_factory=factory,
        )
        repository = JobRepository("synthetic-repaired-worker")
        repository.session_factory = factory
        job = await repository.claim_next(job_id=identity)
        assert job is not None and job.attempt_no == 2
        assert job.result is not None
        legacy = job.result["attempts"][0]
        assert legacy == {
            "attempt": 1,
            "stage": "unknown",
            "outcome": "FAILED",
            "error_code": "FORECAST_DAILY_FAILED",
            "retry": False,
            "finished_at": now.astimezone(UTC).isoformat(),
        }
        assert "exception_type" not in legacy and "diagnostic_id" not in legacy

        async def generate(*_args: Any) -> dict[str, Any]:
            return {"version_id": "synthetic-existing-version", "sequence": 2}

        monkeypatch.setattr(daily, "_generate", generate)
        result = await daily.execute_job(job, repository, settings)
        assert result["outcome"] == "SUCCEEDED"
        async with factory() as session:
            actual = await session.get(AsyncJob, identity)
            assert actual is not None and actual.status == "SUCCEEDED"
            assert actual.result is not None
            assert actual.result["attempts"][0] == legacy
            assert actual.result["attempts"][1]["outcome"] == "SUCCEEDED"
            assert actual.result["attempts"][1]["attempt"] == 2
        assert not await daily.retry_failed_today(
            settings,
            job_id=identity,
            expected_code="FORECAST_DAILY_FAILED",
            now=now,
            session_factory=factory,
        )
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(AsyncJob)) == 1
