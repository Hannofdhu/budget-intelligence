from dataclasses import replace
from decimal import Decimal

from packages.class_size.validation import ClassSizeInput
from packages.headcount.calculation import CombinationInput, RateInput
from packages.optimization.cp_sat_discount import CpSatEntryRatesAndDiscountSolver
from packages.optimization.engine import (
    DiscountDecision,
    EntryDecision,
    OptimizationSolverAdapter,
    OptimizationStage,
    RateDecision,
    ScenarioProfile,
    SolveStatus,
)
from packages.optimization.evaluator import (
    BudgetCandidateEvaluator,
    BudgetEvaluationContext,
    RevenueLineTemplate,
    RevenueTarget,
    optimize_budget,
)
from packages.revenue.calculation import CompositeDiscountTargetInput


def _discount_context() -> BudgetEvaluationContext:
    combination = CombinationInput(
        "combination",
        "track",
        "product",
        "ENTRY",
        "grade",
        "subject",
        "campus",
        10,
    )
    rates: list[RateInput] = []
    rate_decisions: list[RateDecision] = []
    for season in (20272, 20273, 20274):
        for rate_type, value in (("RENEWAL_RATE", 1_000_000), ("NEW_STUDENT_SHARE", 0)):
            stable_id = f"{rate_type}:{season}"
            rates.append(
                RateInput(
                    stable_id,
                    rate_type,  # type: ignore[arg-type]
                    season,
                    "track",
                    "product",
                    "grade",
                    "campus",
                    value,
                )
            )
            rate_decisions.append(
                RateDecision(
                    stable_id,
                    "track",
                    rate_type,
                    value,
                    value,
                    value,
                    locked=True,
                )
            )
    seasons = (20271, 20272, 20273, 20274)
    discount_decisions = tuple(
        DiscountDecision(
            f"discount:{season}",
            season,
            "track",
            "product",
            "grade",
            500_000,
        )
        for season in seasons
    )
    composite_targets = tuple(
        CompositeDiscountTargetInput(
            f"department:{season}",
            "DEPARTMENT",
            None,
            "SEASON",
            season,
            600_000,
        )
        for season in seasons
    ) + (
        CompositeDiscountTargetInput(
            "department:annual",
            "DEPARTMENT",
            None,
            "ANNUAL",
            None,
            600_000,
        ),
        CompositeDiscountTargetInput(
            "track:annual",
            "TRACK",
            "track",
            "ANNUAL",
            None,
            600_000,
        ),
    )
    return BudgetEvaluationContext(
        fiscal_year=2027,
        budget_tax_conversion_factor=Decimal(1),
        fixed_entry_total=10,
        combinations=(combination,),
        rates=tuple(rates),
        plans=(),
        entry_decisions=(EntryDecision("combination", 10, 10, 10),),
        rate_decisions=tuple(rate_decisions),
        class_size_parameters=(ClassSizeInput("class", "track", "product", 1),),
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
                500_000,
                1_000_000,
                Decimal(0),
            )
            for season in seasons
        ),
        composite_discount_targets=composite_targets,
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(2_400_000)),
            RevenueTarget("track", "TRACK", "track", Decimal(2_400_000)),
        ),
        discount_decisions=discount_decisions,
    )


def test_discount_stage_uses_exact_step_and_recalculates_formal_kernels() -> None:
    context = _discount_context()
    result = CpSatEntryRatesAndDiscountSolver(maximum_time_seconds=10).solve(
        context=context,
        track_directions={"track": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=BudgetCandidateEvaluator(context).evaluate,
    )

    assert result.status == SolveStatus.OPTIMAL
    assert set(dict(result.candidates[0].decision.discount_values).values()) == {600_000}
    assert result.candidates[0].targets_closed is True
    assert result.candidates[0].discount_adjustments_ppm == (100_000,) * 4
    assert result.evaluated_state_count == 1


def test_confirmed_unlock_allows_the_staged_optimizer_to_reach_discount() -> None:
    context = _discount_context()
    outcome = optimize_budget(
        context,
        solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
        maximum_solver_seconds=10,
        discount_unlocked=True,
    )

    assert outcome.status == SolveStatus.OPTIMAL
    assert outcome.terminal_stage == OptimizationStage.ENTRY_RATES_AND_DISCOUNT
    assert outcome.discount_unlock_required is False
    assert set(dict(outcome.candidates[0].decision.discount_values).values()) == {600_000}


def test_discount_stage_proves_infeasible_without_expanding_locked_values() -> None:
    context = _discount_context()
    locked = replace(
        context,
        discount_decisions=tuple(replace(item, locked=True) for item in context.discount_decisions),
    )
    result = CpSatEntryRatesAndDiscountSolver(maximum_time_seconds=10).solve(
        context=locked,
        track_directions={"track": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=BudgetCandidateEvaluator(locked).evaluate,
    )

    assert result.status == SolveStatus.INFEASIBLE
    assert result.proof_code == "CP_SAT_DIRECT_DISCOUNT_MODEL_INFEASIBLE"


def test_discount_stage_rejects_bounds_outside_zero_to_one_hundred_percent() -> None:
    context = _discount_context()
    invalid = replace(
        context,
        discount_decisions=(
            replace(context.discount_decisions[0], upper_bound_ppm=1_001_000),
            *context.discount_decisions[1:],
        ),
    )

    result = CpSatEntryRatesAndDiscountSolver(maximum_time_seconds=10).solve(
        context=invalid,
        track_directions={"track": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=BudgetCandidateEvaluator(invalid).evaluate,
    )

    assert result.status == SolveStatus.FAILED
    assert result.proof_code == "CP_SAT_DISCOUNT_COMPILATION_FAILED"


def test_discount_stage_reduces_discount_for_an_over_target_track() -> None:
    context = _discount_context()
    lower_target = replace(
        context,
        composite_discount_targets=tuple(
            replace(item, target_rate_ppm=400_000) for item in context.composite_discount_targets
        ),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(1_600_000)),
            RevenueTarget("track", "TRACK", "track", Decimal(1_600_000)),
        ),
    )
    result = CpSatEntryRatesAndDiscountSolver(maximum_time_seconds=10).solve(
        context=lower_target,
        track_directions={"track": "DOWN"},
        profile=ScenarioProfile.BALANCED,
        evaluator=BudgetCandidateEvaluator(lower_target).evaluate,
    )

    assert result.status == SolveStatus.OPTIMAL
    assert set(dict(result.candidates[0].decision.discount_values).values()) == {400_000}


def test_discount_can_move_when_only_composite_discount_needs_closure() -> None:
    context = _discount_context()
    composite_only = replace(
        context,
        revenue_line_templates=tuple(
            replace(item, list_price_yuan=Decimal(100)) for item in context.revenue_line_templates
        ),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(2_400)),
            RevenueTarget("track", "TRACK", "track", Decimal(2_400)),
        ),
    )

    outcome = optimize_budget(
        composite_only,
        solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
        maximum_solver_seconds=10,
        discount_unlocked=True,
    )

    assert outcome.status == SolveStatus.OPTIMAL
    assert outcome.terminal_stage == OptimizationStage.ENTRY_RATES_AND_DISCOUNT
    assert set(dict(outcome.candidates[0].decision.discount_values).values()) == {600_000}
