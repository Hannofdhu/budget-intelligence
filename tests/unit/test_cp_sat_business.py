from dataclasses import replace
from decimal import Decimal

import pytest

from packages.class_size.validation import ClassSizeInput
from packages.headcount.calculation import CombinationInput, RateInput
from packages.optimization.cp_sat_rates import CpSatEntryAndRatesSolver
from packages.optimization.engine import (
    DecisionVector,
    EntryDecision,
    RateDecision,
    ScenarioProfile,
    SolveStatus,
)
from packages.optimization.evaluator import (
    BudgetCandidateEvaluator,
    BudgetEvaluationContext,
    RevenueLineTemplate,
    RevenueTarget,
)


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
