"""Scale suite: synthetic inputs, no BI or database access."""

import asyncio
import multiprocessing
import resource
import sys
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from time import monotonic

import pytest

from packages.optimization.engine import DecisionVector, OptimizationSolverAdapter, SolveStatus
from packages.optimization.evaluator import (
    BudgetCandidateEvaluator,
    BudgetEvaluationContext,
    RevenueTarget,
    optimize_budget,
)
from packages.optimization.process_runner import run_isolated
from packages.revenue.calculation import CompositeDiscountTargetInput
from tests.unit.test_budget_business_scale import scale_context


def wide_context(two_tracks: bool) -> BudgetEvaluationContext:
    ctx = scale_context(3, True, True)
    if not two_tracks:
        return ctx

    def track(campus: str) -> str:
        return f"track-{int(campus.split('-')[1]) % 2}"

    ctx = replace(
        ctx,
        combinations=tuple(replace(row, track=track(row.campus)) for row in ctx.combinations),
        rates=tuple(replace(row, track=track(row.campus)) for row in ctx.rates),
        rate_decisions=tuple(
            replace(row, track=track(row.stable_id.split(":")[0])) for row in ctx.rate_decisions
        ),
        revenue_line_templates=tuple(
            replace(
                row,
                track=track(row.campus),
                list_price_yuan=row.list_price_yuan
                * (Decimal("1.1") if track(row.campus) == "track-1" else 1),
            )
            for row in ctx.revenue_line_templates
        ),
        class_size_parameters=tuple(
            replace(ctx.class_size_parameters[0], parameter_id=key, track=key)
            for key in ("track-0", "track-1")
        ),
        revenue_targets=(
            RevenueTarget("department", "DEPARTMENT", None, Decimal(1)),
            RevenueTarget("track-0", "TRACK", "track-0", Decimal(1)),
            RevenueTarget("track-1", "TRACK", "track-1", Decimal(1)),
        ),
        composite_discount_targets=tuple(
            CompositeDiscountTargetInput(key or "department", scope, key, "ANNUAL", None, 1000000)
            for scope, key in (("DEPARTMENT", None), ("TRACK", "track-0"), ("TRACK", "track-1"))
        ),
    )
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
    incomes = dict(endpoint.target_variances_yuan)
    return replace(
        ctx,
        revenue_targets=tuple(
            replace(row, target_revenue_yuan=incomes[f"{row.scope_level}:{row.track or ''}"] + 1)
            for row in ctx.revenue_targets
        ),
    )


@pytest.mark.parametrize("two_tracks", [False, True])
def test_full_wide_joint_budget(two_tracks: bool, record_property: Callable[[str, object], None]) -> None:
    ctx = wide_context(two_tracks)
    started = monotonic()
    outcome = optimize_budget(
        ctx, solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES, maximum_solver_seconds=300
    )
    record_property("elapsed_seconds", round(monotonic() - started, 3))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    record_property("process_peak_rss_bytes", rss if sys.platform == "darwin" else rss * 1024)
    record_property("combinations", len(ctx.combinations))
    record_property("season_rows", len(ctx.revenue_line_templates))
    record_property("two_tracks", two_tracks)
    assert outcome.status == SolveStatus.OPTIMAL, outcome.reason_code
    assert outcome.candidates
    assert all(row.targets_closed for row in outcome.candidates)
    assert len([row for row in outcome.attempts if row.status == SolveStatus.OPTIMAL]) == 3
    for candidate in outcome.candidates:
        rates = dict(candidate.decision.rate_values)
        for decision in ctx.rate_decisions:
            assert decision.lower_bound_ppm <= rates[decision.stable_id] <= decision.upper_bound_ppm
            if decision.locked:
                assert rates[decision.stable_id] == decision.current_value_ppm
        assert rates["campus-00:opening"] > 800000
        assert rates["campus-00:20281-NEW_STUDENT_SHARE"] > 200000


def _wide_work() -> object:
    return optimize_budget(
        wide_context(True), solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES
    )


async def test_scale_work_is_killed_by_process_deadline() -> None:
    before = {child.pid for child in multiprocessing.active_children()}
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    task = asyncio.create_task(ticker())
    started = monotonic()
    try:
        with pytest.raises(TimeoutError):
            await run_isolated(_wide_work, kwargs={}, timeout_seconds=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert monotonic() - started < 6
    assert ticks >= 10
    assert {child.pid for child in multiprocessing.active_children()} == before
