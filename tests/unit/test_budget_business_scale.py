"""Synthetic FY28 only: 238 combinations / 952 season rows, no live BI."""

from dataclasses import replace
from decimal import Decimal

import pytest

from packages.optimization.engine import (
    DecisionVector,
    OptimizationSolverAdapter,
    OptimizationStage,
    SolveStatus,
)
from packages.optimization.evaluator import (
    BudgetCandidateEvaluator,
    BudgetEvaluationContext,
    OpeningDecisionInput,
    RevenueTarget,
    optimize_budget,
)
from packages.revenue.calculation import CompositeDiscountTargetInput
from tests.unit.test_cp_sat_discount import _discount_context
from tests.unit.test_opening_optimization import context


def _two_track_context(*, entry_adjustment: bool) -> BudgetEvaluationContext:
    seed = _discount_context()
    combinations = list(seed.combinations[:0])
    entries = list(seed.entry_decisions[:0])
    rates = list(seed.rates[:0])
    decisions = list(seed.rate_decisions[:0])
    templates = list(seed.revenue_line_templates[:0])
    discounts = list(seed.discount_decisions[:0])
    classes = list(seed.class_size_parameters[:0])
    targets: list[RevenueTarget] = []
    composites: list[CompositeDiscountTargetInput] = []
    total = Decimal(0)
    for index, price in enumerate((Decimal(30000), Decimal(27000))):
        track = f"synthetic-track-{index}"
        key = track
        count = (1001 if index == 0 else 999) if entry_adjustment else 1000
        target_discount = 500000 if entry_adjustment else 600000
        target = Decimal(count) * price * 4 * Decimal(target_discount) / 1000000
        total += target
        targets.append(RevenueTarget(track, "TRACK", track, target))
        combinations.append(
            replace(seed.combinations[0], stable_id=key, track=track, entry_initial_value=1000)
        )
        entries.append(
            replace(
                seed.entry_decisions[0],
                stable_id=key,
                current_value=1000,
                hard_lower_bound=999 if entry_adjustment else 1000,
                hard_upper_bound=1001 if entry_adjustment else 1000,
            )
        )
        rates.extend(
            replace(row, rate_id=f"{track}:{row.rate_id}", track=track, fiscal_season=row.fiscal_season + 10)
            for row in seed.rates
        )
        decisions.extend(
            replace(row, stable_id=f"{track}:{row.stable_id}", track=track) for row in seed.rate_decisions
        )
        templates.extend(
            replace(
                row,
                combination_stable_key=key,
                track=track,
                fiscal_season=row.fiscal_season + 10,
                list_price_yuan=price,
            )
            for row in seed.revenue_line_templates
        )
        discounts.extend(
            replace(
                row, stable_id=f"{track}:{row.stable_id}", track=track, fiscal_season=row.fiscal_season + 10
            )
            for row in seed.discount_decisions
        )
        classes.append(replace(seed.class_size_parameters[0], parameter_id=track, track=track))
        composites.extend(
            CompositeDiscountTargetInput(
                f"{track}:{season}",
                "TRACK",
                track,
                "ANNUAL" if season is None else "SEASON",
                season,
                target_discount,
            )
            for season in (None, 20281, 20282, 20283, 20284)
        )
    targets.append(RevenueTarget("department", "DEPARTMENT", None, total))
    composites.append(
        CompositeDiscountTargetInput("department", "DEPARTMENT", None, "ANNUAL", None, target_discount)
    )
    return replace(
        seed,
        fiscal_year=2028,
        fixed_entry_total=2000,
        combinations=tuple(combinations),
        entry_decisions=tuple(entries),
        rates=tuple(rates),
        rate_decisions=tuple(decisions),
        revenue_line_templates=tuple(templates),
        discount_decisions=tuple(discounts),
        class_size_parameters=tuple(classes),
        revenue_targets=tuple(targets),
        composite_discount_targets=tuple(composites),
        opening_inputs=tuple(OpeningDecisionInput(row.stable_id, 0, None) for row in combinations),
    )


