from decimal import Decimal

from packages.optimization.engine import (
    CandidateEvaluation,
    DecisionVector,
    EntryDecision,
    ExactEnumerationAdapter,
    OptimizationStage,
    RateDecision,
    ScenarioProfile,
    SolveAttempt,
    SolveStatus,
    apply_equal_percentage_point_water_level,
    enumerate_fixed_total_entry_structures,
    enumerate_parallel_rate_water_levels,
    run_staged_optimization,
    symmetric_rate_contributions,
)


def _candidate(value: int, variance: str = "0") -> CandidateEvaluation:
    return CandidateEvaluation(
        decision=DecisionVector(entry_values=(("entry-a", value),)),
        target_variances_yuan=(("DEPARTMENT", Decimal(variance)),),
        hard_constraints_satisfied=True,
        headcount_signature=(("row-a", value),),
    )


def test_equal_percentage_point_water_level_uses_100_ppm_and_boundary_exit() -> None:
    rates = [
        RateDecision("a", "A", "RENEWAL_RATE", 700_000, 690_000, 700_200),
        RateDecision("b", "A", "RENEWAL_RATE", 800_000, 790_000, 801_000),
        RateDecision("locked", "A", "RENEWAL_RATE", 600_000, 500_000, 700_000, locked=True),
    ]
    assert apply_equal_percentage_point_water_level(
        rates,
        track="A",
        rate_type="RENEWAL_RATE",
        direction="UP",
        water_level_steps=5,
    ) == (("a", 700_200), ("b", 800_500))


def test_symmetric_contribution_is_exact_and_order_neutral() -> None:
    retention, new_share = symmetric_rate_contributions(
        r00=Decimal("100"),
        r10=Decimal("130"),
        r01=Decimal("120"),
        r11=Decimal("160"),
    )
    assert retention == Decimal("35")
    assert new_share == Decimal("25")
    assert retention + new_share == Decimal("60")


def test_entry_structure_enumeration_keeps_total_and_hard_boundaries() -> None:
    decisions = list(
        enumerate_fixed_total_entry_structures(
            [
                EntryDecision("a", 2, 1, 3),
                EntryDecision("b", 2, 1, 3),
                EntryDecision("fixed-zero", 0, 0, 0, candidate_eligible=False),
            ],
            fixed_total=4,
        )
    )
    assert [item.entry_values for item in decisions] == [
        (("a", 1), ("b", 3), ("fixed-zero", 0)),
        (("a", 2), ("b", 2), ("fixed-zero", 0)),
        (("a", 3), ("b", 1), ("fixed-zero", 0)),
    ]


def test_parallel_rate_water_levels_keep_families_parallel_and_deterministic() -> None:
    rates = [
        RateDecision("renewal-a", "A", "RENEWAL_RATE", 700_000, 699_800, 700_200),
        RateDecision("share-a", "A", "NEW_STUDENT_SHARE", 300_000, 299_900, 300_100),
    ]
    decisions = list(
        enumerate_parallel_rate_water_levels(
            [DecisionVector(entry_values=(("entry-a", 10),))],
            rates=rates,
            track_directions={"A": "UP"},
        )
    )
    assert len(decisions) == 6
    assert decisions[0].rate_values == (("renewal-a", 700_000), ("share-a", 300_000))
    assert decisions[-1].rate_values == (("renewal-a", 700_200), ("share-a", 300_100))


def test_exact_enumerator_never_calls_truncation_infeasible() -> None:
    adapter = ExactEnumerationAdapter(maximum_states=1)
    attempt = adapter.solve(
        stage=OptimizationStage.ENTRY_ONLY,
        profile=ScenarioProfile.ENTRY_STRUCTURE,
        decisions=[DecisionVector(entry_values=(("a", 1),)), DecisionVector(entry_values=(("a", 2),))],
        evaluator=lambda item: _candidate(dict(item.entry_values)["a"], "20000"),
    )
    assert attempt.status == SolveStatus.INCOMPLETE
    assert attempt.proof_code == "SEARCH_SPACE_LIMIT_EXCEEDED"


