from packages.optimization.cp_sat_adapter import (
    INT64_MAX,
    AllowedAssignmentsSpec,
    CpSatProblem,
    DeterministicCpSatAdapter,
    DivisionConstraintSpec,
    ForbiddenAssignmentSpec,
    IntegerVariableSpec,
    LinearConstraintSpec,
    LinearExpressionSpec,
    MultiplicationConstraintSpec,
    ObjectiveTierSpec,
    _map_solver_status,
    _range_is_proven,
    _reduce_constraint,
    _reduce_division,
)
from packages.optimization.engine import SolveStatus


def test_division_reduction_preserves_half_up_rounding_exactly() -> None:
    for coefficient in (0, 100000, 500000, 999999, 1000000):
        original = DivisionConstraintSpec(
            "round", "result", LinearExpressionSpec((("net", coefficient),), 500000), 1000000
        )
        reduced = _reduce_division(original)
        for value in (*range(101), 113000000000000):
            before = (value * coefficient + 500000) // 1000000
            after = (
                value * reduced.numerator.terms[0][1] + reduced.numerator.constant
            ) // reduced.denominator
            assert before == after


def test_large_rounded_division_passes_without_weakening_range_protection() -> None:
    problem = CpSatProblem(
        variables=(
            IntegerVariableSpec("net", 113000000000000, 113000000000000),
            IntegerVariableSpec("result", 0, 113000000000000),
        ),
        constraints=(),
        division_constraints=(
            DivisionConstraintSpec(
                "recognize", "result", LinearExpressionSpec((("net", 700000),), 500000), 1000000
            ),
        ),
        stable_tiebreak_variable_ids=(),
    )
    assert not _range_is_proven(problem)
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)
    assert result.status == SolveStatus.OPTIMAL
    assert dict(result.values)["result"] == 79100000000000


def test_exact_reduction_preserves_signed_bounds_and_constants() -> None:
    original = LinearConstraintSpec(
        "signed", LinearExpressionSpec((("x", 1000000), ("y", -499500)), 500), -1500, 2500
    )
    reduced = _reduce_constraint(original)
    assert reduced.expression.terms == (("x", 2000), ("y", -999))
    assert (reduced.expression.constant, reduced.lower_bound, reduced.upper_bound) == (1, -3, 5)
    for x in range(-10, 11):
        for y in range(-10, 11):
            before = 1000000 * x - 499500 * y + 500
            after = 2000 * x - 999 * y + 1
            assert (-1500 <= before <= 2500) == (-3 <= after <= 5)
    # A one-unit bound must not be rounded to make a model fit.
    indivisible = LinearConstraintSpec("exact", original.expression, lower_bound=1)
    assert _reduce_constraint(indivisible) == indivisible


def test_hundred_million_composite_constraint_is_exactly_reduced() -> None:
    problem = CpSatProblem(
        variables=(
            IntegerVariableSpec("business", 113000000000000, 113000000000000),
            IntegerVariableSpec("list", 226000000000000, 226000000000000),
        ),
        constraints=(
            LinearConstraintSpec(
                "composite",
                LinearExpressionSpec((("business", 1000000), ("list", -500000))),
                lower_bound=0,
                upper_bound=0,
            ),
        ),
        stable_tiebreak_variable_ids=(),
    )
    assert not _range_is_proven(problem)
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)
    assert result.status == SolveStatus.OPTIMAL


def _fixed_total_problem() -> CpSatProblem:
    return CpSatProblem(
        variables=(
            IntegerVariableSpec("entry-a", 0, 10),
            IntegerVariableSpec("entry-b", 0, 10),
        ),
        constraints=(
            LinearConstraintSpec(
                "fixed-entry-total",
                LinearExpressionSpec((("entry-a", 1), ("entry-b", 1))),
                lower_bound=10,
                upper_bound=10,
            ),
        ),
        objective_tiers=(ObjectiveTierSpec("minimize-entry-a", LinearExpressionSpec((("entry-a", 1),))),),
    )


def test_cp_sat_is_deterministic_and_locks_lexicographic_tiers() -> None:
    adapter = DeterministicCpSatAdapter(maximum_time_seconds=5)
    first = adapter.solve(_fixed_total_problem())
    second = adapter.solve(_fixed_total_problem())
    assert first.status == SolveStatus.OPTIMAL
    assert first.values == second.values == (("entry-a", 0), ("entry-b", 10))
    assert first.objective_values == second.objective_values
    assert first.objective_values[0] == ("minimize-entry-a", 0)


