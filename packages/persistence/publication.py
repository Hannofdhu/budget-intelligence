import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    BudgetCalculationJobResponse,
    BudgetPublishRequest,
    FormalPlanConfirmationResponse,
    FormalPlanConfirmRequest,
    PlannedNewStudentConfirmationSummary,
    PublicationCheckResponse,
    PublicationReadinessResponse,
    PublishedBudgetBaselineResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.calculation_queue import create_full_calculation_job
from packages.persistence.entry_planning import (
    _assert_version_access,
    _create_child_revision,
    _lock_current_revision,
)
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    AnnualBudget,
    BudgetCombination,
    BudgetCombinationSetVersion,
    BudgetRevision,
    CampusEntryConfirmation,
    CampusEntrySetVersion,
    ClassSizeResultVersion,
    CombinationSetConfirmation,
    DiscountParameterConfirmation,
    DiscountParameterVersion,
    EntryDetailSetVersion,
    EntryScopeConfirmation,
    EntryScopeVersion,
    EntryTotalVersion,
    FormalPlanConfirmation,
    HeadcountResultVersion,
    PlannedNewStudentCount,
    PlannedNewStudentVersion,
    RateBoundaryConfirmation,
    RateParameter,
    RateParameterCopyException,
    RateParameterVersion,
    ResourceFinanceConfirmation,
    ResourceFinanceParameterVersion,
    RevenueResultVersion,
    RevisionLifecycleEvent,
    SourceSnapshot,
    TargetClosureResult,
    TargetClosureResultVersion,
    TargetSetVersion,
)
from packages.persistence.rate_parameters import _row_boundary_fingerprint
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)

PUBLICATION_GATE_RULE_VERSION = "phase1-publication-gate-v1"
REQUIRED_COMPONENTS = (
    "target_set_version_id",
    "combination_set_version_id",
    "entry_total_version_id",
    "campus_entry_set_version_id",
    "entry_scope_version_id",
    "entry_detail_set_version_id",
    "rate_parameter_version_id",
    "resource_finance_parameter_version_id",
    "discount_parameter_version_id",
    "headcount_result_version_id",
    "class_size_result_version_id",
    "revenue_result_version_id",
    "target_closure_result_version_id",
)


def publication_input_fingerprint(revision: BudgetRevision) -> str:
    manifest = {
        key: value for key, value in revision.component_manifest.items() if key != "formal_confirmation_id"
    }
    return content_hash(
        {
            "schema_version": "1.0",
            "gate_rule_version": PUBLICATION_GATE_RULE_VERSION,
            "annual_budget_id": str(revision.annual_budget_id),
            "components": manifest,
        }
    )


