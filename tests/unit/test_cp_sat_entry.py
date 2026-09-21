from decimal import Decimal

from packages.optimization.cp_sat_entry import CpSatEntryOnlySolver
from packages.optimization.engine import (
    CandidateEvaluation,
    DecisionVector,
    EntryDecision,
    RateDecision,
    ScenarioProfile,
    SolveStatus,
)


def _entries() -> tuple[EntryDecision, ...]:
    return (
        EntryDecision("a", 2, 0, 4),
        EntryDecision("b", 2, 0, 4),
    )


def _rates() -> tuple[RateDecision, ...]:
    return (
        RateDecision(
            stable_id="renewal-a",
            track="智慧空间",
            rate_type="RENEWAL_RATE",
            current_value_ppm=800_000,
            lower_bound_ppm=700_000,
            upper_bound_ppm=900_000,
        ),
    )


def _evaluation(
    decision: DecisionVector, *, accepted_entry_values: tuple[tuple[str, int], ...] | None
) -> CandidateEvaluation:
    accepted = decision.entry_values == accepted_entry_values
    current = {"a": 2, "b": 2}
    return CandidateEvaluation(
        decision=decision,
        target_variances_yuan=(("DEPARTMENT:", Decimal(0 if accepted else 20_000)),),
        hard_constraints_satisfied=True,
        entry_adjustments=tuple(value - current[key] for key, value in decision.entry_values),
    )


def test_entry_only_cp_sat_rejects_kernel_failures_and_returns_next_exact_optimum() -> None:
    seen: list[tuple[tuple[str, int], ...]] = []

    def evaluator(decision: DecisionVector) -> CandidateEvaluation:
        seen.append(decision.entry_values)
        return _evaluation(decision, accepted_entry_values=(("a", 1), ("b", 3)))

    result = CpSatEntryOnlySolver(maximum_time_seconds=5).solve(
        entries=_entries(), rates=_rates(), fixed_total=4, evaluator=evaluator
    )

    assert result.status == SolveStatus.OPTIMAL
    assert result.evaluated_state_count == 2
    assert seen == [(("a", 2), ("b", 2)), (("a", 1), ("b", 3))]
    assert result.candidates[0].decision.rate_values == (("renewal-a", 800_000),)
    assert result.candidates[0].scenario_types == (ScenarioProfile.ENTRY_STRUCTURE,)


def test_entry_only_cp_sat_only_reports_infeasible_after_lazy_space_exhaustion() -> None:
    seen: list[tuple[tuple[str, int], ...]] = []

    def evaluator(decision: DecisionVector) -> CandidateEvaluation:
        seen.append(decision.entry_values)
        return _evaluation(decision, accepted_entry_values=None)

    result = CpSatEntryOnlySolver(maximum_time_seconds=5).solve(
        entries=_entries(),
        rates=_rates(),
        fixed_total=4,
        evaluator=evaluator,
    )

    assert result.status == SolveStatus.INFEASIBLE
    assert result.proof_code == "CP_SAT_LAZY_VALIDATION_SPACE_EXHAUSTED"
    assert result.evaluated_state_count == 5
    assert result.proof_summary is not None
    assert result.proof_summary["rejected_candidate_count"] == 5
    assert seen == [
        (("a", 2), ("b", 2)),
        (("a", 1), ("b", 3)),
        (("a", 3), ("b", 1)),
        (("a", 0), ("b", 4)),
        (("a", 4), ("b", 0)),
    ]


def test_entry_only_cp_sat_validation_cap_is_incomplete_not_infeasible() -> None:
    result = CpSatEntryOnlySolver(maximum_time_seconds=5, maximum_candidate_validations=1).solve(
        entries=_entries(),
        rates=_rates(),
        fixed_total=4,
        evaluator=lambda decision: _evaluation(decision, accepted_entry_values=None),
    )

    assert result.status == SolveStatus.INCOMPLETE
    assert result.proof_code == "CP_SAT_CANDIDATE_VALIDATION_LIMIT_EXCEEDED"
    assert result.evaluated_state_count == 1


def test_entry_only_cp_sat_honors_non_candidate_values() -> None:
    entries = (
        EntryDecision("a", 2, 0, 4, candidate_eligible=False),
        EntryDecision("b", 2, 0, 4),
    )
    result = CpSatEntryOnlySolver(maximum_time_seconds=5).solve(
        entries=entries,
        rates=_rates(),
        fixed_total=4,
        evaluator=lambda decision: _evaluation(decision, accepted_entry_values=(("a", 2), ("b", 2))),
    )

    assert result.status == SolveStatus.OPTIMAL
    assert result.candidates[0].decision.entry_values == (("a", 2), ("b", 2))


def test_entry_only_cp_sat_proves_structural_fixed_total_infeasible_without_evaluation() -> None:
    called = False

    def evaluator(decision: DecisionVector) -> CandidateEvaluation:
        nonlocal called
        called = True
        return _evaluation(decision, accepted_entry_values=decision.entry_values)

    result = CpSatEntryOnlySolver(maximum_time_seconds=5).solve(
        entries=(
            EntryDecision("a", 2, 2, 4),
            EntryDecision("b", 2, 2, 4),
        ),
        rates=_rates(),
        fixed_total=3,
        evaluator=evaluator,
    )

    assert result.status == SolveStatus.INFEASIBLE
    assert result.proof_code == "CP_SAT_ENTRY_MODEL_INFEASIBLE"
    assert result.evaluated_state_count == 0
    assert called is False


def test_entry_only_cp_sat_invalid_decision_configuration_is_failed() -> None:
    result = CpSatEntryOnlySolver(maximum_time_seconds=5).solve(
        entries=(EntryDecision("a", 5, 0, 4),),
        rates=_rates(),
        fixed_total=5,
        evaluator=lambda decision: _evaluation(decision, accepted_entry_values=decision.entry_values),
    )

    assert result.status == SolveStatus.FAILED
    assert result.proof_code == "ENTRY_MODEL_INVALID"
