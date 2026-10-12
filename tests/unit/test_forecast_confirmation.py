"""Narrow empty-budget confirmation review; never invent an absent actual fact.

All fixtures are in memory. These tests make no API calls, write no ledger or
snapshot, and do not confirm a forecast on the user's behalf.
"""

import copy
from decimal import Decimal
from typing import Any

import pytest

from packages.forecast.calculation import (
    CONFIRMATION_POLICY_VERSION,
    DIMS,
    SEASONS,
    _review_empty_budget_actuals,
    calculate_forecast,
)
from packages.forecast.rules import FIXED_RULES_VERSION

CONTRIBUTIONS = (
    "old",
    "new",
    "total",
    "opening",
    "list_revenue",
    "business",
    "current",
    "revenue",
    "carryout",
)


def _dims(campus: str) -> dict[str, str]:
    return {"track": "初中", "product": "智慧空间", "grade": "七年级", "subject": "数学", "campus": campus}


def _proof(snapshot: dict[str, Any]) -> None:
    """Full source-adapter proof with retained sums, not a metric checkbox."""
    departments: dict[str, Any] = {}
    totals: dict[str, Any] = {}
    for season in SEASONS:
        rows = [row for row in snapshot["rows"] if row["season"] == season and not row.get("financial_only")]
        revenue = sum((Decimal(str(row["actual_revenue"])) for row in rows), Decimal(0))
        enrollment = sum((Decimal(str(row["enrollment"])) for row in rows), Decimal(0))
        departments[season] = {"wide": str(revenue), "financial": str(revenue), "matched": True}
        totals[season] = {"actual_revenue": str(revenue), "enrollment": str(enrollment)}
    snapshot.update(fiscal_year=2027, actual_totals=totals)
    snapshot["provenance"] = {
        "dtdate": snapshot["as_of"],
        "metadata_stable_after_extraction": True,
        "count_proof": {
            "summary_rows": len(snapshot["rows"]),
            "excluded_zero_incomplete_rows": 0,
            "checked_complete_rows": len(snapshot["rows"]),
            "aggregated_rows": len(snapshot["rows"]),
            "financial_rows": len(snapshot["rows"]),
            "financial_partition_mismatch_keys": 0,
            "department_financial_reconciliation": departments,
            "complement_failure_count": 0,
            "current_base_failure_count": 0,
        },
    }


def _inputs(slots: int = 1) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    baseline_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    for season in SEASONS:
        for campus in ("已开展业务", *(f"空组合{index}" for index in range(slots))):
            empty = campus.startswith("空组合")
            baseline_rows.append(
                {
                    "season": season,
                    **_dims(campus),
                    **{field: "0.000000" for field in CONTRIBUTIONS},
                    "old": 0 if empty else 80,
                    "new": 0 if empty else 20,
                    "total": 0 if empty else 100,
                    "revenue": "0.000000" if empty else "1000.000000",
                    "price": "106",
                    "discount": "1",
                    "recognition": "0.5",
                    "renewal": "0" if empty else "0.5",
                    "share": "0" if empty else "0.2",
                }
            )
            if empty and season == "暑假":
                continue
            source_rows.append(
                {
                    "season": season,
                    **_dims(campus),
                    "enrollment": 0 if empty else 100,
                    "new_business": 0 if empty else 10,
                    "new_base": 0 if empty else 20,
                    "old_base": 0 if empty else 80,
                    "actual_revenue": "0.000000" if empty else "1060.000000",
                    "actual_carryout": "0.000000" if empty else "100.000000",
                    "history_new_process_n": 0,
                    "history_new_process_d": 100,
                    "history_new_final_n": 10,
                    "history_new_final_d": 100,
                    "history_old_process_n": 0,
                    "history_old_process_d": 100,
                    "history_old_final_n": 5,
                    "history_old_final_d": 100,
                    "history_total_process_n": 0,
                }
            )
    baseline = {"fiscal_year": 2027, "rows": baseline_rows}
    snapshot: dict[str, Any] = {
        "as_of": "2026-10-07",
        "rows": source_rows,
        "metric_verified": True,
        "issues": [],
        "frozen_carryins": [],
    }
    assumptions = {
        "states": {"暑假": "FINALIZED", "秋季": "OPEN", "寒假": "FUTURE", "春季": "FUTURE"},
        "minimum_sample": 30,
        "mappings": [],
        "adjustments": [],
        "trial_mode": False,
        "fixed_rules_version": FIXED_RULES_VERSION,
    }
    _proof(snapshot)
    return baseline, snapshot, assumptions


