from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction

from packages.class_size.validation import (
    ClassSizeInput,
    HeadcountInput,
    validate_class_sizes,
)
from packages.headcount.calculation import (
    CombinationInput,
    HeadcountInvariantError,
    ManualPlanInput,
    RateInput,
    calculate_headcounts,
)
from packages.optimization.engine import (
    CandidateEvaluation,
    DecisionVector,
    DiscountDecision,
    EntryDecision,
    ExactEnumerationAdapter,
    OptimizationOutcome,
    OptimizationSolverAdapter,
    OptimizationStage,
    RateDecision,
    ScenarioProfile,
    SolveAttempt,
    SolveStatus,
    enumerate_fixed_total_entry_structures,
    enumerate_parallel_rate_water_levels,
    run_staged_optimization,
)
from packages.revenue.calculation import (
    CompositeDiscountTargetInput,
    RevenueLineInput,
    calculate_revenue,
)
from packages.target_closure.calculation import TargetRevenueInput, calculate_target_closure


@dataclass(frozen=True)
class RevenueLineTemplate:
    combination_stable_key: str
    fiscal_season: int
    track: str
    product: str
    entry_attribute: str
    grade: str
    subject: str
    campus: str
    list_price_yuan: Decimal
    detail_discount_rate_ppm: int
    current_season_recognition_rate_ppm: int
    opening_carryover_revenue_yuan: Decimal


@dataclass(frozen=True)
class RevenueTarget:
    target_id: str
    scope_level: str
    track: str | None
    target_revenue_yuan: Decimal


@dataclass(frozen=True)
class OpeningDecisionInput:
    combination_stable_id: str
    source_enrollment: int
    renewal_rate_id: str | None


@dataclass(frozen=True)
class BudgetEvaluationContext:
    fiscal_year: int
    budget_tax_conversion_factor: Decimal
    fixed_entry_total: int
    combinations: tuple[CombinationInput, ...]
    rates: tuple[RateInput, ...]
    plans: tuple[ManualPlanInput, ...]
    entry_decisions: tuple[EntryDecision, ...]
    rate_decisions: tuple[RateDecision, ...]
    class_size_parameters: tuple[ClassSizeInput, ...]
    revenue_line_templates: tuple[RevenueLineTemplate, ...]
    composite_discount_targets: tuple[CompositeDiscountTargetInput, ...]
    revenue_targets: tuple[RevenueTarget, ...]
    discount_decisions: tuple[DiscountDecision, ...] = ()
    opening_inputs: tuple[OpeningDecisionInput, ...] | None = None


def candidate_opening_values(
    context: BudgetEvaluationContext,
    rate_values: dict[str, int],
) -> dict[str, Fraction] | None:
    if context.opening_inputs is None:
        return None
    result: dict[str, Fraction] = {}
    rates = {rate.rate_id: rate for rate in context.rates}
    for item in context.opening_inputs:
        if item.combination_stable_id in result or item.source_enrollment < 0:
            raise HeadcountInvariantError("OPENING_CONTENT_INVALID", "候选期初输入重复或人数无效")
        if item.renewal_rate_id is None:
            if item.source_enrollment != 0:
                raise HeadcountInvariantError("OPENING_RATE_MISSING", "非零来源必须绑定续班率")
            result[item.combination_stable_id] = Fraction(0)
            continue
        rate = rates.get(item.renewal_rate_id)
        value = rate_values.get(item.renewal_rate_id)
        if (
            rate is None
            or rate.rate_type != "RENEWAL_RATE"
            or rate.fiscal_season != context.fiscal_year * 10 + 1
            or value is None
            or not 0 <= value <= 1000000
        ):
            raise HeadcountInvariantError("OPENING_RATE_MISSING", "候选缺少有效春续暑续班率")
        result[item.combination_stable_id] = Fraction(item.source_enrollment * value, 1000000)
    if set(result) != {item.stable_id for item in context.combinations}:
        raise HeadcountInvariantError("OPENING_COVERAGE_MISMATCH", "候选期初输入必须完整覆盖组合")
    return result


