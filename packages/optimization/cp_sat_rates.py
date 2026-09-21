from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING

from packages.optimization.cp_sat_adapter import (
    CP_SAT_ADAPTER_VERSION,
    AllowedAssignmentsSpec,
    CpSatProblem,
    DeterministicCpSatAdapter,
    ForbiddenAssignmentSpec,
    IntegerVariableSpec,
    LinearConstraintSpec,
    LinearExpressionSpec,
    ObjectiveTierSpec,
)
from packages.optimization.cp_sat_entry import compile_entry_problem
from packages.optimization.engine import (
    RATE_STEP_PPM,
    CandidateEvaluation,
    DecisionVector,
    EntryDecision,
    OptimizationStage,
    RateDecision,
    ScenarioProfile,
    SolveAttempt,
    SolveStatus,
    candidate_rank,
)

if TYPE_CHECKING:
    from packages.optimization.cp_sat_business import CompiledBusinessProblem
    from packages.optimization.evaluator import BudgetEvaluationContext

ENTRY_AND_RATES_CP_SAT_MODEL_VERSION = "entry-and-rates-cp-sat-v1"

CandidateEvaluator = Callable[[DecisionVector], CandidateEvaluation]


class CpSatEntryAndRatesSolver:
    """Exhaustively prove the best rate-profile candidate over a CP-SAT space.

    Rate water levels and entry structure are encoded exactly. Revenue-based
    contribution deviation remains in the authoritative candidate evaluator, so
    every structural decision must be visited before OPTIMAL can be claimed.
    """

    def __init__(
        self,
        *,
        maximum_time_seconds: float = 300,
        maximum_candidate_validations: int = 10_000,
        maximum_group_table_rows: int = 100_000,
        random_seed: int = 27,
    ) -> None:
        if maximum_time_seconds <= 0:
            raise ValueError("maximum_time_seconds must be positive")
        if maximum_candidate_validations <= 0:
            raise ValueError("maximum_candidate_validations must be positive")
        if maximum_group_table_rows <= 0:
            raise ValueError("maximum_group_table_rows must be positive")
        self.maximum_time_seconds = maximum_time_seconds
        self.maximum_candidate_validations = maximum_candidate_validations
        self.maximum_group_table_rows = maximum_group_table_rows
        self.random_seed = random_seed
        self._cached_context: BudgetEvaluationContext | None = None
        self._cached_directions: tuple[tuple[str, str], ...] = ()
        self._compiled_business: CompiledBusinessProblem | None = None
        self._cached_row_limit: int | None = None

    def solve(
        self,
        *,
        entries: Sequence[EntryDecision],
        rates: Sequence[RateDecision],
        fixed_total: int,
        track_directions: dict[str, str],
        profile: ScenarioProfile,
        evaluator: CandidateEvaluator,
        business_context: BudgetEvaluationContext | None = None,
    ) -> SolveAttempt:
        validation_error = _validate_inputs(entries, rates, fixed_total, track_directions, profile)
        if validation_error is not None:
            return _attempt(
                profile=profile,
                status=SolveStatus.FAILED,
                proof_code="ENTRY_AND_RATES_MODEL_INVALID",
                proof_summary={"validation_error": validation_error},
            )
        if business_context is not None:
            return self._solve_direct_business_model(
                context=business_context,
                track_directions=track_directions,
                profile=profile,
                evaluator=evaluator,
            )

        ordered_entries = tuple(sorted(entries))
        ordered_rates = tuple(sorted(rates))
        entry_variable_ids = tuple(f"entry:{index:06d}" for index in range(len(ordered_entries)))
        rate_variable_ids = tuple(f"rate:{index:06d}" for index in range(len(ordered_rates)))
        excluded: list[ForbiddenAssignmentSpec] = []
        feasible: list[CandidateEvaluation] = []
        evaluated_count = 0
        started = monotonic()

        while True:
            remaining = self.maximum_time_seconds - (monotonic() - started)
            if remaining <= 0:
                return _incomplete_attempt(
                    profile,
                    "CP_SAT_ENTRY_AND_RATES_TIME_LIMIT",
                    evaluated_count,
                    excluded_count=len(excluded),
                    feasible_count=len(feasible),
                )

            problem = compile_entry_and_rates_problem(
                ordered_entries,
                ordered_rates,
                fixed_total=fixed_total,
                track_directions=track_directions,
                entry_variable_ids=entry_variable_ids,
                rate_variable_ids=rate_variable_ids,
                forbidden_assignments=tuple(excluded),
            )
            solution = DeterministicCpSatAdapter(
                random_seed=self.random_seed,
                maximum_time_seconds=remaining,
            ).solve(problem)
            if solution.status == SolveStatus.INFEASIBLE:
                if feasible:
                    winner = min(feasible, key=lambda item: candidate_rank(item, profile))
                    return _attempt(
                        profile=profile,
                        status=SolveStatus.OPTIMAL,
                        candidates=(replace(winner, scenario_types=(profile,)),),
                        evaluated_state_count=evaluated_count,
                        proof_summary={
                            "adapter_version": CP_SAT_ADAPTER_VERSION,
                            "model_version": ENTRY_AND_RATES_CP_SAT_MODEL_VERSION,
                            "enumeration_proof": "FULL_STRUCTURAL_SPACE_EXHAUSTED",
                            "excluded_decision_count": len(excluded),
                            "feasible_candidate_count": len(feasible),
                        },
                    )
                proof_code = (
                    "CP_SAT_ENTRY_AND_RATES_MODEL_INFEASIBLE"
                    if not excluded
                    else "CP_SAT_ENTRY_AND_RATES_VALIDATION_SPACE_EXHAUSTED"
                )
                return _attempt(
                    profile=profile,
                    status=SolveStatus.INFEASIBLE,
                    evaluated_state_count=evaluated_count,
                    proof_code=proof_code,
                    proof_summary={
                        "adapter_version": CP_SAT_ADAPTER_VERSION,
                        "model_version": ENTRY_AND_RATES_CP_SAT_MODEL_VERSION,
                        "excluded_decision_count": len(excluded),
                        "feasible_candidate_count": 0,
                        "solver_status": solution.solver_status,
                    },
                )
            if solution.status != SolveStatus.OPTIMAL:
                return _attempt(
                    profile=profile,
                    status=solution.status,
                    evaluated_state_count=evaluated_count,
                    proof_code=solution.reason_code,
                    proof_summary={
                        "adapter_version": CP_SAT_ADAPTER_VERSION,
                        "model_version": ENTRY_AND_RATES_CP_SAT_MODEL_VERSION,
                        "excluded_decision_count": len(excluded),
                        "feasible_candidate_count": len(feasible),
                        "solver_status": solution.solver_status,
                        "model_validation_error": solution.model_validation_error,
                    },
                )

            raw_values = dict(solution.values)
            entry_values = tuple(
                (entry.stable_id, raw_values[variable_id])
                for entry, variable_id in zip(ordered_entries, entry_variable_ids, strict=True)
            )
            rate_values = tuple(
                (rate.stable_id, raw_values[variable_id])
                for rate, variable_id in zip(ordered_rates, rate_variable_ids, strict=True)
            )
            candidate = evaluator(DecisionVector(entry_values=entry_values, rate_values=rate_values))
            evaluated_count += 1
            if candidate.targets_closed:
                feasible.append(candidate)

            decision_variable_ids = (*entry_variable_ids, *rate_variable_ids)
            excluded.append(
                ForbiddenAssignmentSpec(
                    stable_id=f"excluded:{evaluated_count:08d}",
                    variable_ids=decision_variable_ids,
                    values=(
                        *(value for _, value in entry_values),
                        *(value for _, value in rate_values),
                    ),
                )
            )
            if evaluated_count >= self.maximum_candidate_validations:
                return _incomplete_attempt(
                    profile,
                    "CP_SAT_ENTRY_AND_RATES_VALIDATION_LIMIT_EXCEEDED",
                    evaluated_count,
                    excluded_count=len(excluded),
                    feasible_count=len(feasible),
                )

    def _solve_direct_business_model(
        self,
        *,
        context: BudgetEvaluationContext,
        track_directions: dict[str, str],
        profile: ScenarioProfile,
        evaluator: CandidateEvaluator,
    ) -> SolveAttempt:
        from packages.optimization.cp_sat_business import (
            DIRECT_BUSINESS_MODEL_VERSION,
            BusinessCompilationError,
            BusinessCompilationIncomplete,
            BusinessCompilationInfeasible,
            BusinessCompilationTimeout,
            compile_direct_business_problem,
            reprofile_business_problem,
        )

        deadline = monotonic() + self.maximum_time_seconds
        reused_compilation = False
        try:
            directions = tuple(sorted(track_directions.items()))
            if (
                self._cached_context is context
                and self._cached_directions == directions
                and self._compiled_business is not None
                and self._cached_row_limit == self.maximum_group_table_rows
            ):
                compiled = reprofile_business_problem(self._compiled_business, profile)
                reused_compilation = True
            else:
                compiled = compile_direct_business_problem(
                    context,
                    track_directions=track_directions,
                    profile=profile,
                    maximum_group_table_rows=self.maximum_group_table_rows,
                    deadline=deadline,
                )
                self._cached_context = context
                self._cached_directions = directions
                self._compiled_business = compiled
                self._cached_row_limit = self.maximum_group_table_rows
        except BusinessCompilationTimeout:
            return _attempt(
                profile=profile,
                status=SolveStatus.INCOMPLETE,
                proof_code="CP_SAT_BUSINESS_COMPILATION_TIME_LIMIT",
            )
        except BusinessCompilationIncomplete as exc:
            return _attempt(
                profile=profile,
                status=SolveStatus.INCOMPLETE,
                proof_code="CP_SAT_GROUP_TABLE_LIMIT_EXCEEDED",
                proof_summary={
                    "adapter_version": CP_SAT_ADAPTER_VERSION,
                    "model_version": DIRECT_BUSINESS_MODEL_VERSION,
                    "row_count_lower_bound": exc.row_count_lower_bound,
                    "maximum_group_table_rows": exc.maximum_group_table_rows,
                },
            )
        except BusinessCompilationInfeasible as exc:
            return _attempt(
                profile=profile,
                status=SolveStatus.INFEASIBLE,
                proof_code="CP_SAT_GROUP_KERNEL_SPACE_INFEASIBLE",
                proof_summary={
                    "adapter_version": CP_SAT_ADAPTER_VERSION,
                    "model_version": DIRECT_BUSINESS_MODEL_VERSION,
                    "group_key": list(exc.group_key),
                },
            )
        except BusinessCompilationError as exc:
            return _attempt(
                profile=profile,
                status=SolveStatus.FAILED,
                proof_code="CP_SAT_BUSINESS_COMPILATION_FAILED",
                proof_summary={"safe_error": str(exc)},
            )

        remaining = deadline - monotonic()
        if remaining <= 0:
            return _attempt(
                profile=profile,
                status=SolveStatus.INCOMPLETE,
                proof_code="CP_SAT_BUSINESS_COMPILATION_TIME_LIMIT",
            )
        solution = DeterministicCpSatAdapter(
            random_seed=self.random_seed,
            maximum_time_seconds=remaining,
        ).solve(compiled.problem)
        common_summary: dict[str, object] = {
            "reused_business_compilation": reused_compilation,
            "adapter_version": CP_SAT_ADAPTER_VERSION,
            "model_version": DIRECT_BUSINESS_MODEL_VERSION,
            "compiled_group_row_count": compiled.compiled_group_row_count,
            "solver_status": solution.solver_status,
        }
        if solution.status == SolveStatus.INFEASIBLE:
            return _attempt(
                profile=profile,
                status=SolveStatus.INFEASIBLE,
                proof_code="CP_SAT_DIRECT_BUSINESS_MODEL_INFEASIBLE",
                proof_summary=common_summary,
            )
        if solution.status != SolveStatus.OPTIMAL:
            return _attempt(
                profile=profile,
                status=solution.status,
                proof_code=solution.reason_code,
                proof_summary={
                    **common_summary,
                    "model_validation_error": solution.model_validation_error,
                },
            )

        raw_values = dict(solution.values)
        ordered_entries = tuple(sorted(context.entry_decisions))
        ordered_rates = tuple(sorted(context.rate_decisions))
        decision = DecisionVector(
            entry_values=tuple(
                (item.stable_id, raw_values[f"entry:{index:06d}"])
                for index, item in enumerate(ordered_entries)
            ),
            rate_values=tuple(
                (item.stable_id, raw_values[f"rate:{index:06d}"]) for index, item in enumerate(ordered_rates)
            ),
        )
        candidate = evaluator(decision)
        if not candidate.targets_closed:
            return _attempt(
                profile=profile,
                status=SolveStatus.FAILED,
                evaluated_state_count=1,
                proof_code="CP_SAT_BUSINESS_MODEL_RECOMPUTATION_MISMATCH",
                proof_summary={
                    **common_summary,
                    "validation_codes": list(candidate.validation_codes),
                    "target_variances": [
                        {"scope": scope, "variance_yuan": format(value, "f")}
                        for scope, value in candidate.target_variances_yuan
                    ],
                },
            )
        return _attempt(
            profile=profile,
            status=SolveStatus.OPTIMAL,
            candidates=(replace(candidate, scenario_types=(profile,)),),
            evaluated_state_count=1,
            proof_summary={
                **common_summary,
                "solver_objectives": dict(solution.objective_values),
            },
        )


