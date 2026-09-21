from __future__ import annotations

from dataclasses import dataclass, replace
from math import gcd
from time import monotonic

from ortools.sat.python import cp_model

from packages.optimization.engine import SolveStatus

CP_SAT_ADAPTER_VERSION = "ortools-cp-sat-9.15-v3"
INT64_MAX = 2**63 - 1


@dataclass(frozen=True, order=True)
class IntegerVariableSpec:
    stable_id: str
    lower_bound: int
    upper_bound: int


@dataclass(frozen=True)
class LinearExpressionSpec:
    terms: tuple[tuple[str, int], ...]
    constant: int = 0


@dataclass(frozen=True)
class LinearConstraintSpec:
    stable_id: str
    expression: LinearExpressionSpec
    lower_bound: int | None = None
    upper_bound: int | None = None


@dataclass(frozen=True)
class ObjectiveTierSpec:
    stable_id: str
    expression: LinearExpressionSpec


@dataclass(frozen=True)
class ForbiddenAssignmentSpec:
    stable_id: str
    variable_ids: tuple[str, ...]
    values: tuple[int, ...]


@dataclass(frozen=True)
class AllowedAssignmentsSpec:
    stable_id: str
    variable_ids: tuple[str, ...]
    rows: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class MultiplicationConstraintSpec:
    stable_id: str
    target_variable_id: str
    left_variable_id: str
    right_variable_id: str


@dataclass(frozen=True)
class DivisionConstraintSpec:
    stable_id: str
    target_variable_id: str
    numerator: LinearExpressionSpec
    denominator: int


@dataclass(frozen=True)
class CpSatProblem:
    variables: tuple[IntegerVariableSpec, ...]
    constraints: tuple[LinearConstraintSpec, ...]
    objective_tiers: tuple[ObjectiveTierSpec, ...] = ()
    allowed_assignments: tuple[AllowedAssignmentsSpec, ...] = ()
    forbidden_assignments: tuple[ForbiddenAssignmentSpec, ...] = ()
    multiplication_constraints: tuple[MultiplicationConstraintSpec, ...] = ()
    division_constraints: tuple[DivisionConstraintSpec, ...] = ()
    stable_tiebreak_variable_ids: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CpSatSolution:
    status: SolveStatus
    values: tuple[tuple[str, int], ...] = ()
    objective_values: tuple[tuple[str, int], ...] = ()
    reason_code: str | None = None
    solver_status: str | None = None
    model_validation_error: str | None = None
    num_conflicts: int = 0
    num_branches: int = 0
    wall_time_seconds: float = 0


