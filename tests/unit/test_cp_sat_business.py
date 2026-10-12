from dataclasses import replace
from decimal import Decimal

import pytest

from packages.class_size.validation import ClassSizeInput
from packages.headcount.calculation import CombinationInput, RateInput
from packages.optimization.cp_sat_business import (
    _calculate_group_kernel,
    _evaluate_group,
    compile_direct_business_problem,
)
from packages.optimization.cp_sat_rates import CpSatEntryAndRatesSolver
from packages.optimization.engine import (
    DecisionVector,
    EntryDecision,
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
)
from packages.revenue.calculation import CompositeDiscountTargetInput


def _direct_context() -> BudgetEvaluationContext:
    combination = CombinationInput(
        "combination",
        "track",
        "product",
        "ENTRY",
        "grade",
        "subject",
        "campus",
        10_000,
    )
    rates: list[RateInput] = []
    decisions: list[RateDecision] = []
    for season in (20272, 20273, 20274):
        for rate_type, baseline in (
            ("RENEWAL_RATE", 500_000 if season == 20272 else 1_000_000),
            ("NEW_STUDENT_SHARE", 200_000 if season == 20272 else 0),
        ):
            rate_id = f"{rate_type}:{season}"
            rates.append(
                RateInput(
                    rate_id,
                    rate_type,  # type: ignore[arg-type]
                    season,
                    "track",
                    "product",
                    "grade",
                    "campus",
                    baseline,
                )
            )
            adjustable = season == 20272
            decisions.append(
                RateDecision(
                    rate_id,
                    "track",
                    rate_type,
                    baseline,
                    baseline,
                    baseline + 200 if adjustable else baseline,
                    locked=not adjustable,
                )
            )
    return BudgetEvaluationContext(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal(1),
        fixed_entry_total=10_000,
        combinations=(combination,),
        rates=tuple(rates),
        plans=(),
        entry_decisions=(EntryDecision("combination", 10_000, 10_000, 10_000),),
        rate_decisions=tuple(decisions),
        class_size_parameters=(ClassSizeInput("class-size", "track", "product", 1),),
        revenue_line_templates=tuple(
            RevenueLineTemplate(
                "combination",
                season,
                "track",
                "product",
                "ENTRY",
                "grade",
                "subject",
                "campus",
                Decimal(100_000),
                1_000_000,
                1_000_000,
                Decimal(0),
            )
            for season in (20271, 20272, 20273, 20274)
        ),
        composite_discount_targets=(),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(2_875_900_000)),
            RevenueTarget("track", "TRACK", "track", Decimal(2_875_900_000)),
        ),
    )


@pytest.mark.parametrize(
    ("profile", "renewal", "new_share"),
    (
        (ScenarioProfile.BALANCED, 500_100, 200_200),
        (ScenarioProfile.RETENTION_WEIGHTED, 500_200, 200_100),
        (ScenarioProfile.ACQUISITION_WEIGHTED, 500_100, 200_200),
    ),
)
def test_direct_business_model_optimizes_exact_revenue_contribution_profiles(
    profile: ScenarioProfile,
    renewal: int,
    new_share: int,
) -> None:
    context = _direct_context()
    evaluator = BudgetCandidateEvaluator(context)
    result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=20,
    ).solve(
        entries=context.entry_decisions,
        rates=context.rate_decisions,
        fixed_total=context.fixed_entry_total,
        track_directions={"track": "UP"},
        profile=profile,
        evaluator=evaluator.evaluate,
        business_context=context,
    )

    assert result.status == SolveStatus.OPTIMAL
    values = dict(result.candidates[0].decision.rate_values)
    assert values["RENEWAL_RATE:20272"] == renewal
    assert values["NEW_STUDENT_SHARE:20272"] == new_share
    assert result.candidates[0].targets_closed is True
    assert result.evaluated_state_count == 1
    assert result.proof_summary is not None
    assert result.proof_summary["compiled_group_row_count"] == 9


