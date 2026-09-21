from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

import pytest

from packages.optimization.cp_sat_business import compile_direct_business_problem, reprofile_business_problem
from packages.optimization.cp_sat_rates import CpSatEntryAndRatesSolver
from packages.optimization.engine import OptimizationSolverAdapter, ScenarioProfile, SolveAttempt, SolveStatus
from packages.optimization.evaluator import BudgetCandidateEvaluator, BudgetEvaluationContext, optimize_budget
from tests.unit.test_cp_sat_business import _direct_context


@pytest.mark.parametrize(
    "profile",
    [ScenarioProfile.BALANCED, ScenarioProfile.RETENTION_WEIGHTED, ScenarioProfile.ACQUISITION_WEIGHTED],
)
def test_reprofile_is_identical_to_independent_compilation(profile: ScenarioProfile) -> None:
    ctx = _direct_context()
    base = compile_direct_business_problem(
        ctx, track_directions={"track": "UP"}, profile=ScenarioProfile.BALANCED, maximum_group_table_rows=100
    )
    expected = compile_direct_business_problem(
        ctx, track_directions={"track": "UP"}, profile=profile, maximum_group_table_rows=100
    )
    actual = reprofile_business_problem(base, profile)
    assert actual == expected
    assert actual.problem.allowed_assignments is base.problem.allowed_assignments


def test_three_profiles_compile_once_and_cache_does_not_survive_another_run() -> None:
    with patch(
        "packages.optimization.cp_sat_business.compile_direct_business_problem",
        wraps=compile_direct_business_problem,
    ) as compile_model:
        first = optimize_budget(
            _direct_context(), solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES
        )
        assert first.status == SolveStatus.OPTIMAL
        assert compile_model.call_count == 1
        second = optimize_budget(
            _direct_context(), solver_adapter=OptimizationSolverAdapter.CP_SAT_ENTRY_AND_RATES
        )
        assert second.status == SolveStatus.OPTIMAL
        assert compile_model.call_count == 2
        assert first.candidates == second.candidates


def test_cache_rejects_changed_context_direction_and_row_limit() -> None:
    ctx = _direct_context()
    solver = CpSatEntryAndRatesSolver()

    def solve(current: BudgetEvaluationContext, direction: str = "UP") -> SolveAttempt:
        return solver.solve(
            entries=current.entry_decisions,
            rates=current.rate_decisions,
            fixed_total=current.fixed_entry_total,
            track_directions={"track": direction},
            profile=ScenarioProfile.BALANCED,
            evaluator=BudgetCandidateEvaluator(current).evaluate,
            business_context=current,
        )

    with patch(
        "packages.optimization.cp_sat_business.compile_direct_business_problem",
        wraps=compile_direct_business_problem,
    ) as compile_model:
        assert solve(ctx).status == SolveStatus.OPTIMAL
        reused = solve(ctx)
        assert reused.proof_summary is not None
        assert reused.proof_summary["reused_business_compilation"] is True
        assert compile_model.call_count == 1
        changed = replace(ctx, budget_tax_conversion_factor=Decimal(2))
        solve(changed)
        assert compile_model.call_count == 2
        solver.maximum_group_table_rows = 1
        assert solve(changed).status == SolveStatus.INCOMPLETE
        assert compile_model.call_count == 3
        solve(ctx, "DOWN")
        assert compile_model.call_count == 4
