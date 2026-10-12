from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from packages.class_size.validation import ClassSizeInput
from packages.headcount.calculation import (
    CombinationInput,
    HeadcountCalculation,
    ManualPlanInput,
    RateInput,
    RateType,
    calculate_headcounts,
)
from packages.optimization import evaluator as evaluator_module
from packages.optimization.cp_sat_rates import CpSatEntryAndRatesSolver
from packages.optimization.engine import (
    DecisionVector,
    DiscountDecision,
    EntryDecision,
    OptimizationSolverAdapter,
    OptimizationStage,
    RateDecision,
    ScenarioProfile,
    SolveStatus,
    symmetric_rate_contributions,
)
from packages.optimization.evaluator import (
    BudgetCandidateEvaluator,
    BudgetEvaluationContext,
    RevenueLineTemplate,
    RevenueTarget,
    attribution_blocking_codes,
    optimize_budget,
)
from packages.revenue.calculation import CompositeDiscountTargetInput, RevenueCalculation, calculate_revenue


def _without_final_season[**KernelParameters, KernelOutput: (HeadcountCalculation, RevenueCalculation)](
    kernel: Callable[KernelParameters, KernelOutput],
) -> Callable[KernelParameters, KernelOutput]:
    def incomplete(*args: KernelParameters.args, **kwargs: KernelParameters.kwargs) -> KernelOutput:
        result = kernel(*args, **kwargs)
        return replace(result, rows=result.rows[:-1])

    return incomplete


