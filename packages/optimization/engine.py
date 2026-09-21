from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from packages.domain.versioning.hashing import content_hash

OPTIMIZATION_MODEL_VERSION = "phase1-deterministic-rebalancing-v3"
RATE_STEP_PPM = 100
DISCOUNT_STEP_PPM = 1_000
TARGET_TOLERANCE_YUAN = Decimal("10000.000000")


class OptimizationStage(StrEnum):
    ENTRY_ONLY = "ENTRY_ONLY"
    ENTRY_AND_RATES = "ENTRY_AND_RATES"
    ENTRY_RATES_AND_DISCOUNT = "ENTRY_RATES_AND_DISCOUNT"


class SolveStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


class OptimizationSolverAdapter(StrEnum):
    EXACT_ENUMERATION = "EXACT_ENUMERATION"
    CP_SAT_ENTRY_ONLY = "CP_SAT_ENTRY_ONLY"
    CP_SAT_ENTRY_AND_RATES = "CP_SAT_ENTRY_AND_RATES"


class ScenarioProfile(StrEnum):
    ENTRY_STRUCTURE = "ENTRY_STRUCTURE"
    BALANCED = "BALANCED"
    RETENTION_WEIGHTED = "RETENTION_WEIGHTED"
    ACQUISITION_WEIGHTED = "ACQUISITION_WEIGHTED"


CONTRIBUTION_PROFILES: dict[ScenarioProfile, tuple[int, int]] = {
    ScenarioProfile.BALANCED: (50, 50),
    ScenarioProfile.RETENTION_WEIGHTED: (70, 30),
    ScenarioProfile.ACQUISITION_WEIGHTED: (30, 70),
}


@dataclass(frozen=True, order=True)
class RateDecision:
    stable_id: str
    track: str
    rate_type: str
    current_value_ppm: int
    lower_bound_ppm: int
    upper_bound_ppm: int
    locked: bool = False
    boundary_confirmed: bool = True
    quantitative_new_student: bool = False


@dataclass(frozen=True, order=True)
class EntryDecision:
    stable_id: str
    current_value: int
    hard_lower_bound: int
    hard_upper_bound: int
    candidate_eligible: bool = True


@dataclass(frozen=True, order=True)
class DiscountDecision:
    stable_id: str
    fiscal_season: int
    track: str
    product: str
    grade: str
    current_value_ppm: int
    lower_bound_ppm: int = 0
    upper_bound_ppm: int = 1_000_000
    locked: bool = False


@dataclass(frozen=True)
class DecisionVector:
    entry_values: tuple[tuple[str, int], ...] = ()
    rate_values: tuple[tuple[str, int], ...] = ()
    discount_values: tuple[tuple[str, int], ...] = ()

    def canonical(self) -> DecisionVector:
        return DecisionVector(
            entry_values=tuple(sorted(self.entry_values)),
            rate_values=tuple(sorted(self.rate_values)),
            discount_values=tuple(sorted(self.discount_values)),
        )


@dataclass(frozen=True)
class CandidateEvaluation:
    decision: DecisionVector
    target_variances_yuan: tuple[tuple[str, Decimal], ...]
    hard_constraints_satisfied: bool
    headcount_signature: tuple[tuple[str, int], ...] = ()
    revenue_r00_yuan: Decimal = Decimal(0)
    revenue_r10_yuan: Decimal = Decimal(0)
    revenue_r01_yuan: Decimal = Decimal(0)
    revenue_r11_yuan: Decimal = Decimal(0)
    validation_codes: tuple[str, ...] = ()
    scenario_types: tuple[ScenarioProfile, ...] = ()
    entry_soft_bound_violation: int = 0
    entry_adjustments: tuple[int, ...] = ()
    rate_adjustments_ppm: tuple[int, ...] = ()
    discount_adjustments_ppm: tuple[int, ...] = ()

    @property
    def targets_closed(self) -> bool:
        return self.hard_constraints_satisfied and all(
            abs(variance) <= TARGET_TOLERANCE_YUAN for _, variance in self.target_variances_yuan
        )

    @property
    def result_hash(self) -> str:
        decision = self.decision.canonical()
        return content_hash(
            {
                "model_version": OPTIMIZATION_MODEL_VERSION,
                "entry_values": decision.entry_values,
                "rate_values": decision.rate_values,
                "discount_values": decision.discount_values,
                "headcounts": tuple(sorted(self.headcount_signature)),
            }
        )


