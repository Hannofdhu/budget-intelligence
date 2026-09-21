import uuid
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.class_size.validation import ClassSizeInput
from packages.contracts.versioning import (
    DiscountUnlockConfirmRequest,
    DiscountUnlockCreateRequest,
    DiscountUnlockResponse,
    InfeasibilityProofResponse,
    OptimizationRunCreateRequest,
    OptimizationRunResponse,
    RebalancingScenarioResponse,
    ScenarioChangeResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.optimization.engine import (
    OPTIMIZATION_MODEL_VERSION,
    CandidateEvaluation,
    DecisionVector,
    DiscountDecision,
    EntryDecision,
    RateDecision,
    SolveAttempt,
    SolveStatus,
    symmetric_rate_contributions,
)
from packages.optimization.evaluator import (
    BudgetEvaluationContext,
    OpeningDecisionInput,
    RevenueLineTemplate,
    RevenueTarget,
)
from packages.optimization.process_runner import optimize_budget_isolated
from packages.persistence.entry_planning import (
    _assert_version_access,
    _lock_current_revision,
    _manifest_uuid,
)
from packages.persistence.headcount import _load_basis
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    BudgetRevision,
    BudgetTarget,
    CompositeDiscountTarget,
    DiscountParameterVersion,
    DiscountUnlockConfirmation,
    DiscountUnlockRequest,
    EntryTotalVersion,
    InfeasibilityProof,
    OpeningStudentVersion,
    OptimizationRun,
    RebalancingScenario,
    ResourceFinanceParameterVersion,
    ScenarioChange,
    TargetClosureResultVersion,
)
from packages.persistence.rate_parameters import _current_rate_state
from packages.persistence.resource_finance import _load_resource_rows
from packages.persistence.revenue import _load_discount_rows
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _role_snapshot,
    _store_receipt,
)
from packages.revenue.calculation import CompositeDiscountTargetInput