def test_candidate_is_recalculated_through_headcount_class_and_revenue_kernels() -> None:
    combination = CombinationInput(
        stable_id="combination-a",
        track="track-a",
        product="product-a",
        entry_attribute="ENTRY",
        grade="grade-a",
        subject="subject-a",
        campus="campus-a",
        entry_initial_value=10,
    )
    rates: list[RateInput] = []
    rate_rules: list[RateDecision] = []
    for season in (20272, 20273, 20274):
        for rate_type, value in (("RENEWAL_RATE", 1_000_000), ("NEW_STUDENT_SHARE", 0)):
            rate_id = f"{rate_type}:{season}"
            rates.append(
                RateInput(
                    rate_id=rate_id,
                    rate_type=rate_type,  # type: ignore[arg-type]
                    fiscal_season=season,
                    track="track-a",
                    product="product-a",
                    grade="grade-a",
                    campus="campus-a",
                    value_ppm=value,
                )
            )
            rate_rules.append(
                RateDecision(
                    stable_id=rate_id,
                    track="track-a",
                    rate_type=rate_type,
                    current_value_ppm=value,
                    lower_bound_ppm=value,
                    upper_bound_ppm=value,
                    locked=True,
                )
            )
    context = BudgetEvaluationContext(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal(1),
        fixed_entry_total=10,
        combinations=(combination,),
        rates=tuple(rates),
        plans=(),
        entry_decisions=(EntryDecision("combination-a", 10, 0, 20),),
        rate_decisions=tuple(rate_rules),
        class_size_parameters=(ClassSizeInput("class-a", "track-a", "product-a", 1),),
        revenue_line_templates=tuple(
            RevenueLineTemplate(
                combination_stable_key="combination-a",
                fiscal_season=season,
                track="track-a",
                product="product-a",
                entry_attribute="ENTRY",
                grade="grade-a",
                subject="subject-a",
                campus="campus-a",
                list_price_yuan=Decimal(100),
                detail_discount_rate_ppm=1_000_000,
                current_season_recognition_rate_ppm=1_000_000,
                opening_carryover_revenue_yuan=Decimal(0),
            )
            for season in (20271, 20272, 20273, 20274)
        ),
        composite_discount_targets=(),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(4000)),
            RevenueTarget("track", "TRACK", "track-a", Decimal(4000)),
        ),
    )
    evaluator = BudgetCandidateEvaluator(context)
    result = evaluator.evaluate(
        DecisionVector(
            entry_values=(("combination-a", 10),),
            rate_values=tuple((item.stable_id, item.current_value_ppm) for item in rate_rules),
        )
    )
    assert result.targets_closed is True
    assert result.hard_constraints_satisfied is True
    assert result.revenue_r00_yuan == Decimal("4000.000000")
    assert result.revenue_r11_yuan == Decimal("4000.000000")
    assert len(result.headcount_signature) == 4
    outcome = optimize_budget(context, maximum_enumerated_states=100)
    assert outcome.status == SolveStatus.OPTIMAL
    assert outcome.terminal_stage == OptimizationStage.ENTRY_ONLY
    assert outcome.candidates[0].decision.entry_values == (("combination-a", 10),)

    direct_result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=100,
    ).solve(
        entries=context.entry_decisions,
        rates=context.rate_decisions,
        fixed_total=context.fixed_entry_total,
        track_directions={},
        profile=ScenarioProfile.BALANCED,
        evaluator=evaluator.evaluate,
        business_context=context,
    )
    assert direct_result.status == SolveStatus.OPTIMAL
    assert direct_result.evaluated_state_count == 1
    assert direct_result.candidates[0].result_hash == result.result_hash
    assert direct_result.proof_summary is not None
    assert direct_result.proof_summary["model_version"] == "entry-and-rates-direct-business-v1"

    composite_context = replace(
        context,
        composite_discount_targets=(
            CompositeDiscountTargetInput(
                "department-annual-discount",
                "DEPARTMENT",
                None,
                "ANNUAL",
                None,
                1_000_000,
            ),
        ),
    )
    composite_evaluator = BudgetCandidateEvaluator(composite_context)
    composite_result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=100,
    ).solve(
        entries=composite_context.entry_decisions,
        rates=composite_context.rate_decisions,
        fixed_total=composite_context.fixed_entry_total,
        track_directions={},
        profile=ScenarioProfile.BALANCED,
        evaluator=composite_evaluator.evaluate,
        business_context=composite_context,
    )
    assert composite_result.status == SolveStatus.OPTIMAL
    assert composite_result.candidates[0].hard_constraints_satisfied is True

    limited_direct_result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=100,
        maximum_group_table_rows=1,
    ).solve(
        entries=context.entry_decisions,
        rates=context.rate_decisions,
        fixed_total=context.fixed_entry_total,
        track_directions={},
        profile=ScenarioProfile.BALANCED,
        evaluator=evaluator.evaluate,
        business_context=context,
    )
    assert limited_direct_result.status == SolveStatus.INCOMPLETE
    assert limited_direct_result.proof_code == "CP_SAT_GROUP_TABLE_LIMIT_EXCEEDED"
    assert limited_direct_result.candidates == ()

    open_context = replace(
        context,
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(20_000)),
            RevenueTarget("track", "TRACK", "track-a", Decimal(20_000)),
        ),
    )
    cp_sat_outcome = optimize_budget(
        open_context,
        solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_ONLY,
        maximum_solver_seconds=5,
        maximum_candidate_validations=1,
    )
    assert cp_sat_outcome.status == SolveStatus.INCOMPLETE
    assert cp_sat_outcome.reason_code == "CP_SAT_CANDIDATE_VALIDATION_LIMIT_EXCEEDED"

    first_new_share = next(
        item.stable_id for item in context.rate_decisions if item.rate_type == "NEW_STUDENT_SHARE"
    )
    rate_context = replace(
        open_context,
        rate_decisions=tuple(
            replace(item, locked=False, upper_bound_ppm=200) if item.stable_id == first_new_share else item
            for item in context.rate_decisions
        ),
        discount_decisions=tuple(
            DiscountDecision(
                f"discount:{season}",
                season,
                "track-a",
                "product-a",
                "grade-a",
                1_000_000,
            )
            for season in (20271, 20272, 20273, 20274)
        ),
    )
    cp_sat_rate_outcome = optimize_budget(
        rate_context,
        solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
        maximum_solver_seconds=10,
        maximum_candidate_validations=100,
    )
    assert cp_sat_rate_outcome.status == SolveStatus.INFEASIBLE
    assert cp_sat_rate_outcome.terminal_stage == OptimizationStage.ENTRY_AND_RATES
    assert cp_sat_rate_outcome.discount_unlock_required is True
    assert all(
        item.proof_code == "CP_SAT_DIRECT_BUSINESS_MODEL_INFEASIBLE"
        for item in cp_sat_rate_outcome.attempts[1:]
    )

    unlocked_outcome = optimize_budget(
        rate_context,
        solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
        maximum_solver_seconds=10,
        maximum_candidate_validations=100,
        discount_unlocked=True,
    )
    assert unlocked_outcome.status == SolveStatus.INFEASIBLE
    assert unlocked_outcome.terminal_stage == OptimizationStage.ENTRY_RATES_AND_DISCOUNT
    assert unlocked_outcome.reason_code == "ALL_CONFIRMED_BOUNDARIES_EXHAUSTED"