def test_cp_sat_matches_exhaustive_oracle_on_small_integer_model() -> None:
    problem = CpSatProblem(
        variables=tuple(IntegerVariableSpec(f"x-{index}", 0, 5) for index in range(3)),
        constraints=(
            LinearConstraintSpec(
                "fixed-total",
                LinearExpressionSpec((("x-0", 1), ("x-1", 1), ("x-2", 1))),
                lower_bound=7,
                upper_bound=7,
            ),
            LinearConstraintSpec(
                "capacity",
                LinearExpressionSpec((("x-0", 2), ("x-1", 1), ("x-2", 3))),
                upper_bound=15,
            ),
        ),
        objective_tiers=(
            ObjectiveTierSpec(
                "business-pressure",
                LinearExpressionSpec((("x-0", 3), ("x-1", 2), ("x-2", 1))),
            ),
        ),
    )
    exhaustive = min(
        (3 * x0 + 2 * x1 + x2, x0, x1, x2)
        for x0 in range(6)
        for x1 in range(6)
        for x2 in range(6)
        if x0 + x1 + x2 == 7 and 2 * x0 + x1 + 3 * x2 <= 15
    )
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)
    assert result.status == SolveStatus.OPTIMAL
    assert result.values == (("x-0", exhaustive[1]), ("x-1", exhaustive[2]), ("x-2", exhaustive[3]))


def test_cp_sat_only_maps_proven_hard_model_infeasible_to_infeasible() -> None:
    problem = CpSatProblem(
        variables=(IntegerVariableSpec("x", 0, 1),),
        constraints=(
            LinearConstraintSpec(
                "impossible",
                LinearExpressionSpec((("x", 1),)),
                lower_bound=2,
            ),
        ),
    )
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)
    assert result.status == SolveStatus.INFEASIBLE
    assert result.reason_code == "FULL_HARD_MODEL_INFEASIBLE"


def test_cp_sat_excludes_an_exact_forbidden_assignment() -> None:
    problem = CpSatProblem(
        variables=(IntegerVariableSpec("x", 0, 2), IntegerVariableSpec("y", 0, 2)),
        constraints=(
            LinearConstraintSpec(
                "fixed-total",
                LinearExpressionSpec((("x", 1), ("y", 1))),
                lower_bound=2,
                upper_bound=2,
            ),
        ),
        forbidden_assignments=(ForbiddenAssignmentSpec("reject-0", ("x", "y"), (0, 2)),),
        stable_tiebreak_variable_ids=("x", "y"),
    )
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)
    assert result.status == SolveStatus.OPTIMAL
    assert result.values == (("x", 1), ("y", 1))


def test_cp_sat_enforces_an_allowed_assignment_table() -> None:
    problem = CpSatProblem(
        variables=(IntegerVariableSpec("level", 0, 2), IntegerVariableSpec("rate", 700_000, 700_200)),
        constraints=(),
        objective_tiers=(ObjectiveTierSpec("maximum-rate", LinearExpressionSpec((("rate", -1),))),),
        allowed_assignments=(
            AllowedAssignmentsSpec(
                "rate-water-level",
                ("level", "rate"),
                ((0, 700_000), (1, 700_100), (2, 700_200)),
            ),
        ),
        stable_tiebreak_variable_ids=("level", "rate"),
    )
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)
    assert result.status == SolveStatus.OPTIMAL
    assert result.values == (("level", 2), ("rate", 700_200))


def test_cp_sat_enforces_exact_integer_multiplication_and_rounded_division() -> None:
    problem = CpSatProblem(
        variables=(
            IntegerVariableSpec("left", 3, 3),
            IntegerVariableSpec("right", 4, 4),
            IntegerVariableSpec("product", 12, 12),
            IntegerVariableSpec("quotient", 2, 2),
        ),
        constraints=(),
        multiplication_constraints=(MultiplicationConstraintSpec("multiply", "product", "left", "right"),),
        division_constraints=(
            DivisionConstraintSpec(
                "round-divide",
                "quotient",
                LinearExpressionSpec((("product", 1),), constant=2),
                5,
            ),
        ),
    )
    result = DeterministicCpSatAdapter(maximum_time_seconds=5).solve(problem)
    assert result.status == SolveStatus.OPTIMAL
    assert dict(result.values)["quotient"] == 2


def test_cp_sat_rejects_unproven_int64_range_without_clamping() -> None:
    problem = CpSatProblem(
        variables=(IntegerVariableSpec("x", 0, INT64_MAX),),
        constraints=(),
        objective_tiers=(ObjectiveTierSpec("overflow", LinearExpressionSpec((("x", 2),))),),
    )
    result = DeterministicCpSatAdapter().solve(problem)
    assert result.status == SolveStatus.INCOMPLETE
    assert result.reason_code == "MODEL_RANGE_UNPROVEN"


def test_cp_sat_model_errors_are_failed_not_business_infeasible() -> None:
    problem = CpSatProblem(
        variables=(IntegerVariableSpec("x", 2, 1),),
        constraints=(),
    )
    result = DeterministicCpSatAdapter().solve(problem)
    assert result.status == SolveStatus.FAILED
    assert result.reason_code == "MODEL_INVALID"


def test_solver_status_mapping_keeps_feasible_and_unknown_incomplete() -> None:
    from ortools.sat.python import cp_model

    assert _map_solver_status(cp_model.FEASIBLE) == SolveStatus.INCOMPLETE
    assert _map_solver_status(cp_model.UNKNOWN) == SolveStatus.INCOMPLETE
    assert _map_solver_status(cp_model.MODEL_INVALID) == SolveStatus.FAILED