class DeterministicCpSatAdapter:
    def __init__(self, *, random_seed: int = 27, maximum_time_seconds: float = 900) -> None:
        if maximum_time_seconds <= 0:
            raise ValueError("maximum_time_seconds must be positive")
        self.random_seed = random_seed
        self.maximum_time_seconds = maximum_time_seconds

    def solve(self, problem: CpSatProblem) -> CpSatSolution:
        validation_error = _validate_problem(problem)
        if validation_error is not None:
            return CpSatSolution(
                status=SolveStatus.FAILED,
                reason_code="MODEL_INVALID",
                model_validation_error=validation_error,
            )
        original_problem = problem
        problem = replace(
            problem,
            constraints=tuple(_reduce_constraint(item) for item in problem.constraints),
            division_constraints=tuple(_reduce_division(item) for item in problem.division_constraints),
        )
        if not _range_is_proven(problem):
            return CpSatSolution(
                status=SolveStatus.INCOMPLETE,
                reason_code="MODEL_RANGE_UNPROVEN",
            )
        model = cp_model.CpModel()
        variables = {
            item.stable_id: model.new_int_var(item.lower_bound, item.upper_bound, item.stable_id)
            for item in sorted(problem.variables)
        }
        for item in sorted(problem.constraints, key=lambda row: row.stable_id):
            expression = _cp_expression(item.expression, variables)
            if item.lower_bound is not None:
                model.add(expression >= item.lower_bound)
            if item.upper_bound is not None:
                model.add(expression <= item.upper_bound)
        for allowed in sorted(problem.allowed_assignments, key=lambda row: row.stable_id):
            model.add_allowed_assignments(
                [variables[key] for key in allowed.variable_ids],
                allowed.rows,
            )
        for forbidden in sorted(problem.forbidden_assignments, key=lambda row: row.stable_id):
            model.add_forbidden_assignments(
                [variables[key] for key in forbidden.variable_ids],
                [forbidden.values],
            )
        for multiplication in sorted(problem.multiplication_constraints, key=lambda row: row.stable_id):
            model.add_multiplication_equality(
                variables[multiplication.target_variable_id],
                [
                    variables[multiplication.left_variable_id],
                    variables[multiplication.right_variable_id],
                ],
            )
        for division in sorted(problem.division_constraints, key=lambda row: row.stable_id):
            model.add_division_equality(
                variables[division.target_variable_id],
                _cp_expression(division.numerator, variables),
                division.denominator,
            )
        model_error = model.validate()
        if model_error:
            return CpSatSolution(
                status=SolveStatus.FAILED,
                reason_code="MODEL_INVALID",
                model_validation_error=model_error,
            )

        tiers = [*problem.objective_tiers]
        tiebreak_ids = (
            tuple(sorted(variables))
            if problem.stable_tiebreak_variable_ids is None
            else problem.stable_tiebreak_variable_ids
        )
        tiers.extend(
            ObjectiveTierSpec(
                stable_id=f"stable-id:{stable_id}",
                expression=LinearExpressionSpec(((stable_id, 1),)),
            )
            for stable_id in tiebreak_ids
        )
        if not tiers:
            tiers.append(ObjectiveTierSpec("constant-zero", LinearExpressionSpec(())))
        started = monotonic()
        objective_values: list[tuple[str, int]] = []
        final_solver: cp_model.CpSolver | None = None
        for tier_index, tier in enumerate(tiers):
            remaining = self.maximum_time_seconds - (monotonic() - started)
            if remaining <= 0:
                return CpSatSolution(
                    status=SolveStatus.INCOMPLETE,
                    reason_code="TIME_LIMIT",
                    objective_values=tuple(objective_values),
                    wall_time_seconds=monotonic() - started,
                )
            expression = _cp_expression(tier.expression, variables)
            model.minimize(expression)
            solver = cp_model.CpSolver()
            solver.parameters.random_seed = self.random_seed
            solver.parameters.num_search_workers = 1
            solver.parameters.max_time_in_seconds = remaining
            solver.parameters.log_search_progress = False
            status = solver.solve(model)
            mapped = _map_solver_status(status)
            if mapped != SolveStatus.OPTIMAL:
                if mapped == SolveStatus.INFEASIBLE and tier_index > 0:
                    mapped = SolveStatus.FAILED
                    reason = "LEXICOGRAPHIC_LOCK_BECAME_INFEASIBLE"
                else:
                    reason = _status_reason(status)
                return CpSatSolution(
                    status=mapped,
                    reason_code=reason,
                    solver_status=solver.status_name(status),
                    objective_values=tuple(objective_values),
                    num_conflicts=solver.num_conflicts,
                    num_branches=solver.num_branches,
                    wall_time_seconds=monotonic() - started,
                )
            value = _evaluate_expression(
                tier.expression,
                {key: solver.value(variable) for key, variable in variables.items()},
            )
            objective_values.append((tier.stable_id, value))
            model.add(expression == value)
            final_solver = solver
        assert final_solver is not None
        values = tuple((key, final_solver.value(variable)) for key, variable in sorted(variables.items()))
        if not _solution_satisfies(original_problem, dict(values)):
            return CpSatSolution(
                status=SolveStatus.FAILED,
                reason_code="SOLUTION_RECOMPUTATION_MISMATCH",
                solver_status="OPTIMAL",
                objective_values=tuple(objective_values),
                wall_time_seconds=monotonic() - started,
            )
        return CpSatSolution(
            status=SolveStatus.OPTIMAL,
            values=values,
            objective_values=tuple(objective_values),
            solver_status="OPTIMAL",
            num_conflicts=final_solver.num_conflicts,
            num_branches=final_solver.num_branches,
            wall_time_seconds=monotonic() - started,
        )