def test_candidate_rejects_changed_fixed_entry_total_before_recalculation() -> None:
    context = BudgetEvaluationContext(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal(1),
        fixed_entry_total=1,
        combinations=(),
        rates=(),
        plans=(),
        entry_decisions=(EntryDecision("a", 1, 0, 2),),
        rate_decisions=(),
        class_size_parameters=(),
        revenue_line_templates=(),
        composite_discount_targets=(),
        revenue_targets=(),
    )
    result = BudgetCandidateEvaluator(context).evaluate(
        DecisionVector(entry_values=(("a", 2),), rate_values=())
    )
    assert result.hard_constraints_satisfied is False
    assert result.validation_codes == ("ENTRY_TOTAL_CHANGED",)


def _class_repair_context() -> BudgetEvaluationContext:
    rates: list[RateInput] = []
    for season in (20272, 20273, 20274):
        rate_values: tuple[tuple[RateType, int], ...] = (
            ("RENEWAL_RATE", 800_000 if season == 20272 else 1_000_000),
            ("NEW_STUDENT_SHARE", 0),
        )
        rates.extend(
            RateInput(f"{kind}:{season}", kind, season, "track", "product", "grade", "campus", value)
            for kind, value in rate_values
        )
    return BudgetEvaluationContext(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal(1),
        fixed_entry_total=100,
        combinations=(CombinationInput("a", "track", "product", "ENTRY", "grade", "subject", "campus", 100),),
        rates=tuple(rates),
        plans=(),
        entry_decisions=(EntryDecision("a", 100, 100, 100),),
        rate_decisions=tuple(
            RateDecision(
                rate.rate_id,
                rate.track,
                rate.rate_type,
                rate.value_ppm,
                rate.value_ppm,
                rate.value_ppm + (10_000 if rate.rate_id == "RENEWAL_RATE:20272" else 0),
                locked=rate.rate_id != "RENEWAL_RATE:20272",
            )
            for rate in rates
        ),
        class_size_parameters=(ClassSizeInput("minimum", "track", "product", 81),),
        revenue_line_templates=tuple(
            RevenueLineTemplate(
                "a",
                season,
                "track",
                "product",
                "ENTRY",
                "grade",
                "subject",
                "campus",
                Decimal(100),
                1_000_000,
                1_000_000,
                Decimal(0),
            )
            for season in (20271, 20272, 20273, 20274)
        ),
        composite_discount_targets=(),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(34_300)),
            RevenueTarget("track", "TRACK", "track", Decimal(34_300)),
        ),
    )


