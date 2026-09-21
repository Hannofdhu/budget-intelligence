from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from time import monotonic

from packages.optimization.cp_sat_adapter import (
    CP_SAT_ADAPTER_VERSION,
    CpSatProblem,
    DeterministicCpSatAdapter,
    ForbiddenAssignmentSpec,
    IntegerVariableSpec,
    LinearConstraintSpec,
    LinearExpressionSpec,
    ObjectiveTierSpec,
)
from packages.optimization.engine import (
    CandidateEvaluation,
    DecisionVector,
    EntryDecision,
    OptimizationStage,
    RateDecision,
    ScenarioProfile,
    SolveAttempt,
    SolveStatus,
)

ENTRY_ONLY_CP_SAT_MODEL_VERSION = "entry-only-cp-sat-v1"

CandidateEvaluator = Callable[[DecisionVector], CandidateEvaluation]


class CpSatEntryOnlySolver:
    """Solve entry structure exactly, with full-kernel lazy candidate validation.

    The linear CP-SAT model owns the fixed total, hard boundaries, default
    80%-120% soft-bound pressure, adjustment pressure, and stable-ID tie-break.
    Non-linear business kernels remain authoritative: a structurally optimal
    candidate is recalculated and, when rejected, its exact entry assignment is
    forbidden before the next solve.
    """

    def __init__(
        self,
        *,
        maximum_time_seconds: float = 300,
        maximum_candidate_validations: int = 10_000,
        random_seed: int = 27,
    ) -> None:
        if maximum_time_seconds <= 0:
            raise ValueError("maximum_time_seconds must be positive")
        if maximum_candidate_validations <= 0:
            raise ValueError("maximum_candidate_validations must be positive")
        self.maximum_time_seconds = maximum_time_seconds
        self.maximum_candidate_validations = maximum_candidate_validations
        self.random_seed = random_seed

    def solve(
        self,
        *,
        entries: Sequence[EntryDecision],
        rates: Sequence[RateDecision],
        fixed_total: int,
        evaluator: CandidateEvaluator,
    ) -> SolveAttempt:
        validation_error = _validate_inputs(entries, rates, fixed_total)
        if validation_error is not None:
            return SolveAttempt(
                stage=OptimizationStage.ENTRY_ONLY,
                profile=ScenarioProfile.ENTRY_STRUCTURE,
                status=SolveStatus.FAILED,
                proof_code="ENTRY_MODEL_INVALID",
                proof_summary={"validation_error": validation_error},
            )

        ordered_entries = tuple(sorted(entries))
        ordered_rates = tuple(sorted(rates))
        entry_variable_ids = tuple(f"entry:{index:06d}" for index in range(len(ordered_entries)))
        baseline_rates = tuple((item.stable_id, item.current_value_ppm) for item in ordered_rates)
        rejected: list[ForbiddenAssignmentSpec] = []
        started = monotonic()
        evaluated_count = 0

        while True:
            remaining = self.maximum_time_seconds - (monotonic() - started)
            if remaining <= 0:
                return _incomplete_attempt(
                    "CP_SAT_ENTRY_TIME_LIMIT",
                    evaluated_count,
                    rejected_count=len(rejected),
                )
            problem = compile_entry_problem(
                ordered_entries,
                fixed_total=fixed_total,
                entry_variable_ids=entry_variable_ids,
                forbidden_assignments=tuple(rejected),
            )
            solution = DeterministicCpSatAdapter(
                random_seed=self.random_seed,
                maximum_time_seconds=remaining,
            ).solve(problem)
            if solution.status == SolveStatus.INFEASIBLE:
                proof_code = (
                    "CP_SAT_ENTRY_MODEL_INFEASIBLE"
                    if not rejected
                    else "CP_SAT_LAZY_VALIDATION_SPACE_EXHAUSTED"
                )
                return SolveAttempt(
                    stage=OptimizationStage.ENTRY_ONLY,
                    profile=ScenarioProfile.ENTRY_STRUCTURE,
                    status=SolveStatus.INFEASIBLE,
                    evaluated_state_count=evaluated_count,
                    proof_code=proof_code,
                    proof_summary={
                        "adapter_version": CP_SAT_ADAPTER_VERSION,
                        "model_version": ENTRY_ONLY_CP_SAT_MODEL_VERSION,
                        "rejected_candidate_count": len(rejected),
                        "solver_status": solution.solver_status,
                    },
                )
            if solution.status != SolveStatus.OPTIMAL:
                return SolveAttempt(
                    stage=OptimizationStage.ENTRY_ONLY,
                    profile=ScenarioProfile.ENTRY_STRUCTURE,
                    status=solution.status,
                    evaluated_state_count=evaluated_count,
                    proof_code=solution.reason_code,
                    proof_summary={
                        "adapter_version": CP_SAT_ADAPTER_VERSION,
                        "model_version": ENTRY_ONLY_CP_SAT_MODEL_VERSION,
                        "rejected_candidate_count": len(rejected),
                        "solver_status": solution.solver_status,
                        "model_validation_error": solution.model_validation_error,
                    },
                )

            raw_values = dict(solution.values)
            entry_values = tuple(
                (entry.stable_id, raw_values[variable_id])
                for entry, variable_id in zip(ordered_entries, entry_variable_ids, strict=True)
            )
            candidate = evaluator(
                DecisionVector(
                    entry_values=entry_values,
                    rate_values=baseline_rates,
                )
            )
            evaluated_count += 1
            if candidate.targets_closed:
                return SolveAttempt(
                    stage=OptimizationStage.ENTRY_ONLY,
                    profile=ScenarioProfile.ENTRY_STRUCTURE,
                    status=SolveStatus.OPTIMAL,
                    candidates=(
                        replace(
                            candidate,
                            scenario_types=(ScenarioProfile.ENTRY_STRUCTURE,),
                        ),
                    ),
                    evaluated_state_count=evaluated_count,
                    proof_summary={
                        "adapter_version": CP_SAT_ADAPTER_VERSION,
                        "model_version": ENTRY_ONLY_CP_SAT_MODEL_VERSION,
                        "rejected_candidate_count": len(rejected),
                        "solver_objectives": dict(solution.objective_values),
                    },
                )

            rejected.append(
                ForbiddenAssignmentSpec(
                    stable_id=f"rejected:{evaluated_count:08d}",
                    variable_ids=entry_variable_ids,
                    values=tuple(value for _, value in entry_values),
                )
            )
            if evaluated_count >= self.maximum_candidate_validations:
                return _incomplete_attempt(
                    "CP_SAT_CANDIDATE_VALIDATION_LIMIT_EXCEEDED",
                    evaluated_count,
                    rejected_count=len(rejected),
                )