class BudgetCandidateEvaluator:
    """Recalculate every candidate with the same exact business kernels used by formal results."""

    def __init__(self, context: BudgetEvaluationContext) -> None:
        self.context = context
        self._combination_by_id = {item.stable_id: item for item in context.combinations}
        self._rate_by_id = {item.rate_id: item for item in context.rates}
        self._entry_rule_by_id = {item.stable_id: item for item in context.entry_decisions}
        self._rate_rule_by_id = {item.stable_id: item for item in context.rate_decisions}
        self._discount_rule_by_id = {item.stable_id: item for item in context.discount_decisions}
        self._discount_rule_by_key = {
            (item.fiscal_season, item.track, item.product, item.grade): item
            for item in context.discount_decisions
        }
        if len(self._discount_rule_by_key) != len(context.discount_decisions):
            raise ValueError("discount decisions must have unique business keys")
        self._template_by_key = {
            (item.combination_stable_key, item.fiscal_season): item for item in context.revenue_line_templates
        }
        if len(self._template_by_key) != len(context.revenue_line_templates):
            raise ValueError("revenue line templates must be unique")

    def evaluate(self, decision: DecisionVector) -> CandidateEvaluation:
        canonical = decision.canonical()
        entry_values = dict(canonical.entry_values)
        rate_values = dict(canonical.rate_values)
        discount_values = dict(canonical.discount_values)
        validation_codes = self._validate_decision(entry_values, rate_values, discount_values)
        if validation_codes:
            return CandidateEvaluation(
                decision=canonical,
                target_variances_yuan=(),
                hard_constraints_satisfied=False,
                validation_codes=validation_codes,
            )

        r11 = self._calculate(entry_values, rate_values, discount_values)
        baseline_rates = {item.rate_id: item.value_ppm for item in self.context.rates}
        renewal_only = dict(baseline_rates)
        new_share_only = dict(baseline_rates)
        for rate_id, value in rate_values.items():
            rate = self._rate_by_id[rate_id]
            if rate.rate_type == "RENEWAL_RATE":
                renewal_only[rate_id] = value
            else:
                new_share_only[rate_id] = value
        r00 = self._calculate(entry_values, baseline_rates, discount_values)
        r10 = self._calculate(entry_values, renewal_only, discount_values)
        r01 = self._calculate(entry_values, new_share_only, discount_values)
        all_validation_codes = tuple(
            sorted(
                {
                    *r11.validation_codes,
                    *r00.validation_codes,
                    *r10.validation_codes,
                    *r01.validation_codes,
                }
            )
        )
        targets = {(item.scope_level, item.track): item for item in self.context.revenue_targets}
        closure = calculate_target_closure(
            [
                TargetRevenueInput(
                    target_id=item.target_id,
                    scope_level=item.scope_level,
                    track=item.track,
                    target_revenue_yuan=item.target_revenue_yuan,
                    calculated_revenue_yuan=r11.annual_revenue_by_scope[(item.scope_level, item.track)],
                )
                for item in targets.values()
            ]
        )
        current_entry = {item.stable_id: item.current_value for item in self.context.entry_decisions}
        current_rates = {item.stable_id: item.current_value_ppm for item in self.context.rate_decisions}
        current_discounts = {
            item.stable_id: item.current_value_ppm for item in self.context.discount_decisions
        }
        entry_adjustments = tuple(value - current_entry[key] for key, value in sorted(entry_values.items()))
        rate_adjustments = tuple(value - current_rates[key] for key, value in sorted(rate_values.items()))
        discount_adjustments = tuple(
            value - current_discounts[key] for key, value in sorted(discount_values.items())
        )
        soft_violation = sum(
            _soft_violation(value, self._entry_rule_by_id[key].current_value)
            for key, value in entry_values.items()
        )
        return CandidateEvaluation(
            decision=canonical,
            target_variances_yuan=tuple(
                (f"{item.source.scope_level}:{item.source.track or ''}", item.variance_yuan)
                for item in closure.items
            ),
            hard_constraints_satisfied=not all_validation_codes,
            headcount_signature=r11.headcount_signature,
            revenue_r00_yuan=r00.department_annual_revenue,
            revenue_r10_yuan=r10.department_annual_revenue,
            revenue_r01_yuan=r01.department_annual_revenue,
            revenue_r11_yuan=r11.department_annual_revenue,
            validation_codes=all_validation_codes,
            entry_soft_bound_violation=soft_violation,
            entry_adjustments=entry_adjustments,
            rate_adjustments_ppm=rate_adjustments,
            discount_adjustments_ppm=discount_adjustments,
        )

    def _validate_decision(
        self,
        entry_values: dict[str, int],
        rate_values: dict[str, int],
        discount_values: dict[str, int],
    ) -> tuple[str, ...]:
        codes: list[str] = []
        if set(entry_values) != set(self._entry_rule_by_id):
            codes.append("ENTRY_DECISION_SET_MISMATCH")
        elif sum(entry_values.values()) != self.context.fixed_entry_total:
            codes.append("ENTRY_TOTAL_CHANGED")
        else:
            for key, value in entry_values.items():
                entry_rule = self._entry_rule_by_id[key]
                if not entry_rule.hard_lower_bound <= value <= entry_rule.hard_upper_bound:
                    codes.append("ENTRY_HARD_BOUNDARY_EXCEEDED")
                if not entry_rule.candidate_eligible and value != entry_rule.current_value:
                    codes.append("ENTRY_NON_CANDIDATE_CHANGED")
        if set(rate_values) != set(self._rate_rule_by_id):
            codes.append("RATE_DECISION_SET_MISMATCH")
        else:
            for key, value in rate_values.items():
                rate_rule = self._rate_rule_by_id[key]
                if value % 100:
                    codes.append("RATE_STEP_INVALID")
                if not rate_rule.lower_bound_ppm <= value <= rate_rule.upper_bound_ppm:
                    codes.append("RATE_BOUNDARY_EXCEEDED")
                if (
                    rate_rule.locked or not rate_rule.boundary_confirmed
                ) and value != rate_rule.current_value_ppm:
                    codes.append("RATE_LOCKED_OR_UNCONFIRMED_CHANGED")
        if discount_values:
            if set(discount_values) != set(self._discount_rule_by_id):
                codes.append("DISCOUNT_DECISION_SET_MISMATCH")
            else:
                for key, value in discount_values.items():
                    rule = self._discount_rule_by_id[key]
                    if value % 1_000:
                        codes.append("DISCOUNT_STEP_INVALID")
                    if not rule.lower_bound_ppm <= value <= rule.upper_bound_ppm:
                        codes.append("DISCOUNT_BOUNDARY_EXCEEDED")
                    if rule.locked and value != rule.current_value_ppm:
                        codes.append("DISCOUNT_LOCKED_CHANGED")
        return tuple(sorted(set(codes)))

    def _calculate(
        self,
        entry_values: dict[str, int],
        rate_values: dict[str, int],
        discount_values: dict[str, int],
    ) -> _KernelResult:
        combinations = [
            replace(item, entry_initial_value=entry_values.get(item.stable_id, 0))
            for item in self.context.combinations
        ]
        rates = [replace(item, value_ppm=rate_values[item.rate_id]) for item in self.context.rates]
        headcounts = calculate_headcounts(
            fiscal_year=self.context.fiscal_year,
            combinations=combinations,
            rates=rates,
            plans=list(self.context.plans),
            opening_old_students=candidate_opening_values(self.context, rate_values),
        )
        class_result = validate_class_sizes(
            headcounts=[
                HeadcountInput(
                    result_id=f"{row.combination.stable_id}:{row.fiscal_season}",
                    combination_stable_key=row.combination.stable_id,
                    fiscal_season=row.fiscal_season,
                    track=row.combination.track,
                    product=row.combination.product,
                    entry_attribute=row.combination.entry_attribute,
                    grade=row.combination.grade,
                    subject=row.combination.subject,
                    campus=row.combination.campus,
                    calculation_status=(
                        "CALCULATED" if row.integer_total_students is not None else "BLOCKED"
                    ),
                    integer_total_students=row.integer_total_students,
                )
                for row in headcounts.rows
            ],
            parameters=list(self.context.class_size_parameters),
        )
        revenue_lines: list[RevenueLineInput] = []
        for row in headcounts.rows:
            key = row.combination.stable_id, row.fiscal_season
            template = self._template_by_key.get(key)
            if template is None:
                raise ValueError(f"missing revenue line template for {key}")
            revenue_lines.append(
                RevenueLineInput(
                    result_id=f"{row.combination.stable_id}:{row.fiscal_season}",
                    combination_stable_key=row.combination.stable_id,
                    fiscal_season=row.fiscal_season,
                    track=row.combination.track,
                    product=row.combination.product,
                    entry_attribute=row.combination.entry_attribute,
                    grade=row.combination.grade,
                    subject=row.combination.subject,
                    campus=row.combination.campus,
                    integer_total_students=row.integer_total_students,
                    list_price_yuan=template.list_price_yuan,
                    detail_discount_rate_ppm=self._discount_value(template, discount_values),
                    current_season_recognition_rate_ppm=(template.current_season_recognition_rate_ppm),
                    opening_carryover_revenue_yuan=template.opening_carryover_revenue_yuan,
                )
            )
        revenue = calculate_revenue(
            fiscal_year=self.context.fiscal_year,
            budget_tax_conversion_factor=self.context.budget_tax_conversion_factor,
            lines=revenue_lines,
            composite_targets=list(self.context.composite_discount_targets),
        )
        annual = {
            (item.scope_level, item.track): item.recognized_revenue_yuan
            for item in revenue.aggregates
            if item.period_type == "ANNUAL"
        }
        validation_codes = tuple(
            sorted(
                {
                    *(item.code for item in headcounts.validations),
                    *(item.code for item in class_result.validations),
                    *(item.code for item in revenue.validations),
                }
            )
        )
        return _KernelResult(
            annual_revenue_by_scope=annual,
            validation_codes=validation_codes,
            headcount_signature=tuple(
                sorted(
                    (
                        f"{row.combination.stable_id}:{row.fiscal_season}",
                        row.integer_total_students or 0,
                    )
                    for row in headcounts.rows
                )
            ),
        )

    def _discount_value(
        self,
        template: RevenueLineTemplate,
        discount_values: dict[str, int],
    ) -> int:
        if not discount_values:
            return template.detail_discount_rate_ppm
        rule = self._discount_rule_by_key.get(
            (template.fiscal_season, template.track, template.product, template.grade)
        )
        if rule is None:
            raise ValueError("missing discount decision for revenue template")
        return discount_values[rule.stable_id]