def compile_entry_and_rates_problem(
    entries: tuple[EntryDecision, ...],
    rates: tuple[RateDecision, ...],
    *,
    fixed_total: int,
    track_directions: dict[str, str],
    entry_variable_ids: tuple[str, ...],
    rate_variable_ids: tuple[str, ...],
    forbidden_assignments: tuple[ForbiddenAssignmentSpec, ...],
) -> CpSatProblem:
    entry_problem = compile_entry_problem(
        entries,
        fixed_total=fixed_total,
        entry_variable_ids=entry_variable_ids,
        forbidden_assignments=(),
    )
    variables = list(entry_problem.variables)
    constraints = list(entry_problem.constraints)
    allowed_assignments: list[AllowedAssignmentsSpec] = []
    adjustment_terms: list[tuple[str, int]] = []
    adjustment_upper_bounds: list[int] = []

    eligible_groups = tuple(
        sorted(
            {(item.track, item.rate_type) for item in rates if _rate_is_adjustable(item, track_directions)}
        )
    )
    group_level_ids: dict[tuple[str, str], str] = {}
    group_maximum_levels: dict[tuple[str, str], int] = {}
    for group_index, group in enumerate(eligible_groups):
        track, rate_type = group
        direction = track_directions[track]
        group_rates = [
            item
            for item in rates
            if (item.track, item.rate_type) == group and _rate_is_adjustable(item, track_directions)
        ]
        maximum_delta = max(
            (
                item.upper_bound_ppm - item.current_value_ppm
                if direction == "UP"
                else item.current_value_ppm - item.lower_bound_ppm
            )
            for item in group_rates
        )
        maximum_level = maximum_delta // RATE_STEP_PPM
        level_id = f"rate-water-level:{group_index:06d}"
        group_level_ids[group] = level_id
        group_maximum_levels[group] = maximum_level
        variables.append(IntegerVariableSpec(level_id, 0, maximum_level))

    for index, (item, variable_id) in enumerate(zip(rates, rate_variable_ids, strict=True)):
        group = item.track, item.rate_type
        if _rate_is_adjustable(item, track_directions):
            lower = item.lower_bound_ppm
            upper = item.upper_bound_ppm
            level_id = group_level_ids[group]
            maximum_level = group_maximum_levels[group]
            direction = track_directions[item.track]
            rows = tuple(
                (
                    level,
                    (
                        min(
                            item.current_value_ppm + level * RATE_STEP_PPM,
                            item.upper_bound_ppm,
                        )
                        if direction == "UP"
                        else max(
                            item.current_value_ppm - level * RATE_STEP_PPM,
                            item.lower_bound_ppm,
                        )
                    ),
                )
                for level in range(maximum_level + 1)
            )
            allowed_assignments.append(
                AllowedAssignmentsSpec(
                    stable_id=f"rate-water-table:{index:06d}",
                    variable_ids=(level_id, variable_id),
                    rows=rows,
                )
            )
        else:
            lower = upper = item.current_value_ppm
        variables.append(IntegerVariableSpec(variable_id, lower, upper))

        adjustment_id = f"rate-adjustment:{index:06d}"
        adjustment_upper = max(
            abs(lower - item.current_value_ppm),
            abs(upper - item.current_value_ppm),
        )
        adjustment_upper_bounds.append(adjustment_upper)
        adjustment_terms.append((adjustment_id, 1))
        variables.append(IntegerVariableSpec(adjustment_id, 0, adjustment_upper))
        constraints.extend(
            (
                LinearConstraintSpec(
                    f"rate-adjustment-positive:{index:06d}",
                    LinearExpressionSpec(((adjustment_id, 1), (variable_id, -1))),
                    lower_bound=-item.current_value_ppm,
                ),
                LinearConstraintSpec(
                    f"rate-adjustment-negative:{index:06d}",
                    LinearExpressionSpec(((adjustment_id, 1), (variable_id, 1))),
                    lower_bound=item.current_value_ppm,
                ),
            )
        )

    max_adjustment_id = "maximum-rate-adjustment"
    variables.append(
        IntegerVariableSpec(
            max_adjustment_id,
            0,
            max(adjustment_upper_bounds, default=0),
        )
    )
    for index, _ in enumerate(rates):
        constraints.append(
            LinearConstraintSpec(
                f"maximum-rate-adjustment:{index:06d}",
                LinearExpressionSpec(
                    (
                        (max_adjustment_id, 1),
                        (f"rate-adjustment:{index:06d}", -1),
                    )
                ),
                lower_bound=0,
            )
        )

    return CpSatProblem(
        variables=tuple(variables),
        constraints=tuple(constraints),
        objective_tiers=(
            ObjectiveTierSpec(
                "maximum-rate-adjustment",
                LinearExpressionSpec(((max_adjustment_id, 1),)),
            ),
            ObjectiveTierSpec(
                "total-rate-adjustment",
                LinearExpressionSpec(tuple(adjustment_terms)),
            ),
            *entry_problem.objective_tiers,
        ),
        allowed_assignments=tuple(allowed_assignments),
        forbidden_assignments=forbidden_assignments,
        stable_tiebreak_variable_ids=(*entry_variable_ids, *rate_variable_ids),
    )


