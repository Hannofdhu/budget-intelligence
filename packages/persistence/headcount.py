import uuid
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.contracts.versioning import (
    HeadcountCalculateRequest,
    HeadcountResultVersionResponse,
    HeadcountRoundingTraceResponse,
    HeadcountValidationResponse,
    RevisionCommandResponse,
    SeasonHeadcountResultResponse,
)
from packages.domain.identity.models import Actor, Role
from packages.domain.versioning.errors import DomainError
from packages.domain.versioning.hashing import content_hash
from packages.headcount.calculation import (
    CombinationInput,
    HeadcountInvariantError,
    ManualPlanInput,
    RateInput,
    RoundingTrace,
    SeasonHeadcount,
    calculate_headcounts,
    fraction_string,
)
from packages.persistence.entry_planning import (
    _assert_version_access,
    _create_child_revision,
    _lock_current_revision,
    _manifest_uuid,
    _revision_link_response,
)
from packages.persistence.ids import uuid7
from packages.persistence.models import (
    BudgetCombination,
    BudgetCombinationSetVersion,
    EntryDetailAllocation,
    EntryDetailSetVersion,
    EntryScopeEvaluation,
    EntryScopeVersion,
    HeadcountResultVersion,
    HeadcountRoundingTrace,
    HeadcountValidationResult,
    PlannedNewStudentCount,
    PlannedNewStudentVersion,
    RateParameter,
    RateParameterVersion,
    SeasonHeadcountResult,
)
from packages.persistence.versioning import (
    _add_audit,
    _idempotency_replay,
    _now,
    _store_receipt,
)
from packages.planned_new_students.governance import quarter_name

HEADCOUNT_DECIMAL_QUANTUM = Decimal("0.000000001")
DOWNSTREAM_REMOVALS = (
    "class_size_result_version_id",
    "revenue_result_version_id",
    "target_closure_result_version_id",
    "optimization_run_id",
    "discount_unlock_id",
    "formal_confirmation_id",
)


