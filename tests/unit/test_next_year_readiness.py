"""Class diagnostics are pure, target-independent and do not propose a repair."""

from copy import deepcopy
from typing import Any

import pytest

from packages.forecast.store import fingerprint
from packages.next_year.calculation import NextYearError, calculate_plan
from packages.next_year.readiness import class_readiness
from tests.unit.test_next_year_calculation import _plan_inputs


def _small_entry() -> dict[str, Any]:
    inputs = _plan_inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    inputs["entry_total"] = 15
    inputs["class_sizes"][0]["minimum"] = 24
    return inputs


def test_readiness_without_a_target_is_pure_and_uses_the_actual_gap_key() -> None:
    inputs = _small_entry()
    before = fingerprint(inputs)
    calculation = calculate_plan(inputs)
    result = class_readiness(inputs)
    summer = next(row for row in result["rows"] if row["season"] == "暑假")
    source = next(
        issue for issue in calculation["issues"] if issue["combination_id"] == summer["combination_id"]
    )
    assert source["detail"]["class_size_gap"] == summer["gap"] == 9
    assert summer["current_students"] == 15
    assert summer["minimum"] == 24
    assert summer["old_students"] == 0
    assert summer["new_students"] == 15
    assert summer["bound_proof"]["lower_students"] == summer["bound_proof"]["upper_students"] == 15
    assert summer["bound_proof"]["unreachable"] is True
    assert summer["bound_proof"]["entry_adjustment"] is False
    assert "自动调平前需填写经营目标" in "".join(summer["actions"])
    assert result["calculation_complete"] is True
    assert result["blocking_issue_count"] == 0
    assert result["issue_count"] == 4
    assert result["combination_count"] == result["closure_allowed_count"] == 1
    assert result["carry_protected_count"] == 0
    assert not {"proposed_inputs", "calculation", "status"} & result.keys()
    assert fingerprint(inputs) == before


def test_positive_carryover_protects_the_whole_scope_without_dropping_the_amount() -> None:
    inputs = _small_entry()
    inputs["combinations"][0]["opening_carryover_yuan"] = "123.456789"
    result = class_readiness(inputs)
    assert result["carry_protected_count"] == 1
    assert result["closure_allowed_count"] == 0
    assert all(not row["closure_allowed"] for row in result["rows"])
    assert all(row["carryover_yuan"] == "123.456789" for row in result["rows"])
    assert "不能直接关闭" in "".join(result["rows"][0]["actions"])


@pytest.mark.parametrize("lock", ["entry", "plan", "rate"])
def test_user_locks_block_closing_without_changing_or_unlocking_them(lock: str) -> None:
    inputs = _small_entry()
    if lock == "entry":
        inputs["combinations"][0].update(entry_students=15, entry_locked=True)
    elif lock == "plan":
        inputs["plans"][0].update(students=15, locked=True, reason="业务确认的人次")
    else:
        inputs["rates"][0]["locked"] = True
    before = fingerprint(inputs)
    result = class_readiness(inputs)
    assert result["closure_allowed_count"] == 0
    assert all(row["combination_locked"] for row in result["rows"])
    assert all(not row["closure_allowed"] and row["lock_reasons"] for row in result["rows"])
    assert fingerprint(inputs) == before


def test_seed_plan_is_not_a_user_lock_and_reports_its_optimizer_lock_source() -> None:
    inputs = _plan_inputs()
    inputs["opening_sources"][0]["students"] = 0
    second = inputs["combinations"][1]["id"]
    for row in inputs["plans"]:
        if row["combination_id"] == second:
            row["students"] = 1
    result = class_readiness(inputs)
    summer = next(
        row for row in result["rows"] if row["combination_id"] == second and row["season"] == "暑假"
    )
    assert summer["seed_plan"] is True
    assert summer["plan_locked"] is False
    assert summer["combination_locked"] is False
    assert summer["closure_allowed"] is True
    renewal = summer["rate_parameters"][0]["renewal"]
    assert renewal["lock_source"] == "SEED_PLAN"
    assert renewal["locked"] is True
    assert renewal["lower_bound_ppm"] == renewal["upper_bound_ppm"] == 800000


def test_four_seasons_zero_is_a_whole_combination_not_four_missing_values() -> None:
    inputs = _plan_inputs()
    inputs["entry_total"] = 0
    inputs["opening_sources"][0]["students"] = 0
    for row in inputs["plans"]:
        row["students"] = 0
    result = class_readiness(inputs)
    assert result["issue_count"] == result["combination_count"] == 2
    assert result["closure_allowed_count"] == 2
    for row in result["rows"]:
        assert row["code"] == "ENABLED_COMBINATION_ALL_SEASONS_ZERO"
        assert row["season"] is None
        assert row["current_students"] == row["old_students"] == row["new_students"] == row["gap"] == 0
        assert len(row["rate_parameters"]) == 4
        assert row["bound_proof"] is None


def test_explicit_rate_bounds_are_reported_without_the_default_hidden_cap() -> None:
    inputs = _small_entry()
    inputs["rates"][0].update(renewal_lower_ppm=0, renewal_upper_ppm=1000000, share_upper_ppm=800000)
    row = next(row for row in class_readiness(inputs)["rows"] if row["season"] == "暑假")
    renewal, share = row["rate_parameters"][0]["renewal"], row["rate_parameters"][0]["share"]
    assert (renewal["lower_bound_ppm"], renewal["upper_bound_ppm"]) == (0, 1000000)
    assert (share["lower_bound_ppm"], share["upper_bound_ppm"]) == (150000, 800000)
    assert renewal["bound_source"] == share["bound_source"] == "EXPLICIT"


def test_a_conservative_interval_is_not_a_claim_that_a_candidate_is_feasible() -> None:
    inputs = _small_entry()
    for row in inputs["rates"]:
        row.update(renewal_lower_ppm=0, renewal_upper_ppm=1000000, share_upper_ppm=800000)
    result = class_readiness(inputs)
    autumn = next(row for row in result["rows"] if row["season"] == "秋季")
    assert autumn["bound_proof"]["lower_students"] == 0
    assert autumn["bound_proof"]["unreachable"] is False
    assert "不能证明不可达" in autumn["bound_proof"]["message"]


def test_incomplete_calculation_is_never_reported_as_all_checks_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packages.next_year import readiness

    original = calculate_plan(_plan_inputs())
    incomplete = deepcopy(original)
    incomplete["complete"] = False
    incomplete["issues"] = [{"code": "UPSTREAM_HEADCOUNT_UNAVAILABLE", "severity": "BLOCKING"}]
    incomplete["rows"][0]["total_students"] = None
    monkeypatch.setattr(readiness, "calculate_plan", lambda _: incomplete)
    result = class_readiness(_plan_inputs())
    assert result["issue_count"] == 0
    assert result["calculation_complete"] is False
    assert result["blocking_issue_count"] == 1


def test_invalid_inputs_fail_without_a_partial_diagnostic() -> None:
    inputs = _small_entry()
    inputs["class_sizes"][0]["minimum"] = 0
    with pytest.raises(NextYearError):
        class_readiness(inputs)