def _candidate(baseline: dict[str, Any], season: str = "暑假") -> dict[str, Any]:
    return next(row for row in baseline["rows"] if row["season"] == season and row["campus"] == "空组合0")


def _missing(result: dict[str, Any], season: str = "暑假") -> list[dict[str, Any]]:
    return [
        issue
        for issue in result["issues"]
        if issue["code"] == "FINALIZED_ACTUAL_MISSING" and issue.get("season") == season
    ]


def _assert_blocked(result: dict[str, Any]) -> None:
    assert result["confirmation_checks"] == []
    assert not result["ready"]
    assert _missing(result)
    assert all(issue["severity"] == "BLOCKING" for issue in _missing(result))
    assert all("confirmation_exemption" not in issue.get("detail", {}) for issue in _missing(result))


def _review_fixture() -> tuple[dict[Any, Any], dict[str, Any], dict[str, Any], list[Any], list[Any]]:
    baseline, snapshot, assumptions = _inputs()
    withheld = copy.deepcopy(snapshot)
    withheld.pop("provenance")
    result = calculate_forecast(baseline, withheld, assumptions)
    indexed = {
        (row["season"], tuple(row[key] for key in DIMS)): copy.deepcopy(row) for row in baseline["rows"]
    }
    return indexed, snapshot, assumptions, result["rows"], result["issues"]


def test_all_47_explicit_zero_absent_slots_are_informational_but_raw_actuals_remain_unknown() -> None:
    baseline, snapshot, assumptions = _inputs(slots=47)
    before = copy.deepcopy((baseline, snapshot, assumptions))
    unproven = copy.deepcopy(snapshot)
    unproven.pop("provenance")
    blocked = calculate_forecast(baseline, unproven, assumptions)
    _assert_blocked(blocked)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"] and result["forecast_complete"]
    assert result["trial_mode"] is False and result["trial_ready"] is False
    assert result["confirmation_policy_version"] == CONFIRMATION_POLICY_VERSION
    assert len(_missing(result)) == 47
    assert all(issue["severity"] == "INFO" for issue in _missing(result))
    assert all(
        issue["detail"]
        == {
            "confirmation_exemption": CONFIRMATION_POLICY_VERSION,
            "classification": "EMPTY_BUDGET_SLOT_NO_SOURCE_BUSINESS",
        }
        for issue in _missing(result)
    )
    assert result["confirmation_checks"] == [
        {
            "season": "暑假",
            "as_of": "2026-10-07",
            "source_complete": True,
            "budget_components_zero": True,
            "no_frozen_carryins": True,
            "revenue_matched": True,
            "enrollment_matched": True,
            "excluded_empty_rows": 47,
            "known_revenue": "1060.000000",
            "actual_revenue": "1060.000000",
            "known_enrollment": 100,
            "actual_enrollment": 100,
        }
    ]
    for field in ("rows", "quarters", "annual", "campuses", "withdrawal_evidence", "rule_notes"):
        assert result[field] == blocked[field], f"confirmation classification must not change {field}"
    empty_actuals = [
        row for row in result["rows"] if row["season"] == "暑假" and row["campus"].startswith("空组合")
    ]
    assert len(empty_actuals) == 47
    for row in empty_actuals:
        assert row["method"] == "ACTUAL_UNAVAILABLE"
        assert row["data_status"] == "ACTUAL_UNAVAILABLE"
        assert row["included_in_closed_actual_total"] is False
        for field in (
            "forecast_revenue",
            "forecast_total",
            "forecast_new",
            "forecast_old",
            "forecast_carryout",
        ):
            assert row[field] is None, f"absent {field} cannot be rewritten as zero"
    assert (baseline, snapshot, assumptions) == before


@pytest.mark.parametrize("field", CONTRIBUTIONS)
@pytest.mark.parametrize("value", ["1", None, "-", False, True, "-0.000001", "", "NaN"])
def test_any_nonzero_missing_literal_boolean_or_negative_contribution_refuses_exemption(
    field: str, value: Any
) -> None:
    baseline, snapshot, assumptions = _inputs()
    _candidate(baseline)[field] = value
    before = copy.deepcopy((baseline, snapshot, assumptions))
    _assert_blocked(calculate_forecast(baseline, snapshot, assumptions))
    assert (baseline, snapshot, assumptions) == before


@pytest.mark.parametrize("field", CONTRIBUTIONS)
def test_an_omitted_contribution_is_unknown_not_explicit_zero(field: str) -> None:
    baseline, snapshot, assumptions = _inputs()
    _candidate(baseline).pop(field)
    _assert_blocked(calculate_forecast(baseline, snapshot, assumptions))


