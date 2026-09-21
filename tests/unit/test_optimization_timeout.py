import pytest

import packages.optimization.cp_sat_adapter as cp_sat_adapter
from packages.optimization.cp_sat_adapter import (
    CpSatProblem,
    DeterministicCpSatAdapter,
    IntegerVariableSpec,
)
from packages.optimization.engine import SolveStatus


def test_hard_timeout_is_incomplete_and_never_returns_a_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter((100.0, 101.1, 101.1))
    monkeypatch.setattr(cp_sat_adapter, "monotonic", lambda: next(clock))
    problem = CpSatProblem(variables=(IntegerVariableSpec("entry", 0, 10),), constraints=())

    result = DeterministicCpSatAdapter(maximum_time_seconds=1).solve(problem)

    assert result.status == SolveStatus.INCOMPLETE
    assert result.reason_code == "TIME_LIMIT"
    assert result.values == ()
    assert result.solver_status is None
    assert result.wall_time_seconds == pytest.approx(1.1)