def test_direct_business_group_table_limit_fails_closed() -> None:
    context = _direct_context()
    result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=8,
        maximum_group_table_rows=8,
    ).solve(
        entries=context.entry_decisions,
        rates=context.rate_decisions,
        fixed_total=context.fixed_entry_total,
        track_directions={"track": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=BudgetCandidateEvaluator(context).evaluate,
        business_context=context,
    )

    assert result.status == SolveStatus.INCOMPLETE
    assert result.proof_code == "CP_SAT_GROUP_TABLE_LIMIT_EXCEEDED"
    assert result.candidates == ()


def test_direct_business_table_keeps_largest_remainder_at_track_campus_group_level() -> None:
    base = _direct_context()
    combinations = (
        replace(base.combinations[0], stable_id="a", subject="subject-a", entry_initial_value=5_001),
        replace(base.combinations[0], stable_id="b", subject="subject-b", entry_initial_value=4_999),
    )
    context_without_target = replace(
        base,
        combinations=combinations,
        entry_decisions=(
            EntryDecision("a", 5_001, 5_001, 5_001),
            EntryDecision("b", 4_999, 4_999, 4_999),
        ),
        rate_decisions=tuple(
            replace(item, upper_bound_ppm=item.current_value_ppm, locked=True) for item in base.rate_decisions
        ),
        revenue_line_templates=tuple(
            replace(
                template,
                combination_stable_key=combination.stable_id,
                subject=combination.subject,
            )
            for combination in combinations
            for template in base.revenue_line_templates
        ),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(0)),
            RevenueTarget("track", "TRACK", "track", Decimal(0)),
        ),
    )
    baseline_rates = tuple(
        sorted((item.stable_id, item.current_value_ppm) for item in context_without_target.rate_decisions)
    )
    baseline = BudgetCandidateEvaluator(context_without_target).evaluate(
        DecisionVector(
            entry_values=(("a", 5_001), ("b", 4_999)),
            rate_values=baseline_rates,
        )
    )
    context = replace(
        context_without_target,
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, baseline.revenue_r11_yuan),
            RevenueTarget("track", "TRACK", "track", baseline.revenue_r11_yuan),
        ),
    )
    result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=10,
    ).solve(
        entries=context.entry_decisions,
        rates=context.rate_decisions,
        fixed_total=context.fixed_entry_total,
        track_directions={},
        profile=ScenarioProfile.BALANCED,
        evaluator=BudgetCandidateEvaluator(context).evaluate,
        business_context=context,
    )

    assert result.status == SolveStatus.OPTIMAL
    headcounts = dict(result.candidates[0].headcount_signature)
    assert headcounts["a:20272"] == 3_126
    assert headcounts["b:20272"] == 3_124


def _repair_context() -> BudgetEvaluationContext:
    # Both individual attribution levers remain below 6253. Only the final
    # combined decision satisfies the unchanged minimum-class constraint.
    return replace(
        _direct_context(),
        class_size_parameters=(ClassSizeInput("class-size", "track", "product", 6253),),
    )


