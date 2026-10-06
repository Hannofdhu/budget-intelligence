import copy
import json
from decimal import Decimal
from typing import Any, cast

import pytest

from packages.forecast.calculation import SEASONS, calculate_forecast


def baseline_rows(
    *, campuses: tuple[str, ...] = ("西直门",), subjects: tuple[str, ...] = ("数学",)
) -> list[dict[str, Any]]:
    return [
        {
            "season": season,
            "track": "初中",
            "product": "智慧空间",
            "grade": "七年级",
            "subject": subject,
            "campus": campus,
            "revenue": "1000",
            "old": 80,
            "new": 20,
            "total": 100,
            "price": "106",
            "discount": "1",
            "recognition": "0.5",
            "opening": "0",
            "renewal": "0.5",
            "share": "0.2",
        }
        for season in SEASONS
        for campus in campuses
        for subject in subjects
    ]


def source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **{key: row[key] for key in ("season", "track", "product", "grade", "subject", "campus")},
            "enrollment": 100,
            "new_business": 10,
            "new_base": 20,
            "old_base": 80,
            "actual_revenue": "1060",
            "actual_carryout": "100",
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
        for row in rows
    ]


def inputs(
    *,
    campuses: tuple[str, ...] = ("西直门",),
    subjects: tuple[str, ...] = ("数学",),
    states: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    rows = baseline_rows(campuses=campuses, subjects=subjects)
    return (
        {"fiscal_year": 2027, "rows": rows},
        {"as_of": "2026-10-05", "rows": source_rows(rows), "metric_verified": True, "issues": []},
        {
            "states": states or {"暑假": "FINALIZED", "秋季": "OPEN", "寒假": "FUTURE", "春季": "FUTURE"},
            "minimum_sample": 30,
            "mappings": [],
            "adjustments": [],
        },
    )


def result_row(
    result: dict[str, Any], season: str, *, campus: str = "西直门", subject: str = "数学"
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        next(
            row
            for row in result["rows"]
            if row["season"] == season and row["campus"] == campus and row["subject"] == subject
        ),
    )


def issue_codes(result: dict[str, Any]) -> set[str]:
    return {str(issue["code"]) for issue in result["issues"]}


def prove_financial_snapshot(snapshot: dict[str, Any]) -> None:
    """Mirror the full extraction/reconciliation proof emitted by source.py."""
    departments: dict[str, Any] = {}
    actual_totals: dict[str, Any] = {}
    for season in SEASONS:
        members = [row for row in snapshot["rows"] if row["season"] == season]
        revenue = sum((Decimal(str(row["actual_revenue"])) for row in members), Decimal(0))
        enrollment = sum((Decimal(str(row["enrollment"])) for row in members), Decimal(0))
        departments[season] = {"wide": revenue, "financial": revenue, "matched": True}
        actual_totals[season] = {"actual_revenue": revenue, "enrollment": enrollment}
    snapshot["fiscal_year"] = 2027
    snapshot["actual_totals"] = actual_totals
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


def forecast_scope(
    *, grade: str = "八年级", campus: str = "西直门", kind: str = "UNBUDGETED_BUSINESS"
) -> dict[str, Any]:
    return {
        "dims": {"track": "初中", "product": "智慧空间", "grade": grade, "subject": "数学", "campus": campus},
        "kind": kind,
        "reason": "已开展但未在原预算单列，独立预测，不改变原预算",
        "financial": {
            season: {
                "price": "106",
                "discount": "1",
                "recognition": "0.5",
                "method": "CONFIRMED_PRODUCT_TEMPLATE",
            }
            for season in SEASONS
        },
        "additional": {"秋季": 0},
    }


def add_scope_source(snapshot: dict[str, Any], scope: dict[str, Any]) -> None:
    snapshot["rows"].extend(
        [
            {
                **row,
                **scope["dims"],
                "enrollment": 10,
                "new_business": 1,
                "new_base": 2,
                "old_base": 8,
                "actual_revenue": "200",
                "actual_carryout": "20",
            }
            for row in list(snapshot["rows"])
            if row["campus"] == "西直门" and row["subject"] == "数学"
        ]
    )


def test_frozen_net_revenue_and_actual_carryout_are_not_divided_by_tax() -> None:
    baseline, snapshot, assumptions = inputs()
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    closed = result_row(result, "暑假")
    assert Decimal(closed["forecast_revenue"]) == Decimal("1060")
    assert Decimal(closed["forecast_carryout"]) == Decimal("100")
    opened = result_row(result, "秋季")
    assert (opened["additional"], opened["new_withdrawal"], opened["old_withdrawal"]) == (10, 2, 4)
    assert (opened["forecast_new"], opened["forecast_old"], opened["forecast_total"]) == (28, 76, 104)
    assert Decimal(opened["forecast_revenue"]) == Decimal("5300")
    assert Decimal(opened["forecast_carryout"]) == Decimal("5200")


def test_future_projection_propagates_terminal_counts_and_exact_new_share_without_topup() -> None:
    baseline, snapshot, assumptions = inputs()
    result = calculate_forecast(baseline, snapshot, assumptions)
    winter, spring = result_row(result, "寒假"), result_row(result, "春季")
    assert winter["forecast_total"] == 65  # 104 × .5 old + old × .2 / .8 new
    assert (winter["forecast_old"], winter["forecast_new"]) == (52, 13)
    assert Decimal(winter["forecast_revenue"]) == Decimal("8450")
    assert spring["forecast_total"] == 41  # old=32.5 -> 33; new=8.125 -> 8
    assert Decimal(spring["forecast_revenue"]) == Decimal("5300")
    assert Decimal(result["annual"]["forecast_revenue"]) == Decimal("20110")
    assert Decimal(result["annual"]["gap"]) == Decimal("-16110")
    assert result["annual"]["status"] == "ABOVE_BUDGET"


def test_missing_actual_carryout_blocks_next_revenue_not_replaced_by_budget_carryout() -> None:
    baseline, snapshot, assumptions = inputs()
    baseline["rows"][0]["carryout"] = 999999
    snapshot["rows"][0]["actual_carryout"] = None
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result_row(result, "暑假")["forecast_revenue"] == "1060.000000"
    assert result_row(result, "秋季")["forecast_revenue"] is None
    assert result_row(result, "秋季")["forecast_total"] == 104
    assert "ACTUAL_CARRYOUT_REQUIRED" in issue_codes(result)
    assert result["annual"]["forecast_revenue"] is None


def test_metric_unverified_blocks_open_terminal_but_keeps_closed_actual() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["metric_verified"] = False
    assumptions["adjustments"] = [
        {"season": "秋季", "campus": "西直门", "kind": "new_withdrawal", "value": 0, "reason": "示范"}
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result_row(result, "暑假")["forecast_revenue"] == "1060.000000"
    assert result_row(result, "秋季")["forecast_total"] is None
    assert "WITHDRAWAL_METRICS_UNVERIFIED" in issue_codes(result)


def test_history_numerators_are_summed_before_division_and_new_old_fallback_independent() -> None:
    baseline, snapshot, assumptions = inputs(campuses=("甲", "乙"))
    for row in snapshot["rows"]:
        if row["season"] == "秋季":
            row.update(
                history_new_process_n=0,
                history_new_process_d=15,
                history_new_final_n=1 if row["campus"] == "甲" else 2,
                history_new_final_d=15,
                history_old_process_d=30,
                history_old_final_n=3,
                history_old_final_d=30,
            )
    result = calculate_forecast(baseline, snapshot, assumptions)
    evidence = result["withdrawal_evidence"]
    assert {row["level"] for row in evidence if row["student_type"] == "new"} == {"NO_CAMPUS"}
    assert {row["level"] for row in evidence if row["student_type"] == "old"} == {"EXACT"}
    assert {Decimal(row["remaining_rate"]) for row in evidence if row["student_type"] == "new"} == {
        Decimal("0.1")
    }
    assert {row["rounded"] for row in evidence if row["student_type"] == "new"} == {2}


@pytest.mark.parametrize("denominator,valid", [(0, False), (29, False), (30, True)])
def test_zero_numerator_is_valid_but_denominators_need_minimum(denominator: int, valid: bool) -> None:
    baseline, snapshot, assumptions = inputs()
    for row in snapshot["rows"]:
        row.update(
            history_new_process_n=0,
            history_new_process_d=denominator,
            history_new_final_n=0,
            history_new_final_d=denominator,
        )
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"] is valid
    assert (result_row(result, "秋季")["new_withdrawal"] == 0) is valid
    if not valid:
        assert "WITHDRAWAL_HISTORY_MISSING" in issue_codes(result)


def test_process_and_final_rates_cannot_use_different_fallback_levels() -> None:
    baseline, snapshot, assumptions = inputs(campuses=("甲", "乙"))
    for row in snapshot["rows"]:
        if row["season"] == "秋季":
            row.update(history_new_process_d=100, history_new_final_d=15, history_new_final_n=3)
    result = calculate_forecast(baseline, snapshot, assumptions)
    evidence = [row for row in result["withdrawal_evidence"] if row["student_type"] == "new"]
    assert {row["level"] for row in evidence} == {"NO_CAMPUS"}
    assert {Decimal(row["process_d"]) for row in evidence} == {Decimal(200)}
    assert {Decimal(row["final_d"]) for row in evidence} == {Decimal(30)}


def test_manual_withdrawal_with_no_history_uses_current_class_base_and_explicit_zero() -> None:
    baseline, snapshot, assumptions = inputs(subjects=("数学", "英语"))
    for row in snapshot["rows"]:
        row.update(
            history_new_process_d=None,
            history_new_final_d=None,
            history_old_process_d=None,
            history_old_final_d=None,
        )
    assumptions["adjustments"] = [
        {"season": "秋季", "campus": "西直门", "kind": "new_withdrawal", "value": 1, "reason": "人工预估"},
        {"season": "秋季", "campus": "西直门", "kind": "old_withdrawal", "value": 0, "reason": "无后续退班"},
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    open_rows = [row for row in result["rows"] if row["season"] == "秋季"]
    assert sum(row["new_withdrawal"] for row in open_rows) == 1
    assert sum(row["old_withdrawal"] for row in open_rows) == 0
    assert {row["mode"] for row in result["withdrawal_evidence"]} == {"MANUAL_CURRENT_BASE_ALLOCATION"}


def test_manual_withdrawal_cannot_exceed_class_base() -> None:
    baseline, snapshot, assumptions = inputs()
    assumptions["adjustments"] = [
        {"season": "秋季", "campus": "西直门", "kind": "new_withdrawal", "value": 21, "reason": "不可行输入"}
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result_row(result, "秋季")["forecast_total"] is None
    assert "WITHDRAWAL_ADJUSTMENT_INFEASIBLE" in issue_codes(result)


def test_half_up_is_at_campus_type_not_independent_row_rounding_and_ties_are_stable() -> None:
    baseline, snapshot, assumptions = inputs(subjects=("数学", "英语"))
    for row in baseline["rows"]:
        row["new"] = 1
    for row in snapshot["rows"]:
        row.update(
            enrollment=1,
            new_business=1,
            new_base=1,
            old_base=0,
            history_new_process_n=0,
            history_new_process_d=30,
            history_new_final_n=15,
            history_new_final_d=30,
        )
    result = calculate_forecast(baseline, snapshot, assumptions)
    open_rows = [row for row in result["rows"] if row["season"] == "秋季"]
    assert sum(row["new_withdrawal"] for row in open_rows) == 1  # .5 + .5 -> 1, not 2
    reversed_result = calculate_forecast(
        {**baseline, "rows": list(reversed(baseline["rows"]))},
        {**snapshot, "rows": list(reversed(snapshot["rows"]))},
        assumptions,
    )
    assert result == reversed_result
    assert result_row(result, "秋季")["new_withdrawal"] == 1  # stable dimension key tie break


def test_additional_is_campus_net_gap_then_positive_detail_gap_largest_remainder() -> None:
    baseline, snapshot, assumptions = inputs(subjects=("数学", "英语"))
    for row in snapshot["rows"]:
        row["new_business"] = 30 if row["subject"] == "数学" else 0
    result = calculate_forecast(baseline, snapshot, assumptions)
    # Campus gap 40 - 30 = 10, not sum(max(0,20-30), max(0,20-0))=20.
    assert result_row(result, "秋季", subject="数学")["additional"] == 0
    assert result_row(result, "秋季", subject="英语")["additional"] == 10


def test_already_completed_new_budget_additional_is_zero_and_ended_defaults_zero() -> None:
    baseline, snapshot, assumptions = inputs()
    for row in snapshot["rows"]:
        row["new_business"] = 100
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result_row(result, "秋季")["additional"] == 0
    for row in snapshot["rows"]:
        row["new_business"] = 0
    assumptions["states"]["秋季"] = "ENDED"
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result_row(result, "秋季")["additional"] == 0


def test_unmapped_actual_counts_in_department_closed_revenue_but_blocks_ready() -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    extra = {**snapshot["rows"][0], "campus": "新校区", "actual_revenue": "260", "enrollment": 10}
    snapshot["rows"].append(extra)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert "SOURCE_UNMAPPED" in issue_codes(result)
    assert Decimal(result["quarters"][0]["forecast_revenue"]) == Decimal("1320")
    assert Decimal(result["annual"]["forecast_revenue"]) == Decimal("4500")
    assert next(row for row in result["rows"] if row.get("unmapped"))["method"] == "UNMAPPED_ACTUAL"


def test_explicit_mapping_aggregates_distinct_source_rows_without_dropping_revenue() -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    source = {key: snapshot["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    target = dict(source)
    source["campus"] = "西直门新编码"
    extra = {
        **snapshot["rows"][0],
        **source,
        "actual_revenue": "260",
        "enrollment": 10,
        "new_base": 2,
        "old_base": 8,
    }
    snapshot["rows"].append(extra)
    assumptions["mappings"] = [{"source": source, "target": target, "reason": "同一校区编码合并"}]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert Decimal(result_row(result, "暑假")["forecast_revenue"]) == Decimal("1320")
    assert result_row(result, "暑假")["forecast_total"] == 110


def test_many_to_one_mapping_sums_class_counts_and_history_numerators_denominators() -> None:
    baseline, snapshot, assumptions = inputs()
    original = next(row for row in snapshot["rows"] if row["season"] == "秋季")
    original.update(history_new_process_d=15, history_new_final_n=1, history_new_final_d=15)
    source = {key: original[key] for key in ("track", "product", "grade", "subject", "campus")}
    target = dict(source)
    source["campus"] = "西直门新编码"
    snapshot["rows"].append(
        {
            **original,
            **source,
            "enrollment": 50,
            "new_business": 2,
            "new_base": 10,
            "old_base": 40,
            "history_new_final_n": 2,
            "history_old_final_n": 15,
        }
    )
    assumptions["mappings"] = [{"source": source, "target": target, "reason": "校区编码合并"}]
    result = calculate_forecast(baseline, snapshot, assumptions)
    opened = result_row(result, "秋季")
    assert result["ready"]
    assert (opened["additional"], opened["new_withdrawal"], opened["old_withdrawal"]) == (8, 3, 12)
    assert (opened["forecast_new"], opened["forecast_old"], opened["forecast_total"]) == (35, 108, 143)
    evidence = next(row for row in result["withdrawal_evidence"] if row["student_type"] == "new")
    assert evidence["level"] == "EXACT"
    assert Decimal(evidence["final_n"]) == 3
    assert Decimal(evidence["final_d"]) == 30


def test_mapping_cannot_hide_offsetting_original_grain_base_mismatches() -> None:
    baseline, snapshot, assumptions = inputs()
    original = next(row for row in snapshot["rows"] if row["season"] == "秋季")
    source = {key: original[key] for key in ("track", "product", "grade", "subject", "campus")}
    target = dict(source)
    source["campus"] = "西直门新编码"
    original["enrollment"] = 101  # +1 mismatch
    snapshot["rows"].append({**original, **source, "enrollment": 99})  # -1 mismatch
    assumptions["mappings"] = [{"source": source, "target": target, "reason": "不能上层抵消"}]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert "SOURCE_CLASS_BASE_MISMATCH" in issue_codes(result)
    assert result_row(result, "秋季")["forecast_total"] is None


def test_negative_history_cannot_cancel_in_mapping_aggregation() -> None:
    baseline, snapshot, assumptions = inputs()
    original = next(row for row in snapshot["rows"] if row["season"] == "秋季")
    source = {key: original[key] for key in ("track", "product", "grade", "subject", "campus")}
    target = dict(source)
    source["campus"] = "西直门新编码"
    original["history_new_process_n"] = -1
    original["history_total_process_n"] = -1
    snapshot["rows"].append({**original, **source, "history_new_process_n": 1, "history_total_process_n": 1})
    assumptions["mappings"] = [{"source": source, "target": target, "reason": "不能用合并修负值"}]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert "SOURCE_NEGATIVE_VALUE" in issue_codes(result)
    assert result_row(result, "秋季")["forecast_total"] is None


def test_missing_source_not_zero_and_snapshot_issues_remain_blocking() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"] = [row for row in snapshot["rows"] if row["season"] != "秋季"]
    snapshot["issues"] = [{"code": "SOURCE_INCOMPLETE", "message": "抽取未完整", "severity": "WARNING"}]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert {"SOURCE_INCOMPLETE", "CURRENT_SOURCE_MISSING"} <= issue_codes(result)
    assert (
        next(issue for issue in result["issues"] if issue["code"] == "SOURCE_INCOMPLETE")["severity"]
        == "BLOCKING"
    )
    assert result_row(result, "秋季")["forecast_total"] is None


def test_old_history_strict_complement_mismatch_requires_manual_not_implicit_repair() -> None:
    baseline, snapshot, assumptions = inputs()
    for row in snapshot["rows"]:
        row.update(history_total_process_n=10, history_new_process_n=2, history_old_process_n=1)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result_row(result, "秋季")["old_withdrawal"] is None
    assert "WITHDRAWAL_HISTORY_MISSING" in issue_codes(result)


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("renewal", "-", "RENEWAL_RATE_INVALID"),
        ("share", None, "NEW_STUDENT_SHARE_INVALID"),
        ("share", "1", "NEW_STUDENT_SHARE_INVALID"),
        ("price", "-10", "REVENUE_PARAMETER_INVALID"),
    ],
)
def test_inherited_required_parameters_never_become_silent_zero(field: str, value: object, code: str) -> None:
    baseline, snapshot, assumptions = inputs()
    next(row for row in baseline["rows"] if row["season"] == "寒假")[field] = value
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert code in issue_codes(result)
    assert result_row(result, "寒假")["forecast_revenue"] is None


def test_budget_negatives_preserved_and_actual_class_mismatch_blocks_not_capped() -> None:
    baseline, snapshot, assumptions = inputs()
    baseline["rows"][0]["old"] = -1
    next(row for row in snapshot["rows"] if row["season"] == "秋季")["new_base"] = -1
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result_row(result, "暑假")["budget_old"] == "-1"
    assert {"BASELINE_NEGATIVE_VALUE", "CLASS_BASE_MISMATCH"} <= issue_codes(result)
    assert result_row(result, "秋季")["forecast_total"] is None


def test_finalized_adjustment_forbidden_and_missing_state_is_explicit() -> None:
    baseline, snapshot, assumptions = inputs()
    assumptions["adjustments"] = [
        {"season": "暑假", "campus": "西直门", "kind": "additional", "value": 100, "reason": "不得覆盖实际"}
    ]
    del assumptions["states"]["春季"]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert {"FINALIZED_ADJUSTMENT_FORBIDDEN", "QUARTER_STATE_REQUIRED"} <= issue_codes(result)
    assert result_row(result, "暑假")["forecast_total"] == 100
    assert result_row(result, "春季")["forecast_total"] is None


def test_engine_does_not_mutate_inputs_and_returns_json_serializable_decimal_strings() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"][0]["actual_revenue"] = Decimal("1060.012345")
    originals = copy.deepcopy((baseline, snapshot, assumptions))
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert (baseline, snapshot, assumptions) == originals
    assert result_row(result, "暑假")["forecast_revenue"] == "1060.012345"
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_withdrawal_remaining_is_nonnegative_when_history_final_lower_than_process() -> None:
    baseline, snapshot, assumptions = inputs()
    for row in snapshot["rows"]:
        row.update(history_new_process_n=20, history_new_final_n=10, history_total_process_n=20)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert result_row(result, "秋季")["new_withdrawal"] == 0


def test_forecast_gap_tolerance_is_not_a_forced_rebalance() -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["annual"]["status"] == "NO_SIGNIFICANT_GAP"
    assert Decimal(result["annual"]["gap"]) == Decimal("-240")
    assert Decimal(result["annual"]["forecast_revenue"]) == Decimal("4240")


def test_known_financial_carryins_include_old_business_origins_without_open_origin_double_count() -> None:
    baseline, snapshot, assumptions = inputs()
    dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    snapshot["rows"][0]["actual_carryout"] = None
    snapshot["frozen_carryins"] = [
        {"season": "秋季", "origin_season": 20271, "dims": dims, "revenue": "102.02601"},
        {"season": "秋季", "origin_season": 20264, "dims": dims, "revenue": "5.72525"},
        {"season": "寒假", "origin_season": 20271, "dims": dims, "revenue": "7"},
        {"season": "寒假", "origin_season": 20272, "dims": dims, "revenue": "999999"},
        {"season": "春季", "origin_season": 20264, "dims": dims, "revenue": "3"},
        {"season": "秋季", "origin_season": 20281, "dims": dims, "revenue": "999999"},
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    # Q2 incoming is all known finance-Q2 receipts, not just FY27 Q1 carryout.
    assert result_row(result, "秋季")["frozen_carryin"] == "107.751260"
    assert result_row(result, "秋季")["forecast_revenue"] == "5307.751260"
    # Q3 carries the *forecast* of open Q2 + closed Q1's delayed arrival only.
    assert result_row(result, "寒假")["forecast_revenue"] == "8457.000000"
    assert result_row(result, "春季")["forecast_revenue"] == "5303.000000"
    assert result["annual"]["forecast_revenue"] == "20127.751260"


def test_frozen_carryins_do_not_add_again_to_finalized_quarter_actuals() -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    snapshot["frozen_carryins"] = [
        {"season": "暑假", "origin_season": 20264, "dims": dims, "revenue": "100"},
        {"season": "秋季", "origin_season": 20271, "dims": dims, "revenue": "100"},
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert result["annual"]["forecast_revenue"] == "4240.000000"


def test_known_carryin_mapping_aggregates_multiple_sources_and_negative_or_missing_is_not_zero() -> None:
    baseline, snapshot, assumptions = inputs()
    dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    alias = {**dims, "campus": "西直门新编码"}
    assumptions["mappings"] = [{"source": alias, "target": dims, "reason": "同一真实校区"}]
    snapshot["frozen_carryins"] = [
        {"season": "秋季", "origin_season": 20271, "dims": dims, "revenue": "2"},
        {"season": "秋季", "origin_season": 20264, "dims": alias, "revenue": "3"},
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result_row(result, "秋季")["forecast_revenue"] == "5205.000000"
    snapshot["frozen_carryins"][1]["revenue"] = None
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert "FROZEN_CARRYIN_AMOUNT_INVALID" in issue_codes(result)
    assert result_row(result, "秋季")["forecast_revenue"] is None


def test_unmapped_frozen_carryin_still_counts_as_department_income_without_extra_students() -> None:
    baseline, snapshot, assumptions = inputs()
    dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    snapshot["frozen_carryins"] = [
        {"season": "秋季", "origin_season": 20264, "dims": {**dims, "campus": "来源新校区"}, "revenue": "200"}
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert "FROZEN_CARRYIN_UNMAPPED" in issue_codes(result)
    assert result["quarters"][1]["forecast_revenue"] == "5400.000000"
    assert result["quarters"][1]["forecast_total"] == 104


def test_future_process_rows_are_not_actuals_or_mapping_requirements() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"].append(
        {
            **snapshot["rows"][2],
            "campus": "未来校区",
            "enrollment": 0,
            "history_new_process_n": 100,
            "history_new_final_d": 1000,
        }
    )
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert not any(row.get("unmapped") for row in result["rows"])
    assert result_row(result, "寒假")["forecast_total"] == 65


def test_finalized_metric_history_fields_do_not_block_closed_actuals() -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    snapshot["metric_verified"] = False
    for row in snapshot["rows"]:
        row.update(history_new_process_n=-5, history_old_process_d=None)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert result["annual"]["forecast_revenue"] == "4240.000000"


def test_explicit_all_zero_parent_scope_dash_is_not_applicable_not_missing_rate() -> None:
    baseline, snapshot, assumptions = inputs()
    for row in baseline["rows"]:
        row.update(old=0, new=0, total=0, renewal="-", share="-")
    for row in snapshot["rows"]:
        row.update(enrollment=0, new_business=0, new_base=0, old_base=0)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    future = result_row(result, "寒假")
    assert future["forecast_total"] == 0
    assert future["renewal"] is None
    assert future["renewal_applicability"] == "NOT_APPLICABLE"


def test_department_history_pool_is_not_silently_restricted_to_budget_matched_scope() -> None:
    baseline, snapshot, assumptions = inputs()
    original = next(row for row in snapshot["rows"] if row["season"] == "秋季")
    original.update(
        history_new_process_n=0, history_new_process_d=10, history_new_final_n=1, history_new_final_d=10
    )
    # Same season and student class, different track: only department fallback can use it.
    snapshot["rows"].append(
        {
            **original,
            "track": "其他业务",
            "campus": "未映射校区",
            "history_new_process_d": 20,
            "history_new_final_n": 5,
            "history_new_final_d": 20,
        }
    )
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]  # unmapped current actual is a separate formal blocker
    evidence = next(row for row in result["withdrawal_evidence"] if row["student_type"] == "new")
    assert evidence["level"] == "DEPARTMENT"
    assert Decimal(evidence["final_n"]) == 6
    assert Decimal(evidence["final_d"]) == 30
    assert Decimal(evidence["remaining_rate"]) == Decimal("0.2")


def test_fractional_history_counts_are_not_treated_as_valid_integer_samples() -> None:
    baseline, snapshot, assumptions = inputs()
    for row in snapshot["rows"]:
        row["history_new_final_n"] = "1.5"
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert "HISTORY_COUNT_INVALID" in issue_codes(result)
    assert result_row(result, "秋季")["forecast_total"] is None


def test_frozen_finance_corrections_remain_signed_and_are_not_taxed_or_truncated() -> None:
    baseline, snapshot, assumptions = inputs()
    dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    snapshot["frozen_carryins"] = [
        {"season": "秋季", "origin_season": 20264, "dims": dims, "revenue": "-522.64"},
        {"season": "秋季", "origin_season": 20263, "dims": dims, "revenue": "-82.54"},
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert result_row(result, "秋季")["frozen_carryin"] == "-605.180000"
    assert result_row(result, "秋季")["forecast_revenue"] == "4594.820000"


def test_explicit_zero_current_unmapped_scope_only_contributes_history_pool() -> None:
    baseline, snapshot, assumptions = inputs()
    original = next(row for row in snapshot["rows"] if row["season"] == "秋季")
    original.update(
        history_new_process_n=0, history_new_process_d=10, history_new_final_n=1, history_new_final_d=10
    )
    snapshot["rows"].append(
        {
            **original,
            "track": "其他业务",
            "campus": "仅历史校区",
            "enrollment": 0,
            "new_business": 0,
            "new_base": 0,
            "old_base": 0,
            "actual_revenue": 0,
            "history_new_process_d": 20,
            "history_new_final_n": 5,
            "history_new_final_d": 20,
        }
    )
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert "SOURCE_UNMAPPED" not in issue_codes(result)
    evidence = next(row for row in result["withdrawal_evidence"] if row["student_type"] == "new")
    assert evidence["level"] == "DEPARTMENT"
    assert Decimal(evidence["final_n"]) == 6
    assert Decimal(evidence["final_d"]) == 30
    snapshot["rows"][-1]["actual_revenue"] = None
    missing_result = calculate_forecast(baseline, snapshot, assumptions)
    assert not missing_result["ready"]
    assert "SOURCE_UNMAPPED" in issue_codes(missing_result)


def test_signed_financial_corrections_never_allow_negative_people() -> None:
    baseline, snapshot, assumptions = inputs()
    dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    snapshot["frozen_carryins"] = [
        {"season": "秋季", "origin_season": 20264, "dims": dims, "revenue": "-522.64"}
    ]
    next(row for row in snapshot["rows"] if row["season"] == "秋季")["new_base"] = -1
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result_row(result, "秋季")["forecast_total"] is None
    assert "SOURCE_CLASS_BASE_MISMATCH" in issue_codes(result)


def test_negative_closed_actual_remains_visible_as_original_but_is_not_confirmable() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"][0]["actual_revenue"] = "-10.12"
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result_row(result, "暑假")["frozen_actual_revenue"] == "-10.120000"
    assert result_row(result, "暑假")["forecast_revenue"] is None
    assert "FINALIZED_ACTUAL_INVALID" in issue_codes(result)


def test_closed_complete_department_actual_survives_missing_budget_key_mapping_without_faking_rows() -> None:
    baseline, snapshot, assumptions = inputs(subjects=("数学", "英语"))
    snapshot["rows"] = [
        row for row in snapshot["rows"] if not (row["season"] == "暑假" and row["subject"] == "英语")
    ]
    snapshot["actual_totals"] = {"暑假": {"actual_revenue": "1060", "enrollment": 100}}
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result_row(result, "暑假", subject="英语")["forecast_revenue"] is None
    assert result["quarters"][0]["forecast_revenue"] == "1060.000000"
    assert result["quarters"][0]["forecast_total"] == 100
    assert result["annual"]["forecast_revenue"] is None  # missing carry/headcount still affects later seasons


def test_annual_uses_proven_department_closed_totals_not_incomplete_budget_mappings() -> None:
    baseline, snapshot, assumptions = inputs(
        subjects=("数学", "英语"), states={season: "FINALIZED" for season in SEASONS}
    )
    snapshot["rows"] = [row for row in snapshot["rows"] if row["subject"] == "数学"]
    snapshot["actual_totals"] = {season: {"actual_revenue": "1060", "enrollment": 100} for season in SEASONS}
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert all(result_row(result, season, subject="英语")["forecast_revenue"] is None for season in SEASONS)
    assert result["annual"]["forecast_revenue"] == "4240.000000"
    assert result["annual"]["forecast_total"] == 400
    assert result["annual"]["budget_revenue"] == "8000.000000"


def test_unproven_or_negative_department_actual_totals_are_not_validated_by_presence() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["actual_totals"] = {"暑假": {"actual_revenue": "-10", "enrollment": 100}}
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result["quarters"][0]["forecast_revenue"] is None
    assert result["quarters"][0]["frozen_actual_revenue"] == "-10.000000"
    assert "FINALIZED_TOTAL_INVALID" in issue_codes(result)
    snapshot["actual_totals"]["暑假"]["actual_revenue"] = "2000"
    mismatch = calculate_forecast(baseline, snapshot, assumptions)
    assert not mismatch["ready"]
    assert "FINALIZED_TOTAL_MISMATCH" in issue_codes(mismatch)


def test_full_financial_proof_preserves_signed_closed_details_and_department_actuals() -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    snapshot["rows"][0].update(actual_revenue="-10.12", actual_carryout="-82.54")
    prove_financial_snapshot(snapshot)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert result["rule_version"] == "natural-forecast-fy27-v3"
    assert result["signed_actuals_verified"]
    summer = result_row(result, "暑假")
    assert summer["forecast_revenue"] == "-10.120000"
    assert summer["frozen_actual_revenue"] == "-10.120000"
    assert summer["forecast_carryout"] == "-82.540000"
    assert summer["frozen_actual_carryout"] == "-82.540000"
    assert result["quarters"][0]["forecast_revenue"] == "-10.120000"
    assert result["annual"]["forecast_revenue"] == "3169.880000"
    assert "SOURCE_NEGATIVE_VALUE" not in issue_codes(result)


@pytest.mark.parametrize(
    "failed_check",
    [
        "bare_metric_verification",
        "partition_keys",
        "department_match",
        "department_amount",
        "metadata_changed",
        "different_partition",
        "incomplete_count",
        "different_aggregate_count",
        "financial_empty",
        "retained_row_changed",
        "retained_total_changed",
        "complement_failure",
    ],
)
def test_signed_actual_requires_complete_bound_financial_proof(failed_check: str) -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    snapshot["rows"][0].update(actual_revenue="-10.12", actual_carryout="-82.54")
    prove_financial_snapshot(snapshot)
    proof = snapshot["provenance"]["count_proof"]
    if failed_check == "bare_metric_verification":
        snapshot["provenance"] = {"metric_verified": True, "signed_actuals_verified": True}
    elif failed_check == "partition_keys":
        proof["financial_partition_mismatch_keys"] = 1
    elif failed_check == "department_match":
        proof["department_financial_reconciliation"]["暑假"]["matched"] = False
    elif failed_check == "department_amount":
        proof["department_financial_reconciliation"]["暑假"]["financial"] = "-11.12"
    elif failed_check == "metadata_changed":
        snapshot["provenance"]["metadata_stable_after_extraction"] = False
    elif failed_check == "different_partition":
        snapshot["provenance"]["dtdate"] = "2026-10-04"
    elif failed_check == "incomplete_count":
        proof["checked_complete_rows"] -= 1
    elif failed_check == "different_aggregate_count":
        proof["aggregated_rows"] -= 1
    elif failed_check == "financial_empty":
        proof["financial_rows"] = 0
    elif failed_check == "retained_row_changed":
        snapshot["rows"][0]["actual_revenue"] = "-12.12"
    elif failed_check == "retained_total_changed":
        snapshot["actual_totals"]["暑假"]["actual_revenue"] = "-12.12"
    elif failed_check == "complement_failure":
        proof["complement_failure_count"] = 1
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert not result["signed_actuals_verified"]
    assert result_row(result, "暑假")["forecast_revenue"] is None
    assert result["quarters"][0]["forecast_revenue"] is None
    assert "SOURCE_NEGATIVE_VALUE" in issue_codes(result)
    assert "FINALIZED_ACTUAL_INVALID" in issue_codes(result)


@pytest.mark.parametrize(
    "field", ["enrollment", "new_business", "new_base", "old_base", "history_new_final_n"]
)
def test_financial_signed_proof_never_allows_negative_people_or_history(field: str) -> None:
    baseline, snapshot, assumptions = inputs()
    summer = next(row for row in snapshot["rows"] if row["season"] == "暑假")
    summer.update(actual_revenue="-10.12", actual_carryout="-82.54")
    prove_financial_snapshot(snapshot)
    opened = next(row for row in snapshot["rows"] if row["season"] == "秋季")
    opened[field] = -1
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result["signed_actuals_verified"]  # financial proof is not a count override
    assert any(
        issue["code"] == "SOURCE_NEGATIVE_VALUE" and issue["detail"]["field"] == field
        for issue in result["issues"]
    )


def test_complete_financial_carryins_ignore_unused_signed_legacy_carryout_without_double_count() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"][0].update(actual_revenue="-10.12", actual_carryout="-82.54")
    prove_financial_snapshot(snapshot)
    dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
    snapshot["frozen_carryins"] = [
        {"season": "秋季", "origin_season": 20271, "dims": dims, "revenue": "-82.54"},
        {"season": "秋季", "origin_season": 20264, "dims": dims, "revenue": "5.25"},
    ]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert result_row(result, "秋季")["forecast_revenue"] == "5122.710000"
    assert result_row(result, "秋季")["frozen_carryin"] == "-77.290000"
    assert "ACTUAL_CARRYOUT_INVALID" not in issue_codes(result)


def test_proven_signed_legacy_actual_carryout_is_kept_net_when_complete_arrivals_are_absent() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"][0].update(actual_revenue="-10.12", actual_carryout="-82.54")
    prove_financial_snapshot(snapshot)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["ready"]
    assert result_row(result, "秋季")["forecast_revenue"] == "5117.460000"
    assert result_row(result, "暑假")["forecast_carryout"] == "-82.540000"


def test_unverified_unused_legacy_carryout_has_one_source_block_not_duplicate_finalized_block() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"][0]["actual_carryout"] = "-82.54"
    snapshot["frozen_carryins"] = []
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert "SOURCE_NEGATIVE_VALUE" in issue_codes(result)
    assert "ACTUAL_CARRYOUT_INVALID" not in issue_codes(result)
    assert result_row(result, "暑假")["frozen_actual_carryout"] == "-82.540000"
    assert result_row(result, "暑假")["forecast_carryout"] is None


def test_verified_signed_unmapped_actual_preserves_department_amount_but_mapping_still_blocks() -> None:
    baseline, snapshot, assumptions = inputs(states={season: "FINALIZED" for season in SEASONS})
    snapshot["rows"].append({**snapshot["rows"][0], "campus": "unmapped", "actual_revenue": "-10.12"})
    prove_financial_snapshot(snapshot)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result["signed_actuals_verified"]
    assert "SOURCE_UNMAPPED" in issue_codes(result)
    assert "SOURCE_NEGATIVE_VALUE" not in issue_codes(result)
    unmapped = result_row(result, "暑假", campus="unmapped")
    assert unmapped["forecast_revenue"] == "-10.120000"
    assert unmapped["frozen_actual_revenue"] == "-10.120000"
    assert result["quarters"][0]["forecast_revenue"] == "1049.880000"


def test_trial_open_missing_sources_use_baseline_but_closed_missing_actual_remains_unknown() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"] = []
    snapshot["frozen_carryins"] = []
    snapshot["actual_totals"] = {"暑假": {"actual_revenue": "33312209.53", "enrollment": 12642}}
    assumptions["trial_mode"] = True
    original = copy.deepcopy((baseline, snapshot, assumptions))
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"]
    assert result["trial_ready"]
    summer = result_row(result, "暑假")
    assert summer["data_status"] == "ACTUAL_UNAVAILABLE"
    assert summer["forecast_revenue"] is None and summer["forecast_total"] is None
    assert result["quarters"][0]["forecast_revenue"] == "33312209.530000"
    assert result["quarters"][0]["forecast_total"] == 12642
    autumn = result_row(result, "秋季")
    assert autumn["data_status"] == "BASELINE_ESTIMATE"
    assert autumn["forecast_total"] == 100 and autumn["forecast_new"] == 20
    assert autumn["forecast_revenue"] == "5000.000000"
    assert result_row(result, "寒假")["forecast_total"] == 63
    assert Decimal(result["annual"]["forecast_revenue"]) == Decimal("33312209.53") + sum(
        Decimal(quarter["forecast_revenue"]) for quarter in result["quarters"][1:]
    )
    assert any(note["code"] == "BASELINE_ESTIMATE" for note in result["trial_assumptions"])
    assert (baseline, snapshot, assumptions) == original


def test_scope_additional_is_isolated_and_missing_grade_peer_holds_size() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope()
    add_scope_source(snapshot, scope)
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["trial_ready"] and not result["ready"]
    assert "SOURCE_UNMAPPED" not in issue_codes(result)
    baseline_autumn = result_row(result, "秋季")
    scoped_autumn = next(
        row for row in result["rows"] if row["season"] == "秋季" and row["grade"] == "八年级"
    )
    assert baseline_autumn["additional"] == 10
    assert scoped_autumn["additional"] == 0
    assert scoped_autumn["forecast_total"] == 10
    assert scoped_autumn["forecast_revenue"] == "520.000000"
    assert not scoped_autumn["budget_covered"]
    for season in ("寒假", "春季"):
        scoped_future = next(
            row for row in result["rows"] if row["season"] == season and row["grade"] == "八年级"
        )
        assert scoped_future["forecast_total"] == 10
        assert scoped_future["forecast_new"] == 0
        assert scoped_future["renewal"] is None
        assert scoped_future["data_status"] == "TRIAL_SIZE_HELD"
    assert result["annual"]["budget_revenue"] == "4000.000000"
    assert result["quarters"][0]["forecast_revenue"] == "1260.000000"


def test_scope_manual_additional_changes_only_scope_and_campus_total_not_normal_budget_gap() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope()
    scope["additional"]["秋季"] = 3
    add_scope_source(snapshot, scope)
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    scoped = next(row for row in result["rows"] if row["season"] == "秋季" and row["grade"] == "八年级")
    assert result_row(result, "秋季")["additional"] == 10
    assert scoped["additional"] == 3 and scoped["forecast_total"] == 13
    campus = next(row for row in result["campuses"] if row["season"] == "秋季")
    assert campus["budgeted_additional"] == 10
    assert campus["unbudgeted_additional"] == 3
    assert campus["additional"] == 13


def test_trial_grade_peer_ratios_weight_parameters_not_average_or_saved_rounded_old_counts() -> None:
    baseline, snapshot, assumptions = inputs(campuses=("西直门", "other"))
    for row in baseline["rows"]:
        row["total"] = 100 if row["campus"] == "西直门" else 300
        row["old"] = 0  # saved rounded/top-up counts must not define inherited renewal
        row["renewal"] = "0.4" if row["campus"] == "西直门" else "0.8"
        row["share"] = "0.1" if row["campus"] == "西直门" else "0.3"
    scope = forecast_scope(grade="七年级", campus="new campus")
    add_scope_source(snapshot, scope)
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    winter = result_row(result, "寒假", campus="new campus")
    assert winter["future_method"] == "PEER_GRADE_WEIGHTED"
    assert Decimal(winter["renewal"]) == Decimal("0.7")
    assert winter["forecast_old"] == 7 and winter["forecast_new"] == 1
    evidence = next(
        note
        for note in result["trial_assumptions"]
        if note["code"] == "PEER_GRADE_WEIGHTED" and note["season"] == "寒假"
    )
    assert evidence["detail"]["renewal_n"] == "280"
    assert evidence["detail"]["renewal_d"] == 400
    assert evidence["detail"]["share_n"] == 40
    assert evidence["detail"]["share_d"] == 400


def test_financial_only_scope_carries_income_once_but_never_projects_students() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope(campus="pure finance", kind="FINANCIAL_ONLY")
    scope["financial"] = {}
    snapshot["frozen_carryins"] = [
        {"season": "秋季", "origin_season": 20264, "dims": scope["dims"], "revenue": "55.25"}
    ]
    snapshot["actual_totals"] = {"暑假": {"actual_revenue": "1060", "enrollment": 100}}
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["trial_ready"] and not result["ready"]
    assert "FROZEN_CARRYIN_UNMAPPED" not in issue_codes(result)
    autumn = result_row(result, "秋季", campus="pure finance")
    assert autumn["forecast_revenue"] == "55.250000"
    assert autumn["forecast_total"] == 0
    assert autumn["counts_not_applicable"]
    assert result["quarters"][1]["forecast_revenue"] == "5255.250000"
    assert result_row(result, "寒假", campus="pure finance")["forecast_total"] == 0
    assert result_row(result, "寒假", campus="pure finance")["forecast_revenue"] == "0.000000"


def test_scope_mapping_keeps_independent_product_and_counts_without_changing_baseline() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope(campus="canonical")
    add_scope_source(snapshot, scope)
    for row in snapshot["rows"]:
        if row["campus"] == "canonical":
            row["campus"] = "alias"
    assumptions.update(
        trial_mode=True,
        forecast_scopes=[scope],
        mappings=[
            {
                "source": {**scope["dims"], "campus": "alias"},
                "target": scope["dims"],
                "reason": "explicit alias only",
            }
        ],
    )
    original = copy.deepcopy(baseline)
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["trial_ready"]
    assert "MAPPING_TARGET_NOT_FOUND" not in issue_codes(result)
    scoped = next(row for row in result["rows"] if row["season"] == "秋季" and row["campus"] == "canonical")
    assert scoped["product"] == "智慧空间" and scoped["grade"] == "八年级"
    assert scoped["forecast_total"] == 10
    assert baseline == original


def test_trial_flag_does_not_allow_missing_frozen_department_totals_or_invalid_actual_counts() -> None:
    baseline, snapshot, assumptions = inputs()
    snapshot["rows"] = []
    snapshot["frozen_carryins"] = []
    assumptions["trial_mode"] = True
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["trial_ready"]
    assert result["quarters"][0]["forecast_revenue"] is None
    baseline, snapshot, assumptions = inputs()
    assumptions["trial_mode"] = True
    next(row for row in snapshot["rows"] if row["season"] == "秋季")["enrollment"] = -1
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"] and not result["trial_ready"]
    assert result_row(result, "秋季")["forecast_total"] is None


def test_trial_never_crosses_product_template_or_overrides_invalid_manual_value() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope()
    scope["dims"]["product"] = "distinct product"
    scope["financial"] = {}
    add_scope_source(snapshot, scope)
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["trial_ready"]
    assert not any(note["code"] == "PEER_PRODUCT_FINANCIAL_TEMPLATE" for note in result["trial_assumptions"])
    scope["financial"] = forecast_scope()["financial"]
    scope["financial"]["秋季"]["price"] = "-1"
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["trial_ready"]


def test_strict_scope_missing_future_rates_is_blocked_not_implicitly_held() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope()
    add_scope_source(snapshot, scope)
    assumptions["forecast_scopes"] = [scope]
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["ready"] and not result["trial_ready"]
    assert "RENEWAL_RATE_INVALID" in issue_codes(result)
    assert not any(row["data_status"] == "TRIAL_SIZE_HELD" for row in result["rows"])


@pytest.mark.parametrize(
    "failure", ["source_issue", "duplicate_baseline", "duplicate_mapping", "negative_people"]
)
def test_known_trial_department_results_cannot_hide_real_quality_blockers(failure: str) -> None:
    baseline, snapshot, assumptions = inputs()
    assumptions["trial_mode"] = True
    if failure == "source_issue":
        snapshot["issues"] = [{"code": "FINANCIAL_PARTITION_MISMATCH", "message": "source is not reconciled"}]
    elif failure == "duplicate_baseline":
        baseline["rows"].append(copy.deepcopy(baseline["rows"][0]))
    elif failure == "duplicate_mapping":
        dims = {key: baseline["rows"][0][key] for key in ("track", "product", "grade", "subject", "campus")}
        assumptions["mappings"] = [{"source": dims, "target": dims, "reason": "exact"}] * 2
    elif failure == "negative_people":
        snapshot["rows"][0]["old_base"] = -1
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["annual"]["forecast_revenue"] is not None
    assert not result["ready"] and not result["trial_ready"]


@pytest.mark.parametrize("explicit_field", ["renewal", "share"])
def test_partial_manual_future_parameter_is_preserved_and_only_missing_peer_field_fills(
    explicit_field: str,
) -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope(grade="七年级", campus="new campus")
    scope["financial"]["寒假"][explicit_field] = "0.8" if explicit_field == "renewal" else "0.4"
    add_scope_source(snapshot, scope)
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    winter = result_row(result, "寒假", campus="new campus")
    assert result["trial_ready"]
    if explicit_field == "renewal":
        assert Decimal(winter["renewal"]) == Decimal("0.8")
        assert winter["forecast_old"] == 8 and winter["forecast_new"] == 2
    else:
        assert Decimal(winter["renewal"]) == Decimal("0.5")
        assert winter["forecast_old"] == 5 and winter["forecast_new"] == 3
    assert scope["financial"]["寒假"][explicit_field] == ("0.8" if explicit_field == "renewal" else "0.4")


def test_partial_manual_parameter_without_peer_is_disclosed_as_unapplied_size_hold() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope()
    scope["financial"]["寒假"]["renewal"] = "0.8"
    add_scope_source(snapshot, scope)
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    winter = next(row for row in result["rows"] if row["season"] == "寒假" and row["grade"] == "八年级")
    assert winter["data_status"] == "TRIAL_SIZE_HELD"
    assert winter["forecast_total"] == 10 and winter["renewal"] is None
    evidence = next(
        note
        for note in result["trial_assumptions"]
        if note["season"] == "寒假" and note["code"] == "TRIAL_SIZE_HELD"
    )
    assert evidence["detail"]["unapplied_renewal"] == "0.8"
    assert scope["financial"]["寒假"]["renewal"] == "0.8"


@pytest.mark.parametrize("value", [1, -1, None])
@pytest.mark.parametrize("field", ["enrollment", "new_business", "new_base", "old_base"])
def test_financial_only_classification_cannot_hide_original_source_student_counts(
    field: str, value: int | None
) -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope(campus="pure finance", kind="FINANCIAL_ONLY")
    for season in ("暑假", "秋季"):
        row = {
            **snapshot["rows"][0],
            **scope["dims"],
            "season": season,
            "enrollment": 0,
            "new_business": 0,
            "new_base": 0,
            "old_base": 0,
            "actual_revenue": "55.25",
            "actual_carryout": "0",
        }
        if season == "秋季":
            row[field] = value
        snapshot["rows"].append(row)
    snapshot["frozen_carryins"] = []
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert not result["trial_ready"] and not result["ready"]
    assert "FINANCIAL_ONLY_SOURCE_HAS_STUDENTS" in issue_codes(result)
    assert result["quarters"][0]["forecast_revenue"] == "1115.250000"
    assert result_row(result, "秋季", campus="pure finance")["forecast_total"] is None


def test_verified_zero_financial_only_source_is_allowed_and_closed_income_is_not_doubled() -> None:
    baseline, snapshot, assumptions = inputs()
    scope = forecast_scope(campus="pure finance", kind="FINANCIAL_ONLY")
    snapshot["rows"].extend(
        [
            {
                **row,
                **scope["dims"],
                "enrollment": 0,
                "new_business": 0,
                "new_base": 0,
                "old_base": 0,
                "actual_revenue": "55.25",
                "actual_carryout": "0",
            }
            for row in list(snapshot["rows"])
        ]
    )
    snapshot["frozen_carryins"] = []
    prove_financial_snapshot(snapshot)
    assumptions.update(trial_mode=True, forecast_scopes=[scope])
    result = calculate_forecast(baseline, snapshot, assumptions)
    assert result["trial_ready"] and not result["ready"]
    assert "FINANCIAL_ONLY_SOURCE_HAS_STUDENTS" not in issue_codes(result)
    assert result["quarters"][0]["forecast_revenue"] == "1115.250000"
    assert result["quarters"][0]["forecast_total"] == 100
    pure_finance = result_row(result, "暑假", campus="pure finance")
    assert pure_finance["forecast_revenue"] == "55.250000"
    assert pure_finance["counts_not_applicable"] and pure_finance["forecast_total"] == 0
