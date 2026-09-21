import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    RevisionCommandResponse,
    ScenarioApplicationResponse,
    ScenarioApplicationRevertRequest,
    ScenarioApplicationRevertResponse,
    ScenarioApplicationStatusResponse,
    ScenarioApplyRequest,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.persistence.calculation_queue import create_full_calculation_job
from packages.persistence.entry_planning import (
    _assert_version_access,
    _create_child_revision,
    _lock_current_revision,
    _manifest_uuid,
)
from packages.persistence.headcount import _load_plan_state
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    AsyncJob,
    BudgetRevision,
    CampusEntryAllocation,
    CampusEntrySetVersion,
    CompositeDiscountTarget,
    DetailDiscountParameter,
    DiscountParameterConfirmation,
    DiscountParameterVersion,
    DiscountUnlockConfirmation,
    EntryDetailAllocation,
    EntryDetailSetVersion,
    OptimizationRun,
    PlannedNewStudentCount,
    PlannedNewStudentVersion,
    RateBoundaryConfirmation,
    RateParameter,
    RateParameterCopyException,
    RateParameterVersion,
    RebalancingScenario,
    RevisionLifecycleEvent,
    ScenarioApplication,
    ScenarioApplicationRevert,
    ScenarioChange,
)
from packages.persistence.optimization import _build_context
from packages.persistence.rate_parameters import _current_rate_state, _row_boundary_fingerprint
from packages.persistence.revenue import _discount_confirmation, _load_discount_rows
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)

INVALIDATED_COMPONENTS = (
    "headcount_result_version_id",
    "class_size_result_version_id",
    "revenue_result_version_id",
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)

REVERT_EXCLUDED_COMPONENTS = frozenset((*INVALIDATED_COMPONENTS, "scenario_application_id"))
CALCULATION_COMMANDS = frozenset(
    {
        "CALCULATE_HEADCOUNT_RESULT",
        "VALIDATE_CLASS_SIZE_RESULT",
        "CALCULATE_REVENUE_RESULT",
        "VALIDATE_TARGET_CLOSURE",
    }
)


async def _revert_lineage(
    session: AsyncSession, parent: BudgetRevision, application: ScenarioApplication
) -> list[uuid.UUID]:
    applied = await session.get(BudgetRevision, application.applied_revision_id)
    if applied is None or applied.annual_budget_id != parent.annual_budget_id:
        raise DomainError(code="SCENARIO_APPLICATION_NOT_FOUND", message="候选应用不存在", status_code=404)
    cursor = parent
    lineage: list[uuid.UUID] = []
    while True:
        lineage.append(cursor.budget_revision_id)
        if (
            _restorable_source_components(cursor.component_manifest)
            != _restorable_source_components(applied.component_manifest)
            or "formal_confirmation_id" in cursor.component_manifest
        ):
            raise DomainError(
                code="SCENARIO_APPLICATION_REVERT_INPUT_CHANGED",
                message="应用后已有输入修改或正式确认，请保留后续工作并重新制定方案",
                status_code=409,
            )
        if cursor.budget_revision_id == applied.budget_revision_id:
            break
        if cursor.command_type not in CALCULATION_COMMANDS or cursor.parent_revision_id is None:
            raise DomainError(
                code="SCENARIO_APPLICATION_REVERT_INPUT_CHANGED",
                message="应用后已有非计算修订，不能一键覆盖后续工作",
                status_code=409,
            )
        ancestor = await session.get(BudgetRevision, cursor.parent_revision_id)
        if ancestor is None or ancestor.annual_budget_id != parent.annual_budget_id:
            raise DomainError(code="REVISION_LINEAGE_INVALID", message="修订链不完整", status_code=409)
        cursor = ancestor
    published = await session.scalar(
        select(RevisionLifecycleEvent.revision_lifecycle_event_id)
        .where(
            RevisionLifecycleEvent.budget_revision_id.in_(lineage),
            RevisionLifecycleEvent.event_type == "PUBLISHED",
        )
        .limit(1)
    )
    if published is not None:
        raise DomainError(code="PUBLISHED_REVISION_READ_ONLY", message="已发布预算不可撤销", status_code=409)
    return lineage


async def get_scenario_application_status(
    session: AsyncSession, *, actor: Actor, revision_id: uuid.UUID
) -> ScenarioApplicationStatusResponse:
    revision, budget = await _assert_version_access(session, actor=actor, revision_id=revision_id)
    if not revision.component_manifest.get("scenario_application_id"):
        raise DomainError(
            code="SCENARIO_APPLICATION_NOT_FOUND", message="当前修订没有候选应用", status_code=404
        )
    application_id = _manifest_uuid(revision, "scenario_application_id", "当前修订没有候选应用")
    application = await session.get(ScenarioApplication, application_id)
    current = await session.get(BudgetRevision, budget.current_draft_revision_id)
    if application is None or current is None:
        raise DomainError(code="SCENARIO_APPLICATION_NOT_FOUND", message="候选应用不存在", status_code=404)
    applied = await session.get(BudgetRevision, application.applied_revision_id)
    if applied is None or applied.annual_budget_id != budget.annual_budget_id:
        raise DomainError(code="SCENARIO_APPLICATION_NOT_FOUND", message="候选应用不存在", status_code=404)
    code = None
    existing = await session.scalar(
        select(ScenarioApplicationRevert).where(
            ScenarioApplicationRevert.scenario_application_id == application_id
        )
    )
    try:
        await _revert_lineage(session, current, application)
    except DomainError as exc:
        code = exc.code
    if existing is not None:
        code = "SCENARIO_APPLICATION_ALREADY_REVERTED"
    job = await session.scalar(
        select(AsyncJob).where(
            AsyncJob.dedupe_key
            == f"budget-calculation:{application.applied_revision_id}:phase1-full-calculation-v1"
        )
    )
    if job is None:
        raise DomainError(code="CALCULATION_JOB_NOT_FOUND", message="应用复算作业不存在", status_code=409)
    return ScenarioApplicationStatusResponse(
        scenario_application_id=application_id,
        applied_revision_id=application.applied_revision_id,
        current_revision_id=current.budget_revision_id,
        can_revert=code is None,
        blocking_code=code,
        application=_response(
            application,
            RevisionCommandResponse(
                annual_budget_id=budget.annual_budget_id,
                previous_revision_id=application.source_revision_id,
                new_revision_id=application.applied_revision_id,
                links={},
            ),
            job,
        ),
    )