@dataclass(frozen=True)
class SolveAttempt:
    stage: OptimizationStage
    profile: ScenarioProfile
    status: SolveStatus
    candidates: tuple[CandidateEvaluation, ...] = ()
    evaluated_state_count: int = 0
    proof_code: str | None = None
    proof_summary: dict[str, object] | None = None


@dataclass(frozen=True)
class OptimizationOutcome:
    status: SolveStatus
    terminal_stage: OptimizationStage
    candidates: tuple[CandidateEvaluation, ...]
    attempts: tuple[SolveAttempt, ...]
    discount_unlock_required: bool
    reason_code: str | None = None


Evaluator = Callable[[DecisionVector], CandidateEvaluation]
StageSolver = Callable[[OptimizationStage, ScenarioProfile], SolveAttempt]


def apply_equal_percentage_point_water_level(
    rates: Sequence[RateDecision],
    *,
    track: str,
    rate_type: str,
    direction: str,
    water_level_steps: int,
) -> tuple[tuple[str, int], ...]:
    """Apply one common 100 ppm water level; boundary hits naturally leave the pool."""
    if direction not in {"UP", "DOWN"}:
        raise ValueError("direction must be UP or DOWN")
    if water_level_steps < 0:
        raise ValueError("water_level_steps must be non-negative")
    delta = water_level_steps * RATE_STEP_PPM
    result: list[tuple[str, int]] = []
    for item in sorted(rates):
        if item.current_value_ppm % RATE_STEP_PPM:
            raise ValueError("rate values must use the exact 100 ppm step")
        if item.lower_bound_ppm % RATE_STEP_PPM or item.upper_bound_ppm % RATE_STEP_PPM:
            raise ValueError("rate boundaries must use the exact 100 ppm step")
        if not item.lower_bound_ppm <= item.current_value_ppm <= item.upper_bound_ppm:
            raise ValueError("rate value must be inside its confirmed boundaries")
        if item.track != track or item.rate_type != rate_type:
            continue
        if item.locked or not item.boundary_confirmed or item.quantitative_new_student:
            continue
        if direction == "UP":
            value = min(item.current_value_ppm + delta, item.upper_bound_ppm)
        else:
            value = max(item.current_value_ppm - delta, item.lower_bound_ppm)
        result.append((item.stable_id, value))
    return tuple(result)


def symmetric_rate_contributions(
    *, r00: Decimal, r10: Decimal, r01: Decimal, r11: Decimal
) -> tuple[Decimal, Decimal]:
    retention = ((r10 - r00) + (r11 - r01)) / Decimal(2)
    new_share = ((r01 - r00) + (r11 - r10)) / Decimal(2)
    if retention + new_share != r11 - r00:
        raise ArithmeticError("symmetric contribution attribution lost exact additivity")
    return retention, new_share


class ExactEnumerationAdapter:
    """Reference exact adapter for bounded models and exhaustive test oracles.

    It never labels a truncated search as infeasible. A scalable CP-SAT adapter can
    replace this class without changing stage orchestration or candidate semantics.
    """

    def __init__(self, *, maximum_states: int = 100_000) -> None:
        if maximum_states <= 0:
            raise ValueError("maximum_states must be positive")
        self.maximum_states = maximum_states

    def solve(
        self,
        *,
        stage: OptimizationStage,
        profile: ScenarioProfile,
        decisions: Iterable[DecisionVector],
        evaluator: Evaluator,
    ) -> SolveAttempt:
        evaluations: list[CandidateEvaluation] = []
        for index, raw_item in enumerate(decisions):
            item = raw_item.canonical()
            if index >= self.maximum_states:
                return SolveAttempt(
                    stage=stage,
                    profile=profile,
                    status=SolveStatus.INCOMPLETE,
                    evaluated_state_count=len(evaluations),
                    proof_code="SEARCH_SPACE_LIMIT_EXCEEDED",
                    proof_summary={
                        "state_count_lower_bound": index + 1,
                        "maximum_states": self.maximum_states,
                    },
                )
            evaluations.append(evaluator(item))
        feasible = [item for item in evaluations if item.targets_closed]
        if not feasible:
            return SolveAttempt(
                stage=stage,
                profile=profile,
                status=SolveStatus.INFEASIBLE,
                evaluated_state_count=len(evaluations),
                proof_code="EXHAUSTIVE_STATE_SPACE_PROVEN",
                proof_summary={"state_count": len(evaluations)},
            )
        winner = min(feasible, key=lambda item: candidate_rank(item, profile))
        return SolveAttempt(
            stage=stage,
            profile=profile,
            status=SolveStatus.OPTIMAL,
            candidates=(replace(winner, scenario_types=(profile,)),),
            evaluated_state_count=len(evaluations),
        )