def test_counterfactual_class_gaps_do_not_reject_a_fully_legal_final_candidate() -> None:
    context = _repair_context()
    evaluator = BudgetCandidateEvaluator(context)
    rates = {item.stable_id: item.current_value_ppm for item in context.rate_decisions}
    baseline = evaluator.evaluate(DecisionVector((("combination", 10000),), tuple(sorted(rates.items()))))
    assert baseline.validation_codes == ("BELOW_MINIMUM_CLASS_SIZE",)
    assert baseline.hard_constraints_satisfied is False
    rates.update({"RENEWAL_RATE:20272": 500100, "NEW_STUDENT_SHARE:20272": 200200})
    repaired = evaluator.evaluate(DecisionVector((("combination", 10000),), tuple(sorted(rates.items()))))
    assert repaired.hard_constraints_satisfied is True
    assert repaired.targets_closed is True
    assert repaired.validation_codes == ()
    renewal, new_share = symmetric_rate_contributions(
        r00=repaired.revenue_r00_yuan,
        r10=repaired.revenue_r10_yuan,
        r01=repaired.revenue_r01_yuan,
        r11=repaired.revenue_r11_yuan,
    )
    assert renewal + new_share == repaired.revenue_r11_yuan - repaired.revenue_r00_yuan
    assert all(value >= 6253 for _, value in repaired.headcount_signature)

    direct = CpSatEntryAndRatesSolver(maximum_time_seconds=10).solve(
        entries=context.entry_decisions,
        rates=context.rate_decisions,
        fixed_total=context.fixed_entry_total,
        track_directions={"track": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=evaluator.evaluate,
        business_context=context,
    )
    assert direct.status == SolveStatus.OPTIMAL
    assert direct.candidates[0].result_hash == repaired.result_hash


def test_uncomputable_counterfactuals_still_block_evaluator_and_direct_group() -> None:
    context = _repair_context()
    baseline_rates = {item.rate_id: item.value_ppm for item in context.rates}
    selected = {**baseline_rates, "RENEWAL_RATE:20272": 500100, "NEW_STUDENT_SHARE:20272": 200200}
    failure = "UPSTREAM_HEADCOUNT_UNAVAILABLE"

    class CounterfactualFailureEvaluator(BudgetCandidateEvaluator):
        def _calculate(self, entry_values, rate_values, discount_values):
            result = super()._calculate(entry_values, rate_values, discount_values)
            if rate_values == baseline_rates:
                return replace(result, validation_codes=(*result.validation_codes, failure))
            return result

    result = CounterfactualFailureEvaluator(context).evaluate(
        DecisionVector((("combination", 10000),), tuple(sorted(selected.items())))
    )
    assert failure in result.validation_codes
    assert result.hard_constraints_satisfied is False

    def kernel_lookup(entries, rates):
        values = dict(rates)
        kernel = _calculate_group_kernel(
            context, combinations=context.combinations, entry_values=dict(entries), rate_values=values
        )
        return (
            replace(kernel, validation_codes=(*kernel.validation_codes, failure))
            if values == baseline_rates
            else kernel
        )

    assert (
        _evaluate_group(
            context,
            combinations=context.combinations,
            entry_values={"combination": 10000},
            rate_values=selected,
            kernel_lookup=kernel_lookup,
        )
        is None
    )


def test_unavailable_kernel_outputs_are_not_treated_as_zero_for_attribution() -> None:
    original = _direct_context()
    removed_id = "NEW_STUDENT_SHARE:20272"
    context = replace(
        original,
        rates=tuple(item for item in original.rates if item.rate_id != removed_id),
        rate_decisions=tuple(item for item in original.rate_decisions if item.stable_id != removed_id),
    )
    result = BudgetCandidateEvaluator(context).evaluate(
        DecisionVector(
            (("combination", 10000),),
            tuple((item.stable_id, item.current_value_ppm) for item in context.rate_decisions),
        )
    )
    assert "KERNEL_OUTPUT_UNAVAILABLE" in result.validation_codes
    assert "NEW_STUDENT_SHARE_MISSING" in result.validation_codes
    assert result.hard_constraints_satisfied is False


def test_composite_discount_gate_applies_to_final_candidate_not_counterfactuals() -> None:
    context = replace(
        _direct_context(),
        composite_discount_targets=(
            CompositeDiscountTargetInput("target", "DEPARTMENT", None, "ANNUAL", None, 1_000_000),
        ),
    )
    compiled = compile_direct_business_problem(
        context,
        track_directions={"track": "UP"},
        profile=ScenarioProfile.BALANCED,
        maximum_group_table_rows=100,
    )
    composite_ids = [
        item.stable_id for item in compiled.problem.constraints if item.stable_id.startswith("composite-")
    ]
    assert len(composite_ids) == 3
    assert all(identifier.endswith(":3") for identifier in composite_ids)
    illegal = replace(
        context,
        revenue_line_templates=tuple(
            replace(item, detail_discount_rate_ppm=900000) for item in context.revenue_line_templates
        ),
    )
    result = BudgetCandidateEvaluator(illegal).evaluate(
        DecisionVector(
            (("combination", 10000),),
            tuple((item.stable_id, item.current_value_ppm) for item in illegal.rate_decisions),
        )
    )
    assert result.hard_constraints_satisfied is False
    assert "COMPOSITE_DISCOUNT_OUT_OF_TOLERANCE" in result.validation_codes
