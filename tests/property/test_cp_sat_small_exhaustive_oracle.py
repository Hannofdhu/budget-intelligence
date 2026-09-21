from itertools import product

import pytest

from packages.optimization.cp_sat_adapter import (
    CpSatProblem,
    DeterministicCpSatAdapter,
    IntegerVariableSpec,
    LinearConstraintSpec,
    LinearExpressionSpec,
    ObjectiveTierSpec,
)
from packages.optimization.engine import SolveStatus


def _small_problem(case: int) -> CpSatProblem:
    variable_count = 2 + case % 4
    variables = tuple(
        IntegerVariableSpec(f"x-{index}", 0, 2 + (case + index) % 2) for index in range(variable_count)
    )
    maximum_total = sum(item.upper_bound for item in variables)
    fixed_total = maximum_total + 1 if case in {7, 19} else case % (maximum_total + 1)
    total = LinearExpressionSpec(tuple((item.stable_id, 1) for item in variables))
    weighted = LinearExpressionSpec(
        tuple((item.stable_id, 1 + (case + index) % 3) for index, item in enumerate(variables))
    )
    return CpSatProblem(
        variables=variables,
        constraints=(
            LinearConstraintSpec("fixed-total", total, fixed_total, fixed_total),
            LinearConstraintSpec("weighted-cap", weighted, upper_bound=max(fixed_total, case % 13 + 2)),
        ),
        objective_tiers=(
            ObjectiveTierSpec(
                "business-pressure",
                LinearExpressionSpec(
                    tuple(
                        (item.stable_id, 1 + (index * 2 + case) % 5) for index, item in enumerate(variables)
                    )
                ),
            ),
        ),
    )


def _evaluate(expression: LinearExpressionSpec, values: dict[str, int]) -> int:
    return expression.constant + sum(values[key] * coefficient for key, coefficient in expression.terms)


def _exhaustive_oracle(problem: CpSatProblem) -> tuple[tuple[str, int], ...] | None:
    variable_ids = tuple(sorted(item.stable_id for item in problem.variables))
    bounds = {item.stable_id: range(item.lower_bound, item.upper_bound + 1) for item in problem.variables}
    feasible: list[tuple[tuple[int, ...], tuple[tuple[str, int], ...]]] = []
    for row in product(*(bounds[key] for key in variable_ids)):
        values = dict(zip(variable_ids, row, strict=True))
        if any(
            (
                constraint.lower_bound is not None
                and _evaluate(constraint.expression, values) < constraint.lower_bound
            )
            or (
                constraint.upper_bound is not None
                and _evaluate(constraint.expression, values) > constraint.upper_bound
            )
            for constraint in problem.constraints
        ):
            continue
        ranking = tuple(_evaluate(tier.expression, values) for tier in problem.objective_tiers) + row
        feasible.append((ranking, tuple(zip(variable_ids, row, strict=True))))
    return min(feasible)[1] if feasible else None


@pytest.mark.parametrize("case", range(24))
def test_cp_sat_matches_independent_exhaustive_oracle_for_small_models(case: int) -> None:
    problem = _small_problem(case)
    oracle = _exhaustive_oracle(problem)
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)

    if oracle is None:
        assert result.status == SolveStatus.INFEASIBLE
        assert result.values == ()
    else:
        assert result.status == SolveStatus.OPTIMAL
        assert result.values == oracle