def compile_entry_problem(
    entries: tuple[EntryDecision, ...],
    *,
    fixed_total: int,
    entry_variable_ids: tuple[str, ...],
    forbidden_assignments: tuple[ForbiddenAssignmentSpec, ...],
) -> CpSatProblem:
    variables: list[IntegerVariableSpec] = []
    constraints: list[LinearConstraintSpec] = []
    soft_terms: list[tuple[str, int]] = []
    adjustment_terms: list[tuple[str, int]] = []
    adjustment_upper_bounds: list[int] = []

    for index, (item, variable_id) in enumerate(zip(entries, entry_variable_ids, strict=True)):
        lower = item.hard_lower_bound if item.candidate_eligible else item.current_value
        upper = item.hard_upper_bound if item.candidate_eligible else item.current_value
        variables.append(IntegerVariableSpec(variable_id, lower, upper))

        adjustment_id = f"adjustment:{index:06d}"
        adjustment_upper = max(abs(lower - item.current_value), abs(upper - item.current_value))
        adjustment_upper_bounds.append(adjustment_upper)
        variables.append(IntegerVariableSpec(adjustment_id, 0, adjustment_upper))
        adjustment_terms.append((adjustment_id, 1))
        constraints.extend(
            (
                LinearConstraintSpec(
                    f"adjustment-positive:{index:06d}",
                    LinearExpressionSpec(((adjustment_id, 1), (variable_id, -1))),
                    lower_bound=-item.current_value,
                ),
                LinearConstraintSpec(
                    f"adjustment-negative:{index:06d}",
                    LinearExpressionSpec(((adjustment_id, 1), (variable_id, 1))),
                    lower_bound=item.current_value,
                ),
            )
        )

        if item.current_value > 0:
            soft_lower = (item.current_value * 80 + 99) // 100
            soft_upper = item.current_value * 120 // 100
            under_id = f"soft-under:{index:06d}"
            over_id = f"soft-over:{index:06d}"
            variables.extend(
                (
                    IntegerVariableSpec(under_id, 0, max(soft_lower - lower, 0)),
                    IntegerVariableSpec(over_id, 0, max(upper - soft_upper, 0)),
                )
            )
            soft_terms.extend(((under_id, 1), (over_id, 1)))
            constraints.extend(
                (
                    LinearConstraintSpec(
                        f"soft-lower:{index:06d}",
                        LinearExpressionSpec(((under_id, 1), (variable_id, 1))),
                        lower_bound=soft_lower,
                    ),
                    LinearConstraintSpec(
                        f"soft-upper:{index:06d}",
                        LinearExpressionSpec(((over_id, 1), (variable_id, -1))),
                        lower_bound=-soft_upper,
                    ),
                )
            )

    max_adjustment_id = "maximum-adjustment"
    maximum_adjustment_upper = max(adjustment_upper_bounds, default=0)
    variables.append(IntegerVariableSpec(max_adjustment_id, 0, maximum_adjustment_upper))
    for index, _ in enumerate(entries):
        constraints.append(
            LinearConstraintSpec(
                f"maximum-adjustment:{index:06d}",
                LinearExpressionSpec(((max_adjustment_id, 1), (f"adjustment:{index:06d}", -1))),
                lower_bound=0,
            )
        )
    constraints.append(
        LinearConstraintSpec(
            "fixed-entry-total",
            LinearExpressionSpec(tuple((item, 1) for item in entry_variable_ids)),
            lower_bound=fixed_total,
            upper_bound=fixed_total,
        )
    )
    return CpSatProblem(
        variables=tuple(variables),
        constraints=tuple(constraints),
        objective_tiers=(
            ObjectiveTierSpec("entry-soft-bound-violation", LinearExpressionSpec(tuple(soft_terms))),
            ObjectiveTierSpec(
                "maximum-entry-adjustment",
                LinearExpressionSpec(((max_adjustment_id, 1),)),
            ),
            ObjectiveTierSpec(
                "total-entry-adjustment",
                LinearExpressionSpec(tuple(adjustment_terms)),
            ),
        ),
        forbidden_assignments=forbidden_assignments,
        stable_tiebreak_variable_ids=entry_variable_ids,
    )


def _validate_inputs(
    entries: Sequence[EntryDecision],
    rates: Sequence[RateDecision],
    fixed_total: int,
) -> str | None:
    if not entries:
        return "at least one entry decision is required"
    if fixed_total < 0:
        return "fixed entry total must be non-negative"
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
    return None


def _incomplete_attempt(
    proof_code: str,
    evaluated_count: int,
    *,
    rejected_count: int,
) -> SolveAttempt:
    return SolveAttempt(
        stage=OptimizationStage.ENTRY_ONLY,
        profile=ScenarioProfile.ENTRY_STRUCTURE,
        status=SolveStatus.INCOMPLETE,
        evaluated_state_count=evaluated_count,
        proof_code=proof_code,
        proof_summary={
            "adapter_version": CP_SAT_ADAPTER_VERSION,
            "model_version": ENTRY_ONLY_CP_SAT_MODEL_VERSION,
            "rejected_candidate_count": rejected_count,
        },
    )
