import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.database import get_session_factory
from packages.persistence.models import AsyncJob


class JobRepository:
    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id
        self.session_factory = get_session_factory()

    async def claim_next(self, *, job_id: uuid.UUID | None = None) -> AsyncJob | None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            statement = select(AsyncJob).where(AsyncJob.status == "QUEUED", AsyncJob.available_at <= now)
            if job_id is not None:
                statement = statement.where(AsyncJob.job_id == job_id)
            result = await session.execute(
                statement.order_by(AsyncJob.priority, AsyncJob.created_at, AsyncJob.job_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = result.scalar_one_or_none()
            if job is None:
                return None
            job.status = "RUNNING"
            job.attempt_no += 1
            job.locked_by = self.worker_id
            job.heartbeat_at = now
            await session.flush()
            session.expunge(job)
            return job

    async def mark_succeeded(self, job_id: uuid.UUID, result: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(AsyncJob)
                .where(
                    AsyncJob.job_id == job_id,
                    AsyncJob.locked_by == self.worker_id,
                    AsyncJob.status == "RUNNING",
                )
                .values(status="SUCCEEDED", result=result, finished_at=now)
            )

    async def mark_failed(self, job_id: uuid.UUID, code: str, detail: str) -> None:
        now = datetime.now(UTC)
        safe_detail = detail[:500]
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(AsyncJob)
                .where(
                    AsyncJob.job_id == job_id,
                    AsyncJob.locked_by == self.worker_id,
                    AsyncJob.status == "RUNNING",
                )
                .values(
                    status="FAILED",
                    last_error_code=code,
                    last_error_safe=safe_detail,
                    finished_at=now,
                )
            )

    async def mark_incomplete(
        self,
        job_id: uuid.UUID,
        code: str,
        detail: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC)
        safe_detail = detail[:500]
        async with self.session_factory() as session, session.begin():
            await session.execute(
                update(AsyncJob)
                .where(
                    AsyncJob.job_id == job_id,
                    AsyncJob.locked_by == self.worker_id,
                    AsyncJob.status == "RUNNING",
                )
                .values(
                    status="INCOMPLETE",
                    result=result,
                    last_error_code=code,
                    last_error_safe=safe_detail,
                    finished_at=now,
                )
            )


async def requeue_incomplete_job(session: AsyncSession, *, dedupe_key: str) -> AsyncJob | None:
    job = await session.scalar(select(AsyncJob).where(AsyncJob.dedupe_key == dedupe_key))
    if job is None or job.status != "INCOMPLETE":
        return job
    job.status = "QUEUED"
    job.result = None
    job.available_at = datetime.now(UTC)
    job.locked_by = None
    job.heartbeat_at = None
    job.timeout_at = None
    job.last_error_code = None
    job.last_error_safe = None
    job.finished_at = None
    await session.flush()
    return job


async def requeue_budget_calculation_job(
    session: AsyncSession,
    *,
    revision_id: uuid.UUID,
    calculation_pipeline_version: str = "phase1-full-calculation-v1",
) -> AsyncJob | None:
    return await requeue_incomplete_job(
        session,
        dedupe_key=f"budget-calculation:{revision_id}:{calculation_pipeline_version}",
    )
