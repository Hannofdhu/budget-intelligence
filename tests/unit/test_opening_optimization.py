from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from packages.class_size.validation import ClassSizeInput
from packages.headcount.calculation import CombinationInput, HeadcountInvariantError, RateInput, RateType
from packages.optimization.engine import (
    DecisionVector,
    EntryDecision,
    OptimizationSolverAdapter,
    RateDecision,
    SolveStatus,
)
from packages.optimization.evaluator import (
    BudgetCandidateEvaluator,
    BudgetEvaluationContext,
    OpeningDecisionInput,
    RevenueLineTemplate,
    RevenueTarget,
    candidate_opening_values,
    optimize_budget,
)


def context() -> BudgetEvaluationContext:
    combo = CombinationInput("a", "track", "product", "NON_ENTRY", "八年级", "数学", "campus")
    rates = [RateInput("opening", "RENEWAL_RATE", 20281, "track", "product", "七年级", "campus", 800000)]
    kinds: list[tuple[RateType, int]] = [("RENEWAL_RATE", 1000000), ("NEW_STUDENT_SHARE", 0)]
    rates += [
        RateInput(f"{season}-{kind}", kind, season, "track", "product", "八年级", "campus", value)
        for season in range(20281, 20285)
        for kind, value in kinds
    ]
    decisions = tuple(
        RateDecision(
            row.rate_id,
            row.track,
            row.rate_type,
            row.value_ppm,
            row.value_ppm,
            801000 if row.rate_id == "opening" else row.value_ppm,
            locked=row.rate_id != "opening",
        )
        for row in rates
    )
    return BudgetEvaluationContext(
        fiscal_year=2028,
        budget_tax_conversion_factor=Decimal(1),
        fixed_entry_total=0,
        combinations=(combo,),
        rates=tuple(rates),
        plans=(),
        entry_decisions=(EntryDecision("a", 0, 0, 0),),
        rate_decisions=decisions,
        class_size_parameters=(ClassSizeInput("class", "track", "product", 1),),
        revenue_line_templates=tuple(
            RevenueLineTemplate(
                "a",
                s,
                "track",
                "product",
                "NON_ENTRY",
                "八年级",
                "数学",
                "campus",
                Decimal(10000),
                1000000,
                1000000,
                Decimal(0),
            )
            for s in range(20281, 20285)
        ),
        composite_discount_targets=(),
        revenue_targets=(
            RevenueTarget("target", "DEPARTMENT", None, Decimal(32040000)),
            RevenueTarget("track-target", "TRACK", "track", Decimal(32040000)),
        ),
        opening_inputs=(OpeningDecisionInput("a", 1000, "opening"),),
    )


def test_candidate_opening_uses_candidate_rate_not_frozen_count() -> None:
    ctx = context()
    values = {row.rate_id: row.value_ppm for row in ctx.rates}
    assert candidate_opening_values(ctx, values) == {"a": Fraction(800)}
    values["opening"] = 801000
    assert candidate_opening_values(ctx, values) == {"a": Fraction(801)}
    evaluation = BudgetCandidateEvaluator(ctx).evaluate(
        DecisionVector(entry_values=(("a", 0),), rate_values=tuple(sorted(values.items())))
    )
    assert evaluation.targets_closed
    result = optimize_budget(ctx)
    assert result.status == SolveStatus.OPTIMAL
    assert dict(result.candidates[0].decision.rate_values)["opening"] > 800000


def test_lock_and_boundaries_still_apply_to_opening_renewal() -> None:
    ctx = context()
    ctx = replace(ctx, rate_decisions=tuple(replace(row, locked=True) for row in ctx.rate_decisions))
    values = {row.rate_id: row.value_ppm for row in ctx.rates}
    values["opening"] = 802000
    result = BudgetCandidateEvaluator(ctx).evaluate(
        DecisionVector(entry_values=(("a", 0),), rate_values=tuple(sorted(values.items())))
    )
    assert not result.targets_closed