def _rate_is_adjustable(item: RateDecision, track_directions: dict[str, str]) -> bool:
    return (
        item.track in track_directions
        and not item.locked
        and item.boundary_confirmed
        and not item.quantitative_new_student
    )


def _validate_inputs(
    entries: Sequence[EntryDecision],
    rates: Sequence[RateDecision],
    fixed_total: int,
    track_directions: dict[str, str],
    profile: ScenarioProfile,
) -> str | None:
    if not entries:
        return "at least one entry decision is required"
    if fixed_total < 0:
        return "fixed entry total must be non-negative"
    if profile not in {
        ScenarioProfile.BALANCED,
        ScenarioProfile.RETENTION_WEIGHTED,
        ScenarioProfile.ACQUISITION_WEIGHTED,
    }:
        return "entry-and-rates profile is invalid"
    if any(direction not in {"UP", "DOWN"} for direction in track_directions.values()):
        return "track direction must be UP or DOWN"
    entry_ids = [item.stable_id for item in entries]
    rate_ids = [item.stable_id for item in rates]
    if len(entry_ids) != len(set(entry_ids)) or any(not item for item in entry_ids):
        return "entry stable IDs must be non-empty and unique"
    if len(rate_ids) != len(set(rate_ids)) or any(not item for item in rate_ids):
        return "rate stable IDs must be non-empty and unique"
    if any(
        item.current_value < 0
        or item.hard_lower_bound < 0
        or item.hard_upper_bound < item.hard_lower_bound
        or not item.hard_lower_bound <= item.current_value <= item.hard_upper_bound
        for item in entries
    ):
        return "entry values or hard boundaries are invalid"
    if any(
        item.current_value_ppm % RATE_STEP_PPM
        or item.lower_bound_ppm % RATE_STEP_PPM
        or item.upper_bound_ppm % RATE_STEP_PPM
        or item.lower_bound_ppm > item.upper_bound_ppm
        or not item.lower_bound_ppm <= item.current_value_ppm <= item.upper_bound_ppm
        for item in rates
    ):
        return "rate values or confirmed boundaries are invalid"
    return None


def _attempt(
    *,
    profile: ScenarioProfile,
    status: SolveStatus,
    candidates: tuple[CandidateEvaluation, ...] = (),
    evaluated_state_count: int = 0,
    proof_code: str | None = None,
    proof_summary: dict[str, object] | None = None,
) -> SolveAttempt:
    return SolveAttempt(
        stage=OptimizationStage.ENTRY_AND_RATES,
        profile=profile,
        status=status,
        candidates=candidates,
        evaluated_state_count=evaluated_state_count,
        proof_code=proof_code,
        proof_summary=proof_summary,
    )


def _incomplete_attempt(
    profile: ScenarioProfile,
    proof_code: str,
    evaluated_count: int,
    *,
    excluded_count: int,
    feasible_count: int,
) -> SolveAttempt:
    return _attempt(
        profile=profile,
        status=SolveStatus.INCOMPLETE,
        evaluated_state_count=evaluated_count,
        proof_code=proof_code,
        proof_summary={
            "adapter_version": CP_SAT_ADAPTER_VERSION,
            "model_version": ENTRY_AND_RATES_CP_SAT_MODEL_VERSION,
            "excluded_decision_count": excluded_count,
            "feasible_candidate_count": feasible_count,
        },
    )
