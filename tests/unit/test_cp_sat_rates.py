from decimal import Decimal

import pytest

from packages.optimization.cp_sat_rates import (
    CpSatEntryAndRatesSolver,
    compile_entry_and_rates_problem,
)
from packages.optimization.engine import (
    CandidateEvaluation,
    DecisionVector,
    EntryDecision,
    RateDecision,
    ScenarioProfile,
    SolveStatus,
)


def _entries() -> tuple[EntryDecision, ...]:
    return (EntryDecision("entry", 1, 1, 1),)


def _rates() -> tuple[RateDecision, ...]:
    return (
        RateDecision(
            stable_id="a-renewal",
            track="track-a",
            rate_type="RENEWAL_RATE",
            current_value_ppm=700_000,
            lower_bound_ppm=700_000,
            upper_bound_ppm=700_200,
        ),
        RateDecision(
            stable_id="b-new-share",
            track="track-a",
            rate_type="NEW_STUDENT_SHARE",
            current_value_ppm=300_000,
            lower_bound_ppm=300_000,
            upper_bound_ppm=300_200,
        ),
    )


def _profile_evaluator(decision: DecisionVector) -> CandidateEvaluation:
    values = dict(decision.rate_values)
    retention = (values["a-renewal"] - 700_000) // 100
    new_share = (values["b-new-share"] - 300_000) // 100
    accepted = retention + new_share == 3
    return CandidateEvaluation(
        decision=decision,
        target_variances_yuan=(("DEPARTMENT:", Decimal(0 if accepted else 20_000)),),
        hard_constraints_satisfied=True,
        revenue_r00_yuan=Decimal(0),
        revenue_r10_yuan=Decimal(retention),
        revenue_r01_yuan=Decimal(new_share),
        revenue_r11_yuan=Decimal(retention + new_share),
        entry_adjustments=(0,),
        rate_adjustments_ppm=(retention * 100, new_share * 100),
    )


@pytest.mark.parametrize(
    ("profile", "expected_rates"),
    (
        (
            ScenarioProfile.BALANCED,
            (("a-renewal", 700_100), ("b-new-share", 300_200)),
        ),
        (
            ScenarioProfile.RETENTION_WEIGHTED,
            (("a-renewal", 700_200), ("b-new-share", 300_100)),
        ),
        (
            ScenarioProfile.ACQUISITION_WEIGHTED,
            (("a-renewal", 700_100), ("b-new-share", 300_200)),
        ),
    ),
)
def test_rate_cp_sat_exhausts_space_and_selects_the_exact_profile_winner(
    profile: ScenarioProfile,
    expected_rates: tuple[tuple[str, int], ...],
) -> None:
    result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=20,
    ).solve(
        entries=_entries(),
        rates=_rates(),
        fixed_total=1,
        track_directions={"track-a": "UP"},
        profile=profile,
        evaluator=_profile_evaluator,
    )

    assert result.status == SolveStatus.OPTIMAL
    assert result.evaluated_state_count == 9
    assert result.candidates[0].decision.rate_values == expected_rates
    assert result.candidates[0].scenario_types == (profile,)
    assert result.proof_summary is not None
    assert result.proof_summary["enumeration_proof"] == "FULL_STRUCTURAL_SPACE_EXHAUSTED"


def test_rate_water_level_tables_encode_equal_points_and_boundary_exit() -> None:
    rates = (
        RateDecision(
            "a",
            "track-a",
            "RENEWAL_RATE",
            700_000,
            700_000,
            700_100,
        ),
        RateDecision(
            "b",
            "track-a",
            "RENEWAL_RATE",
            800_000,
            800_000,
            800_200,
        ),
    )
    problem = compile_entry_and_rates_problem(
        _entries(),
        rates,
        fixed_total=1,
        track_directions={"track-a": "UP"},
        entry_variable_ids=("entry:000000",),
        rate_variable_ids=("rate:000000", "rate:000001"),
        forbidden_assignments=(),
    )

    tables = {item.stable_id: item.rows for item in problem.allowed_assignments}
    assert tables["rate-water-table:000000"] == (
        (0, 700_000),
        (1, 700_100),
        (2, 700_100),
    )
    assert tables["rate-water-table:000001"] == (
        (0, 800_000),
        (1, 800_100),
        (2, 800_200),
    )


def test_rate_cp_sat_does_not_publish_a_best_so_far_candidate_at_the_validation_cap() -> None:
    result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=5,
        maximum_candidate_validations=1,
    ).solve(
        entries=_entries(),
        rates=_rates(),
        fixed_total=1,
        track_directions={"track-a": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=lambda decision: CandidateEvaluation(
            decision=decision,
            target_variances_yuan=(("DEPARTMENT:", Decimal(0)),),
            hard_constraints_satisfied=True,
        ),
    )

    assert result.status == SolveStatus.INCOMPLETE
    assert result.proof_code == "CP_SAT_ENTRY_AND_RATES_VALIDATION_LIMIT_EXCEEDED"
    assert result.candidates == ()
    assert result.proof_summary is not None
    assert result.proof_summary["feasible_candidate_count"] == 1


def test_rate_cp_sat_proves_no_kernel_feasible_decision_only_after_space_exhaustion() -> None:
    result = CpSatEntryAndRatesSolver(
        maximum_time_seconds=10,
        maximum_candidate_validations=20,
    ).solve(
        entries=_entries(),
        rates=_rates(),
        fixed_total=1,
        track_directions={"track-a": "UP"},
        profile=ScenarioProfile.BALANCED,
        evaluator=lambda decision: CandidateEvaluation(
            decision=decision,
            target_variances_yuan=(("DEPARTMENT:", Decimal(20_000)),),
            hard_constraints_satisfied=True,
        ),
    )

    assert result.status == SolveStatus.INFEASIBLE
    assert result.proof_code == "CP_SAT_ENTRY_AND_RATES_VALIDATION_SPACE_EXHAUSTED"
    assert result.evaluated_state_count == 9