def optimize_budget(
    context: BudgetEvaluationContext,
    *,
    solver_adapter: OptimizationSolverAdapter = OptimizationSolverAdapter.EXACT_ENUMERATION,
    maximum_enumerated_states: int = 100_000,
    maximum_solver_seconds: float = 300,
    maximum_candidate_validations: int = 10_000,
    maximum_group_table_rows: int = 100_000,
    discount_unlocked: bool = False,
) -> OptimizationOutcome:
    """Run the exact stages allowed by the current immutable unlock state."""
    evaluator = BudgetCandidateEvaluator(context)
    adapter = ExactEnumerationAdapter(maximum_states=maximum_enumerated_states)
    baseline_entries = tuple(sorted((item.stable_id, item.current_value) for item in context.entry_decisions))
    baseline_rates = tuple(
        sorted((item.stable_id, item.current_value_ppm) for item in context.rate_decisions)
    )
    baseline = evaluator.evaluate(DecisionVector(entry_values=baseline_entries, rate_values=baseline_rates))
    if baseline.targets_closed:
        baseline = replace(baseline, scenario_types=(ScenarioProfile.ENTRY_STRUCTURE,))
        attempt = SolveAttempt(
            stage=OptimizationStage.ENTRY_ONLY,
            profile=ScenarioProfile.ENTRY_STRUCTURE,
            status=SolveStatus.OPTIMAL,
            candidates=(baseline,),
            evaluated_state_count=1,
        )
        return OptimizationOutcome(
            status=SolveStatus.OPTIMAL,
            terminal_stage=OptimizationStage.ENTRY_ONLY,
            candidates=(baseline,),
            attempts=(attempt,),
            discount_unlock_required=False,
        )
    track_directions = {
        scope.split(":", 1)[1]: ("UP" if variance < 0 else "DOWN")
        for scope, variance in baseline.target_variances_yuan
        if scope.startswith("TRACK:") and abs(variance) > Decimal("10000.000000")
    }

    def entry_space() -> Iterator[DecisionVector]:
        raw = enumerate_fixed_total_entry_structures(
            context.entry_decisions,
            fixed_total=context.fixed_entry_total,
        )
        return (DecisionVector(entry_values=item.entry_values, rate_values=baseline_rates) for item in raw)

    from packages.optimization.cp_sat_rates import CpSatEntryAndRatesSolver

    rate_solver = CpSatEntryAndRatesSolver(
        maximum_time_seconds=maximum_solver_seconds,
        maximum_candidate_validations=maximum_candidate_validations,
        maximum_group_table_rows=maximum_group_table_rows,
    )

    def solver(stage: OptimizationStage, profile: ScenarioProfile) -> SolveAttempt:
        if stage == OptimizationStage.ENTRY_ONLY:
            if solver_adapter in {
                OptimizationSolverAdapter.CP_SAT_ENTRY_ONLY,
                OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
            }:
                from packages.optimization.cp_sat_entry import CpSatEntryOnlySolver

                return CpSatEntryOnlySolver(
                    maximum_time_seconds=maximum_solver_seconds,
                    maximum_candidate_validations=maximum_candidate_validations,
                ).solve(
                    entries=context.entry_decisions,
                    rates=context.rate_decisions,
                    fixed_total=context.fixed_entry_total,
                    evaluator=evaluator.evaluate,
                )
            decisions = entry_space()
        elif stage == OptimizationStage.ENTRY_AND_RATES:
            if solver_adapter == OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES:
                return rate_solver.solve(
                    entries=context.entry_decisions,
                    rates=context.rate_decisions,
                    fixed_total=context.fixed_entry_total,
                    track_directions=track_directions,
                    profile=profile,
                    evaluator=evaluator.evaluate,
                    business_context=context,
                )
            decisions = enumerate_parallel_rate_water_levels(
                entry_space(),
                rates=context.rate_decisions,
                track_directions=track_directions,
            )
        elif stage == OptimizationStage.ENTRY_RATES_AND_DISCOUNT:
            if solver_adapter == OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES:
                from packages.optimization.cp_sat_discount import (
                    CpSatEntryRatesAndDiscountSolver,
                )

                return CpSatEntryRatesAndDiscountSolver(
                    maximum_time_seconds=maximum_solver_seconds,
                    maximum_group_table_rows=maximum_group_table_rows,
                ).solve(
                    context=context,
                    track_directions=track_directions,
                    profile=profile,
                    evaluator=evaluator.evaluate,
                )
            return SolveAttempt(
                stage=stage,
                profile=profile,
                status=SolveStatus.INCOMPLETE,
                proof_code="DISCOUNT_STAGE_SOLVER_NOT_IMPLEMENTED",
            )
        else:
            raise ValueError(f"unsupported optimization stage: {stage}")
        return adapter.solve(
            stage=stage,
            profile=profile,
            decisions=decisions,
            evaluator=evaluator.evaluate,
        )

    return run_staged_optimization(solver, discount_unlocked=discount_unlocked)


@dataclass(frozen=True)
class _KernelResult:
    annual_revenue_by_scope: dict[tuple[str, str | None], Decimal]
    validation_codes: tuple[str, ...]
    headcount_signature: tuple[tuple[str, int], ...]

    @property
    def department_annual_revenue(self) -> Decimal:
        return self.annual_revenue_by_scope[("DEPARTMENT", None)]


def _soft_violation(value: int, initial: int) -> int:
    if initial <= 0:
        return 0
    lower = (initial * 80 + 99) // 100
    upper = initial * 120 // 100
    return max(lower - value, 0) + max(value - upper, 0)