def enumerate_fixed_total_entry_structures(
    entries: Sequence[EntryDecision], *, fixed_total: int
) -> Iterator[DecisionVector]:
    """Enumerate the complete stable-ID ordered integer space under a fixed total."""
    ordered = tuple(sorted(entries))
    if fixed_total < 0:
        raise ValueError("fixed_total must be non-negative")
    if any(
        item.current_value < 0 or item.hard_lower_bound < 0 or item.hard_upper_bound < item.hard_lower_bound
        for item in ordered
    ):
        raise ValueError("entry values and hard boundaries are invalid")
    fixed_sum = sum(item.current_value for item in ordered if not item.candidate_eligible)
    adjustable = tuple(item for item in ordered if item.candidate_eligible)
    remaining = fixed_total - fixed_sum
    if not adjustable:
        if remaining == 0:
            yield DecisionVector(entry_values=tuple((item.stable_id, item.current_value) for item in ordered))
        return
    suffix_min = [0] * (len(adjustable) + 1)
    suffix_max = [0] * (len(adjustable) + 1)
    for index in range(len(adjustable) - 1, -1, -1):
        suffix_min[index] = suffix_min[index + 1] + adjustable[index].hard_lower_bound
        suffix_max[index] = suffix_max[index + 1] + adjustable[index].hard_upper_bound
    if remaining < suffix_min[0] or remaining > suffix_max[0]:
        return
    fixed_values = {item.stable_id: item.current_value for item in ordered if not item.candidate_eligible}

    def walk(index: int, residual: int, chosen: list[tuple[str, int]]) -> Iterator[DecisionVector]:
        item = adjustable[index]
        low = max(item.hard_lower_bound, residual - suffix_max[index + 1])
        high = min(item.hard_upper_bound, residual - suffix_min[index + 1])
        for value in range(low, high + 1):
            next_chosen = [*chosen, (item.stable_id, value)]
            if index + 1 == len(adjustable):
                if value == residual:
                    all_values = {**fixed_values, **dict(next_chosen)}
                    yield DecisionVector(entry_values=tuple(sorted(all_values.items())))
            else:
                yield from walk(index + 1, residual - value, next_chosen)

    yield from walk(0, remaining, [])