@pytest.mark.parametrize("zeros", [0, "0", "0.000000", "-0.000000", Decimal("0")])
def test_recorded_numeric_zero_formats_are_valid_without_zeroing_price_or_rates(zeros: Any) -> None:
    baseline, snapshot, assumptions = _inputs()
    row = _candidate(baseline)
    row.update({field: zeros for field in CONTRIBUTIONS})
    row.update(price="9999.123456", discount="0.876543", recognition="0.2", renewal="0.3", share="0.2")
    before = copy.deepcopy((baseline, snapshot, assumptions))
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert _missing(result)[0]["severity"] == "INFO"
    assert (baseline, snapshot, assumptions) == before


def test_offsetting_positive_negative_budget_components_cannot_be_netted_into_an_empty_slot() -> None:
    baseline, snapshot, assumptions = _inputs()
    row = _candidate(baseline)
    row.update(list_revenue="10.01", business="-10.01")
    assert Decimal(row["list_revenue"]) + Decimal(row["business"]) == 0
    _assert_blocked(calculate_forecast(baseline, snapshot, assumptions))


def test_one_nonempty_missing_member_keeps_the_whole_season_unreviewed_not_partially_exempted() -> None:
    baseline, snapshot, assumptions = _inputs(slots=2)
    next(row for row in baseline["rows"] if row["season"] == "暑假" and row["campus"] == "空组合1")[
        "list_revenue"
    ] = "0.000001"
    result = calculate_forecast(baseline, snapshot, assumptions)
    _assert_blocked(result)
    assert len(_missing(result)) == 2
    assert all(issue["severity"] == "BLOCKING" for issue in _missing(result))


@pytest.mark.parametrize(
    "failure",
    [
        "missing_provenance",
        "missing_count_proof",
        "metadata_false",
        "metadata_missing",
        "dtdate_changed",
        "fiscal_year_changed",
        "partition_mismatch",
        "complement_failure",
        "current_base_failure",
        "aggregated_count_changed",
        "coverage_count_changed",
        "department_missing",
        "department_unmatched",
        "financial_sum_changed",
        "metric_checkbox_without_proof",
        "missing_frozen_carryins",
    ],
)
def test_full_retained_source_and_financial_proof_is_mandatory(failure: str) -> None:
    baseline, snapshot, assumptions = _inputs()
    provenance = snapshot["provenance"]
    proof = provenance["count_proof"]
    if failure in {"missing_provenance", "metric_checkbox_without_proof"}:
        snapshot.pop("provenance")
    elif failure == "missing_count_proof":
        provenance.pop("count_proof")
    elif failure == "metadata_false":
        provenance["metadata_stable_after_extraction"] = False
    elif failure == "metadata_missing":
        provenance.pop("metadata_stable_after_extraction")
    elif failure == "dtdate_changed":
        provenance["dtdate"] = "2026-10-08"
    elif failure == "fiscal_year_changed":
        snapshot["fiscal_year"] = 2028
    elif failure == "partition_mismatch":
        proof["financial_partition_mismatch_keys"] = 1
    elif failure == "complement_failure":
        proof["complement_failure_count"] = 1
    elif failure == "current_base_failure":
        proof["current_base_failure_count"] = 1
    elif failure == "aggregated_count_changed":
        proof["aggregated_rows"] += 1
    elif failure == "coverage_count_changed":
        proof["checked_complete_rows"] -= 1
    elif failure == "department_missing":
        proof["department_financial_reconciliation"].pop("春季")
    elif failure == "department_unmatched":
        proof["department_financial_reconciliation"]["暑假"]["matched"] = False
    elif failure == "financial_sum_changed":
        proof["department_financial_reconciliation"]["暑假"]["financial"] = "1060.010001"
    elif failure == "missing_frozen_carryins":
        snapshot.pop("frozen_carryins")
    _assert_blocked(calculate_forecast(baseline, snapshot, assumptions))


@pytest.mark.parametrize(
    "trial,policy", [(True, FIXED_RULES_VERSION), (True, None), (False, None), (False, "unknown-policy")]
)
def test_only_the_saved_nontrial_fixed_policy_can_review_empty_slots(trial: bool, policy: str | None) -> None:
    baseline, snapshot, assumptions = _inputs()
    assumptions.update(trial_mode=trial, fixed_rules_version=policy)
    _assert_blocked(calculate_forecast(baseline, snapshot, assumptions))