def test_two_track_hundred_million_fixed_entry_structure() -> None:
    ctx = _two_track_context(entry_adjustment=True)
    result = optimize_budget(
        ctx, solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES, maximum_solver_seconds=30
    )
    assert result.status == SolveStatus.OPTIMAL
    assert result.terminal_stage == OptimizationStage.ENTRY_ONLY
    assert result.candidates
    for candidate in result.candidates:
        assert candidate.targets_closed
        assert dict(candidate.decision.entry_values) == {"synthetic-track-0": 1001, "synthetic-track-1": 999}
        assert sum(dict(candidate.decision.entry_values).values()) == 2000
        assert dict(candidate.decision.rate_values) == {row.rate_id: row.value_ppm for row in ctx.rates}


@pytest.mark.parametrize("tax_factor", [Decimal(1), Decimal("1.06")])
@pytest.mark.parametrize("recognition_ppm", [1000000, 700000])
def test_two_track_hundred_million_discount_requires_unlock(
    tax_factor: Decimal, recognition_ppm: int
) -> None:
    ctx = _two_track_context(entry_adjustment=False)
    ctx = replace(
        ctx,
        budget_tax_conversion_factor=tax_factor,
        revenue_line_templates=tuple(
            replace(row, current_season_recognition_rate_ppm=recognition_ppm)
            for row in ctx.revenue_line_templates
        ),
        revenue_targets=tuple(
            replace(
                row,
                target_revenue_yuan=(
                    row.target_revenue_yuan
                    / tax_factor
                    * (Decimal(3) + Decimal(recognition_ppm) / 1000000)
                    / 4
                ).quantize(Decimal("0.000001")),
            )
            for row in ctx.revenue_targets
        ),
    )
    locked = optimize_budget(
        ctx, solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES, maximum_solver_seconds=30
    )
    assert locked.discount_unlock_required
    assert not locked.candidates
    result = optimize_budget(
        ctx,
        solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
        maximum_solver_seconds=30,
        discount_unlocked=True,
    )
    assert result.status == SolveStatus.OPTIMAL
    assert result.terminal_stage == OptimizationStage.ENTRY_RATES_AND_DISCOUNT
    assert result.candidates
    for candidate in result.candidates:
        assert candidate.targets_closed
        assert set(dict(candidate.decision.discount_values).values()) == {600000}
        assert sum(dict(candidate.decision.entry_values).values()) == 2000
        assert dict(candidate.decision.rate_values) == {row.rate_id: row.value_ppm for row in ctx.rates}