def _reduce_division(item: DivisionConstraintSpec) -> DivisionConstraintSpec:
    """Preserve the exact quotient, including its integer rounding offset."""
    divisor = gcd(item.denominator, item.numerator.constant)
    for _, coefficient in item.numerator.terms:
        divisor = gcd(divisor, coefficient)
    if divisor <= 1:
        return item
    return replace(
        item,
        numerator=LinearExpressionSpec(
            tuple((key, coefficient // divisor) for key, coefficient in item.numerator.terms),
            item.numerator.constant // divisor,
        ),
        denominator=item.denominator // divisor,
    )


def _reduce_constraint(item: LinearConstraintSpec) -> LinearConstraintSpec:
    """Exact integer reduction; never round money, bounds or tolerances."""
    divisor = abs(item.expression.constant)
    for _, coefficient in item.expression.terms:
        divisor = gcd(divisor, coefficient)
    for bound in (item.lower_bound, item.upper_bound):
        if bound is not None:
            divisor = gcd(divisor, bound)
    if divisor <= 1:
        return item
    return replace(
        item,
        expression=LinearExpressionSpec(
            tuple((key, coefficient // divisor) for key, coefficient in item.expression.terms),
            item.expression.constant // divisor,
        ),
        lower_bound=None if item.lower_bound is None else item.lower_bound // divisor,
        upper_bound=None if item.upper_bound is None else item.upper_bound // divisor,
    )


def _cp_expression(spec: LinearExpressionSpec, variables: dict[str, cp_model.IntVar]) -> cp_model.LinearExpr:
    return (
        cp_model.LinearExpr.sum([variables[key] * coefficient for key, coefficient in spec.terms])
        + spec.constant
    )


def _evaluate_expression(spec: LinearExpressionSpec, values: dict[str, int]) -> int:
    return spec.constant + sum(values[key] * coefficient for key, coefficient in spec.terms)


def _validate_problem(problem: CpSatProblem) -> str | None:
    if not problem.variables:
        return "at least one integer variable is required"
    variable_ids = [item.stable_id for item in problem.variables]
    if len(variable_ids) != len(set(variable_ids)) or any(not item for item in variable_ids):
        return "variable stable IDs must be non-empty and unique"
    if any(item.lower_bound > item.upper_bound for item in problem.variables):
        return "variable lower bound exceeds upper bound"
    known = set(variable_ids)
    expression_rows = [
        *(item.expression for item in problem.constraints),
        *(item.expression for item in problem.objective_tiers),
        *(item.numerator for item in problem.division_constraints),
    ]
    if any(key not in known for expression in expression_rows for key, _ in expression.terms):
        return "expression references an unknown variable"
    if problem.stable_tiebreak_variable_ids is not None and (
        len(problem.stable_tiebreak_variable_ids) != len(set(problem.stable_tiebreak_variable_ids))
        or any(key not in known for key in problem.stable_tiebreak_variable_ids)
    ):
        return "stable tiebreak variables must be unique known variables"
    forbidden_ids = [item.stable_id for item in problem.forbidden_assignments]
    if len(forbidden_ids) != len(set(forbidden_ids)):
        return "forbidden assignment stable IDs must be unique"
    if any(
        not item.variable_ids
        or len(item.variable_ids) != len(item.values)
        or len(item.variable_ids) != len(set(item.variable_ids))
        or any(key not in known for key in item.variable_ids)
        for item in problem.forbidden_assignments
    ):
        return "forbidden assignments must use aligned unique known variables"
    allowed_ids = [item.stable_id for item in problem.allowed_assignments]
    if len(allowed_ids) != len(set(allowed_ids)):
        return "allowed assignment stable IDs must be unique"
    if any(
        not item.variable_ids
        or not item.rows
        or len(item.variable_ids) != len(set(item.variable_ids))
        or any(key not in known for key in item.variable_ids)
        or any(len(row) != len(item.variable_ids) for row in item.rows)
        for item in problem.allowed_assignments
    ):
        return "allowed assignments must use non-empty aligned unique known variables"
    multiplication_ids = [item.stable_id for item in problem.multiplication_constraints]
    division_ids = [item.stable_id for item in problem.division_constraints]
    if len(multiplication_ids) != len(set(multiplication_ids)):
        return "multiplication constraint stable IDs must be unique"
    if len(division_ids) != len(set(division_ids)):
        return "division constraint stable IDs must be unique"
    if any(
        item.target_variable_id not in known
        or item.left_variable_id not in known
        or item.right_variable_id not in known
        for item in problem.multiplication_constraints
    ):
        return "multiplication constraints must reference known variables"
    if any(
        item.target_variable_id not in known or item.denominator <= 0 for item in problem.division_constraints
    ):
        return "division constraints must reference known targets and use positive denominators"
    constraint_ids = [item.stable_id for item in problem.constraints]
    objective_ids = [item.stable_id for item in problem.objective_tiers]
    if len(constraint_ids) != len(set(constraint_ids)) or len(objective_ids) != len(set(objective_ids)):
        return "constraint and objective stable IDs must be unique within their kinds"
    if any(item.lower_bound is None and item.upper_bound is None for item in problem.constraints):
        return "constraint must have at least one bound"
    if any(
        item.lower_bound is not None and item.upper_bound is not None and item.lower_bound > item.upper_bound
        for item in problem.constraints
    ):
        return "constraint lower bound exceeds upper bound"
    return None


def _range_is_proven(problem: CpSatProblem) -> bool:
    bounds = {item.stable_id: (item.lower_bound, item.upper_bound) for item in problem.variables}
    if any(abs(value) > INT64_MAX for pair in bounds.values() for value in pair):
        return False
    expressions = [
        *(item.expression for item in problem.constraints),
        *(item.expression for item in problem.objective_tiers),
        *(item.numerator for item in problem.division_constraints),
    ]
    for expression in expressions:
        maximum_absolute = abs(expression.constant)
        for key, coefficient in expression.terms:
            lower, upper = bounds[key]
            maximum_absolute += max(abs(lower * coefficient), abs(upper * coefficient))
            if maximum_absolute > INT64_MAX:
                return False
    constraint_bounds = [
        value
        for item in problem.constraints
        for value in (item.lower_bound, item.upper_bound)
        if value is not None
    ]
    table_values = [value for item in problem.allowed_assignments for row in item.rows for value in row]
    multiplication_safe = all(
        max(abs(bounds[item.left_variable_id][0]), abs(bounds[item.left_variable_id][1]))
        * max(abs(bounds[item.right_variable_id][0]), abs(bounds[item.right_variable_id][1]))
        <= INT64_MAX
        for item in problem.multiplication_constraints
    )
    return multiplication_safe and all(
        abs(value) <= INT64_MAX for value in (*constraint_bounds, *table_values)
    )


def _solution_satisfies(problem: CpSatProblem, values: dict[str, int]) -> bool:
    variable_ok = all(
        item.lower_bound <= values[item.stable_id] <= item.upper_bound for item in problem.variables
    )
    if not variable_ok:
        return False
    for item in problem.constraints:
        value = _evaluate_expression(item.expression, values)
        if item.lower_bound is not None and value < item.lower_bound:
            return False
        if item.upper_bound is not None and value > item.upper_bound:
            return False
    for forbidden in problem.forbidden_assignments:
        if tuple(values[key] for key in forbidden.variable_ids) == forbidden.values:
            return False
    for allowed in problem.allowed_assignments:
        if tuple(values[key] for key in allowed.variable_ids) not in allowed.rows:
            return False
    for multiplication in problem.multiplication_constraints:
        if values[multiplication.target_variable_id] != (
            values[multiplication.left_variable_id] * values[multiplication.right_variable_id]
        ):
            return False
    for division in problem.division_constraints:
        numerator = _evaluate_expression(division.numerator, values)
        if numerator < 0:
            return False
        if values[division.target_variable_id] != numerator // division.denominator:
            return False
    return True


def _map_solver_status(status: cp_model.CpSolverStatus) -> SolveStatus:
    if status == cp_model.OPTIMAL:
        return SolveStatus.OPTIMAL
    if status == cp_model.INFEASIBLE:
        return SolveStatus.INFEASIBLE
    if status in {cp_model.FEASIBLE, cp_model.UNKNOWN}:
        return SolveStatus.INCOMPLETE
    return SolveStatus.FAILED


def _status_reason(status: cp_model.CpSolverStatus) -> str:
    if status == cp_model.INFEASIBLE:
        return "FULL_HARD_MODEL_INFEASIBLE"
    if status == cp_model.FEASIBLE:
        return "LEXICOGRAPHIC_OPTIMUM_UNPROVEN"
    if status == cp_model.UNKNOWN:
        return "SOLVER_UNKNOWN_OR_TIME_LIMIT"
    return "MODEL_INVALID"