async def create_optimization_run(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    payload: OptimizationRunCreateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> OptimizationRunResponse:
    command_type = "CREATE_OPTIMIZATION_RUN"
    request_hash = content_hash({"revision_id": str(revision_id), **payload.model_dump(mode="json")})
    async with session.begin():
        replay = await _idempotency_replay(
            session,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return OptimizationRunResponse.model_validate(replay)
        revision, budget = await _assert_version_access(session, actor=actor, revision_id=revision_id)
        context, closure_version, input_fingerprint = await _build_context(
            session, revision, budget.fiscal_year
        )
        active_discount_unlock = await _active_discount_unlock(
            session,
            revision_id=revision.budget_revision_id,
            input_fingerprint=input_fingerprint,
        )
        outcome = await optimize_budget_isolated(
            context,
            solver_adapter=payload.solver_adapter,
            maximum_enumerated_states=payload.maximum_enumerated_states,
            maximum_solver_seconds=payload.maximum_solver_seconds,
            maximum_candidate_validations=payload.maximum_candidate_validations,
            maximum_group_table_rows=payload.maximum_group_table_rows,
            discount_unlocked=active_discount_unlock is not None,
        )
        occurred_at = _now()
        run_id = uuid7()
        attempts = [_attempt_content(item) for item in outcome.attempts]
        run_content = {
            "schema_version": "1.2",
            "budget_revision_id": str(revision_id),
            "target_closure_result_version_id": str(closure_version.target_closure_result_version_id),
            "optimization_model_version": OPTIMIZATION_MODEL_VERSION,
            "terminal_stage": outcome.terminal_stage.value,
            "status": outcome.status.value,
            "discount_unlock_required": outcome.discount_unlock_required,
            "discount_unlock_confirmation_id": (
                str(active_discount_unlock.discount_unlock_confirmation_id)
                if active_discount_unlock
                else None
            ),
            "reason_code": outcome.reason_code,
            "solver_adapter": payload.solver_adapter.value,
            "maximum_enumerated_states": payload.maximum_enumerated_states,
            "maximum_solver_seconds": payload.maximum_solver_seconds,
            "maximum_candidate_validations": payload.maximum_candidate_validations,
            "maximum_group_table_rows": payload.maximum_group_table_rows,
            "attempts": attempts,
            "input_fingerprint": input_fingerprint,
        }
        run = OptimizationRun(
            optimization_run_id=run_id,
            budget_revision_id=revision_id,
            target_closure_result_version_id=closure_version.target_closure_result_version_id,
            optimization_model_version=OPTIMIZATION_MODEL_VERSION,
            terminal_stage=outcome.terminal_stage.value,
            status=outcome.status.value,
            discount_unlock_required=outcome.discount_unlock_required,
            discount_unlock_confirmation_id=(
                active_discount_unlock.discount_unlock_confirmation_id if active_discount_unlock else None
            ),
            reason_code=outcome.reason_code,
            solver_adapter=payload.solver_adapter.value,
            maximum_enumerated_states=payload.maximum_enumerated_states,
            maximum_solver_seconds=payload.maximum_solver_seconds,
            maximum_candidate_validations=payload.maximum_candidate_validations,
            maximum_group_table_rows=payload.maximum_group_table_rows,
            evaluated_state_count=sum(item.evaluated_state_count for item in outcome.attempts),
            attempts=attempts,
            input_fingerprint=input_fingerprint,
            content_hash=content_hash(run_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(run)
        await session.flush()
        scenarios, changes = _scenario_models(run_id, outcome.candidates, context)
        session.add_all(scenarios)
        await session.flush()
        session.add_all(changes)
        proofs = _proof_models(run_id, outcome.attempts, input_fingerprint)
        session.add_all(proofs)
        response = _run_response(run, scenarios, changes, proofs)
        _add_audit(
            session,
            event_type="OPTIMIZATION_RUN_CREATED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="OPTIMIZATION_RUN",
            object_key={"optimization_run_id": str(run_id)},
            detail={
                "status": run.status,
                "terminal_stage": run.terminal_stage,
                "discount_unlock_required": run.discount_unlock_required,
                "candidate_count": len(scenarios),
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
            response=response.model_dump(mode="json"),
            occurred_at=occurred_at,
        )
    return response


async def get_optimization_run(
    session: AsyncSession, *, actor: Actor, optimization_run_id: uuid.UUID
) -> OptimizationRunResponse:
    run = await session.get(OptimizationRun, optimization_run_id)
    if run is None:
        raise DomainError(code="OPTIMIZATION_RUN_NOT_FOUND", message="自动调平运行不存在", status_code=404)
    await _assert_version_access(session, actor=actor, revision_id=run.budget_revision_id)
    scenarios = list(
        await session.scalars(
            select(RebalancingScenario).where(RebalancingScenario.optimization_run_id == optimization_run_id)
        )
    )
    scenario_ids = [item.rebalancing_scenario_id for item in scenarios]
    changes = (
        list(
            await session.scalars(
                select(ScenarioChange).where(ScenarioChange.rebalancing_scenario_id.in_(scenario_ids))
            )
        )
        if scenario_ids
        else []
    )
    proofs = list(
        await session.scalars(
            select(InfeasibilityProof).where(InfeasibilityProof.optimization_run_id == optimization_run_id)
        )
    )
    return _run_response(run, scenarios, changes, proofs)


async def get_latest_optimization_run(
    session: AsyncSession, *, actor: Actor, revision_id: uuid.UUID
) -> OptimizationRunResponse:
    await _assert_version_access(session, actor=actor, revision_id=revision_id)
    run_id = await session.scalar(
        select(OptimizationRun.optimization_run_id)
        .where(OptimizationRun.budget_revision_id == revision_id)
        .order_by(OptimizationRun.created_at.desc(), OptimizationRun.optimization_run_id.desc())
        .limit(1)
    )
    if run_id is None:
        raise DomainError(
            code="OPTIMIZATION_RUN_NOT_FOUND",
            message="当前预算修订尚未生成自动调平运行",
            status_code=404,
        )
    return await get_optimization_run(session, actor=actor, optimization_run_id=run_id)


async def request_discount_unlock(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: DiscountUnlockCreateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> DiscountUnlockResponse:
    command_type = "REQUEST_DISCOUNT_UNLOCK"
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
            return DiscountUnlockResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        run = await session.get(OptimizationRun, payload.optimization_run_id)
        proof = await session.get(InfeasibilityProof, payload.infeasibility_proof_id)
        if run is None:
            raise DomainError(
                code="OPTIMIZATION_RUN_NOT_FOUND", message="自动调平运行不存在", status_code=404
            )
        if proof is None:
            raise DomainError(
                code="INFEASIBILITY_PROOF_NOT_FOUND", message="不可行证明不存在", status_code=404
            )
        if run.budget_revision_id != revision.budget_revision_id:
            raise DomainError(
                code="DISCOUNT_UNLOCK_RUN_REVISION_MISMATCH",
                message="折扣解锁运行不属于当前预算修订",
                status_code=409,
            )
        if not (
            run.status == SolveStatus.INFEASIBLE.value
            and run.terminal_stage == "ENTRY_AND_RATES"
            and run.discount_unlock_required
        ):
            raise DomainError(
                code="DISCOUNT_UNLOCK_NOT_ELIGIBLE",
                message="只有入口和比例阶段被精确证明不可行后才能申请折扣解锁",
                status_code=409,
            )
        if not (proof.optimization_run_id == run.optimization_run_id and proof.stage == "ENTRY_AND_RATES"):
            raise DomainError(
                code="DISCOUNT_UNLOCK_PROOF_MISMATCH",
                message="不可行证明不属于当前运行的入口和比例阶段",
                status_code=409,
            )
        duplicate = await session.scalar(
            select(DiscountUnlockRequest).where(
                DiscountUnlockRequest.budget_revision_id == revision.budget_revision_id,
                DiscountUnlockRequest.input_fingerprint == run.input_fingerprint,
            )
        )
        if duplicate is not None:
            raise DomainError(
                code="DISCOUNT_UNLOCK_ALREADY_REQUESTED",
                message="当前预算输入已经申请折扣解锁",
                status_code=409,
            )
        occurred_at = _now()
        unlock_content = {
            "schema_version": "1.0",
            "budget_revision_id": str(revision.budget_revision_id),
            "optimization_run_id": str(run.optimization_run_id),
            "infeasibility_proof_id": str(proof.infeasibility_proof_id),
            "input_fingerprint": run.input_fingerprint,
            "reason": payload.reason.strip(),
        }
        unlock = DiscountUnlockRequest(
            discount_unlock_request_id=uuid7(),
            budget_revision_id=revision.budget_revision_id,
            optimization_run_id=run.optimization_run_id,
            infeasibility_proof_id=proof.infeasibility_proof_id,
            input_fingerprint=run.input_fingerprint,
            reason=payload.reason.strip(),
            requested_by=actor.user_id,
            role_snapshot=_role_snapshot(actor),
            requested_at=occurred_at,
            content_hash=content_hash(unlock_content),
            request_hash=request_hash,
        )
        session.add(unlock)
        await session.flush()
        response = _discount_unlock_response(unlock, None)
        _add_audit(
            session,
            event_type="DISCOUNT_UNLOCK_REQUESTED",
            actor=actor,
            acting_role=Role.DATA_MAINTAINER,
            object_type="DISCOUNT_UNLOCK_REQUEST",
            object_key={"discount_unlock_request_id": str(unlock.discount_unlock_request_id)},
            detail={
                "optimization_run_id": str(run.optimization_run_id),
                "infeasibility_proof_id": str(proof.infeasibility_proof_id),
                "input_fingerprint": run.input_fingerprint,
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


async def get_current_discount_unlock(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    input_fingerprint: str,
) -> DiscountUnlockResponse:
    await _assert_version_access(session, actor=actor, revision_id=revision_id)
    unlock = await session.scalar(
        select(DiscountUnlockRequest).where(
            DiscountUnlockRequest.budget_revision_id == revision_id,
            DiscountUnlockRequest.input_fingerprint == input_fingerprint,
        )
    )
    if unlock is None:
        raise DomainError(
            code="DISCOUNT_UNLOCK_NOT_FOUND",
            message="当前输入指纹尚无折扣解锁申请",
            status_code=404,
        )
    confirmation = await session.scalar(
        select(DiscountUnlockConfirmation).where(
            DiscountUnlockConfirmation.discount_unlock_request_id == unlock.discount_unlock_request_id
        )
    )
    return _discount_unlock_response(unlock, confirmation)


async def confirm_discount_unlock(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: DiscountUnlockConfirmRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
) -> DiscountUnlockResponse:
    command_type = "CONFIRM_DISCOUNT_UNLOCK"
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
            return DiscountUnlockResponse.model_validate(replay)
        revision, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        unlock = await session.get(DiscountUnlockRequest, payload.discount_unlock_request_id)
        if unlock is None:
            raise DomainError(
                code="DISCOUNT_UNLOCK_REQUEST_NOT_FOUND",
                message="折扣解锁申请不存在",
                status_code=404,
            )
        if unlock.budget_revision_id != revision.budget_revision_id:
            raise DomainError(
                code="DISCOUNT_UNLOCK_REQUEST_REVISION_MISMATCH",
                message="折扣解锁申请不属于当前预算修订",
                status_code=409,
            )
        _, _, current_input_fingerprint = await _build_context(session, revision, budget.fiscal_year)
        if unlock.input_fingerprint != current_input_fingerprint:
            raise DomainError(
                code="DISCOUNT_UNLOCK_INPUT_STALE",
                message="预算输入已变化，原折扣解锁申请失效",
                status_code=409,
            )
        existing = await session.scalar(
            select(DiscountUnlockConfirmation).where(
                DiscountUnlockConfirmation.discount_unlock_request_id == unlock.discount_unlock_request_id
            )
        )
        occurred_at = _now()
        if existing is None:
            confirmation_content = {
                "schema_version": "1.0",
                "discount_unlock_request_id": str(unlock.discount_unlock_request_id),
                "budget_revision_id": str(revision.budget_revision_id),
                "input_fingerprint": unlock.input_fingerprint,
            }
            existing = DiscountUnlockConfirmation(
                discount_unlock_confirmation_id=uuid7(),
                discount_unlock_request_id=unlock.discount_unlock_request_id,
                budget_revision_id=revision.budget_revision_id,
                input_fingerprint=unlock.input_fingerprint,
                confirmed_by=actor.user_id,
                role_snapshot=_role_snapshot(actor),
                confirmed_at=occurred_at,
                content_hash=content_hash(confirmation_content),
                request_hash=request_hash,
            )
            session.add(existing)
            await session.flush()
        response = _discount_unlock_response(unlock, existing)
        _add_audit(
            session,
            event_type="DISCOUNT_UNLOCK_CONFIRMED",
            actor=actor,
            acting_role=Role.DEPARTMENT_BUDGET_OWNER,
            object_type="DISCOUNT_UNLOCK_CONFIRMATION",
            object_key={"discount_unlock_confirmation_id": str(existing.discount_unlock_confirmation_id)},
            detail={
                "discount_unlock_request_id": str(unlock.discount_unlock_request_id),
                "input_fingerprint": unlock.input_fingerprint,
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


async def _active_discount_unlock(
    session: AsyncSession,
    *,
    revision_id: uuid.UUID,
    input_fingerprint: str,
) -> DiscountUnlockConfirmation | None:
    return cast(
        DiscountUnlockConfirmation | None,
        await session.scalar(
            select(DiscountUnlockConfirmation)
            .join(
                DiscountUnlockRequest,
                DiscountUnlockRequest.discount_unlock_request_id
                == DiscountUnlockConfirmation.discount_unlock_request_id,
            )
            .where(
                DiscountUnlockRequest.budget_revision_id == revision_id,
                DiscountUnlockRequest.input_fingerprint == input_fingerprint,
                DiscountUnlockConfirmation.budget_revision_id == revision_id,
                DiscountUnlockConfirmation.input_fingerprint == input_fingerprint,
            )
        ),
    )


def _discount_unlock_response(
    unlock: DiscountUnlockRequest,
    confirmation: DiscountUnlockConfirmation | None,
) -> DiscountUnlockResponse:
    return DiscountUnlockResponse(
        discount_unlock_request_id=unlock.discount_unlock_request_id,
        budget_revision_id=unlock.budget_revision_id,
        optimization_run_id=unlock.optimization_run_id,
        infeasibility_proof_id=unlock.infeasibility_proof_id,
        input_fingerprint=unlock.input_fingerprint,
        reason=unlock.reason,
        status="ACTIVE" if confirmation else "REQUESTED",
        requested_by=unlock.requested_by,
        requested_at=unlock.requested_at,
        request_content_hash=unlock.content_hash,
        discount_unlock_confirmation_id=(
            confirmation.discount_unlock_confirmation_id if confirmation else None
        ),
        confirmed_by=confirmation.confirmed_by if confirmation else None,
        confirmed_at=confirmation.confirmed_at if confirmation else None,
        confirmation_content_hash=confirmation.content_hash if confirmation else None,
    )


async def _build_context(
    session: AsyncSession, revision: BudgetRevision, fiscal_year: int
) -> tuple[BudgetEvaluationContext, TargetClosureResultVersion, str]:
    closure_id = _manifest_uuid(
        revision, "target_closure_result_version_id", "自动调平前必须完成目标闭合校验"
    )
    closure = await session.get(TargetClosureResultVersion, closure_id)
    if closure is None:
        raise DomainError(
            code="TARGET_CLOSURE_RESULT_NOT_FOUND",
            message="目标闭合结果不存在",
            status_code=404,
        )
    basis = await _load_basis(session, revision, fiscal_year, allow_opening=True)
    rate_version, rate_rows, confirmations = await _current_rate_state(session, revision)
    if rate_version is None:
        raise DomainError(code="RATE_PARAMETER_VERSION_REQUIRED", message="缺少比例参数版本", status_code=409)
    entry_total_id = _manifest_uuid(revision, "entry_total_version_id", "缺少已锁定财年入口总值")
    entry_total = await session.get(EntryTotalVersion, entry_total_id)
    resource_id = _manifest_uuid(revision, "resource_finance_parameter_version_id", "缺少资源与财务参数版本")
    discount_id = _manifest_uuid(revision, "discount_parameter_version_id", "缺少折扣参数版本")
    resource = await session.get(ResourceFinanceParameterVersion, resource_id)
    discount = await session.get(DiscountParameterVersion, discount_id)
    if entry_total is None or resource is None or discount is None:
        raise DomainError(code="OPTIMIZATION_BASIS_NOT_FOUND", message="自动调平依据不存在", status_code=404)
    class_rows, revenue_rows, opening_rows, resource_confirmation = await _load_resource_rows(
        session, resource
    )
    discount_rows, composite_rows = await _load_discount_rows(session, discount)
    if resource_confirmation is None:
        raise DomainError(
            code="RESOURCE_FINANCE_CONFIRMATION_REQUIRED",
            message="资源与财务参数尚未确认",
            status_code=409,
        )
    targets = list(
        await session.scalars(
            select(BudgetTarget).where(BudgetTarget.target_set_version_id == closure.target_set_version_id)
        )
    )
    revenue_by_key = {(row.fiscal_season, row.track, row.product): row for row in revenue_rows}
    opening_by_key = {
        (row.fiscal_season, row.track, row.product, row.grade, row.subject, row.campus): row
        for row in opening_rows
    }
    discount_by_key = {(row.fiscal_season, row.track, row.product, row.grade): row for row in discount_rows}
    templates: list[RevenueLineTemplate] = []
    missing: list[str] = []
    for combination in basis.combination_inputs:
        for season_index in range(1, 5):
            season = fiscal_year * 10 + season_index
            revenue = revenue_by_key.get((season, combination.track, combination.product))
            detail_discount = discount_by_key.get(
                (season, combination.track, combination.product, combination.grade)
            )
            opening = opening_by_key.get(
                (
                    season,
                    combination.track,
                    combination.product,
                    combination.grade,
                    combination.subject,
                    combination.campus,
                )
            )
            if revenue is None or detail_discount is None or (season_index == 1 and opening is None):
                missing.append(f"{combination.stable_id}:{season}")
                continue
            templates.append(
                RevenueLineTemplate(
                    combination_stable_key=combination.stable_id,
                    fiscal_season=season,
                    track=combination.track,
                    product=combination.product,
                    entry_attribute=combination.entry_attribute,
                    grade=combination.grade,
                    subject=combination.subject,
                    campus=combination.campus,
                    list_price_yuan=revenue.list_price_yuan,
                    detail_discount_rate_ppm=detail_discount.discount_rate_ppm,
                    current_season_recognition_rate_ppm=revenue.current_season_recognition_rate_ppm,
                    opening_carryover_revenue_yuan=(opening.amount_yuan if opening else Decimal(0)),
                )
            )
    if missing:
        raise DomainError(
            code="OPTIMIZATION_REVENUE_TEMPLATE_INCOMPLETE",
            message="自动调平收入参数未覆盖全部预算明细",
            status_code=409,
            details={"missing": missing},
        )
    confirmed_keys = {item.rate_parameter_stable_key for item in confirmations}
    allocations_by_stable = {
        row.combination_stable_key: row for row in basis.allocation_by_combination.values()
    }
    entry_decisions = tuple(
        EntryDecision(
            stable_id=key,
            current_value=row.entry_initial_value,
            hard_lower_bound=0,
            hard_upper_bound=entry_total.locked_value,
            candidate_eligible=row.candidate_eligible,
        )
        for key, row in sorted(allocations_by_stable.items())
    )
    rate_decisions = tuple(
        RateDecision(
            stable_id=str(row.rate_parameter_id),
            track=row.track,
            rate_type=row.rate_type,
            current_value_ppm=row.current_value_ppm,
            lower_bound_ppm=row.lower_bound_ppm,
            upper_bound_ppm=row.upper_bound_ppm,
            locked=row.locked,
            boundary_confirmed=row.rate_parameter_stable_key in confirmed_keys,
        )
        for row in sorted(rate_rows, key=lambda item: item.rate_parameter_stable_key)
    )
    discount_decisions = tuple(
        DiscountDecision(
            stable_id=row.detail_discount_stable_key,
            fiscal_season=row.fiscal_season,
            track=row.track,
            product=row.product,
            grade=row.grade,
            current_value_ppm=row.discount_rate_ppm,
        )
        for row in sorted(
            discount_rows,
            key=lambda item: (
                item.fiscal_season,
                item.track,
                item.product,
                item.grade,
            ),
        )
    )
    opening_inputs = None
    if basis.opening_old_students is not None:
        from packages.contracts.opening import OpeningCreateRequest

        opening_id = _manifest_uuid(revision, "opening_student_version_id", "缺少期初老生版本")
        opening_version = await session.get(OpeningStudentVersion, opening_id)
        assert opening_version is not None  # Already validated by _load_basis.
        request = OpeningCreateRequest.model_validate(opening_version.content["request"])
        by_key = {
            (row.track, row.product, row.grade, row.subject, row.campus): row.stable_id
            for row in basis.combination_inputs
        }
        terms = []
        for row in request.rows:
            if row.grade == "九年级" or (row.grade == "八年级" and row.subject in {"地理", "生物"}):
                continue
            target_grade = "八年级" if row.grade == "七年级" else "九年级"
            stable = by_key[(row.track, row.product, target_grade, row.subject, row.campus)]
            terms.append(OpeningDecisionInput(stable, row.enrollment, str(row.rate_parameter_id)))
        covered = {term.combination_stable_id for term in terms}
        terms.extend(
            OpeningDecisionInput(stable, 0, None)
            for stable in basis.opening_old_students
            if stable not in covered
        )
        opening_inputs = tuple(sorted(terms, key=lambda term: term.combination_stable_id))
    context = BudgetEvaluationContext(
        fiscal_year=fiscal_year,
        budget_tax_conversion_factor=resource.budget_tax_conversion_factor,
        fixed_entry_total=entry_total.locked_value,
        combinations=tuple(basis.combination_inputs),
        rates=tuple(basis.rate_inputs),
        plans=tuple(basis.plan_inputs),
        entry_decisions=entry_decisions,
        rate_decisions=rate_decisions,
        class_size_parameters=tuple(
            ClassSizeInput(str(row.class_size_parameter_id), row.track, row.product, row.final_value)
            for row in class_rows
        ),
        revenue_line_templates=tuple(templates),
        composite_discount_targets=tuple(_composite_target(item) for item in composite_rows),
        revenue_targets=tuple(
            RevenueTarget(
                str(item.budget_target_id),
                item.scope_level,
                item.track,
                item.target_revenue_yuan,
            )
            for item in targets
        ),
        discount_decisions=discount_decisions,
        opening_inputs=opening_inputs,
    )
    input_fingerprint = content_hash(
        {
            "schema_version": "1.0",
            "optimization_model_version": OPTIMIZATION_MODEL_VERSION,
            "revision_content_hash": revision.revision_content_hash,
            "target_closure": closure.content_hash,
            "rate_parameter_version": rate_version.content_hash,
            "resource_finance_version": resource.content_hash,
            "discount_parameter_version": discount.content_hash,
        }
    )
    return context, closure, input_fingerprint


def _composite_target(row: CompositeDiscountTarget) -> CompositeDiscountTargetInput:
    return CompositeDiscountTargetInput(
        target_id=str(row.composite_discount_target_id),
        scope_level=row.scope_level,
        track=row.track,
        period_type=row.period_type,
        fiscal_season=row.fiscal_season,
        target_rate_ppm=row.target_rate_ppm,
    )


def _attempt_content(item: SolveAttempt) -> dict[str, object]:
    return {
        "stage": item.stage.value,
        "profile": item.profile.value,
        "status": item.status.value,
        "evaluated_state_count": item.evaluated_state_count,
        "proof_code": item.proof_code,
        "proof_summary": item.proof_summary or {},
    }


def _scenario_models(
    run_id: uuid.UUID,
    candidates: tuple[CandidateEvaluation, ...],
    context: BudgetEvaluationContext,
) -> tuple[list[RebalancingScenario], list[ScenarioChange]]:
    original_entry = {item.stable_id: item.current_value for item in context.entry_decisions}
    original_rate = {item.stable_id: item.current_value_ppm for item in context.rate_decisions}
    original_discount = {item.stable_id: item.current_value_ppm for item in context.discount_decisions}
    entry_bounds = {
        item.stable_id: (item.hard_lower_bound, item.hard_upper_bound) for item in context.entry_decisions
    }
    rate_bounds = {
        item.stable_id: (item.lower_bound_ppm, item.upper_bound_ppm) for item in context.rate_decisions
    }
    discount_bounds = {
        item.stable_id: (item.lower_bound_ppm, item.upper_bound_ppm) for item in context.discount_decisions
    }
    scenarios: list[RebalancingScenario] = []
    pending_changes: list[tuple[RebalancingScenario, dict[str, object]]] = []
    for raw in candidates:
        retention, new_share = symmetric_rate_contributions(
            r00=raw.revenue_r00_yuan,
            r10=raw.revenue_r10_yuan,
            r01=raw.revenue_r01_yuan,
            r11=raw.revenue_r11_yuan,
        )
        scenario_content = {
            "result_hash": raw.result_hash,
            "scenario_types": [item.value for item in raw.scenario_types],
            "target_variances": [
                {"scope": scope, "variance_yuan": format(value, "f")}
                for scope, value in raw.target_variances_yuan
            ],
            "retention_contribution_yuan": format(retention, "f"),
            "new_share_contribution_yuan": format(new_share, "f"),
            "headcount_signature": [
                {"row_key": key, "integer_total_students": value} for key, value in raw.headcount_signature
            ],
            "objective_values": {
                "department_revenue_yuan": format(raw.revenue_r11_yuan, "f"),
                "entry_soft_bound_violation": raw.entry_soft_bound_violation,
                "maximum_entry_adjustment": max(map(abs, raw.entry_adjustments), default=0),
                "total_entry_adjustment": sum(map(abs, raw.entry_adjustments)),
                "maximum_rate_adjustment_ppm": max(map(abs, raw.rate_adjustments_ppm), default=0),
                "total_rate_adjustment_ppm": sum(map(abs, raw.rate_adjustments_ppm)),
                "maximum_discount_adjustment_ppm": max(map(abs, raw.discount_adjustments_ppm), default=0),
                "total_discount_adjustment_ppm": sum(map(abs, raw.discount_adjustments_ppm)),
                "validation_codes": list(raw.validation_codes),
                "boundary_hits": _boundary_hits(
                    raw.decision,
                    entry_bounds=entry_bounds,
                    rate_bounds=rate_bounds,
                    discount_bounds=discount_bounds,
                ),
            },
        }
        scenario = RebalancingScenario(
            rebalancing_scenario_id=uuid7(),
            optimization_run_id=run_id,
            result_hash=raw.result_hash,
            scenario_types=scenario_content["scenario_types"],
            target_variances=scenario_content["target_variances"],
            retention_contribution_yuan=retention,
            new_share_contribution_yuan=new_share,
            headcount_signature=scenario_content["headcount_signature"],
            objective_values=scenario_content["objective_values"],
            content_hash=content_hash(scenario_content),
        )
        scenarios.append(scenario)
        for object_type, values, originals in (
            ("ENTRY", raw.decision.entry_values, original_entry),
            ("RATE", raw.decision.rate_values, original_rate),
            ("DISCOUNT", raw.decision.discount_values, original_discount),
        ):
            for key, value in values:
                adjustment = value - originals[key]
                if adjustment:
                    pending_changes.append(
                        (
                            scenario,
                            {
                                "object_type": object_type,
                                "object_stable_key": key,
                                "original_value": originals[key],
                                "suggested_value": value,
                                "adjustment": adjustment,
                            },
                        )
                    )
    changes = [
        ScenarioChange(
            scenario_change_id=uuid7(),
            rebalancing_scenario_id=scenario.rebalancing_scenario_id,
            **item,
            content_hash=content_hash(item),
        )
        for scenario, item in pending_changes
    ]
    return scenarios, changes


def _boundary_hits(
    decision: DecisionVector,
    *,
    entry_bounds: dict[str, tuple[int, int]],
    rate_bounds: dict[str, tuple[int, int]],
    discount_bounds: dict[str, tuple[int, int]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for object_type, values, bounds in (
        ("ENTRY", decision.entry_values, entry_bounds),
        ("RATE", decision.rate_values, rate_bounds),
        ("DISCOUNT", decision.discount_values, discount_bounds),
    ):
        for stable_key, value in values:
            lower, upper = bounds[stable_key]
            if value == lower:
                rows.append({"object_type": object_type, "stable_key": stable_key, "boundary": "LOWER"})
            if value == upper:
                rows.append({"object_type": object_type, "stable_key": stable_key, "boundary": "UPPER"})
    return rows


def _proof_models(
    run_id: uuid.UUID, attempts: tuple[SolveAttempt, ...], input_fingerprint: str
) -> list[InfeasibilityProof]:
    rows: list[InfeasibilityProof] = []
    for raw in attempts:
        if raw.status != SolveStatus.INFEASIBLE:
            continue
        model_hash = content_hash(
            {"input_fingerprint": input_fingerprint, "stage": raw.stage.value, "profile": raw.profile.value}
        )
        content = {
            "stage": raw.stage.value,
            "profile": raw.profile.value,
            "proof_code": raw.proof_code,
            "model_hash": model_hash,
            "hard_constraint_groups": [
                "FIXED_ENTRY_TOTAL",
                "ENTRY_HARD_BOUNDARIES",
                "CLASS_SIZE",
                "TARGET_CLOSURE",
                "COMPOSITE_DISCOUNT",
            ],
            "proof_summary": raw.proof_summary or {},
        }
        rows.append(
            InfeasibilityProof(
                infeasibility_proof_id=uuid7(),
                optimization_run_id=run_id,
                **content,
                content_hash=content_hash(content),
            )
        )
    return rows


def _run_response(
    run: OptimizationRun,
    scenarios: list[RebalancingScenario],
    changes: list[ScenarioChange],
    proofs: list[InfeasibilityProof],
) -> OptimizationRunResponse:
    changes_by_scenario: dict[uuid.UUID, list[ScenarioChange]] = {}
    for item in changes:
        changes_by_scenario.setdefault(item.rebalancing_scenario_id, []).append(item)
    return OptimizationRunResponse(
        optimization_run_id=run.optimization_run_id,
        budget_revision_id=run.budget_revision_id,
        target_closure_result_version_id=run.target_closure_result_version_id,
        optimization_model_version=run.optimization_model_version,
        terminal_stage=run.terminal_stage,
        status=run.status,
        discount_unlock_required=run.discount_unlock_required,
        discount_unlock_confirmation_id=run.discount_unlock_confirmation_id,
        reason_code=run.reason_code,
        solver_adapter=run.solver_adapter,
        maximum_enumerated_states=run.maximum_enumerated_states,
        maximum_solver_seconds=run.maximum_solver_seconds,
        maximum_candidate_validations=run.maximum_candidate_validations,
        maximum_group_table_rows=run.maximum_group_table_rows,
        evaluated_state_count=run.evaluated_state_count,
        attempts=run.attempts,
        input_fingerprint=run.input_fingerprint,
        content_hash=run.content_hash,
        created_by=run.created_by,
        created_at=run.created_at,
        scenarios=[
            RebalancingScenarioResponse(
                rebalancing_scenario_id=item.rebalancing_scenario_id,
                result_hash=item.result_hash,
                scenario_types=item.scenario_types,
                target_variances=item.target_variances,
                retention_contribution_yuan=item.retention_contribution_yuan,
                new_share_contribution_yuan=item.new_share_contribution_yuan,
                headcount_signature=item.headcount_signature,
                objective_values=item.objective_values,
                content_hash=item.content_hash,
                changes=[
                    ScenarioChangeResponse.model_validate(change, from_attributes=True)
                    for change in sorted(
                        changes_by_scenario.get(item.rebalancing_scenario_id, []),
                        key=lambda row: (row.object_type, row.object_stable_key),
                    )
                ],
            )
            for item in sorted(scenarios, key=lambda row: row.result_hash)
        ],
        infeasibility_proofs=[
            InfeasibilityProofResponse.model_validate(item, from_attributes=True)
            for item in sorted(proofs, key=lambda row: (row.stage, row.profile))
        ],
    )