def enumerate_parallel_rate_water_levels(
    entry_structures: Iterable[DecisionVector],
    *,
    rates: Sequence[RateDecision],
    track_directions: dict[str, str],
) -> Iterator[DecisionVector]:
    """Cross entry structures with independent track/family water levels.

    Renewal and new-student share are parallel dimensions. Within one dimension,
    every eligible row receives the same added percentage points until its own
    confirmed boundary clips it.
    """
    ordered_rates = tuple(sorted(rates))
    for direction in track_directions.values():
        if direction not in {"UP", "DOWN"}:
            raise ValueError("track direction must be UP or DOWN")
    groups = tuple(
        sorted(
            {
                (item.track, item.rate_type)
                for item in ordered_rates
                if item.track in track_directions
                and not item.locked
                and item.boundary_confirmed
                and not item.quantitative_new_student
            }
        )
    )
    level_ranges: list[range] = []
    for track, rate_type in groups:
        direction = track_directions[track]
        group_rates = [
            item
            for item in ordered_rates
            if item.track == track
            and item.rate_type == rate_type
            and not item.locked
            and item.boundary_confirmed
            and not item.quantitative_new_student
        ]
        maximum_delta = max(
            (
                item.upper_bound_ppm - item.current_value_ppm
                if direction == "UP"
                else item.current_value_ppm - item.lower_bound_ppm
            )
            for item in group_rates
        )
        level_ranges.append(range(maximum_delta // RATE_STEP_PPM + 1))

    def levels(index: int, chosen: list[int]) -> Iterator[tuple[int, ...]]:
        if index == len(level_ranges):
            yield tuple(chosen)
            return
        for value in level_ranges[index]:
            yield from levels(index + 1, [*chosen, value])

    for entry in entry_structures:
        for water_levels in levels(0, []):
            rate_values = {item.stable_id: item.current_value_ppm for item in ordered_rates}
            for (track, rate_type), water_level in zip(groups, water_levels, strict=True):
                rate_values.update(
                    apply_equal_percentage_point_water_level(
                        ordered_rates,
                        track=track,
                        rate_type=rate_type,
                        direction=track_directions[track],
                        water_level_steps=water_level,
                    )
                )
            yield DecisionVector(
                entry_values=entry.entry_values,
                rate_values=tuple(sorted(rate_values.items())),
            )


def run_staged_optimization(solver: StageSolver, *, discount_unlocked: bool = False) -> OptimizationOutcome:
    attempts: list[SolveAttempt] = []
    entry_attempt = _validated_attempt(
        solver(OptimizationStage.ENTRY_ONLY, ScenarioProfile.ENTRY_STRUCTURE),
        OptimizationStage.ENTRY_ONLY,
        ScenarioProfile.ENTRY_STRUCTURE,
    )
    attempts.append(entry_attempt)
    if entry_attempt.status == SolveStatus.OPTIMAL:
        return _optimal_outcome(attempts, entry_attempt.stage)
    if entry_attempt.status != SolveStatus.INFEASIBLE:
        return _stopped_outcome(attempts, entry_attempt)

    rate_attempts = [
        _validated_attempt(
            solver(OptimizationStage.ENTRY_AND_RATES, profile),
            OptimizationStage.ENTRY_AND_RATES,
            profile,
        )
        for profile in CONTRIBUTION_PROFILES
    ]
    attempts.extend(rate_attempts)
    if any(item.status == SolveStatus.OPTIMAL for item in rate_attempts):
        if any(item.status in {SolveStatus.INCOMPLETE, SolveStatus.FAILED} for item in rate_attempts):
            failing = next(
                item for item in rate_attempts if item.status in {SolveStatus.INCOMPLETE, SolveStatus.FAILED}
            )
            return _stopped_outcome(attempts, failing)
        return _optimal_outcome(attempts, OptimizationStage.ENTRY_AND_RATES)
    non_infeasible = next((item for item in rate_attempts if item.status != SolveStatus.INFEASIBLE), None)
    if non_infeasible is not None:
        return _stopped_outcome(attempts, non_infeasible)
    if not discount_unlocked:
        return OptimizationOutcome(
            status=SolveStatus.INFEASIBLE,
            terminal_stage=OptimizationStage.ENTRY_AND_RATES,
            candidates=(),
            attempts=tuple(attempts),
            discount_unlock_required=True,
            reason_code="DISCOUNT_UNLOCK_REQUIRED",
        )

    discount_attempts = [
        _validated_attempt(
            solver(OptimizationStage.ENTRY_RATES_AND_DISCOUNT, profile),
            OptimizationStage.ENTRY_RATES_AND_DISCOUNT,
            profile,
        )
        for profile in CONTRIBUTION_PROFILES
    ]
    attempts.extend(discount_attempts)
    if any(item.status == SolveStatus.OPTIMAL for item in discount_attempts):
        if any(item.status in {SolveStatus.INCOMPLETE, SolveStatus.FAILED} for item in discount_attempts):
            failing = next(
                item
                for item in discount_attempts
                if item.status in {SolveStatus.INCOMPLETE, SolveStatus.FAILED}
            )
            return _stopped_outcome(attempts, failing)
        return _optimal_outcome(attempts, OptimizationStage.ENTRY_RATES_AND_DISCOUNT)
    non_infeasible = next((item for item in discount_attempts if item.status != SolveStatus.INFEASIBLE), None)
    if non_infeasible is not None:
        return _stopped_outcome(attempts, non_infeasible)
    return OptimizationOutcome(
        status=SolveStatus.INFEASIBLE,
        terminal_stage=OptimizationStage.ENTRY_RATES_AND_DISCOUNT,
        candidates=(),
        attempts=tuple(attempts),
        discount_unlock_required=False,
        reason_code="ALL_CONFIRMED_BOUNDARIES_EXHAUSTED",
    )


def _validated_attempt(
    attempt: SolveAttempt, expected_stage: OptimizationStage, expected_profile: ScenarioProfile
) -> SolveAttempt:
    if attempt.stage != expected_stage or attempt.profile != expected_profile:
        raise ValueError("solver returned a result for a different stage or profile")
    if attempt.status == SolveStatus.OPTIMAL and not attempt.candidates:
        raise ValueError("OPTIMAL must include at least one verified candidate")
    if attempt.status != SolveStatus.OPTIMAL and attempt.candidates:
        raise ValueError("only OPTIMAL may expose candidates")
    if attempt.status == SolveStatus.INFEASIBLE and not attempt.proof_code:
        raise ValueError("INFEASIBLE must include an exact proof code")
    return attempt


def _optimal_outcome(
    attempts: Sequence[SolveAttempt], terminal_stage: OptimizationStage
) -> OptimizationOutcome:
    candidates = _deduplicate_candidates(
        replace(candidate, scenario_types=(attempt.profile,))
        for attempt in attempts
        if attempt.status == SolveStatus.OPTIMAL
        for candidate in attempt.candidates
    )
    return OptimizationOutcome(
        status=SolveStatus.OPTIMAL,
        terminal_stage=terminal_stage,
        candidates=candidates,
        attempts=tuple(attempts),
        discount_unlock_required=False,
    )


def _stopped_outcome(attempts: Sequence[SolveAttempt], attempt: SolveAttempt) -> OptimizationOutcome:
    return OptimizationOutcome(
        status=attempt.status,
        terminal_stage=attempt.stage,
        candidates=(),
        attempts=tuple(attempts),
        discount_unlock_required=False,
        reason_code=attempt.proof_code,
    )


def _deduplicate_candidates(
    candidates: Iterable[CandidateEvaluation],
) -> tuple[CandidateEvaluation, ...]:
    by_hash: dict[str, CandidateEvaluation] = {}
    for item in candidates:
        existing = by_hash.get(item.result_hash)
        labels = set(item.scenario_types)
        if existing is not None:
            labels.update(existing.scenario_types)
        by_hash[item.result_hash] = replace(
            existing or item,
            scenario_types=tuple(sorted(labels, key=str)),
        )
    return tuple(by_hash[key] for key in sorted(by_hash))


def candidate_rank(item: CandidateEvaluation, profile: ScenarioProfile) -> tuple[object, ...]:
    decision = item.decision.canonical()
    if profile in CONTRIBUTION_PROFILES:
        retention, new_share = symmetric_rate_contributions(
            r00=item.revenue_r00_yuan,
            r10=item.revenue_r10_yuan,
            r01=item.revenue_r01_yuan,
            r11=item.revenue_r11_yuan,
        )
        retention_weight, new_weight = CONTRIBUTION_PROFILES[profile]
        contribution_deviation: object = abs(
            retention * Decimal(new_weight) - new_share * Decimal(retention_weight)
        )
    else:
        contribution_deviation = Decimal(0)
    rate_adjustments = [abs(value) for value in item.rate_adjustments_ppm]
    entry_adjustments = [abs(value) for value in item.entry_adjustments]
    discount_adjustments = [abs(value) for value in item.discount_adjustments_ppm]
    return (
        contribution_deviation,
        max(rate_adjustments, default=0),
        sum(rate_adjustments),
        item.entry_soft_bound_violation,
        max(entry_adjustments, default=0),
        sum(entry_adjustments),
        max(discount_adjustments, default=0),
        sum(discount_adjustments),
        _decision_sort_key(decision),
    )


def _decision_sort_key(item: DecisionVector) -> tuple[object, ...]:
    canonical = item.canonical()
    return (canonical.entry_values, canonical.rate_values, canonical.discount_values)