def test_cp_sat_matches_small_exhaustive_opening_problem() -> None:
    exact = optimize_budget(context())
    cp = optimize_budget(context(), solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES)
    assert cp.status == exact.status == SolveStatus.OPTIMAL
    assert {item.headcount_signature for item in cp.candidates} == {
        item.headcount_signature for item in exact.candidates
    }


def test_missing_opening_rate_never_falls_back_to_zero() -> None:
    with pytest.raises(HeadcountInvariantError):
        candidate_opening_values(context(), {})


def test_two_campus_rounding_groups_match_exhaustive_solver() -> None:
    old = context()
    ctx = replace(
        old,
        combinations=old.combinations + (replace(old.combinations[0], stable_id="b", campus="second"),),
        rates=old.rates
        + tuple(replace(row, rate_id=f"second-{row.rate_id}", campus="second") for row in old.rates),
        rate_decisions=old.rate_decisions
        + tuple(replace(row, stable_id=f"second-{row.stable_id}") for row in old.rate_decisions),
        entry_decisions=old.entry_decisions + (EntryDecision("b", 0, 0, 0),),
        revenue_line_templates=old.revenue_line_templates
        + tuple(
            replace(row, combination_stable_key="b", campus="second") for row in old.revenue_line_templates
        ),
        revenue_targets=tuple(
            replace(row, target_revenue_yuan=row.target_revenue_yuan * 2) for row in old.revenue_targets
        ),
        opening_inputs=(
            OpeningDecisionInput("a", 1000, "opening"),
            OpeningDecisionInput("b", 1000, "second-opening"),
        ),
    )
    exact = optimize_budget(ctx)
    cp = optimize_budget(ctx, solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES)
    assert exact.status == cp.status == SolveStatus.OPTIMAL
    assert {item.headcount_signature for item in cp.candidates} == {
        item.headcount_signature for item in exact.candidates
    }


def test_discount_stage_recalculates_nonzero_opening_students() -> None:
    from packages.optimization.cp_sat_discount import CpSatEntryRatesAndDiscountSolver
    from packages.optimization.engine import ScenarioProfile
    from tests.unit.test_cp_sat_discount import _discount_context

    old = _discount_context()
    opening_rate = RateInput(
        "opening", "RENEWAL_RATE", 20281, "track", "product", "prior-grade", "campus", 1000000
    )
    ctx = replace(
        old,
        fiscal_year=2028,
        rates=tuple(replace(row, fiscal_season=row.fiscal_season + 10) for row in old.rates)
        + (opening_rate,),
        rate_decisions=old.rate_decisions
        + (RateDecision("opening", "track", "RENEWAL_RATE", 1000000, 1000000, 1000000, locked=True),),
        revenue_line_templates=tuple(
            replace(row, fiscal_season=row.fiscal_season + 10, list_price_yuan=row.list_price_yuan / 10)
            for row in old.revenue_line_templates
        ),
        discount_decisions=tuple(
            replace(row, fiscal_season=row.fiscal_season + 10) for row in old.discount_decisions
        ),
        composite_discount_targets=tuple(
            replace(row, fiscal_season=row.fiscal_season + 10 if row.fiscal_season else None)
            for row in old.composite_discount_targets
        ),
        revenue_targets=tuple(
            replace(row, target_revenue_yuan=row.target_revenue_yuan / 5) for row in old.revenue_targets
        ),
        opening_inputs=(OpeningDecisionInput("combination", 10, "opening"),),
    )
    solved = CpSatEntryRatesAndDiscountSolver(maximum_time_seconds=10).solve(
        context=ctx,
        track_directions={"track": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=BudgetCandidateEvaluator(ctx).evaluate,
    )
    assert solved.status == SolveStatus.OPTIMAL
    assert solved.candidates[0].targets_closed
    assert set(dict(solved.candidates[0].decision.discount_values).values()) == {600000}
