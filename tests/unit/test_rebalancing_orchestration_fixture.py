"""Simulated stage-orchestration fixture, not a real end-to-end acceptance test."""

import json
from decimal import Decimal
from pathlib import Path

from packages.optimization.engine import (
    CandidateEvaluation,
    DecisionVector,
    OptimizationStage,
    ScenarioProfile,
    SolveAttempt,
    SolveStatus,
    run_staged_optimization,
)
from packages.persistence.scenario_application import (
    REVERT_EXCLUDED_COMPONENTS,
    _restorable_source_components,
)

FIXTURE = Path(__file__).parents[1] / "golden" / "fy27" / "rebalancing_end_to_end.json"


def _candidate(rows: dict[str, list[list[object]]]) -> CandidateEvaluation:
    return CandidateEvaluation(
        decision=DecisionVector(
            entry_values=tuple((str(key), int(str(value))) for key, value in rows["entry_values"]),
            discount_values=tuple((str(key), int(str(value))) for key, value in rows["discount_values"]),
        ),
        target_variances_yuan=(("DEPARTMENT", Decimal("0")),),
        hard_constraints_satisfied=True,
        headcount_signature=(("department", 100),),
    )


def test_simulated_orchestration_fixture_covers_unlock_dedup_and_restore_helper() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    candidates = fixture["candidate_decisions"]

    def solver(stage: OptimizationStage, profile: ScenarioProfile) -> SolveAttempt:
        if stage != OptimizationStage.ENTRY_RATES_AND_DISCOUNT:
            return SolveAttempt(stage, profile, SolveStatus.INFEASIBLE, proof_code="EXACT_SPACE_PROVEN")
        selected = (
            candidates["retention"] if profile == ScenarioProfile.RETENTION_WEIGHTED else candidates["shared"]
        )
        return SolveAttempt(stage, profile, SolveStatus.OPTIMAL, candidates=(_candidate(selected),))

    locked = run_staged_optimization(solver)
    expected_locked = fixture["expected"]["before_discount_unlock"]
    assert locked.status == expected_locked["status"]
    assert locked.terminal_stage == expected_locked["terminal_stage"]
    assert locked.reason_code == expected_locked["reason_code"]
    assert len(locked.candidates) == expected_locked["candidate_count"]
    assert locked.discount_unlock_required is expected_locked["discount_unlock_required"]

    unlocked = run_staged_optimization(solver, discount_unlocked=True)
    expected_unlocked = fixture["expected"]["after_discount_unlock"]
    assert unlocked.status == expected_unlocked["status"]
    assert unlocked.terminal_stage == expected_unlocked["terminal_stage"]
    assert len(unlocked.candidates) == expected_unlocked["candidate_count"]
    actual_groups = sorted(
        sorted(profile.value for profile in item.scenario_types) for item in unlocked.candidates
    )
    assert actual_groups == sorted(expected_unlocked["profile_groups"])

    restored = _restorable_source_components(fixture["source_component_manifest"])
    expected_revert = fixture["expected"]["revert"]
    assert sorted(restored) == expected_revert["restored_component_keys"]
    assert sorted(REVERT_EXCLUDED_COMPONENTS) == expected_revert["invalidated_result_keys"]