def test_evaluator_allows_repair_from_infeasible_attribution_baseline_without_relaxing_final_class() -> None:
    context = _class_repair_context()
    evaluator = BudgetCandidateEvaluator(context)
    baseline_rates = {rate.rate_id: rate.value_ppm for rate in context.rates}
    baseline = evaluator.evaluate(DecisionVector((("a", 100),), tuple(sorted(baseline_rates.items()))))
    assert baseline.validation_codes == ("BELOW_MINIMUM_CLASS_SIZE",)
    assert not baseline.hard_constraints_satisfied
    selected = {**baseline_rates, "RENEWAL_RATE:20272": 810_000}
    repaired = evaluator.evaluate(DecisionVector((("a", 100),), tuple(sorted(selected.items()))))
    assert repaired.hard_constraints_satisfied and repaired.targets_closed
    assert repaired.revenue_r00_yuan == Decimal(34_000)
    assert repaired.revenue_r11_yuan == Decimal(34_300)
    assert dict(repaired.headcount_signature) == {
        "a:20271": 100,
        "a:20272": 81,
        "a:20273": 81,
        "a:20274": 81,
    }
    renewal, acquisition = symmetric_rate_contributions(
        r00=repaired.revenue_r00_yuan,
        r10=repaired.revenue_r10_yuan,
        r01=repaired.revenue_r01_yuan,
        r11=repaired.revenue_r11_yuan,
    )
    assert renewal + acquisition == repaired.revenue_r11_yuan - repaired.revenue_r00_yuan


def test_attribution_filter_retains_missing_inputs_unavailable_outputs_and_unknown_errors() -> None:
    assert attribution_blocking_codes(
        (
            "BELOW_MINIMUM_CLASS_SIZE",
            "ENABLED_COMBINATION_ALL_SEASONS_ZERO",
            "COMPOSITE_DISCOUNT_OUT_OF_TOLERANCE",
            "COMPOSITE_DISCOUNT_DENOMINATOR_ZERO",
            "CLASS_SIZE_PARAMETER_MISSING",
            "RENEWAL_RATE_MISSING",
            "KERNEL_OUTPUT_UNAVAILABLE",
            "UNRECOGNIZED_KERNEL_FAILURE",
        )
    ) == (
        "CLASS_SIZE_PARAMETER_MISSING",
        "RENEWAL_RATE_MISSING",
        "KERNEL_OUTPUT_UNAVAILABLE",
        "UNRECOGNIZED_KERNEL_FAILURE",
    )


def test_evaluator_still_rejects_a_final_enabled_combination_with_four_zero_seasons() -> None:
    base = _class_repair_context()
    context = replace(
        base,
        fixed_entry_total=0,
        combinations=(replace(base.combinations[0], entry_initial_value=0),),
        entry_decisions=(EntryDecision("a", 0, 0, 0),),
        plans=tuple(
            ManualPlanInput(f"plan:{season}", "a", season, 0, True) for season in (20271, 20272, 20273, 20274)
        ),
    )
    result = BudgetCandidateEvaluator(context).evaluate(
        DecisionVector((("a", 0),), tuple((row.rate_id, row.value_ppm) for row in context.rates))
    )
    assert not result.hard_constraints_satisfied
    assert "ENABLED_COMBINATION_ALL_SEASONS_ZERO" in result.validation_codes
    assert "KERNEL_OUTPUT_UNAVAILABLE" not in result.validation_codes


@pytest.mark.parametrize("kernel", ["calculate_headcounts", "calculate_revenue"])
def test_evaluator_rejects_incomplete_kernel_row_coverage(
    monkeypatch: pytest.MonkeyPatch, kernel: str
) -> None:
    if kernel == "calculate_headcounts":
        monkeypatch.setattr(evaluator_module, kernel, _without_final_season(calculate_headcounts))
    else:
        assert kernel == "calculate_revenue"
        monkeypatch.setattr(evaluator_module, kernel, _without_final_season(calculate_revenue))
    context = _class_repair_context()
    rates = {row.rate_id: row.value_ppm for row in context.rates}
    rates["RENEWAL_RATE:20272"] = 810_000
    result = BudgetCandidateEvaluator(context).evaluate(
        DecisionVector((("a", 100),), tuple(sorted(rates.items())))
    )
    assert not result.hard_constraints_satisfied
    assert "KERNEL_OUTPUT_UNAVAILABLE" in result.validation_codes
    if kernel == "calculate_headcounts":
        assert "INCOMPLETE_REVENUE_SEASONS" in result.validation_codes