async def calculate_headcount_result(
    session: AsyncSession,
    *,
    actor: Actor,
    revision_id: uuid.UUID,
    expected_revision_id: uuid.UUID,
    payload: HeadcountCalculateRequest,
    idempotency_key: uuid.UUID,
    request_id: str,
    acting_role: Role = Role.DATA_MAINTAINER,
) -> HeadcountResultVersionResponse:
    command_type = "CALCULATE_HEADCOUNT_RESULT"
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
            return HeadcountResultVersionResponse.model_validate(replay)
        parent, budget = await _lock_current_revision(
            session,
            actor=actor,
            revision_id=revision_id,
            expected_revision_id=expected_revision_id,
        )
        basis = await _load_basis(session, parent, budget.fiscal_year, allow_opening=True)
        try:
            calculation = calculate_headcounts(
                fiscal_year=budget.fiscal_year,
                combinations=basis.combination_inputs,
                rates=basis.rate_inputs,
                plans=basis.plan_inputs,
                opening_old_students=basis.opening_old_students,
            )
        except HeadcountInvariantError as exc:
            raise DomainError(code=exc.code, message=exc.message, status_code=409) from exc

        if payload.calculation_rule_version != calculation.rule_version:
            raise DomainError(
                code="HEADCOUNT_RULE_VERSION_MISMATCH",
                message="请求计算规则与财年输入不匹配；FY28 期初老生应显式使用 opening-v2",
                status_code=409,
            )

        input_fingerprint = content_hash(
            {
                "schema_version": "1.0",
                "calculation_rule_version": calculation.rule_version,
                **({"opening_student_version_hash": basis.opening_hash} if basis.opening_hash else {}),
                "combination_set_version": basis.combination_version.content_hash,
                "entry_scope_version": basis.scope_version.content_hash,
                "entry_detail_set_version": basis.detail_version.content_hash,
                "rate_parameter_version": basis.rate_version.content_hash,
                "planned_new_student_version": (
                    basis.plan_version.content_hash if basis.plan_version is not None else None
                ),
            }
        )
        rows_content = [
            _calculation_row_content(
                row,
                fiscal_year=budget.fiscal_year,
                combination_by_stable=basis.combination_by_stable,
                allocation_by_combination=basis.allocation_by_combination,
            )
            for row in calculation.rows
        ]
        validation_content = [
            {
                "fiscal_season": issue.fiscal_season,
                "combination_stable_key": issue.combination_stable_id or "0" * 64,
                "code": issue.code,
                "severity": "BLOCKING",
                "message": issue.message,
                "detail": issue.detail,
            }
            for issue in calculation.validations
        ]
        version_content = {
            "schema_version": "1.0",
            "input_fingerprint": input_fingerprint,
            "fiscal_year": budget.fiscal_year,
            "calculation_rule_version": calculation.rule_version,
            "status": "SUCCEEDED",
            "publishable": calculation.publishable,
            "rows": rows_content,
            "validations": validation_content,
        }
        occurred_at = _now()
        version_id = uuid7()
        revision, revision_response = await _create_child_revision(
            session,
            parent=parent,
            budget=budget,
            actor=actor,
            command_type=command_type,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            component_updates={"headcount_result_version_id": str(version_id)},
            component_removals=DOWNSTREAM_REMOVALS,
        )
        version = HeadcountResultVersion(
            headcount_result_version_id=version_id,
            budget_revision_id=revision.budget_revision_id,
            combination_set_version_id=basis.combination_version.combination_set_version_id,
            entry_scope_version_id=basis.scope_version.entry_scope_version_id,
            entry_detail_set_version_id=basis.detail_version.entry_detail_set_version_id,
            rate_parameter_version_id=basis.rate_version.rate_parameter_version_id,
            planned_new_student_version_id=(
                basis.plan_version.planned_new_student_version_id if basis.plan_version is not None else None
            ),
            fiscal_year=budget.fiscal_year,
            calculation_rule_version=calculation.rule_version,
            status="SUCCEEDED",
            publishable=calculation.publishable,
            record_count=len(rows_content),
            validation_count=len(validation_content),
            input_fingerprint=input_fingerprint,
            content_hash=content_hash(version_content),
            request_hash=request_hash,
            created_by=actor.user_id,
            created_at=occurred_at,
        )
        session.add(version)
        await session.flush()
        rows, traces = _build_result_models(version_id, rows_content)
        validations = [
            HeadcountValidationResult(
                headcount_validation_result_id=uuid7(),
                headcount_result_version_id=version_id,
                **item,
                content_hash=content_hash(item),
            )
            for item in validation_content
        ]
        session.add_all(rows)
        await session.flush()
        session.add_all(traces)
        session.add_all(validations)
        response = _version_response(version, rows, traces, validations, revision_response)
        _add_audit(
            session,
            event_type="HEADCOUNT_RESULT_CALCULATED",
            actor=actor,
            acting_role=acting_role,
            object_type="HEADCOUNT_RESULT_VERSION",
            object_key={"headcount_result_version_id": str(version_id)},
            detail={
                "record_count": version.record_count,
                "validation_count": version.validation_count,
                "publishable": version.publishable,
                "input_fingerprint": input_fingerprint,
                "content_hash": version.content_hash,
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


async def get_headcount_result_version(
    session: AsyncSession,
    *,
    actor: Actor,
    headcount_result_version_id: uuid.UUID,
) -> HeadcountResultVersionResponse:
    version = await session.get(HeadcountResultVersion, headcount_result_version_id)
    if version is None:
        raise DomainError(
            code="HEADCOUNT_RESULT_VERSION_NOT_FOUND", message="人数结果版本不存在", status_code=404
        )
    revision, _ = await _assert_version_access(session, actor=actor, revision_id=version.budget_revision_id)
    rows = list(
        await session.scalars(
            select(SeasonHeadcountResult)
            .where(SeasonHeadcountResult.headcount_result_version_id == headcount_result_version_id)
            .order_by(
                SeasonHeadcountResult.fiscal_season,
                SeasonHeadcountResult.combination_stable_key,
            )
        )
    )
    row_ids = [row.season_headcount_result_id for row in rows]
    traces = (
        list(
            await session.scalars(
                select(HeadcountRoundingTrace)
                .where(HeadcountRoundingTrace.season_headcount_result_id.in_(row_ids))
                .order_by(
                    HeadcountRoundingTrace.season_headcount_result_id,
                    HeadcountRoundingTrace.student_type,
                )
            )
        )
        if row_ids
        else []
    )
    validations = list(
        await session.scalars(
            select(HeadcountValidationResult)
            .where(HeadcountValidationResult.headcount_result_version_id == headcount_result_version_id)
            .order_by(
                HeadcountValidationResult.fiscal_season,
                HeadcountValidationResult.combination_stable_key,
                HeadcountValidationResult.code,
            )
        )
    )
    return _version_response(version, rows, traces, validations, _revision_link_response(revision))


class _Basis:
    def __init__(
        self,
        *,
        combination_version: BudgetCombinationSetVersion,
        scope_version: EntryScopeVersion,
        detail_version: EntryDetailSetVersion,
        rate_version: RateParameterVersion,
        plan_version: PlannedNewStudentVersion | None,
        combination_inputs: list[CombinationInput],
        rate_inputs: list[RateInput],
        plan_inputs: list[ManualPlanInput],
        combination_by_stable: dict[str, BudgetCombination],
        allocation_by_combination: dict[uuid.UUID, EntryDetailAllocation],
        opening_old_students: dict[str, Fraction] | None = None,
        opening_hash: str | None = None,
    ) -> None:
        self.combination_version = combination_version
        self.scope_version = scope_version
        self.detail_version = detail_version
        self.rate_version = rate_version
        self.plan_version = plan_version
        self.combination_inputs = combination_inputs
        self.rate_inputs = rate_inputs
        self.plan_inputs = plan_inputs
        self.combination_by_stable = combination_by_stable
        self.allocation_by_combination = allocation_by_combination
        self.opening_old_students = opening_old_students
        self.opening_hash = opening_hash


async def _load_basis(
    session: AsyncSession,
    revision: object,
    fiscal_year: int,
    *,
    allow_opening: bool = True,
) -> _Basis:
    from packages.persistence.models import BudgetRevision

    assert isinstance(revision, BudgetRevision)
    combination_version_id = _manifest_uuid(
        revision, "combination_set_version_id", "人数推演前必须先生成预算组合"
    )
    scope_version_id = _manifest_uuid(revision, "entry_scope_version_id", "人数推演前必须先确认入口范围")
    detail_version_id = _manifest_uuid(
        revision, "entry_detail_set_version_id", "人数推演前必须先计算入口明细"
    )
    rate_version_id = _manifest_uuid(
        revision, "rate_parameter_version_id", "人数推演前必须先建立比例参数版本"
    )
    combination_version = await session.get(BudgetCombinationSetVersion, combination_version_id)
    scope_version = await session.get(EntryScopeVersion, scope_version_id)
    detail_version = await session.get(EntryDetailSetVersion, detail_version_id)
    rate_version = await session.get(RateParameterVersion, rate_version_id)
    if None in (combination_version, scope_version, detail_version, rate_version):
        raise DomainError(
            code="HEADCOUNT_BASIS_NOT_FOUND", message="人数推演所引用的上游版本不存在", status_code=404
        )
    assert combination_version is not None
    assert scope_version is not None
    assert detail_version is not None
    assert rate_version is not None
    if (
        combination_version.target_fiscal_year != fiscal_year
        or scope_version.fiscal_year != fiscal_year
        or detail_version.fiscal_year != fiscal_year
        or rate_version.fiscal_year != fiscal_year
        or scope_version.combination_set_version_id != combination_version_id
        or detail_version.combination_set_version_id != combination_version_id
        or detail_version.entry_scope_version_id != scope_version_id
    ):
        raise DomainError(
            code="HEADCOUNT_BASIS_MISMATCH",
            message="预算组合、入口范围、入口明细、比例参数或财年不一致",
            status_code=409,
        )
    combinations = list(
        await session.scalars(
            select(BudgetCombination)
            .where(
                BudgetCombination.combination_set_version_id == combination_version_id,
                BudgetCombination.enabled.is_(True),
            )
            .order_by(BudgetCombination.combination_stable_key)
        )
    )
    evaluations = list(
        await session.scalars(
            select(EntryScopeEvaluation).where(
                EntryScopeEvaluation.entry_scope_version_id == scope_version_id
            )
        )
    )
    evaluation_by_combination = {row.combination_id: row for row in evaluations}
    if set(evaluation_by_combination) != {row.combination_id for row in combinations}:
        raise DomainError(
            code="HEADCOUNT_SCOPE_INCOMPLETE",
            message="入口范围判定未完整覆盖全部启用预算组合",
            status_code=409,
        )
    allocations = list(
        await session.scalars(
            select(EntryDetailAllocation).where(
                EntryDetailAllocation.entry_detail_set_version_id == detail_version_id
            )
        )
    )
    allocation_by_combination = {row.combination_id: row for row in allocations}
    combination_inputs = [
        CombinationInput(
            stable_id=row.combination_stable_key,
            track=row.track,
            product=row.product,
            entry_attribute=evaluation_by_combination[row.combination_id].effective_entry_attribute,
            grade=row.grade,
            subject=row.subject,
            campus=row.campus,
            entry_initial_value=(
                allocation_by_combination[row.combination_id].entry_initial_value
                if row.combination_id in allocation_by_combination
                else 0
            ),
        )
        for row in combinations
    ]
    rates = list(
        await session.scalars(
            select(RateParameter).where(RateParameter.rate_parameter_version_id == rate_version_id)
        )
    )
    rate_inputs = [
        RateInput(
            rate_id=str(row.rate_parameter_id),
            rate_type=row.rate_type,  # type: ignore[arg-type]
            fiscal_season=row.target_fiscal_season,
            track=row.track,
            product=row.product,
            grade=row.grade,
            campus=row.campus,
            value_ppm=row.current_value_ppm,
        )
        for row in rates
    ]
    plan_version, plans = await _load_plan_state(session, revision)
    if plan_version is not None and (
        plan_version.fiscal_year != fiscal_year
        or plan_version.combination_set_version_id != combination_version_id
        or plan_version.entry_scope_version_id != scope_version_id
        or plan_version.entry_detail_set_version_id != detail_version_id
    ):
        raise DomainError(
            code="HEADCOUNT_PLANNED_INPUT_MISMATCH",
            message="计划新生人次版本与当前人数推演依据不一致",
            status_code=409,
        )
    plan_inputs = [
        ManualPlanInput(
            plan_id=str(row.planned_new_student_count_id),
            combination_stable_id=row.combination_stable_key,
            fiscal_season=row.fiscal_season,
            value=row.planned_new_students,
            locked=row.locked,
            manual_renewal_rate_ppm=(
                int(row.lock_origin["manual_renewal_rate_ppm"])
                if row.lock_origin.get("manual_renewal_rate_ppm") is not None
                else None
            ),
        )
        for row in plans
    ]
    opening_values = None
    opening_hash = None
    if allow_opening:
        from packages.persistence.opening import load_opening_headcounts

        opening_values, opening_hash = await load_opening_headcounts(
            session,
            revision=revision,
            fiscal_year=fiscal_year,
            combinations=combinations,
            combination_version_id=combination_version_id,
            rate_version_id=rate_version_id,
        )
    return _Basis(
        combination_version=combination_version,
        scope_version=scope_version,
        detail_version=detail_version,
        rate_version=rate_version,
        plan_version=plan_version,
        combination_inputs=combination_inputs,
        rate_inputs=rate_inputs,
        plan_inputs=plan_inputs,
        combination_by_stable={row.combination_stable_key: row for row in combinations},
        allocation_by_combination=allocation_by_combination,
        opening_old_students=opening_values,
        opening_hash=opening_hash,
    )


async def _load_plan_state(
    session: AsyncSession, revision: object
) -> tuple[PlannedNewStudentVersion | None, list[PlannedNewStudentCount]]:
    from packages.persistence.models import BudgetRevision

    assert isinstance(revision, BudgetRevision)
    raw = revision.component_manifest.get("planned_new_student_version_id")
    if raw is None:
        return None, []
    try:
        version_id = uuid.UUID(str(raw))
    except ValueError as exc:
        raise DomainError(
            code="REVISION_MANIFEST_INVALID", message="计划新生人次版本清单无效", status_code=409
        ) from exc
    version = await session.get(PlannedNewStudentVersion, version_id)
    if version is None:
        raise DomainError(
            code="PLANNED_NEW_STUDENT_VERSION_NOT_FOUND",
            message="计划新生人次版本不存在",
            status_code=404,
        )
    rows = list(
        await session.scalars(
            select(PlannedNewStudentCount).where(
                PlannedNewStudentCount.planned_new_student_version_id == version_id
            )
        )
    )
    return version, rows


def _calculation_row_content(
    row: SeasonHeadcount,
    *,
    fiscal_year: int,
    combination_by_stable: dict[str, BudgetCombination],
    allocation_by_combination: dict[uuid.UUID, EntryDetailAllocation],
) -> dict[str, object]:
    combination = combination_by_stable[row.combination.stable_id]
    allocation = allocation_by_combination.get(combination.combination_id)
    old_variance = _variance(row.integer_old_students, row.theoretical_old_students)
    new_variance = _variance(row.integer_new_students, row.theoretical_new_students)
    name = quarter_name(fiscal_year, row.fiscal_season)
    if name is None:
        raise HeadcountInvariantError("SEASON_INVALID", "人数结果季节不属于当前财年")
    return {
        "combination_id": str(combination.combination_id),
        "entry_detail_allocation_id": (
            str(allocation.entry_detail_allocation_id) if allocation is not None else None
        ),
        "renewal_rate_parameter_id": row.renewal_rate_id,
        "new_student_share_parameter_id": row.new_student_share_rate_id,
        "planned_new_student_count_id": row.planned_input_id,
        "combination_stable_key": row.combination.stable_id,
        "fiscal_year": fiscal_year,
        "fiscal_season": row.fiscal_season,
        "season_index": row.season_index,
        "quarter_name": name,
        "track": row.combination.track,
        "product": row.combination.product,
        "entry_attribute": row.combination.entry_attribute,
        "grade": row.combination.grade,
        "subject": row.combination.subject,
        "campus": row.combination.campus,
        "calculation_status": ("CALCULATED" if row.integer_total_students is not None else "BLOCKED"),
        "new_student_source": row.new_student_source,
        "planned_eligibility_status": row.planned_eligibility_status,
        "theoretical_old_students": _decimal(row.theoretical_old_students),
        "theoretical_new_students": _decimal(row.theoretical_new_students),
        "theoretical_old_fraction": (
            fraction_string(row.theoretical_old_students)
            if row.theoretical_old_students is not None
            else None
        ),
        "theoretical_new_fraction": (
            fraction_string(row.theoretical_new_students)
            if row.theoretical_new_students is not None
            else None
        ),
        "integer_old_students": row.integer_old_students,
        "integer_new_students": row.integer_new_students,
        "integer_total_students": row.integer_total_students,
        "renewal_rate_ppm": row.renewal_rate_ppm,
        "new_student_share_input_ppm": row.new_student_share_input_ppm,
        "realized_retention_rate_ppm": row.realized_retention_rate_ppm,
        "realized_new_student_share_ppm": row.realized_new_student_share_ppm,
        "old_rounding_variance": old_variance,
        "new_rounding_variance": new_variance,
        "old_rounding_trace": _trace_content(row.old_rounding_trace),
        "new_rounding_trace": _trace_content(row.new_rounding_trace),
    }


def _build_result_models(
    version_id: uuid.UUID, rows_content: list[dict[str, object]]
) -> tuple[list[SeasonHeadcountResult], list[HeadcountRoundingTrace]]:
    rows: list[SeasonHeadcountResult] = []
    traces: list[HeadcountRoundingTrace] = []
    for item in rows_content:
        row_id = uuid7()
        row_content = {
            key: value
            for key, value in item.items()
            if key not in {"old_rounding_trace", "new_rounding_trace"}
        }
        row = SeasonHeadcountResult(
            season_headcount_result_id=row_id,
            headcount_result_version_id=version_id,
            **{
                **row_content,
                "combination_id": uuid.UUID(str(row_content["combination_id"])),
                "entry_detail_allocation_id": _optional_uuid(row_content["entry_detail_allocation_id"]),
                "renewal_rate_parameter_id": _optional_uuid(row_content["renewal_rate_parameter_id"]),
                "new_student_share_parameter_id": _optional_uuid(
                    row_content["new_student_share_parameter_id"]
                ),
                "planned_new_student_count_id": _optional_uuid(row_content["planned_new_student_count_id"]),
            },
            content_hash=content_hash(row_content),
        )
        rows.append(row)
        for trace_key in ("old_rounding_trace", "new_rounding_trace"):
            raw_trace = item[trace_key]
            if not isinstance(raw_trace, dict):
                continue
            traces.append(
                HeadcountRoundingTrace(
                    headcount_rounding_trace_id=uuid7(),
                    season_headcount_result_id=row_id,
                    **raw_trace,
                    content_hash=content_hash(raw_trace),
                )
            )
    return rows, traces


def _trace_content(trace: RoundingTrace | None) -> dict[str, object] | None:
    if trace is None:
        return None
    return {
        "student_type": trace.student_type,
        "exact_theory_fraction": fraction_string(trace.exact_theory),
        "floor_value": trace.floor_value,
        "exact_remainder_fraction": fraction_string(trace.exact_remainder),
        "fixed_input": trace.fixed_input,
        "remainder_rank": trace.remainder_rank,
        "tail_assigned": trace.tail_assigned,
        "group_target": trace.group_target,
        "fixed_total": trace.fixed_total,
        "adjustable_target": trace.adjustable_target,
    }


def _optional_uuid(value: object) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value is not None else None


def _decimal(value: Fraction | None) -> Decimal | None:
    if value is None:
        return None
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        HEADCOUNT_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP
    )


def _variance(integer: int | None, theory: Fraction | None) -> Decimal | None:
    if integer is None or theory is None:
        return None
    return _decimal(Fraction(integer) - theory)


def _version_response(
    version: HeadcountResultVersion,
    rows: Sequence[SeasonHeadcountResult],
    traces: Sequence[HeadcountRoundingTrace],
    validations: Sequence[HeadcountValidationResult],
    revision: RevisionCommandResponse,
) -> HeadcountResultVersionResponse:
    trace_by_key = {(row.season_headcount_result_id, row.student_type): row for row in traces}
    items = [
        SeasonHeadcountResultResponse(
            season_headcount_result_id=row.season_headcount_result_id,
            combination_id=row.combination_id,
            entry_detail_allocation_id=row.entry_detail_allocation_id,
            combination_stable_key=row.combination_stable_key,
            fiscal_year=row.fiscal_year,
            fiscal_season=row.fiscal_season,
            season_index=row.season_index,
            quarter_name=row.quarter_name,
            track=row.track,
            product=row.product,
            entry_attribute=row.entry_attribute,
            grade=row.grade,
            subject=row.subject,
            campus=row.campus,
            calculation_status=row.calculation_status,
            new_student_source=row.new_student_source,
            planned_eligibility_status=row.planned_eligibility_status,
            theoretical_old_students=row.theoretical_old_students,
            theoretical_new_students=row.theoretical_new_students,
            theoretical_old_fraction=row.theoretical_old_fraction,
            theoretical_new_fraction=row.theoretical_new_fraction,
            integer_old_students=row.integer_old_students,
            integer_new_students=row.integer_new_students,
            integer_total_students=row.integer_total_students,
            renewal_rate_ppm=row.renewal_rate_ppm,
            new_student_share_input_ppm=row.new_student_share_input_ppm,
            realized_retention_rate_ppm=row.realized_retention_rate_ppm,
            realized_new_student_share_ppm=row.realized_new_student_share_ppm,
            old_rounding_variance=row.old_rounding_variance,
            new_rounding_variance=row.new_rounding_variance,
            old_rounding_trace=_trace_response(trace_by_key.get((row.season_headcount_result_id, "OLD"))),
            new_rounding_trace=_trace_response(trace_by_key.get((row.season_headcount_result_id, "NEW"))),
            content_hash=row.content_hash,
        )
        for row in sorted(rows, key=lambda item: (item.fiscal_season, item.combination_stable_key))
    ]
    validation_items = [
        HeadcountValidationResponse(
            headcount_validation_result_id=row.headcount_validation_result_id,
            fiscal_season=row.fiscal_season,
            combination_stable_key=row.combination_stable_key,
            code=row.code,
            severity=row.severity,
            message=row.message,
            detail=row.detail,
            content_hash=row.content_hash,
        )
        for row in validations
    ]
    return HeadcountResultVersionResponse(
        headcount_result_version_id=version.headcount_result_version_id,
        budget_revision_id=version.budget_revision_id,
        combination_set_version_id=version.combination_set_version_id,
        entry_scope_version_id=version.entry_scope_version_id,
        entry_detail_set_version_id=version.entry_detail_set_version_id,
        rate_parameter_version_id=version.rate_parameter_version_id,
        planned_new_student_version_id=version.planned_new_student_version_id,
        fiscal_year=version.fiscal_year,
        calculation_rule_version=version.calculation_rule_version,
        status=version.status,
        publishable=version.publishable,
        record_count=version.record_count,
        validation_count=version.validation_count,
        input_fingerprint=version.input_fingerprint,
        content_hash=version.content_hash,
        created_by=version.created_by,
        created_at=version.created_at,
        items=items,
        validations=validation_items,
        revision=revision,
    )


def _trace_response(row: HeadcountRoundingTrace | None) -> HeadcountRoundingTraceResponse | None:
    if row is None:
        return None
    return HeadcountRoundingTraceResponse(
        student_type=row.student_type,
        exact_theory_fraction=row.exact_theory_fraction,
        floor_value=row.floor_value,
        exact_remainder_fraction=row.exact_remainder_fraction,
        fixed_input=row.fixed_input,
        remainder_rank=row.remainder_rank,
        tail_assigned=row.tail_assigned,
        group_target=row.group_target,
        fixed_total=row.fixed_total,
        adjustable_target=row.adjustable_target,
    )