@pytest.mark.parametrize(
    "flag,value",
    [("_forecast_only", True), ("_scope_kind", "UNBUDGETED_BUSINESS"), ("_scope_kind", "FINANCIAL_ONLY")],
)
def test_forecast_only_or_financial_only_rows_are_not_original_empty_budget_slots(
    flag: str, value: Any
) -> None:
    baseline, snapshot, assumptions = _inputs()
    _candidate(baseline)[flag] = value
    _assert_blocked(calculate_forecast(baseline, snapshot, assumptions))


@pytest.mark.parametrize("shape", ["flat", "nested", "mapped", "positive_negative_net_zero", "future_zero"])
def test_any_retained_carry_for_the_business_refuses_exemption_including_nested_mapped_and_net_zero(
    shape: str,
) -> None:
    baseline, snapshot, assumptions = _inputs()
    dims = _dims("空组合0")
    carry: dict[str, Any] = {"season": "秋季", "origin_season": 20264, "revenue": "0.000000", **dims}
    if shape in {"nested", "mapped"}:
        carry = {"season": "秋季", "origin_season": 20264, "revenue": "0.000000", "dims": dims}
    if shape == "mapped":
        carry["dims"] = _dims("旧业务名称")
        assumptions["mappings"] = [{"source": _dims("旧业务名称"), "target": dims, "reason": "明确对应"}]
    if shape == "future_zero":
        carry["season"] = "春季"
    snapshot["frozen_carryins"] = [carry]
    if shape == "positive_negative_net_zero":
        snapshot["frozen_carryins"] = [{**carry, "revenue": "10.123456"}, {**carry, "revenue": "-10.123456"}]
    _assert_blocked(calculate_forecast(baseline, snapshot, assumptions))


def test_a_source_business_mapped_away_cannot_be_reclassified_as_absent_original_business() -> None:
    baseline, snapshot, assumptions = _inputs()
    source = {
        **snapshot["rows"][0],
        **_dims("空组合0"),
        "enrollment": 0,
        "new_business": 0,
        "new_base": 0,
        "old_base": 0,
        "actual_revenue": "0",
        "actual_carryout": "0",
    }
    snapshot["rows"].append(source)
    assumptions["mappings"] = [
        {"source": _dims("空组合0"), "target": _dims("已开展业务"), "reason": "明确合并"}
    ]
    _proof(snapshot)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["signed_actuals_verified"]
    _assert_blocked(result)


@pytest.mark.parametrize("shape", ["flat", "nested", "positive_negative_net_zero"])
def test_original_carry_business_mapped_away_is_not_absent_even_when_its_target_is_valid(shape: str) -> None:
    baseline, snapshot, assumptions = _inputs()
    original, target = _dims("空组合0"), _dims("已开展业务")
    assumptions["mappings"] = [{"source": original, "target": target, "reason": "明确转入另一预算业务"}]
    carry = {"season": "秋季", "origin_season": 20264, "revenue": "10.123456", **original}
    if shape == "nested":
        carry = {"season": "秋季", "origin_season": 20264, "revenue": "10.123456", "dims": original}
    snapshot["frozen_carryins"] = [carry]
    if shape == "positive_negative_net_zero":
        snapshot["frozen_carryins"] = [{**carry, "revenue": "10.123456"}, {**carry, "revenue": "-10.123456"}]
    before = copy.deepcopy((baseline, snapshot, assumptions))
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["signed_actuals_verified"]
    _assert_blocked(result)
    assert (baseline, snapshot, assumptions) == before


@pytest.mark.parametrize("prior_carryout", ["1", "-1", None, "-", False])
def test_a_later_finalized_empty_slot_requires_explicit_zero_previous_budget_carryout(
    prior_carryout: Any,
) -> None:
    baseline, snapshot, assumptions = _inputs()
    assumptions["states"]["秋季"] = "FINALIZED"
    snapshot["rows"] = [
        row for row in snapshot["rows"] if not (row["season"] == "秋季" and row["campus"] == "空组合0")
    ]
    _candidate(baseline)["carryout"] = prior_carryout
    _proof(snapshot)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"] is False
    assert all(issue["severity"] == "BLOCKING" for issue in _missing(result, "秋季"))
    assert not any(check["season"] == "秋季" for check in result["confirmation_checks"])