def test_exact_enumerator_proves_small_model_infeasible_only_after_exhaustion() -> None:
    adapter = ExactEnumerationAdapter(maximum_states=10)
    attempt = adapter.solve(
        stage=OptimizationStage.ENTRY_ONLY,
        profile=ScenarioProfile.ENTRY_STRUCTURE,
        decisions=[DecisionVector(entry_values=(("a", 1),)), DecisionVector(entry_values=(("a", 2),))],
        evaluator=lambda item: _candidate(dict(item.entry_values)["a"], "10000.01"),
    )
    assert attempt.status == SolveStatus.INFEASIBLE
    assert attempt.evaluated_state_count == 2
    assert attempt.proof_code == "EXHAUSTIVE_STATE_SPACE_PROVEN"


def test_stage_gate_does_not_open_rates_after_incomplete_entry_search() -> None:
    calls: list[tuple[OptimizationStage, ScenarioProfile]] = []

    def solver(stage: OptimizationStage, profile: ScenarioProfile) -> SolveAttempt:
        calls.append((stage, profile))
        return SolveAttempt(
            stage=stage,
            profile=profile,
            status=SolveStatus.INCOMPLETE,
            proof_code="TIME_LIMIT",
        )

    outcome = run_staged_optimization(solver)
    assert outcome.status == SolveStatus.INCOMPLETE
    assert calls == [(OptimizationStage.ENTRY_ONLY, ScenarioProfile.ENTRY_STRUCTURE)]


def test_exact_rate_infeasibility_is_the_only_discount_unlock_gate() -> None:
    def solver(stage: OptimizationStage, profile: ScenarioProfile) -> SolveAttempt:
        return SolveAttempt(
            stage=stage,
            profile=profile,
            status=SolveStatus.INFEASIBLE,
            proof_code="EXACT",
        )

    outcome = run_staged_optimization(solver)
    assert outcome.status == SolveStatus.INFEASIBLE
    assert outcome.terminal_stage == OptimizationStage.ENTRY_AND_RATES
    assert outcome.discount_unlock_required is True
    assert outcome.reason_code == "DISCOUNT_UNLOCK_REQUIRED"


def test_converged_profiles_are_deduplicated_and_labels_accumulate() -> None:
    candidate = _candidate(10)

    def solver(stage: OptimizationStage, profile: ScenarioProfile) -> SolveAttempt:
        if stage == OptimizationStage.ENTRY_ONLY:
            return SolveAttempt(stage, profile, SolveStatus.INFEASIBLE, proof_code="EXACT")
        return SolveAttempt(stage, profile, SolveStatus.OPTIMAL, candidates=(candidate,))

    outcome = run_staged_optimization(solver)
    assert outcome.status == SolveStatus.OPTIMAL
    assert len(outcome.candidates) == 1
    assert set(outcome.candidates[0].scenario_types) == {
        ScenarioProfile.BALANCED,
        ScenarioProfile.RETENTION_WEIGHTED,
        ScenarioProfile.ACQUISITION_WEIGHTED,
    }


def test_entry_candidate_stops_before_rate_solver() -> None:
    calls = 0

    def solver(stage: OptimizationStage, profile: ScenarioProfile) -> SolveAttempt:
        nonlocal calls
        calls += 1
        return SolveAttempt(stage, profile, SolveStatus.OPTIMAL, candidates=(_candidate(10),))

    outcome = run_staged_optimization(solver)
    assert outcome.terminal_stage == OptimizationStage.ENTRY_ONLY
    assert calls == 1


def test_over_target_gap_uses_the_same_exact_engine_and_adjusts_downward() -> None:
    adapter = ExactEnumerationAdapter(maximum_states=10)
    attempt = adapter.solve(
        stage=OptimizationStage.ENTRY_ONLY,
        profile=ScenarioProfile.ENTRY_STRUCTURE,
        decisions=[
            DecisionVector(entry_values=(("entry-a", 12),)),
            DecisionVector(entry_values=(("entry-a", 10),)),
            DecisionVector(entry_values=(("entry-a", 8),)),
        ],
        evaluator=lambda item: _candidate(
            dict(item.entry_values)["entry-a"],
            str((dict(item.entry_values)["entry-a"] - 8) * 20_000),
        ),
    )

    assert attempt.status == SolveStatus.OPTIMAL
    assert attempt.candidates[0].decision.entry_values == (("entry-a", 8),)
