"""Read-only workbook evidence regression and adversarial reconciliation checks."""

import copy
import json
from decimal import Decimal
from typing import Any

import pytest

from analysis.fy27_shadow.reconcile import OUT, audit, half_up, parameter_maps, replay_new


@pytest.fixture(scope="module")
def source() -> dict[str, Any]:
    path = OUT / "source-cells.json"
    if not path.exists():
        pytest.skip("Local FY27 workbook extraction not available")
    payload: dict[str, Any] = json.loads(path.read_text())
    return payload


@pytest.fixture(scope="module")
def result(source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    return audit(source)


def test_shadow_round_half_up_and_optional_excel_only_topup() -> None:
    assert half_up(Decimal("2.5")) == 3
    assert replay_new(5, Decimal(".2"), 24, False) == (1, 1)
    assert replay_new(5, Decimal(".2"), 24, True) == (19, 1)
    with pytest.raises(ValueError):
        replay_new(0, Decimal(1), 24, False)


def test_shadow_primary_grain_and_replay(result: tuple[dict[str, Any], Any]) -> None:
    summary, _ = result
    assert (summary["scope_rows"], summary["season_rows"], summary["unique_dimensions"]) == (238, 952, 238)
    assert summary["formula_failures"] == 0
    assert summary["primary_sheet_cached_errors"] == []
    assert summary["entry_parameter_total"] == summary["entry_new_total"] == 8007
    assert abs(summary["annual_targets"][0]["excel_yuan"] - Decimal("113004618.592924502")) < Decimal(
        ".00001"
    )


def test_shadow_negative_opening_remains_blocked_not_clamped(result: tuple[dict[str, Any], Any]) -> None:
    summary, _ = result
    assert summary["negative_opening_rows"] == [151]
    assert summary["negative_opening_total"] == Decimal("-.01")
    assert summary["full_revenue_kernel_error"] == "NEGATIVE_REVENUE_INPUT"
    assert summary["kernel_revenue_rows"] == 948
    assert len(summary["kernel_revenue_exclusions"]) == 4
    assert summary["kernel_revenue_failures"] == 0
    assert summary["kernel_revenue_max_abs_delta_yuan"] < Decimal(".000001")


def test_shadow_no_invented_plans_or_publishing_success(result: tuple[dict[str, Any], Any]) -> None:
    summary, _ = result
    assert summary["strict_headcount_publishable"] is False
    assert summary["strict_headcount_complete_rows"] == 0
    assert summary["strict_headcount_incomplete_rows"] == 952
    assert summary["opening_old_students"] == 3876
    assert summary["topup_row_seasons"] == 183
    assert summary["same_input_class_size_validation_codes"] == {
        "BELOW_MINIMUM_CLASS_SIZE": 3,
        "ENABLED_COMBINATION_ALL_SEASONS_ZERO": 61,
    }


def test_shadow_aggregate_discount_not_average_detail_percent(result: tuple[dict[str, Any], Any]) -> None:
    summary, _ = result
    annual = summary["discount_targets"][0]
    assert annual["actual_percent"] == Decimal("119228804.600") / Decimal("136718075") * 100
    assert all(not row["within_0_05pp"] for row in summary["discount_targets"])


def test_shadow_cache_mutation_is_reported(source: dict[str, Any]) -> None:
    changed = copy.deepcopy(source)
    changed["sheets"]["滚动表"]["rows"][1]["cells"]["W"]["value"] += 1
    summary, details = audit(changed)
    assert summary["formula_failures"] == 1
    assert [x["cell"] for x in details["formula-checks"] if not x["passed"]] == ["W2"]


def test_shadow_unknown_formula_fails_closed_even_with_same_cache(source: dict[str, Any]) -> None:
    changed = copy.deepcopy(source)
    changed["sheets"]["滚动表"]["rows"][1]["cells"]["AW"]["formula"] = "=AS2*AV2/1.07"
    with pytest.raises(ValueError, match="Unsupported formula at AW2"):
        audit(changed)


def test_shadow_missing_lookup_and_matched_blank_are_distinct(
    source: dict[str, Any], result: tuple[dict[str, Any], Any]
) -> None:
    summary, _ = result
    assert len([x for x in summary["missing_lookup_fallbacks"] if x["block"] == "F"]) == 27
    assert len(summary["matched_blank_parameter_cells"]) == 105
    blank = summary["matched_blank_parameter_cells"][0]
    assert blank["found"] is True and blank["value"] == 0
    maps, duplicates = parameter_maps(source["sheets"]["参数表"]["rows"])
    assert not duplicates
    assert maps["AB"][blank["key"]][0] is None