@pytest.mark.parametrize("difference", ["0.000001", "-0.000001"])
def test_department_revenue_difference_of_one_retained_money_unit_is_not_within_a_publish_tolerance(
    difference: str,
) -> None:
    indexed, snapshot, assumptions, output, issues = _review_fixture()
    snapshot["actual_totals"]["暑假"]["actual_revenue"] = str(Decimal("1060") + Decimal(difference))
    before = copy.deepcopy(output)
    assert _review_empty_budget_actuals(indexed, snapshot, assumptions, output, issues) == []
    assert _missing({"issues": issues})[0]["severity"] == "BLOCKING"
    assert output == before


def test_known_money_is_compared_at_six_digits_without_rounding_away_a_one_unit_difference() -> None:
    indexed, snapshot, assumptions, output, issues = _review_fixture()
    known = next(row for row in output if row["season"] == "暑假" and row["campus"] == "已开展业务")
    known["forecast_revenue"] = "1060.000001"
    before = copy.deepcopy(output)
    assert _review_empty_budget_actuals(indexed, snapshot, assumptions, output, issues) == []
    assert output == before
    assert _missing({"issues": issues})[0]["severity"] == "BLOCKING"


def test_fractional_source_precision_does_not_hide_a_six_digit_difference_between_detail_and_department() -> (
    None
):
    baseline, snapshot, assumptions = _inputs()
    for original in list(baseline["rows"]):
        if original["campus"] == "已开展业务":
            baseline["rows"].append({**original, **_dims("另一个已开展业务")})
    for original in list(snapshot["rows"]):
        if original["campus"] == "已开展业务":
            original["actual_revenue"] = "1060.0000004"
            snapshot["rows"].append({**original, **_dims("另一个已开展业务"), "actual_revenue": "0.0000004"})
    _proof(snapshot)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["signed_actuals_verified"]
    assert result["quarters"][0]["forecast_revenue"] == "1060.000001"
    known = [row for row in result["rows"] if row["season"] == "暑假" and row["method"] == "FROZEN_ACTUAL"]
    assert sum((Decimal(row["forecast_revenue"]) for row in known), Decimal(0)) == Decimal("1060.000000")
    _assert_blocked(result)


@pytest.mark.parametrize("side", ["department", "known"])
def test_department_or_known_enrollment_difference_of_one_person_refuses_exemption(side: str) -> None:
    indexed, snapshot, assumptions, output, issues = _review_fixture()
    if side == "department":
        snapshot["actual_totals"]["暑假"]["enrollment"] = "101"
    else:
        next(row for row in output if row["season"] == "暑假" and row["campus"] == "已开展业务")[
            "forecast_total"
        ] = 101
    assert _review_empty_budget_actuals(indexed, snapshot, assumptions, output, issues) == []
    assert _missing({"issues": issues})[0]["severity"] == "BLOCKING"


def test_other_blockers_cannot_be_hidden_even_when_an_empty_slot_and_its_department_totals_are_proven() -> (
    None
):
    baseline, snapshot, assumptions = _inputs()
    snapshot["issues"] = [
        {"code": "SOURCE_QUALITY_UNRESOLVED", "severity": "WARNING", "message": "仍有源数据核对问题"}
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    _assert_blocked(result)
    assert any(
        issue["code"] == "SOURCE_QUALITY_UNRESOLVED" and issue["severity"] == "BLOCKING"
        for issue in result["issues"]
    )


def test_unrelated_warning_is_preserved_and_does_not_become_a_new_blocker_or_modify_the_empty_slot() -> None:
    baseline, snapshot, assumptions = _inputs()
    next(row for row in baseline["rows"] if row["campus"] == "已开展业务")["opening"] = "-0.01"
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    warning = next(issue for issue in result["issues"] if issue["code"] == "BASELINE_NEGATIVE_VALUE")
    assert warning["severity"] == "WARNING" and warning["detail"]["value"] == "-0.01"
    assert _missing(result)[0]["severity"] == "INFO"


def test_issue_reclassification_retains_other_evidence_and_never_mutates_output_or_snapshot() -> None:
    indexed, snapshot, assumptions, output, issues = _review_fixture()
    missing = _missing({"issues": issues})[0]
    missing["detail"] = {"source_batch": "retained-batch", "raw_missing": None}
    before = copy.deepcopy((indexed, snapshot, assumptions, output))
    checks = _review_empty_budget_actuals(indexed, snapshot, assumptions, output, issues)
    assert len(checks) == 1
    assert missing["severity"] == "INFO"
    assert missing["detail"]["source_batch"] == "retained-batch" and missing["detail"]["raw_missing"] is None
    assert missing["detail"]["confirmation_exemption"] == CONFIRMATION_POLICY_VERSION
    assert (indexed, snapshot, assumptions, output) == before