def scale_context(adjustable_campuses: int, joint_rates: bool, wide_timeout: bool) -> BudgetEvaluationContext:
    seed = context()
    if joint_rates:
        joint_id = "20281-NEW_STUDENT_SHARE"
        seed = replace(
            seed,
            rates=tuple(
                replace(row, value_ppm=200000) if row.rate_id == joint_id else row for row in seed.rates
            ),
            rate_decisions=tuple(
                replace(
                    row,
                    current_value_ppm=200000,
                    lower_bound_ppm=200000,
                    upper_bound_ppm=210000 if wide_timeout else 201000,
                    locked=False,
                )
                if row.stable_id == joint_id
                else row
                for row in seed.rate_decisions
            ),
        )
    combinations = []
    rates = list(seed.rates[:0])
    decisions = list(seed.rate_decisions[:0])
    entries = []
    templates = list(seed.revenue_line_templates[:0])
    openings = []
    for campus in range(17):
        prefix = f"campus-{campus:02d}"
        rates.extend(replace(row, rate_id=f"{prefix}:{row.rate_id}", campus=prefix) for row in seed.rates)
        decisions.extend(
            replace(
                row,
                stable_id=f"{prefix}:{row.stable_id}",
                locked=campus >= adjustable_campuses or row.locked,
                upper_bound_ppm=(801000 if joint_rates and not wide_timeout else 810000)
                if row.stable_id == "opening"
                else row.upper_bound_ppm,
            )
            for row in seed.rate_decisions
        )
        for subject in range(14):
            key = f"{prefix}:subject-{subject:02d}"
            combinations.append(
                replace(seed.combinations[0], stable_id=key, campus=prefix, subject=f"subject-{subject}")
            )
            entries.append(replace(seed.entry_decisions[0], stable_id=key))
            openings.append(OpeningDecisionInput(key, 1000, f"{prefix}:opening"))
            templates.extend(
                replace(
                    row,
                    combination_stable_key=key,
                    campus=prefix,
                    subject=f"subject-{subject}",
                    list_price_yuan=Decimal(120 if joint_rates else 150),
                )
                for row in seed.revenue_line_templates
            )
    ctx = replace(
        seed,
        combinations=tuple(combinations),
        rates=tuple(rates),
        rate_decisions=tuple(decisions),
        entry_decisions=tuple(entries),
        revenue_line_templates=tuple(templates),
        opening_inputs=tuple(openings),
        revenue_targets=tuple(
            replace(row, target_revenue_yuan=Decimal(114240000 + 84000 * adjustable_campuses))
            for row in seed.revenue_targets
        ),
        composite_discount_targets=(
            CompositeDiscountTargetInput("annual", "DEPARTMENT", None, "ANNUAL", None, 1000000),
        ),
    )
    assert len(ctx.combinations) == 238
    assert len(ctx.revenue_line_templates) == 952
    if joint_rates:
        endpoint = BudgetCandidateEvaluator(ctx).evaluate(
            DecisionVector(
                entry_values=tuple((row.stable_id, row.current_value) for row in ctx.entry_decisions),
                rate_values=tuple(
                    (row.stable_id, row.current_value_ppm if row.locked else row.upper_bound_ppm)
                    for row in ctx.rate_decisions
                ),
            )
        )
        assert endpoint.hard_constraints_satisfied
        assert endpoint.revenue_r11_yuan > Decimal(114250000)
        ctx = replace(
            ctx,
            revenue_targets=tuple(
                replace(row, target_revenue_yuan=endpoint.revenue_r11_yuan) for row in ctx.revenue_targets
            ),
        )
    return ctx


@pytest.mark.parametrize(
    "adjustable_campuses,joint_rates,wide_timeout",
    [(1, False, False), (3, False, False), (3, True, False), (3, True, True)],
)
def test_238_combinations_with_opening_and_hundred_million_revenue(
    adjustable_campuses: int, joint_rates: bool, wide_timeout: bool
) -> None:
    ctx = scale_context(adjustable_campuses, joint_rates, wide_timeout)
    if wide_timeout:
        from packages.optimization.cp_sat_rates import CpSatEntryAndRatesSolver
        from packages.optimization.engine import ScenarioProfile

        timed = CpSatEntryAndRatesSolver(maximum_time_seconds=0.2).solve(
            entries=ctx.entry_decisions,
            rates=ctx.rate_decisions,
            fixed_total=ctx.fixed_entry_total,
            business_context=ctx,
            track_directions={"track": "UP"},
            profile=ScenarioProfile.BALANCED,
            evaluator=BudgetCandidateEvaluator(ctx).evaluate,
        )
        assert timed.status == SolveStatus.INCOMPLETE
        assert not timed.candidates
        assert timed.proof_code == "CP_SAT_BUSINESS_COMPILATION_TIME_LIMIT"
        return
    solved = optimize_budget(
        ctx,
        solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES,
        maximum_solver_seconds=300,
    )
    assert solved.status == SolveStatus.OPTIMAL
    assert solved.candidates
    assert all(candidate.targets_closed for candidate in solved.candidates)
    assert all(
        dict(candidate.decision.rate_values)["campus-00:opening"] > 800000 for candidate in solved.candidates
    )
    if joint_rates:
        for candidate in solved.candidates:
            values = dict(candidate.decision.rate_values)
            for campus in range(adjustable_campuses):
                prefix = f"campus-{campus:02d}"
                assert 800000 < values[f"{prefix}:opening"] <= 801000
                assert 200000 < values[f"{prefix}:20281-NEW_STUDENT_SHARE"] <= 201000
                assert values[f"{prefix}:opening"] == values["campus-00:opening"]
                assert (
                    values[f"{prefix}:20281-NEW_STUDENT_SHARE"] == values["campus-00:20281-NEW_STUDENT_SHARE"]
                )
            for row in ctx.rate_decisions:
                if row.locked:
                    assert values[row.stable_id] == row.current_value_ppm