async def apply_rebalancing_scenario(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    scenario_id: uuid.UUID,
    payload: ScenarioApplyRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> ScenarioApplicationResponse:
    command_type = "APPLY_REBALANCING_SCENARIO"
    request_hash = content_hash(
        {
            "revision_id": str(revision_id),
            "expected_revision_id": str(expected_revision_id),
            "scenario_id": str(scenario_id),
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
            return ScenarioApplicationResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        scenario = await session.get(RebalancingScenario, scenario_id)
        if scenario is None:
            raise DomainError(
                code="REBALANCING_SCENARIO_NOT_FOUND", message="候选调平方案不存在", status_code=404
            )
        existing_application = await session.scalar(
            select(ScenarioApplication).where(ScenarioApplication.rebalancing_scenario_id == scenario_id)
        )
        if existing_application is not None:
            raise DomainError(
                code="REBALANCING_SCENARIO_ALREADY_APPLIED",
                message="该候选方案已应用，不能重复生成预算修订",
                status_code=409,
                details={"applied_revision_id": str(existing_application.applied_revision_id)},
            )
        run = await session.get(OptimizationRun, scenario.optimization_run_id)
        if run is None:
            raise DomainError(
                code="OPTIMIZATION_RUN_NOT_FOUND", message="自动调平运行不存在", status_code=404
            )
        if run.budget_revision_id != parent.budget_revision_id or run.status != "OPTIMAL":
            raise DomainError(
                code="SCENARIO_NOT_APPLICABLE",
                message="候选方案不属于当前修订的最优调平运行",
                status_code=409,
            )
        _, _, current_fingerprint = await _build_context(session, parent, budget.fiscal_year)
        if run.input_fingerprint != current_fingerprint:
            raise DomainError(
                code="SCENARIO_INPUT_STALE",
                message="当前预算输入已变化，候选方案已失效",
                status_code=409,
            )
        changes = list(
            await session.scalars(
                select(ScenarioChange)
                .where(ScenarioChange.rebalancing_scenario_id == scenario_id)
                .order_by(ScenarioChange.object_type, ScenarioChange.object_stable_key)
            )
        )
        if not changes:
            raise DomainError(
                code="SCENARIO_HAS_NO_CHANGES",
                message="候选方案没有可应用的参数变更",
                status_code=409,
            )
        _validate_change_hashes(changes)
        counts = Counter(item.object_type for item in changes)
        reason = payload.reason.strip()
        occurred_at = _now()
        application_id = uuid7()

        entry_state = await _prepare_entry_state(session, parent, changes, reason)
        rate_state = await _prepare_rate_state(session, parent, changes)
        discount_state = await _prepare_discount_state(session, parent, run, changes, reason)

        component_updates = {"scenario_application_id": str(application_id)}
        component_updates.update(entry_state.component_updates)
        component_updates.update(rate_state.component_updates)
        component_updates.update(discount_state.component_updates)
        opening_version_id = None
        if "opening_student_version_id" in parent.component_manifest and rate_state.version_id is not None:
            opening_version_id = uuid7()
            component_updates["opening_student_version_id"] = str(opening_version_id)
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates=component_updates,
            component_removals=INVALIDATED_COMPONENTS,
        )
        await entry_state.persist(session, revision, actor, occurred_at)
        await rate_state.persist(session, revision, actor, occurred_at)
        if opening_version_id is not None:
            from packages.persistence.opening import rebuild_opening_for_application

            assert rate_state.version_id is not None
            await rebuild_opening_for_application(
                session,
                parent=parent,
                revision=revision,
                new_version_id=opening_version_id,
                rate_version_id=rate_state.version_id,
                actor=actor,
                occurred_at=occurred_at,
            )
        inherited_discount_confirmation_id = await discount_state.persist(
            session, revision, actor, occurred_at, scenario_id
        )
        calculation_job = await create_full_calculation_job(
            session,
            actor=actor,
            revision=revision,
            request_id=request_id,
            source="SCENARIO_APPLICATION",
            scenario_application_id=application_id,
            campus_confirmation_required=entry_state.campus_confirmation_required,
        )
        application_content = {
            "schema_version": "1.0",
            "rebalancing_scenario_id": str(scenario_id),
            "optimization_run_id": str(run.optimization_run_id),
            "source_revision_id": str(parent.budget_revision_id),
            "applied_revision_id": str(revision.budget_revision_id),
            "input_fingerprint": run.input_fingerprint,
            "reason": reason,
            "change_counts": dict(sorted(counts.items())),
            "created_component_versions": component_updates,
            "inherited_discount_confirmation_id": (
                str(inherited_discount_confirmation_id)
                if inherited_discount_confirmation_id is not None
                else None
            ),
            "campus_confirmation_required": entry_state.campus_confirmation_required,
            "invalidated_components": list(INVALIDATED_COMPONENTS),
            "calculation_job_id": str(calculation_job.job_id),
        }
        application = ScenarioApplication(
            scenario_application_id=application_id,
            rebalancing_scenario_id=scenario_id,
            optimization_run_id=run.optimization_run_id,
            source_revision_id=parent.budget_revision_id,
            applied_revision_id=revision.budget_revision_id,
            input_fingerprint=run.input_fingerprint,
            reason=reason,
            change_count=len(changes),
            entry_change_count=counts["ENTRY"],
            rate_change_count=counts["RATE"],
            discount_change_count=counts["DISCOUNT"],
            campus_entry_set_version_id=entry_state.campus_version_id,
            entry_detail_set_version_id=entry_state.entry_version_id,
            planned_new_student_version_id=entry_state.plan_version_id,
            rate_parameter_version_id=rate_state.version_id,
            discount_parameter_version_id=discount_state.version_id,
            inherited_discount_confirmation_id=inherited_discount_confirmation_id,
            campus_confirmation_required=entry_state.campus_confirmation_required,
            invalidated_components=list(INVALIDATED_COMPONENTS),
            applied_by=actor.user_id,
            role_snapshot=_role_snapshot(actor),
            applied_at=occurred_at,
            content_hash=content_hash(application_content),
            request_hash=request_hash,
        )
        session.add(application)
        await session.flush()
        response = _response(application, revision_response, calculation_job)
        _add_audit(
            session,
            event_type="REBALANCING_SCENARIO_APPLIED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="SCENARIO_APPLICATION",
            object_key={"scenario_application_id": str(application_id)},
            detail={
                "rebalancing_scenario_id": str(scenario_id),
                "optimization_run_id": str(run.optimization_run_id),
                "source_revision_id": str(parent.budget_revision_id),
                "applied_revision_id": str(revision.budget_revision_id),
                "change_counts": dict(sorted(counts.items())),
                "campus_confirmation_required": entry_state.campus_confirmation_required,
                "invalidated_components": list(INVALIDATED_COMPONENTS),
                "calculation_job_id": str(calculation_job.job_id),
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


async def revert_scenario_application(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    application_id: uuid.UUID,
    payload: ScenarioApplicationRevertRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> ScenarioApplicationRevertResponse:
    """Undo an applied candidate by creating a new immutable revision.

    Calculation-only descendants may be reverted; intervening input edits,
    formal confirmations and published revisions remain protected.
    """

    command_type = "REVERT_REBALANCING_SCENARIO_APPLICATION"
    request_hash = content_hash(
        {
            "revision_id": str(revision_id),
            "expected_revision_id": str(expected_revision_id),
            "scenario_application_id": str(application_id),
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
            return ScenarioApplicationRevertResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        application = await session.get(ScenarioApplication, application_id)
        if application is None:
            raise DomainError(
                code="SCENARIO_APPLICATION_NOT_FOUND", message="候选应用记录不存在", status_code=404
            )
        lineage = await _revert_lineage(session, parent, application)
        existing = await session.scalar(
            select(ScenarioApplicationRevert).where(
                ScenarioApplicationRevert.scenario_application_id == application_id
            )
        )
        if existing is not None:
            raise DomainError(
                code="SCENARIO_APPLICATION_ALREADY_REVERTED",
                message="该候选应用已被撤销",
                status_code=409,
            )
        source = await session.get(BudgetRevision, application.source_revision_id)
        if source is None or source.annual_budget_id != parent.annual_budget_id:
            raise DomainError(
                code="SCENARIO_APPLICATION_SOURCE_INVALID",
                message="候选应用的来源修订不存在或不属于当前年度预算",
                status_code=409,
            )

        restored_components = _restorable_source_components(source.component_manifest)
        reason = payload.reason.strip()
        occurred_at = _now()
        revert_id = uuid7()
        await session.execute(
            update(AsyncJob)
            .where(
                AsyncJob.job_type == "CALCULATE_BUDGET_REVISION",
                AsyncJob.payload["source_revision_id"].astext.in_([str(key) for key in lineage]),
                AsyncJob.status.in_(["QUEUED", "RUNNING", "INCOMPLETE"]),
            )
            .values(
                status="CANCELLED",
                finished_at=occurred_at,
                last_error_code="SCENARIO_APPLICATION_REVERTED",
                last_error_safe="候选应用已撤销，旧复算作业停止",
            )
        )
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates=restored_components,
            component_removals=tuple(parent.component_manifest),
        )
        calculation_job = await create_full_calculation_job(
            session,
            actor=actor,
            revision=revision,
            request_id=request_id,
            source="SCENARIO_APPLICATION_REVERT",
            campus_confirmation_required=False,
        )
        invalidated = sorted(REVERT_EXCLUDED_COMPONENTS)
        revert_content: dict[str, object] = {
            "schema_version": "1.0",
            "scenario_application_id": str(application_id),
            "reverted_from_revision_id": str(parent.budget_revision_id),
            "restored_source_revision_id": str(source.budget_revision_id),
            "reverted_revision_id": str(revision.budget_revision_id),
            "reason": reason,
            "restored_component_keys": sorted(restored_components),
            "invalidated_components": invalidated,
            "calculation_job_id": str(calculation_job.job_id),
        }
        revert = ScenarioApplicationRevert(
            scenario_application_revert_id=revert_id,
            scenario_application_id=application_id,
            reverted_from_revision_id=parent.budget_revision_id,
            restored_source_revision_id=source.budget_revision_id,
            reverted_revision_id=revision.budget_revision_id,
            reason=reason,
            restored_component_keys=sorted(restored_components),
            invalidated_components=invalidated,
            calculation_job_id=calculation_job.job_id,
            reverted_by=actor.user_id,
            role_snapshot=_role_snapshot(actor),
            reverted_at=occurred_at,
            content_hash=content_hash(revert_content),
            request_hash=request_hash,
        )
        session.add(revert)
        await session.flush()
        response = _revert_response(revert, revision_response, calculation_job)
        _add_audit(
            session,
            event_type="REBALANCING_SCENARIO_APPLICATION_REVERTED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="SCENARIO_APPLICATION_REVERT",
            object_key={"scenario_application_revert_id": str(revert_id)},
            detail=revert_content,
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


def _restorable_source_components(manifest: Mapping[str, object]) -> dict[str, str]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in REVERT_EXCLUDED_COMPONENTS and key != "schema_version" and isinstance(value, str)
    }


class _EntryState:
    def __init__(self) -> None:
        self.component_updates: dict[str, str] = {}
        self.campus_version_id: uuid.UUID | None = None
        self.entry_version_id: uuid.UUID | None = None
        self.plan_version_id: uuid.UUID | None = None
        self.campus_confirmation_required = False
        self.campus_parent: CampusEntrySetVersion | None = None
        self.campus_rows: list[dict[str, object]] = []
        self.entry_parent: EntryDetailSetVersion | None = None
        self.entry_rows: list[dict[str, object]] = []
        self.plan_parent: PlannedNewStudentVersion | None = None
        self.plan_rows: list[PlannedNewStudentCount] = []

    async def persist(
        self, session: AsyncSession, revision: BudgetRevision, actor: Actor, occurred_at: datetime
    ) -> None:
        if self.entry_version_id is None or self.entry_parent is None:
            return
        if self.campus_version_id is not None and self.campus_parent is not None:
            campus_content = {
                "schema_version": "1.0",
                "source": "SCENARIO_APPLICATION",
                "parent_campus_entry_set_version_id": str(self.campus_parent.campus_entry_set_version_id),
                "rows": self.campus_rows,
            }
            campus_version = CampusEntrySetVersion(
                campus_entry_set_version_id=self.campus_version_id,
                budget_revision_id=revision.budget_revision_id,
                basis_combination_set_version_id=self.campus_parent.basis_combination_set_version_id,
                entry_total_version_id=self.campus_parent.entry_total_version_id,
                suggestion_version_id=self.campus_parent.suggestion_version_id,
                fiscal_year_entry_total=self.campus_parent.fiscal_year_entry_total,
                determined_total=sum(cast(int, row["determined_entry_value"]) for row in self.campus_rows),
                total_difference=0,
                incomplete_count=0,
                record_count=len(self.campus_rows),
                content_hash=content_hash(campus_content),
                request_hash=revision.request_hash or "",
                created_by=actor.user_id,
                created_at=occurred_at,
            )
            session.add(campus_version)
            await session.flush()
            session.add_all(
                CampusEntryAllocation(
                    campus_entry_allocation_id=uuid7(),
                    campus_entry_set_version_id=self.campus_version_id,
                    **row,
                    content_hash=content_hash(row),
                )
                for row in self.campus_rows
            )
        entry_content = {
            "schema_version": "1.0",
            "source": "SCENARIO_APPLICATION",
            "parent_entry_detail_set_version_id": str(self.entry_parent.entry_detail_set_version_id),
            "campus_entry_set_version_id": str(
                self.campus_version_id or self.entry_parent.campus_entry_set_version_id
            ),
            "rows": self.entry_rows,
        }
        entry_version = EntryDetailSetVersion(
            entry_detail_set_version_id=self.entry_version_id,
            budget_revision_id=revision.budget_revision_id,
            combination_set_version_id=self.entry_parent.combination_set_version_id,
            entry_scope_version_id=self.entry_parent.entry_scope_version_id,
            campus_entry_set_version_id=(
                self.campus_version_id or self.entry_parent.campus_entry_set_version_id
            ),
            source_snapshot_id=self.entry_parent.source_snapshot_id,
            fiscal_year=self.entry_parent.fiscal_year,
            source_fiscal_season=self.entry_parent.source_fiscal_season,
            campus_entry_total=sum(cast(int, row["entry_initial_value"]) for row in self.entry_rows),
            allocated_entry_total=sum(cast(int, row["entry_initial_value"]) for row in self.entry_rows),
            record_count=len(self.entry_rows),
            zero_initial_count=sum(cast(int, row["entry_initial_value"]) == 0 for row in self.entry_rows),
            content_hash=content_hash(entry_content),
            request_hash=revision.request_hash or "",
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(entry_version)
        await session.flush()
        allocations = [
            EntryDetailAllocation(
                entry_detail_allocation_id=uuid7(),
                entry_detail_set_version_id=self.entry_version_id,
                **{**row, "combination_id": uuid.UUID(str(row["combination_id"]))},
                content_hash=content_hash(row),
            )
            for row in self.entry_rows
        ]
        session.add_all(allocations)
        await session.flush()
        if self.plan_version_id is None or self.plan_parent is None:
            return
        allocation_by_key = {row.combination_stable_key: row for row in allocations}
        plan_content: list[dict[str, object]] = []
        for row in self.plan_rows:
            item = _plan_row_content(row)
            if row.entry_detail_allocation_id is not None:
                item["entry_detail_allocation_id"] = str(
                    allocation_by_key[row.combination_stable_key].entry_detail_allocation_id
                )
            plan_content.append(item)
        plan_version_content = {
            "schema_version": "1.0",
            "source": "SCENARIO_REBASE",
            "parent_planned_new_student_version_id": str(self.plan_parent.planned_new_student_version_id),
            "entry_detail_set_version_id": str(self.entry_version_id),
            "rows": plan_content,
        }
        plan_version = PlannedNewStudentVersion(
            planned_new_student_version_id=self.plan_version_id,
            budget_revision_id=revision.budget_revision_id,
            parent_planned_new_student_version_id=self.plan_parent.planned_new_student_version_id,
            combination_set_version_id=self.plan_parent.combination_set_version_id,
            entry_scope_version_id=self.plan_parent.entry_scope_version_id,
            entry_detail_set_version_id=self.entry_version_id,
            fiscal_year=self.plan_parent.fiscal_year,
            operation_type="SCENARIO_REBASE",
            record_count=len(plan_content),
            locked_count=sum(bool(row["locked"]) for row in plan_content),
            content_hash=content_hash(plan_version_content),
            request_hash=revision.request_hash or "",
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(plan_version)
        await session.flush()
        session.add_all(
            PlannedNewStudentCount(
                planned_new_student_count_id=uuid7(),
                planned_new_student_version_id=self.plan_version_id,
                **{
                    **row,
                    "combination_id": uuid.UUID(str(row["combination_id"])),
                    "entry_detail_allocation_id": (
                        uuid.UUID(str(row["entry_detail_allocation_id"]))
                        if row["entry_detail_allocation_id"] is not None
                        else None
                    ),
                },
                content_hash=content_hash(row),
            )
            for row in plan_content
        )


class _RateState:
    def __init__(self) -> None:
        self.component_updates: dict[str, str] = {}
        self.version_id: uuid.UUID | None = None
        self.parent: RateParameterVersion | None = None
        self.rows: list[dict[str, object]] = []
        self.confirmations: list[RateBoundaryConfirmation] = []
        self.exceptions: list[RateParameterCopyException] = []

    async def persist(
        self, session: AsyncSession, revision: BudgetRevision, actor: Actor, occurred_at: datetime
    ) -> None:
        if self.version_id is None or self.parent is None:
            return
        version_content = {
            "schema_version": "1.0",
            "source": "SCENARIO_APPLICATION",
            "parent_rate_parameter_version_id": str(self.parent.rate_parameter_version_id),
            "rows": self.rows,
        }
        version = RateParameterVersion(
            rate_parameter_version_id=self.version_id,
            budget_revision_id=revision.budget_revision_id,
            parent_rate_parameter_version_id=self.parent.rate_parameter_version_id,
            rate_reference_version_id=self.parent.rate_reference_version_id,
            fiscal_year=self.parent.fiscal_year,
            operation_type="SCENARIO_APPLY",
            record_count=len(self.rows),
            locked_count=sum(bool(row["locked"]) for row in self.rows),
            copy_exception_count=len(self.exceptions),
            content_hash=content_hash(version_content),
            request_hash=revision.request_hash or "",
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows = [
            RateParameter(
                rate_parameter_id=uuid7(),
                rate_parameter_version_id=self.version_id,
                **{**row, "rate_reference_id": uuid.UUID(str(row["rate_reference_id"]))},
                content_hash=content_hash(row),
            )
            for row in self.rows
        ]
        session.add_all(rows)
        await session.flush()
        prior_by_key = {row.rate_parameter_stable_key: row for row in self.confirmations}
        session.add_all(
            RateBoundaryConfirmation(
                rate_boundary_confirmation_id=uuid7(),
                rate_parameter_version_id=self.version_id,
                rate_parameter_id=row.rate_parameter_id,
                rate_parameter_stable_key=row.rate_parameter_stable_key,
                boundary_fingerprint=_row_boundary_fingerprint(row),
                confirmed_revision_id=revision.budget_revision_id,
                inherited_from_confirmation_id=prior_by_key[
                    row.rate_parameter_stable_key
                ].rate_boundary_confirmation_id,
                confirmed_by=prior_by_key[row.rate_parameter_stable_key].confirmed_by,
                role_snapshot=prior_by_key[row.rate_parameter_stable_key].role_snapshot,
                confirmed_at=prior_by_key[row.rate_parameter_stable_key].confirmed_at,
            )
            for row in rows
            if row.rate_parameter_stable_key in prior_by_key
        )
        session.add_all(
            RateParameterCopyException(
                rate_parameter_copy_exception_id=uuid7(),
                rate_parameter_version_id=self.version_id,
                source_stable_key=row.source_stable_key,
                cross_year_match_key=row.cross_year_match_key,
                error_code=row.error_code,
                detail=row.detail,
                content_hash=row.content_hash,
            )
            for row in self.exceptions
        )


class _DiscountState:
    def __init__(self) -> None:
        self.component_updates: dict[str, str] = {}
        self.version_id: uuid.UUID | None = None
        self.confirmation_id: uuid.UUID | None = None
        self.parent: DiscountParameterVersion | None = None
        self.details: list[dict[str, object]] = []
        self.targets: list[dict[str, object]] = []
        self.unlock: DiscountUnlockConfirmation | None = None
        self.reason = ""

    async def persist(
        self,
        session: AsyncSession,
        revision: BudgetRevision,
        actor: Actor,
        occurred_at: datetime,
        scenario_id: uuid.UUID,
    ) -> uuid.UUID | None:
        if self.version_id is None or self.parent is None:
            return None
        version_content = {
            "schema_version": "1.0",
            "source": "SCENARIO_APPLICATION",
            "parent_discount_parameter_version_id": str(self.parent.discount_parameter_version_id),
            "details": self.details,
            "targets": self.targets,
        }
        version_hash = content_hash(version_content)
        version = DiscountParameterVersion(
            discount_parameter_version_id=self.version_id,
            budget_revision_id=revision.budget_revision_id,
            parent_discount_parameter_version_id=self.parent.discount_parameter_version_id,
            combination_set_version_id=self.parent.combination_set_version_id,
            fiscal_year=self.parent.fiscal_year,
            operation_type="SCENARIO_APPLY",
            detail_record_count=len(self.details),
            target_record_count=len(self.targets),
            batch_content_hash=content_hash(
                {"detail_items": self.details, "composite_targets": self.targets}
            ),
            content_hash=version_hash,
            request_hash=revision.request_hash or "",
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        session.add_all(
            DetailDiscountParameter(
                detail_discount_parameter_id=uuid7(),
                discount_parameter_version_id=self.version_id,
                **row,
                content_hash=content_hash(row),
            )
            for row in self.details
        )
        session.add_all(
            CompositeDiscountTarget(
                composite_discount_target_id=uuid7(),
                discount_parameter_version_id=self.version_id,
                **row,
                content_hash=content_hash(row),
            )
            for row in self.targets
        )
        if self.confirmation_id is None or self.unlock is None:
            raise DomainError(
                code="SCENARIO_DISCOUNT_UNLOCK_REQUIRED",
                message="折扣变更缺少已确认的折扣解锁",
                status_code=409,
            )
        summary = {
            "schema_version": "1.0",
            "confirmation_mode": "INHERITED_FROM_DISCOUNT_UNLOCK",
            "discount_unlock_confirmation_id": str(self.unlock.discount_unlock_confirmation_id),
            "rebalancing_scenario_id": str(scenario_id),
            "application_reason": self.reason,
        }
        session.add(
            DiscountParameterConfirmation(
                discount_parameter_confirmation_id=self.confirmation_id,
                discount_parameter_version_id=self.version_id,
                confirmed_revision_id=revision.budget_revision_id,
                input_fingerprint=version_hash,
                confirmed_by=self.unlock.confirmed_by,
                role_snapshot=self.unlock.role_snapshot,
                summary=summary,
                confirmed_at=self.unlock.confirmed_at,
            )
        )
        return self.confirmation_id


async def _prepare_entry_state(
    session: AsyncSession,
    revision: BudgetRevision,
    changes: list[ScenarioChange],
    reason: str,
) -> _EntryState:
    state = _EntryState()
    requested = {row.object_stable_key: row for row in changes if row.object_type == "ENTRY"}
    if not requested:
        return state
    detail_id = _manifest_uuid(revision, "entry_detail_set_version_id", "候选方案缺少当前入口明细")
    detail = await session.get(EntryDetailSetVersion, detail_id)
    if detail is None:
        raise DomainError(code="ENTRY_DETAIL_NOT_FOUND", message="入口明细版本不存在", status_code=404)
    rows = list(
        await session.scalars(
            select(EntryDetailAllocation)
            .where(EntryDetailAllocation.entry_detail_set_version_id == detail_id)
            .order_by(EntryDetailAllocation.combination_stable_key)
        )
    )
    by_key = {row.combination_stable_key: row for row in rows}
    _validate_change_base(requested, {key: row.entry_initial_value for key, row in by_key.items()})
    new_values = {
        key: requested[key].suggested_value if key in requested else row.entry_initial_value
        for key, row in by_key.items()
    }
    if sum(new_values.values()) != detail.campus_entry_total:
        raise DomainError(
            code="SCENARIO_ENTRY_TOTAL_MISMATCH",
            message="候选入口明细与锁定入口总值不一致",
            status_code=409,
        )
    state.entry_parent = detail
    state.entry_version_id = uuid7()
    state.component_updates["entry_detail_set_version_id"] = str(state.entry_version_id)
    state.entry_rows = [_entry_row_content(row, new_values[row.combination_stable_key]) for row in rows]

    campus_id = detail.campus_entry_set_version_id
    campus = await session.get(CampusEntrySetVersion, campus_id)
    campus_rows = list(
        await session.scalars(
            select(CampusEntryAllocation)
            .where(CampusEntryAllocation.campus_entry_set_version_id == campus_id)
            .order_by(CampusEntryAllocation.campus_stable_key)
        )
    )
    if campus is None:
        raise DomainError(code="CAMPUS_ENTRY_SET_NOT_FOUND", message="校区入口版本不存在", status_code=404)
    totals: dict[str, int] = defaultdict(int)
    for row in state.entry_rows:
        totals[str(row["campus"])] += cast(int, row["entry_initial_value"])
    old_totals = {row.campus: row.determined_entry_value for row in campus_rows}
    if totals != old_totals:
        if set(totals) != set(old_totals):
            raise DomainError(
                code="SCENARIO_CAMPUS_SET_MISMATCH",
                message="候选入口明细改变了校区集合",
                status_code=409,
            )
        state.campus_parent = campus
        state.campus_version_id = uuid7()
        state.campus_confirmation_required = True
        state.component_updates["campus_entry_set_version_id"] = str(state.campus_version_id)
        state.campus_rows = [
            {
                "campus": row.campus,
                "campus_stable_key": row.campus_stable_key,
                "suggested_entry_value": row.suggested_entry_value,
                "determined_entry_value": totals[row.campus],
                "adjustment_difference": totals[row.campus] - row.suggested_entry_value,
                "adjustment_reason_category": "STRATEGIC_ADJUSTMENT",
                "adjustment_reason_detail": reason,
                "performance_evidence": row.performance_evidence,
                "classroom_resource": row.classroom_resource,
                "sales_resource": row.sales_resource,
            }
            for row in campus_rows
        ]
    plan, plan_rows = await _load_plan_state(session, revision)
    if plan is not None:
        state.plan_parent = plan
        state.plan_rows = plan_rows
        state.plan_version_id = uuid7()
        state.component_updates["planned_new_student_version_id"] = str(state.plan_version_id)
    return state


async def _prepare_rate_state(
    session: AsyncSession,
    revision: BudgetRevision,
    changes: list[ScenarioChange],
) -> _RateState:
    state = _RateState()
    requested = {row.object_stable_key: row for row in changes if row.object_type == "RATE"}
    if not requested:
        return state
    version, rows, confirmations = await _current_rate_state(session, revision)
    if version is None:
        raise DomainError(code="RATE_PARAMETER_VERSION_REQUIRED", message="缺少比例参数版本", status_code=409)
    by_key = {str(row.rate_parameter_id): row for row in rows}
    _validate_change_base(requested, {key: row.current_value_ppm for key, row in by_key.items()})
    state.parent = version
    state.version_id = uuid7()
    state.component_updates["rate_parameter_version_id"] = str(state.version_id)
    confirmed_keys = {row.rate_parameter_stable_key for row in confirmations}
    for key, row in sorted(by_key.items()):
        value = requested[key].suggested_value if key in requested else row.current_value_ppm
        if key in requested and row.rate_parameter_stable_key not in confirmed_keys:
            raise DomainError(
                code="SCENARIO_RATE_BOUNDARY_UNCONFIRMED",
                message="候选比例变更缺少已确认的调整边界",
                status_code=409,
                details={"object_stable_key": key},
            )
        if (
            value % 100
            or not row.lower_bound_ppm <= value <= row.upper_bound_ppm
            or (row.locked and value != row.current_value_ppm)
        ):
            raise DomainError(
                code="SCENARIO_RATE_VALUE_INVALID",
                message="候选比例超出已确认边界、步长或锁定约束",
                status_code=409,
                details={"object_stable_key": key},
            )
        item = _rate_row_content(row)
        item["current_value_ppm"] = value
        if row.locked:
            item["locked_value_ppm"] = value
        state.rows.append(item)
    state.confirmations = confirmations
    state.exceptions = list(
        await session.scalars(
            select(RateParameterCopyException).where(
                RateParameterCopyException.rate_parameter_version_id == version.rate_parameter_version_id
            )
        )
    )
    return state


async def _prepare_discount_state(
    session: AsyncSession,
    revision: BudgetRevision,
    run: OptimizationRun,
    changes: list[ScenarioChange],
    reason: str,
) -> _DiscountState:
    state = _DiscountState()
    requested = {row.object_stable_key: row for row in changes if row.object_type == "DISCOUNT"}
    if not requested:
        return state
    version_id = _manifest_uuid(revision, "discount_parameter_version_id", "缺少折扣参数版本")
    version = await session.get(DiscountParameterVersion, version_id)
    if version is None:
        raise DomainError(
            code="DISCOUNT_PARAMETER_VERSION_NOT_FOUND", message="折扣参数版本不存在", status_code=404
        )
    current_confirmation = await _discount_confirmation(session, version)
    if current_confirmation is None:
        raise DomainError(
            code="DISCOUNT_PARAMETER_CONFIRMATION_REQUIRED",
            message="当前折扣参数尚未确认",
            status_code=409,
        )
    if run.discount_unlock_confirmation_id is None:
        raise DomainError(
            code="SCENARIO_DISCOUNT_UNLOCK_REQUIRED",
            message="折扣变更缺少已确认的折扣解锁",
            status_code=409,
        )
    unlock = await session.get(DiscountUnlockConfirmation, run.discount_unlock_confirmation_id)
    if (
        unlock is None
        or unlock.budget_revision_id != revision.budget_revision_id
        or unlock.input_fingerprint != run.input_fingerprint
    ):
        raise DomainError(
            code="SCENARIO_DISCOUNT_UNLOCK_STALE",
            message="候选方案使用的折扣解锁已失效",
            status_code=409,
        )
    details, targets = await _load_discount_rows(session, version)
    by_key = {row.detail_discount_stable_key: row for row in details}
    _validate_change_base(requested, {key: row.discount_rate_ppm for key, row in by_key.items()})
    state.parent = version
    state.version_id = uuid7()
    state.confirmation_id = uuid7()
    state.unlock = unlock
    state.reason = reason
    state.component_updates["discount_parameter_version_id"] = str(state.version_id)
    for key, row in sorted(by_key.items()):
        value = requested[key].suggested_value if key in requested else row.discount_rate_ppm
        if not 0 <= value <= 1_000_000 or value % 1_000:
            raise DomainError(
                code="SCENARIO_DISCOUNT_VALUE_INVALID",
                message="候选折扣率超出 0%–100% 或 0.1 个百分点步长",
                status_code=409,
                details={"object_stable_key": key},
            )
        item = _discount_row_content(row)
        item["discount_rate_ppm"] = value
        item["change_reason"] = f"候选方案应用：{reason}"
        state.details.append(item)
    state.targets = [_discount_target_content(row) for row in targets]
    return state


def _validate_change_hashes(changes: list[ScenarioChange]) -> None:
    for row in changes:
        content = {
            "object_type": row.object_type,
            "object_stable_key": row.object_stable_key,
            "original_value": row.original_value,
            "suggested_value": row.suggested_value,
            "adjustment": row.adjustment,
        }
        if row.content_hash != content_hash(content) or row.adjustment == 0:
            raise DomainError(
                code="SCENARIO_CHANGE_INTEGRITY_FAILED",
                message="候选方案差异记录完整性校验失败",
                status_code=409,
            )


def _validate_change_base(requested: dict[str, ScenarioChange], current: dict[str, int]) -> None:
    if not set(requested) <= set(current):
        raise DomainError(
            code="SCENARIO_CHANGE_OBJECT_MISSING",
            message="候选方案引用的当前对象不存在",
            status_code=409,
            details={"missing": sorted(set(requested) - set(current))},
        )
    mismatched = [key for key, change in requested.items() if change.original_value != current[key]]
    if mismatched:
        raise DomainError(
            code="SCENARIO_CHANGE_BASE_MISMATCH",
            message="候选方案的原值与当前预算不一致",
            status_code=409,
            details={"object_stable_keys": sorted(mismatched)},
        )


def _entry_row_content(row: EntryDetailAllocation, value: int) -> dict[str, object]:
    if value < 0:
        raise DomainError(
            code="SCENARIO_ENTRY_VALUE_INVALID", message="候选入口人次不得为负数", status_code=409
        )
    return {
        "combination_id": str(row.combination_id),
        "combination_stable_key": row.combination_stable_key,
        "track": row.track,
        "product": row.product,
        "grade": row.grade,
        "subject": row.subject,
        "campus": row.campus,
        "effective_entry_attribute": row.effective_entry_attribute,
        "scope_evidence": row.scope_evidence,
        "origin_type": row.origin_type,
        "historical_business_new_students": row.historical_business_new_students,
        "manual_reservation_ratio_ppm": row.manual_reservation_ratio_ppm,
        "manual_reservation_reason": row.manual_reservation_reason,
        "allocation_source_type": row.allocation_source_type,
        "theoretical_value": Decimal(value),
        "floor_value": value,
        "fraction_value": Decimal(0),
        "tail_assigned": False,
        "entry_initial_value": value,
        "soft_lower_bound": (4 * value + 4) // 5 if value > 0 else None,
        "soft_upper_bound": (6 * value) // 5 if value > 0 else None,
        "candidate_eligible": value > 0,
        "candidate_blocking_reason": None if value > 0 else "ZERO_INITIAL_REQUIRES_PLAN_AND_HARD_BOUNDS",
    }


def _rate_row_content(row: RateParameter) -> dict[str, object]:
    return {
        "rate_reference_id": str(row.rate_reference_id),
        "rate_parameter_stable_key": row.rate_parameter_stable_key,
        "cross_year_match_key": row.cross_year_match_key,
        "rate_type": row.rate_type,
        "rate_usage_mode": row.rate_usage_mode,
        "fiscal_year": row.fiscal_year,
        "source_fiscal_season": row.source_fiscal_season,
        "target_fiscal_season": row.target_fiscal_season,
        "quarter_name": row.quarter_name,
        "track": row.track,
        "product": row.product,
        "grade": row.grade,
        "campus": row.campus,
        "renewal_relation_code": row.renewal_relation_code,
        "source_type": row.source_type,
        "system_value_ppm": row.system_value_ppm,
        "current_value_ppm": row.current_value_ppm,
        "lower_bound_ppm": row.lower_bound_ppm,
        "upper_bound_ppm": row.upper_bound_ppm,
        "boundary_reason": row.boundary_reason,
        "manual_reason": row.manual_reason,
        "locked": row.locked,
        "locked_value_ppm": row.locked_value_ppm,
        "lock_origin": row.lock_origin,
    }


def _plan_row_content(row: PlannedNewStudentCount) -> dict[str, object]:
    return {
        "combination_id": str(row.combination_id),
        "entry_detail_allocation_id": (
            str(row.entry_detail_allocation_id) if row.entry_detail_allocation_id else None
        ),
        "planned_new_student_stable_key": row.planned_new_student_stable_key,
        "combination_stable_key": row.combination_stable_key,
        "fiscal_year": row.fiscal_year,
        "fiscal_season": row.fiscal_season,
        "quarter_name": row.quarter_name,
        "track": row.track,
        "product": row.product,
        "entry_attribute": row.entry_attribute,
        "grade": row.grade,
        "subject": row.subject,
        "campus": row.campus,
        "planned_new_students": row.planned_new_students,
        "manual_reason": row.manual_reason,
        "calculated_new_student_share_ppm": row.calculated_new_student_share_ppm,
        "locked": row.locked,
        "locked_value": row.locked_value,
        "handling_status": row.handling_status,
        "lock_origin": row.lock_origin,
    }


def _discount_row_content(row: DetailDiscountParameter) -> dict[str, object]:
    return {
        "detail_discount_stable_key": row.detail_discount_stable_key,
        "fiscal_year": row.fiscal_year,
        "fiscal_season": row.fiscal_season,
        "quarter_name": row.quarter_name,
        "track": row.track,
        "product": row.product,
        "grade": row.grade,
        "discount_rate_ppm": row.discount_rate_ppm,
        "source_evidence": row.source_evidence,
        "change_reason": row.change_reason,
    }


def _discount_target_content(row: CompositeDiscountTarget) -> dict[str, object]:
    return {
        "composite_target_stable_key": row.composite_target_stable_key,
        "scope_level": row.scope_level,
        "track": row.track,
        "period_type": row.period_type,
        "fiscal_season": row.fiscal_season,
        "target_rate_ppm": row.target_rate_ppm,
        "source_evidence": row.source_evidence,
        "change_reason": row.change_reason,
    }


def _response(
    application: ScenarioApplication,
    revision: RevisionCommandResponse,
    calculation_job: AsyncJob,
) -> ScenarioApplicationResponse:
    return ScenarioApplicationResponse(
        scenario_application_id=application.scenario_application_id,
        rebalancing_scenario_id=application.rebalancing_scenario_id,
        optimization_run_id=application.optimization_run_id,
        source_revision_id=application.source_revision_id,
        applied_revision_id=application.applied_revision_id,
        input_fingerprint=application.input_fingerprint,
        reason=application.reason,
        change_count=application.change_count,
        entry_change_count=application.entry_change_count,
        rate_change_count=application.rate_change_count,
        discount_change_count=application.discount_change_count,
        campus_entry_set_version_id=application.campus_entry_set_version_id,
        entry_detail_set_version_id=application.entry_detail_set_version_id,
        planned_new_student_version_id=application.planned_new_student_version_id,
        rate_parameter_version_id=application.rate_parameter_version_id,
        discount_parameter_version_id=application.discount_parameter_version_id,
        inherited_discount_confirmation_id=application.inherited_discount_confirmation_id,
        campus_confirmation_required=application.campus_confirmation_required,
        invalidated_components=application.invalidated_components,
        calculation_job_id=calculation_job.job_id,
        calculation_job_status=calculation_job.status,
        links={"calculation_job": f"/api/v1/jobs/{calculation_job.job_id}"},
        applied_by=application.applied_by,
        applied_at=application.applied_at,
        content_hash=application.content_hash,
        revision=revision,
    )


def _revert_response(
    revert: ScenarioApplicationRevert,
    revision: RevisionCommandResponse,
    calculation_job: AsyncJob,
) -> ScenarioApplicationRevertResponse:
    return ScenarioApplicationRevertResponse(
        scenario_application_revert_id=revert.scenario_application_revert_id,
        scenario_application_id=revert.scenario_application_id,
        reverted_from_revision_id=revert.reverted_from_revision_id,
        restored_source_revision_id=revert.restored_source_revision_id,
        reverted_revision_id=revert.reverted_revision_id,
        reason=revert.reason,
        restored_component_keys=revert.restored_component_keys,
        invalidated_components=revert.invalidated_components,
        calculation_job_id=calculation_job.job_id,
        calculation_job_status=calculation_job.status,
        links={
            "revision": f"/api/v1/revisions/{revert.reverted_revision_id}",
            "calculation_job": f"/api/v1/jobs/{calculation_job.job_id}",
        },
        reverted_by=revert.reverted_by,
        reverted_at=revert.reverted_at,
        content_hash=revert.content_hash,
        revision=revision,
    )
