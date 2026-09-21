from dataclasses import replace
from unittest.mock import patch

import pytest

from packages.optimization.cp_sat_business import (
    BusinessCompilationIncomplete,
    BusinessCompilationTimeout,
    _entry_states,
    _rate_states,
)
from packages.optimization.cp_sat_discount import CpSatEntryRatesAndDiscountSolver
from packages.optimization.cp_sat_rates import CpSatEntryAndRatesSolver
from packages.optimization.engine import EntryDecision, ScenarioProfile, SolveStatus
from packages.optimization.evaluator import BudgetCandidateEvaluator
from tests.unit.test_cp_sat_discount import _discount_context


def test_large_state_spaces_are_capped_before_materialization() -> None:
    with pytest.raises(BusinessCompilationIncomplete) as captured:
        _entry_states((EntryDecision("a", 1, 0, 1000000000),), maximum_states=10)
    assert captured.value.row_count_lower_bound == 1000000001
    rate = replace(
        _discount_context().rate_decisions[0],
        current_value_ppm=500000,
        lower_bound_ppm=500000,
        upper_bound_ppm=510000,
        locked=False,
    )
    with pytest.raises(BusinessCompilationIncomplete) as captured:
        _rate_states(
            all_rates=(rate,), local_rates=(rate,), track_directions={"track": "UP"}, maximum_states=10
        )
    assert captured.value.row_count_lower_bound == 11


def test_deadline_interrupts_entry_and_rate_materialization() -> None:
    ctx = _discount_context()
    with pytest.raises(BusinessCompilationTimeout):
        _entry_states(ctx.entry_decisions, deadline=0)
    with pytest.raises(BusinessCompilationTimeout):
        _rate_states(
            all_rates=ctx.rate_decisions,
            local_rates=ctx.rate_decisions,
            track_directions={"track": "UP"},
            deadline=0,
        )


def test_discount_compilation_timeout_is_incomplete_and_never_starts_solver() -> None:
    ctx = _discount_context()
    with (
        patch(
            "packages.optimization.cp_sat_discount.compile_direct_discount_problem",
            side_effect=BusinessCompilationTimeout,
        ),
        patch("packages.optimization.cp_sat_discount.DeterministicCpSatAdapter.solve") as solve,
    ):
        result = CpSatEntryRatesAndDiscountSolver().solve(
            context=ctx,
            track_directions={"track": "UP"},
            profile=ScenarioProfile.BALANCED,
            evaluator=BudgetCandidateEvaluator(ctx).evaluate,
        )
    solve.assert_not_called()
    assert result.status == SolveStatus.INCOMPLETE
    assert result.proof_code == "CP_SAT_BUSINESS_COMPILATION_TIME_LIMIT"


def test_rate_compilation_timeout_is_incomplete_and_never_starts_solver() -> None:
    ctx = _discount_context()
    with (
        patch(
            "packages.optimization.cp_sat_business.compile_direct_business_problem",
            side_effect=BusinessCompilationTimeout,
        ),
        patch("packages.optimization.cp_sat_rates.DeterministicCpSatAdapter.solve") as solve,
    ):
        result = CpSatEntryAndRatesSolver().solve(
            business_context=ctx,
            entries=ctx.entry_decisions,
            rates=ctx.rate_decisions,
            fixed_total=ctx.fixed_entry_total,
            track_directions={"track": "UP"},
            profile=ScenarioProfile.BALANCED,
            evaluator=BudgetCandidateEvaluator(ctx).evaluate,
        )
    solve.assert_not_called()
    assert result.status == SolveStatus.INCOMPLETE
    assert result.proof_code == "CP_SAT_BUSINESS_COMPILATION_TIME_LIMIT"