async def enqueue_formal_plan_confirmation(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: FormalPlanConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> BudgetCalculationJobResponse:
    _require_budget_owner(actor)
    command_type = "REQUEST_FORMAL_PLAN_CONFIRMATION"
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
        if "formal_confirmation_id" in revision.component_manifest:
            raise DomainError(
                code="FORMAL_PLAN_ALREADY_CONFIRMED",
                message="当前预算修订已经完成正式方案确认",
                status_code=409,
            )
        job = await create_full_calculation_job(
            session,
            actor=actor,
            revision=revision,
            request_id=request_id,
            source="FORMAL_CONFIRMATION",
            calculation_pipeline_version=payload.calculation_pipeline_version,
            formal_confirmation_reason=payload.reason.strip(),
        )
        response = BudgetCalculationJobResponse(
            job_id=job.job_id,
            source_revision_id=revision.budget_revision_id,
            calculation_pipeline_version=payload.calculation_pipeline_version,
            status=job.status,
            links={"job": f"/api/v1/jobs/{job.job_id}"},
        )
        occurred_at = _now()
        _add_audit(
            session,
            event_type="FORMAL_PLAN_CONFIRMATION_REQUESTED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="BUDGET_REVISION",
            object_key={"budget_revision_id": str(revision.budget_revision_id)},
            detail={
                "calculation_job_id": str(job.job_id),
                "calculation_pipeline_version": payload.calculation_pipeline_version,
                "reason": payload.reason.strip(),
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


async def get_publication_readiness(
    session: AsyncSession, *, actor: Actor, revision_id: uuid.UUID
) -> PublicationReadinessResponse:
    revision = await session.get(BudgetRevision, revision_id)
    await _assert_version_access(session, actor=actor, revision_id=revision_id)
    assert revision is not None
    return await _build_publication_readiness(session, revision)


async def finalize_formal_plan_confirmation(
    session: AsyncSession,
    *,
    actor: Actor,
    source_revision_id: uuid.UUID,
    calculated_revision_id: uuid.UUID,
    reason: str,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> FormalPlanConfirmationResponse:
    _require_budget_owner(actor)
    command_type = "FINALIZE_FORMAL_PLAN_CONFIRMATION"
    request_hash = content_hash(
        {
            "source_revision_id": str(source_revision_id),
            "calculated_revision_id": str(calculated_revision_id),
            "reason": reason,
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
            return FormalPlanConfirmationResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=calculated_revision_id,
            expected_revision_id=calculated_revision_id,
        )
        readiness = await _build_publication_readiness(session, parent)
        if readiness.blocker_count:
            raise DomainError(
                code="FORMAL_PLAN_NOT_CONFIRMABLE",
                message="完整复算仍存在发布阻断，未生成正式方案确认",
                status_code=422,
                details={
                    "blockers": [item.model_dump(mode="json") for item in readiness.checks if not item.passed]
                },
            )
        occurred_at = _now()
        confirmation_id = uuid7()
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates={"formal_confirmation_id": str(confirmation_id)},
        )
        summary = readiness.model_dump(mode="json")
        confirmation_content = {
            "schema_version": "1.0",
            "source_revision_id": str(source_revision_id),
            "confirmed_revision_id": str(revision.budget_revision_id),
            "input_fingerprint": readiness.input_fingerprint,
            "reason": reason.strip(),
            "summary": summary,
            "confirmed_by": actor.user_id,
            "role_snapshot": _role_snapshot(actor),
            "confirmed_at": occurred_at.isoformat(),
        }
        confirmation = FormalPlanConfirmation(
            formal_plan_confirmation_id=confirmation_id,
            source_revision_id=source_revision_id,
            confirmed_revision_id=revision.budget_revision_id,
            input_fingerprint=readiness.input_fingerprint,
            reason=reason.strip(),
            summary=summary,
            confirmed_by=actor.user_id,
            role_snapshot=_role_snapshot(actor),
            confirmed_at=occurred_at,
            content_hash=content_hash(confirmation_content),
        )
        session.add(confirmation)
        session.add(
            RevisionLifecycleEvent(
                revision_lifecycle_event_id=uuid7(),
                budget_revision_id=revision.budget_revision_id,
                event_type="FORMAL_PLAN_CONFIRMED",
                actor_id=actor.user_id,
                detail={
                    "schema_version": "1.0",
                    "formal_plan_confirmation_id": str(confirmation_id),
                    "input_fingerprint": readiness.input_fingerprint,
                    "source_revision_id": str(source_revision_id),
                },
                occurred_at=occurred_at,
            )
        )
        response = FormalPlanConfirmationResponse(
            formal_plan_confirmation_id=confirmation_id,
            source_revision_id=source_revision_id,
            confirmed_revision_id=revision.budget_revision_id,
            input_fingerprint=readiness.input_fingerprint,
            reason=confirmation.reason,
            summary=summary,
            confirmed_by=actor.user_id,
            confirmed_at=occurred_at,
            content_hash=confirmation.content_hash,
            revision=revision_response,
        )
        _add_audit(
            session,
            event_type="FORMAL_PLAN_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="FORMAL_PLAN_CONFIRMATION",
            object_key={"formal_plan_confirmation_id": str(confirmation_id)},
            detail={
                "source_revision_id": str(source_revision_id),
                "input_fingerprint": readiness.input_fingerprint,
                "content_hash": confirmation.content_hash,
                "planned_new_student_count": len(readiness.planned_new_students),
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


async def publish_budget_revision(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: BudgetPublishRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> PublishedBudgetBaselineResponse:
    _require_budget_owner(actor)
    command_type = "PUBLISH_ANNUAL_BUDGET_BASELINE"
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
            return PublishedBudgetBaselineResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        existing = await session.scalar(
            select(RevisionLifecycleEvent).where(
                RevisionLifecycleEvent.budget_revision_id == revision_id,
                RevisionLifecycleEvent.event_type == "PUBLISHED",
            )
        )
        if existing is not None:
            response = PublishedBudgetBaselineResponse.model_validate(existing.detail["response"])
            _store_receipt(
                session,
                actor=actor,
                command_type=command_type,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response=response.model_dump(mode="json"),
                occurred_at=_now(),
            )
            return response
        confirmation_id = _manifest_uuid(revision, "formal_confirmation_id")
        confirmation = await session.get(FormalPlanConfirmation, confirmation_id)
        if confirmation is None or confirmation.confirmed_revision_id != revision_id:
            raise DomainError(
                code="FORMAL_PLAN_CONFIRMATION_REQUIRED",
                message="当前预算修订尚未完成正式方案确认",
                status_code=409,
            )
        readiness = await _build_publication_readiness(session, revision)
        if readiness.blocker_count:
            raise DomainError(
                code="PUBLICATION_BLOCKED",
                message="当前预算修订仍存在发布硬约束阻断",
                status_code=422,
                details={
                    "blockers": [item.model_dump(mode="json") for item in readiness.checks if not item.passed]
                },
            )
        if confirmation.input_fingerprint != readiness.input_fingerprint:
            raise DomainError(
                code="FORMAL_PLAN_CONFIRMATION_STALE",
                message="正式方案确认与当前发布输入指纹不一致",
                status_code=409,
            )
        occurred_at = _now()
        previous_published_revision_id = budget.current_published_revision_id
        publication_content = {
            "schema_version": "1.0",
            "annual_budget_id": str(budget.annual_budget_id),
            "fiscal_year": budget.fiscal_year,
            "published_revision_id": str(revision_id),
            "previous_published_revision_id": (
                str(previous_published_revision_id) if previous_published_revision_id is not None else None
            ),
            "formal_plan_confirmation_id": str(confirmation_id),
            "publication_input_fingerprint": readiness.input_fingerprint,
            "component_manifest": revision.component_manifest,
            "checks": [item.model_dump(mode="json") for item in readiness.checks],
            "published_by": actor.user_id,
            "role_snapshot": _role_snapshot(actor),
            "published_at": occurred_at.isoformat(),
            "publication_note": payload.publication_note,
        }
        publication_hash = content_hash(publication_content)
        response = PublishedBudgetBaselineResponse(
            annual_budget_id=budget.annual_budget_id,
            fiscal_year=budget.fiscal_year,
            published_revision_id=revision_id,
            previous_published_revision_id=previous_published_revision_id,
            formal_plan_confirmation_id=confirmation_id,
            publication_input_fingerprint=readiness.input_fingerprint,
            published_by=actor.user_id,
            published_at=occurred_at,
            content_hash=publication_hash,
            links={
                "revision": f"/api/v1/revisions/{revision_id}",
                "annual_budget": f"/api/v1/annual-budgets/{budget.annual_budget_id}",
            },
        )
        response_json = response.model_dump(mode="json")
        budget.current_published_revision_id = revision_id
        session.add_all(
            [
                RevisionLifecycleEvent(
                    revision_lifecycle_event_id=uuid7(),
                    budget_revision_id=revision_id,
                    event_type="PUBLISHED",
                    actor_id=actor.user_id,
                    detail={
                        **publication_content,
                        "content_hash": publication_hash,
                        "response": response_json,
                    },
                    occurred_at=occurred_at,
                ),
                RevisionLifecycleEvent(
                    revision_lifecycle_event_id=uuid7(),
                    budget_revision_id=revision_id,
                    event_type="CURRENT_POINTER_MOVED",
                    actor_id=actor.user_id,
                    detail={
                        "schema_version": "1.0",
                        "pointer": "current_published_revision_id",
                        "previous_revision_id": (
                            str(previous_published_revision_id)
                            if previous_published_revision_id is not None
                            else None
                        ),
                        "current_revision_id": str(revision_id),
                    },
                    occurred_at=occurred_at,
                ),
            ]
        )
        _add_audit(
            session,
            event_type="PUBLISHED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="ANNUAL_BUDGET_BASELINE",
            object_key={"budget_revision_id": str(revision_id)},
            detail={
                "formal_plan_confirmation_id": str(confirmation_id),
                "publication_input_fingerprint": readiness.input_fingerprint,
                "content_hash": publication_hash,
                "previous_published_revision_id": (
                    str(previous_published_revision_id)
                    if previous_published_revision_id is not None
                    else None
                ),
            },
            request_id=request_id,
            occurred_at=occurred_at,
            annual_budget_id=budget.annual_budget_id,
            budget_revision_id=revision_id,
        )
        _store_receipt(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=response_json,
            occurred_at=occurred_at,
        )
    return response


async def _build_publication_readiness(
    session: AsyncSession, revision: BudgetRevision
) -> PublicationReadinessResponse:
    budget = await session.get(AnnualBudget, revision.annual_budget_id)
    source = await session.get(SourceSnapshot, revision.source_snapshot_id)
    ids = {key: _optional_manifest_uuid(revision, key) for key in REQUIRED_COMPONENTS}
    combination = await _get(session, BudgetCombinationSetVersion, ids["combination_set_version_id"])
    entry_total = await _get(session, EntryTotalVersion, ids["entry_total_version_id"])
    campus = await _get(session, CampusEntrySetVersion, ids["campus_entry_set_version_id"])
    scope = await _get(session, EntryScopeVersion, ids["entry_scope_version_id"])
    detail = await _get(session, EntryDetailSetVersion, ids["entry_detail_set_version_id"])
    rate = await _get(session, RateParameterVersion, ids["rate_parameter_version_id"])
    resource = await _get(
        session, ResourceFinanceParameterVersion, ids["resource_finance_parameter_version_id"]
    )
    discount = await _get(session, DiscountParameterVersion, ids["discount_parameter_version_id"])
    headcount = await _get(session, HeadcountResultVersion, ids["headcount_result_version_id"])
    class_size = await _get(session, ClassSizeResultVersion, ids["class_size_result_version_id"])
    revenue = await _get(session, RevenueResultVersion, ids["revenue_result_version_id"])
    closure = await _get(session, TargetClosureResultVersion, ids["target_closure_result_version_id"])
    target = await _get(session, TargetSetVersion, ids["target_set_version_id"])
    plan_id = _optional_manifest_uuid(revision, "planned_new_student_version_id")
    plan = await _get(session, PlannedNewStudentVersion, plan_id)

    combination_confirmation = await _confirmation(
        session,
        CombinationSetConfirmation,
        CombinationSetConfirmation.combination_set_version_id,
        combination.combination_set_version_id if combination else None,
        CombinationSetConfirmation.input_fingerprint,
        combination.content_hash if combination else None,
    )
    campus_confirmation = await _confirmation(
        session,
        CampusEntryConfirmation,
        CampusEntryConfirmation.campus_entry_set_version_id,
        campus.campus_entry_set_version_id if campus else None,
        CampusEntryConfirmation.input_fingerprint,
        campus.content_hash if campus else None,
    )
    scope_confirmation = await _confirmation(
        session,
        EntryScopeConfirmation,
        EntryScopeConfirmation.entry_scope_version_id,
        scope.entry_scope_version_id if scope else None,
        EntryScopeConfirmation.input_fingerprint,
        scope.content_hash if scope else None,
    )
    resource_confirmation = await _confirmation(
        session,
        ResourceFinanceConfirmation,
        ResourceFinanceConfirmation.resource_finance_parameter_version_id,
        resource.resource_finance_parameter_version_id if resource else None,
        ResourceFinanceConfirmation.input_fingerprint,
        resource.content_hash if resource else None,
    )
    discount_confirmation = await _confirmation(
        session,
        DiscountParameterConfirmation,
        DiscountParameterConfirmation.discount_parameter_version_id,
        discount.discount_parameter_version_id if discount else None,
        DiscountParameterConfirmation.input_fingerprint,
        discount.content_hash if discount else None,
    )
    rate_rows = (
        list(
            await session.scalars(
                select(RateParameter).where(
                    RateParameter.rate_parameter_version_id == rate.rate_parameter_version_id
                )
            )
        )
        if rate
        else []
    )
    rate_confirmations = (
        list(
            await session.scalars(
                select(RateBoundaryConfirmation).where(
                    RateBoundaryConfirmation.rate_parameter_version_id == rate.rate_parameter_version_id
                )
            )
        )
        if rate
        else []
    )
    rate_confirmation_map = {
        item.rate_parameter_stable_key: item.boundary_fingerprint for item in rate_confirmations
    }
    rate_boundaries_current = bool(rate_rows) and all(
        rate_confirmation_map.get(row.rate_parameter_stable_key) == _row_boundary_fingerprint(row)
        for row in rate_rows
    )
    rate_exception_count = (
        len(
            list(
                await session.scalars(
                    select(RateParameterCopyException).where(
                        RateParameterCopyException.rate_parameter_version_id == rate.rate_parameter_version_id
                    )
                )
            )
        )
        if rate
        else 0
    )
    closure_rows = (
        list(
            await session.scalars(
                select(TargetClosureResult).where(
                    TargetClosureResult.target_closure_result_version_id
                    == closure.target_closure_result_version_id
                )
            )
        )
        if closure
        else []
    )
    combination_rows = (
        list(
            await session.scalars(
                select(BudgetCombination).where(
                    BudgetCombination.combination_set_version_id == combination.combination_set_version_id,
                    BudgetCombination.enabled.is_(True),
                )
            )
        )
        if combination
        else []
    )
    plan_rows = (
        list(
            await session.scalars(
                select(PlannedNewStudentCount)
                .where(
                    PlannedNewStudentCount.planned_new_student_version_id
                    == plan.planned_new_student_version_id
                )
                .order_by(
                    PlannedNewStudentCount.fiscal_season,
                    PlannedNewStudentCount.track,
                    PlannedNewStudentCount.product,
                    PlannedNewStudentCount.grade,
                    PlannedNewStudentCount.subject,
                    PlannedNewStudentCount.campus,
                )
            )
        )
        if plan
        else []
    )
    required_present = all(ids.values())
    exact_dimensions = all(
        row.track.strip()
        and row.product.strip()
        and row.grade.strip()
        and row.subject.strip()
        and row.campus.strip()
        for row in combination_rows
    )
    public_keys = [(row.track, row.product, row.grade, row.subject, row.campus) for row in combination_rows]
    if (
        combination is None
        or scope is None
        or detail is None
        or rate is None
        or resource is None
        or discount is None
        or target is None
        or headcount is None
        or class_size is None
        or revenue is None
        or closure is None
    ):
        result_chain_current = False
    else:
        result_chain_current = (
            class_size.headcount_result_version_id == headcount.headcount_result_version_id
            and revenue.headcount_result_version_id == headcount.headcount_result_version_id
            and revenue.class_size_result_version_id == class_size.class_size_result_version_id
            and closure.revenue_result_version_id == revenue.revenue_result_version_id
            and closure.target_set_version_id == target.target_set_version_id
            and headcount.combination_set_version_id == combination.combination_set_version_id
            and headcount.entry_scope_version_id == scope.entry_scope_version_id
            and headcount.entry_detail_set_version_id == detail.entry_detail_set_version_id
            and headcount.rate_parameter_version_id == rate.rate_parameter_version_id
            and headcount.planned_new_student_version_id
            == (plan.planned_new_student_version_id if plan else None)
            and class_size.resource_finance_parameter_version_id
            == resource.resource_finance_parameter_version_id
            and revenue.resource_finance_parameter_version_id
            == resource.resource_finance_parameter_version_id
            and revenue.discount_parameter_version_id == discount.discount_parameter_version_id
        )
    entry_balanced = (
        entry_total is not None
        and campus is not None
        and detail is not None
        and campus_confirmation is not None
        and campus.determined_total == entry_total.locked_value
        and detail.allocated_entry_total == campus.determined_total
    )
    all_targets_closed = (
        closure is not None
        and target is not None
        and closure.publishable
        and closure.closure_status == "WITHIN_TOLERANCE"
        and bool(closure_rows)
        and all(row.check_status == "WITHIN_TOLERANCE" for row in closure_rows)
        and sum(row.scope_level == "DEPARTMENT" for row in closure_rows) == 1
        and sum(row.scope_level == "TRACK" for row in closure_rows) >= 1
    )
    opening_ready = (
        budget is not None
        and budget.fiscal_year < 2028
        and "opening_student_version_id" not in revision.component_manifest
    )
    opening_error = None
    if budget is not None and budget.fiscal_year >= 2028:
        from packages.headcount.calculation import OPENING_HEADCOUNT_RULE_VERSION
        from packages.persistence.headcount import _load_basis

        try:
            basis = await _load_basis(session, revision, budget.fiscal_year, allow_opening=True)
            expected_headcount_hash = content_hash(
                {
                    "schema_version": "1.0",
                    "calculation_rule_version": OPENING_HEADCOUNT_RULE_VERSION,
                    "opening_student_version_hash": basis.opening_hash,
                    "combination_set_version": basis.combination_version.content_hash,
                    "entry_scope_version": basis.scope_version.content_hash,
                    "entry_detail_set_version": basis.detail_version.content_hash,
                    "rate_parameter_version": basis.rate_version.content_hash,
                    "planned_new_student_version": basis.plan_version.content_hash
                    if basis.plan_version
                    else None,
                }
            )
            opening_ready = bool(
                headcount
                and headcount.calculation_rule_version == OPENING_HEADCOUNT_RULE_VERSION
                and headcount.input_fingerprint == expected_headcount_hash
            )
        except DomainError as error:
            opening_error = error.code
    checks = [
        _check(
            "OPENING_CALCULATION_READY",
            "HEADCOUNT",
            opening_ready,
            {
                "opening_student_version_id": revision.component_manifest.get("opening_student_version_id"),
                "error_code": opening_error,
            },
            "FY28 起须核验期初来源、当前依赖版本和 v2 人数计算指纹；FY27 保留历史规则",
            f"/api/v1/revisions/{revision.budget_revision_id}",
        ),
        _check(
            "CURRENT_DRAFT_REVISION",
            "VERSION",
            budget is not None and budget.current_draft_revision_id == revision.budget_revision_id,
            {"current_draft_revision_id": str(budget.current_draft_revision_id) if budget else None},
            "只能确认或发布当前预算草稿修订",
            f"/api/v1/annual-budgets/{revision.annual_budget_id}",
        ),
        _check(
            "SOURCE_SNAPSHOT_FROZEN",
            "SOURCE",
            source is not None and source.status == "FROZEN",
            {"status": source.status if source else None},
            "正式参数必须绑定已对账冻结的来源快照",
            f"/api/v1/source-snapshots/{revision.source_snapshot_id}",
        ),
        _check(
            "REQUIRED_COMPONENTS_PRESENT",
            "VERSION",
            required_present,
            {"missing": [key for key, value in ids.items() if value is None]},
            "发布所需输入、参数和四阶段结果版本必须齐全",
            f"/api/v1/revisions/{revision.budget_revision_id}:calculate",
        ),
        _check(
            "BUDGET_COMBINATIONS_CONFIRMED",
            "COMBINATION",
            combination is not None and combination_confirmation is not None,
            {"record_count": combination.record_count if combination else 0},
            "预算组合必须由部门预算负责人确认",
            "/budget/combinations",
        ),
        _check(
            "BUDGET_COMBINATION_KEYS_EXACT",
            "COMBINATION",
            bool(combination_rows) and exact_dimensions and len(public_keys) == len(set(public_keys)),
            {"enabled_count": len(combination_rows), "unique_count": len(set(public_keys))},
            "启用组合必须填写全部具体维度且公共维度不得重复",
            "/budget/combinations",
        ),
        _check(
            "CAMPUS_ENTRY_CONFIRMED_AND_BALANCED",
            "ENTRY",
            entry_balanced,
            {
                "entry_total": entry_total.locked_value if entry_total else None,
                "campus_total": campus.determined_total if campus else None,
                "detail_total": detail.allocated_entry_total if detail else None,
            },
            "校区入口必须确认，且校区及明细合计严格等于已锁定财年入口总值",
            "/budget/entry-allocation",
        ),
        _check(
            "ENTRY_SCOPE_CONFIRMED",
            "ENTRY",
            scope is not None and scope_confirmation is not None,
            {"record_count": scope.record_count if scope else 0},
            "入口范围必须由部门预算负责人确认",
            "/budget/entry-scope",
        ),
        _check(
            "RATE_BOUNDARIES_CONFIRMED",
            "RATE",
            rate is not None and rate_boundaries_current and rate_exception_count == 0,
            {
                "record_count": len(rate_rows),
                "confirmed_count": sum(
                    rate_confirmation_map.get(row.rate_parameter_stable_key) == _row_boundary_fingerprint(row)
                    for row in rate_rows
                ),
                "copy_exception_count": rate_exception_count,
            },
            "所有比例边界必须确认且不存在跨财年复制异常",
            "/budget/rates",
        ),
        _check(
            "PLANNED_NEW_STUDENTS_LOCKED",
            "HEADCOUNT",
            plan is None or (plan.locked_count == plan.record_count and all(row.locked for row in plan_rows)),
            {
                "record_count": plan.record_count if plan else 0,
                "locked_count": plan.locked_count if plan else 0,
            },
            "已填写计划新生人次必须逐条锁定；锁定零与空值严格区分",
            "/budget/headcount",
        ),
        _check(
            "RESOURCE_FINANCE_CONFIRMED",
            "RESOURCE_FINANCE",
            resource is not None and resource_confirmation is not None,
            {"confirmed": resource_confirmation is not None},
            "资源与财务参数版本必须由部门预算负责人确认",
            "/budget/resource-finance",
        ),
        _check(
            "DISCOUNT_PARAMETERS_CONFIRMED",
            "DISCOUNT",
            discount is not None and discount_confirmation is not None,
            {"confirmed": discount_confirmation is not None},
            "折扣参数版本必须由部门预算负责人确认",
            "/budget/discounts",
        ),
        _check(
            "CALCULATION_RESULT_CHAIN_CURRENT",
            "CALCULATION",
            result_chain_current,
            {"current": result_chain_current},
            "人数、班均、收入和目标闭合必须引用同一组当前输入",
            f"/api/v1/revisions/{revision.budget_revision_id}:calculate",
        ),
        _check(
            "HEADCOUNT_AND_CLASS_SIZE_PUBLISHABLE",
            "CALCULATION",
            bool(headcount and class_size and headcount.publishable and class_size.publishable),
            {
                "headcount_publishable": headcount.publishable if headcount else False,
                "class_size_publishable": class_size.publishable if class_size else False,
            },
            "人数不得缺失，且不得存在未处理的不可成班或全年全零组合",
            "/budget/class-size",
        ),
        _check(
            "REVENUE_AND_DISCOUNT_PUBLISHABLE",
            "REVENUE",
            bool(revenue and revenue.publishable),
            {"revenue_publishable": revenue.publishable if revenue else False},
            "收入、结转和完整精度综合折扣校验必须全部通过",
            "/budget/revenue",
        ),
        _check(
            "ALL_TARGETS_WITHIN_TOLERANCE",
            "TARGET",
            all_targets_closed,
            {
                "closure_status": closure.closure_status if closure else None,
                "department_count": sum(row.scope_level == "DEPARTMENT" for row in closure_rows),
                "track_count": sum(row.scope_level == "TRACK" for row in closure_rows),
                "failed_targets": [
                    row.target_stable_key for row in closure_rows if row.check_status != "WITHIN_TOLERANCE"
                ],
            },
            "部门合计及每个赛道必须分别进入正负一万元容差，禁止跨赛道抵消",
            "/budget/targets",
        ),
    ]
    planned_summary = [
        PlannedNewStudentConfirmationSummary(
            fiscal_season=row.fiscal_season,
            track=row.track,
            product=row.product,
            entry_attribute=row.entry_attribute,
            grade=row.grade,
            subject=row.subject,
            campus=row.campus,
            planned_new_students=row.planned_new_students,
            reason=row.manual_reason,
            locked=row.locked,
            handling_status=row.handling_status,
        )
        for row in plan_rows
    ]
    blocker_count = sum(not item.passed for item in checks)
    return PublicationReadinessResponse(
        budget_revision_id=revision.budget_revision_id,
        input_fingerprint=publication_input_fingerprint(revision),
        gate_rule_version=PUBLICATION_GATE_RULE_VERSION,
        status="READY" if blocker_count == 0 else "BLOCKED",
        blocker_count=blocker_count,
        checks=checks,
        planned_new_students=planned_summary,
    )


def _check(
    code: str,
    group: str,
    passed: bool,
    actual: dict[str, object],
    rule: str,
    repair_route: str,
) -> PublicationCheckResponse:
    return PublicationCheckResponse(
        code=code,
        group=group,
        passed=passed,
        actual=actual,
        rule=rule,
        repair_route=repair_route,
    )


def _optional_manifest_uuid(revision: BudgetRevision, key: str) -> uuid.UUID | None:
    raw = revision.component_manifest.get(key)
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _manifest_uuid(revision: BudgetRevision, key: str) -> uuid.UUID:
    value = _optional_manifest_uuid(revision, key)
    if value is None:
        raise DomainError(
            code="FORMAL_PLAN_CONFIRMATION_REQUIRED",
            message="当前预算修订尚未完成正式方案确认",
            status_code=409,
        )
    return value


async def _get(session: AsyncSession, model: type[Any], item_id: uuid.UUID | None) -> Any | None:
    return await session.get(model, item_id) if item_id is not None else None


async def _confirmation(
    session: AsyncSession,
    model: type[Any],
    id_column: Any,
    item_id: uuid.UUID | None,
    fingerprint_column: Any,
    fingerprint: str | None,
) -> Any | None:
    if item_id is None or fingerprint is None:
        return None
    return await session.scalar(select(model).where(id_column == item_id, fingerprint_column == fingerprint))


def _require_budget_owner(actor: Actor) -> None:
    if not actor.has_role(Role.DEPARTMENT_BUDGET_OWNER):
        raise DomainError(
            code="ROLE_FORBIDDEN",
            message="该命令要求部门预算负责人角色",
            status_code=403,
        )
