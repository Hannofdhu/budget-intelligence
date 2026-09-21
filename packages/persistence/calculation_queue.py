import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    BudgetCalculationEnqueueRequest,
    BudgetCalculationJobResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.hashing import content_hash
from packages.persistence.entry_planning import _lock_current_revision
from packages.persistence.ids import uuid7
from packages.persistence.models import AsyncJob, BudgetRevision
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _store_receipt,
)

FULL_CALCULATION_JOB_TYPE = "CALCULATE_BUDGET_REVISION"
FULL_CALCULATION_PIPELINE_VERSION = "phase1-full-calculation-v1"


async def enqueue_budget_calculation(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: BudgetCalculationEnqueueRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> BudgetCalculationJobResponse:
    command_type = "ENQUEUE_BUDGET_CALCULATION"
    request_hash = content_hash(
        {
            "revision_id": str(revision_id),
            "expected_revision_id": str(expected_revision_id),
            **payload.model_dump(mode="json"),
        }
    )
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return BudgetCalculationJobResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        job = await create_full_calculation_job(
            session,
            actor=actor,
            revision=revision,
            request_id=request_id,
            source="EXPLICIT_REQUEST",
            calculation_pipeline_version=payload.calculation_pipeline_version,
        )
        response = calculation_job_response(job, revision.budget_revision_id)
        occurred_at = _now()
        _add_audit(
            session,
            event_type="BUDGET_CALCULATION_QUEUED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="ASYNC_JOB",
            object_key={"job_id": str(job.job_id)},
            detail={
                "job_type": job.job_type,
                "source_revision_id": str(revision.budget_revision_id),
                "calculation_pipeline_version": payload.calculation_pipeline_version,
                "source": "EXPLICIT_REQUEST",
            },
            request_id=request_id,
            occurred_at=occurred_at,
            annual_budget_id=budget.annual_budget_id,
            budget_revision_id=revision.budget_revision_id,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response.model_dump(mode="json"),
            occurred_at=occurred_at,
        )
    return response


async def create_full_calculation_job(
    session: AsyncSession,
    *,
    actor: Actor,
    revision: BudgetRevision,
    request_id: str,
    source: str,
    calculation_pipeline_version: str = FULL_CALCULATION_PIPELINE_VERSION,
    scenario_application_id: uuid.UUID | None = None,
    campus_confirmation_required: bool = False,
    formal_confirmation_reason: str | None = None,
) -> AsyncJob:
    purpose = "formal-confirmation" if formal_confirmation_reason is not None else "calculation"
    dedupe_key = f"budget-{purpose}:{revision.budget_revision_id}:{calculation_pipeline_version}"
    existing = await session.scalar(select(AsyncJob).where(AsyncJob.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    occurred_at = _now()
    job = AsyncJob(
        job_id=uuid7(),
        job_type=FULL_CALCULATION_JOB_TYPE,
        dedupe_key=dedupe_key,
        payload={
            "schema_version": "1.0",
            "calculation_pipeline_version": calculation_pipeline_version,
            "source": source,
            "source_revision_id": str(revision.budget_revision_id),
            "scenario_application_id": (
                str(scenario_application_id) if scenario_application_id is not None else None
            ),
            "campus_confirmation_required": campus_confirmation_required,
            "formal_confirmation_reason": formal_confirmation_reason,
            "department_id": actor.department_id,
            "actor_id": actor.user_id,
            "roles": sorted(role.value for role in actor.roles),
            "request_id": request_id,
        },
        result=None,
        status="QUEUED",
        priority=50,
        attempt_no=0,
        available_at=occurred_at,
        locked_by=None,
        heartbeat_at=None,
        timeout_at=None,
        last_error_code=None,
        last_error_safe=None,
        created_at=occurred_at,
        finished_at=None,
    )
    session.add(job)
    await session.flush()
    return job


def calculation_job_response(job: AsyncJob, source_revision_id: uuid.UUID) -> BudgetCalculationJobResponse:
    return BudgetCalculationJobResponse(
        job_id=job.job_id,
        source_revision_id=source_revision_id,
        calculation_pipeline_version=str(job.payload["calculation_pipeline_version"]),
        status=job.status,
        links={"job": f"/api/v1/jobs/{job.job_id}"},
    )
