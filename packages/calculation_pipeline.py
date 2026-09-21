import uuid

from sqlalchemy import select

from packages.contracts.versioning import (
    ClassSizeResultVersionResponse,
    ClassSizeValidateRequest,
    HeadcountCalculateRequest,
    HeadcountResultVersionResponse,
    RevenueCalculateRequest,
    RevenueResultVersionResponse,
    TargetClosureResultVersionResponse,
    TargetClosureValidateRequest,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.headcount.calculation import HEADCOUNT_RULE_VERSION, OPENING_HEADCOUNT_RULE_VERSION
from packages.persistence.database import get_session_factory
from packages.persistence.entry_planning import _assert_version_access
from packages.persistence.headcount import calculate_headcount_result
from packages.persistence.models import (
    BudgetRevision,
    CampusEntryConfirmation,
    CampusEntrySetVersion,
)
from packages.persistence.publication import finalize_formal_plan_confirmation
from packages.persistence.resource_finance import validate_class_size_result
from packages.persistence.revenue import calculate_revenue_result
from packages.persistence.target_closure import validate_target_closure

FULL_CALCULATION_PIPELINE_VERSION = "phase1-full-calculation-v1"


async def run_full_calculation_job(job_id: uuid.UUID, payload: dict[str, object]) -> dict[str, object]:
    actor, source_revision_id, request_id, acting_role = _parse_payload(payload)
    if bool(payload.get("campus_confirmation_required")):
        await _require_campus_confirmation(source_revision_id)

    headcount = await _calculate_headcount(job_id, actor, source_revision_id, request_id, acting_role)
    class_size = await _validate_class_size(
        job_id, actor, headcount.revision.new_revision_id, request_id, acting_role
    )
    revenue = await _calculate_revenue(
        job_id, actor, class_size.revision.new_revision_id, request_id, acting_role
    )
    closure = await _validate_closure(
        job_id, actor, revenue.revision.new_revision_id, request_id, acting_role
    )
    formal_confirmation = None
    final_revision_id = closure.revision.new_revision_id
    formal_reason = payload.get("formal_confirmation_reason")
    if isinstance(formal_reason, str):
        if not closure.publishable:
            raise DomainError(
                code="FORMAL_PLAN_NOT_CONFIRMABLE",
                message="完整复算仍存在发布阻断，未生成正式方案确认",
                status_code=422,
            )
        async with get_session_factory()() as session:
            formal_confirmation = await finalize_formal_plan_confirmation(
                session,
                actor=actor,
                source_revision_id=source_revision_id,
                calculated_revision_id=closure.revision.new_revision_id,
                reason=formal_reason,
                idempotency_key=uuid.uuid5(job_id, "FORMAL_PLAN_CONFIRMATION"),
                request_id=_stage_request_id(job_id, request_id, "formal-plan-confirmation"),
            )
        final_revision_id = formal_confirmation.confirmed_revision_id
    result = {
        "schema_version": "1.0",
        "calculation_pipeline_version": FULL_CALCULATION_PIPELINE_VERSION,
        "source_revision_id": str(source_revision_id),
        "final_revision_id": str(final_revision_id),
        "calculated_revision_id": str(closure.revision.new_revision_id),
        "headcount_result_version_id": str(headcount.headcount_result_version_id),
        "class_size_result_version_id": str(class_size.class_size_result_version_id),
        "revenue_result_version_id": str(revenue.revenue_result_version_id),
        "target_closure_result_version_id": str(closure.target_closure_result_version_id),
        "headcount_publishable": headcount.publishable,
        "headcount_calculation_rule_version": headcount.calculation_rule_version,
        "class_size_publishable": class_size.publishable,
        "revenue_publishable": revenue.publishable,
        "closure_status": closure.closure_status,
        "publishable": closure.publishable,
        "stages": [
            {"stage": "HEADCOUNT", "status": headcount.status},
            {"stage": "CLASS_SIZE", "status": class_size.status},
            {"stage": "REVENUE", "status": revenue.status},
            {"stage": "TARGET_CLOSURE", "status": closure.status},
        ],
    }
    if formal_confirmation is not None:
        result["formal_plan_confirmation_id"] = str(formal_confirmation.formal_plan_confirmation_id)
        result["formal_confirmation_status"] = "CONFIRMED"
    return result


def _parse_payload(payload: dict[str, object]) -> tuple[Actor, uuid.UUID, str, Role]:
    try:
        raw_roles = payload["roles"]
        if not isinstance(raw_roles, list):
            raise TypeError
        actor = Actor(
            user_id=str(payload["actor_id"]),
            department_id=str(payload["department_id"]),
            roles=frozenset(Role(str(value)) for value in raw_roles),
        )
        source_revision_id = uuid.UUID(str(payload["source_revision_id"]))
        request_id = str(payload["request_id"])
        pipeline_version = str(payload["calculation_pipeline_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DomainError(
            code="JOB_PAYLOAD_INVALID", message="完整计算作业载荷无效", status_code=409
        ) from exc
    source = str(payload.get("source", ""))
    required_role = Role.DEPARTMENT_BUDGET_OWNER if source == "FORMAL_CONFIRMATION" else Role.DATA_MAINTAINER
    if not actor.has_role(required_role):
        raise DomainError(
            code="JOB_ROLE_INVALID",
            message=f"完整计算作业缺少{required_role.value}角色",
            status_code=403,
        )
    if pipeline_version != FULL_CALCULATION_PIPELINE_VERSION:
        raise DomainError(
            code="CALCULATION_PIPELINE_VERSION_UNSUPPORTED",
            message="完整计算作业版本不受支持",
            status_code=409,
        )
    return actor, source_revision_id, request_id, required_role


async def _require_campus_confirmation(revision_id: uuid.UUID) -> None:
    async with get_session_factory()() as session:
        revision = await session.get(BudgetRevision, revision_id)
        if revision is None:
            raise DomainError(
                code="BUDGET_REVISION_NOT_FOUND",
                message="完整计算作业引用的预算修订不存在",
                status_code=404,
            )
        raw_version_id = revision.component_manifest.get("campus_entry_set_version_id")
        try:
            version_id = uuid.UUID(str(raw_version_id))
        except (TypeError, ValueError) as exc:
            raise DomainError(
                code="CAMPUS_ENTRY_SET_NOT_FOUND",
                message="完整计算前缺少待确认的校区入口版本",
                status_code=409,
            ) from exc
        version = await session.get(CampusEntrySetVersion, version_id)
        confirmation = (
            await session.scalar(
                select(CampusEntryConfirmation).where(
                    CampusEntryConfirmation.campus_entry_set_version_id == version_id,
                    CampusEntryConfirmation.input_fingerprint == version.content_hash,
                )
            )
            if version is not None
            else None
        )
        if confirmation is None:
            raise DomainError(
                code="CAMPUS_ENTRY_CONFIRMATION_REQUIRED",
                message="候选改变了校区入口合计，部门预算负责人确认后才能继续完整计算",
                status_code=409,
            )


async def _calculate_headcount(
    job_id: uuid.UUID,
    actor: Actor,
    revision_id: uuid.UUID,
    request_id: str,
    acting_role: Role,
) -> HeadcountResultVersionResponse:
    # Read the immutable source revision, not the mutable current-draft pointer.
    # Close this transaction before the command opens its own transaction.
    async with get_session_factory()() as session:
        _, budget = await _assert_version_access(session, actor=actor, revision_id=revision_id)
        rule = OPENING_HEADCOUNT_RULE_VERSION if budget.fiscal_year >= 2028 else HEADCOUNT_RULE_VERSION
    async with get_session_factory()() as session:
        return await calculate_headcount_result(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=revision_id,
            payload=HeadcountCalculateRequest(calculation_rule_version=rule),
            idempotency_key=uuid.uuid5(job_id, "HEADCOUNT"),
            request_id=_stage_request_id(job_id, request_id, "headcount"),
            acting_role=acting_role,
        )


async def _validate_class_size(
    job_id: uuid.UUID,
    actor: Actor,
    revision_id: uuid.UUID,
    request_id: str,
    acting_role: Role,
) -> ClassSizeResultVersionResponse:
    async with get_session_factory()() as session:
        return await validate_class_size_result(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=revision_id,
            payload=ClassSizeValidateRequest(),
            idempotency_key=uuid.uuid5(job_id, "CLASS_SIZE"),
            request_id=_stage_request_id(job_id, request_id, "class-size"),
            acting_role=acting_role,
        )


async def _calculate_revenue(
    job_id: uuid.UUID,
    actor: Actor,
    revision_id: uuid.UUID,
    request_id: str,
    acting_role: Role,
) -> RevenueResultVersionResponse:
    async with get_session_factory()() as session:
        return await calculate_revenue_result(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=revision_id,
            payload=RevenueCalculateRequest(),
            idempotency_key=uuid.uuid5(job_id, "REVENUE"),
            request_id=_stage_request_id(job_id, request_id, "revenue"),
            acting_role=acting_role,
        )


async def _validate_closure(
    job_id: uuid.UUID,
    actor: Actor,
    revision_id: uuid.UUID,
    request_id: str,
    acting_role: Role,
) -> TargetClosureResultVersionResponse:
    async with get_session_factory()() as session:
        return await validate_target_closure(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=revision_id,
            payload=TargetClosureValidateRequest(),
            idempotency_key=uuid.uuid5(job_id, "TARGET_CLOSURE"),
            request_id=_stage_request_id(job_id, request_id, "target-closure"),
            acting_role=acting_role,
        )


def _stage_request_id(job_id: uuid.UUID, original_request_id: str, stage: str) -> str:
    return f"job:{job_id}:{stage}:{original_request_id}"[:100]
