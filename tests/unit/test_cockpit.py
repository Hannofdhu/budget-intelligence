from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from packages.cockpit.projection import CockpitProjectionError, project_cockpit
from packages.forecast.store import fingerprint

Json = dict[str, Any]


def _fixture() -> tuple[Json, Json, Json]:
    dimensions = dict(track="空间", campus="甲校区", product="普通", grade="七年级", subject="数学")
    rows = [{**dimensions, "season": season} for season in ("暑假", "秋季", "寒假", "春季")]
    baseline = {
        "sha256": "immutable",
        "fiscal_year": 2027,
        "rows": [{**row, "revenue": {"value": "100"}} for row in rows],
    }
    snapshot = {
        "rows": [
            {**row, "actual_revenue": value} for row, value in zip(rows, ["40", "-2", "0", "1"], strict=True)
        ],
        "actual_totals": {
            row["season"]: {"actual_revenue": value}
            for row, value in zip(rows, ["40", "-2", "0", "1"], strict=True)
        },
    }
    calculation = {"ready": True, "rows": [{**row, "forecast_revenue": "90"} for row in rows]}
    forecast = {
        "id": "p26",
        "sequence": 26,
        "status": "DRAFT",
        "baseline_hash": "immutable",
        "as_of": "2026-10-23",
        "snapshot": snapshot,
        "snapshot_hash": fingerprint(snapshot),
        "calculation": calculation,
        "calculation_hash": fingerprint(calculation),
        "assumptions": {},
    }
    draft = {
        "id": "n25",
        "sequence": 25,
        "status": "DRAFT",
        "source": {"id": "p20", "sequence": 20, "as_of": "2026-10-15"},
        "inputs": {"fiscal_year": 2028, "target_revenue_yuan": "1500"},
        "calculation": {"annual": {}, "rows": [{**row, "revenue_yuan": "120"} for row in rows]},
    }
    return baseline, forecast, draft


def test_four_saved_outputs_reconcile_without_mutation_or_engine_calls() -> None:
    inputs = _fixture()
    before = deepcopy(inputs)
    result = project_cockpit(*inputs)
    assert result["read_only"] and result["unit"] == "yuan"
    assert result["totals"] == {"budget": "400", "actual": "39", "forecast": "360", "next_budget": "480"}
    assert result["sources"]["forecast"]["sequence"] == 26
    assert result["sources"]["next_budget"]["source"]["sequence"] == 20
    assert result["sources"]["next_budget"]["status"] == "DRAFT"
    assert inputs == before


def test_legacy_store_verified_calculation_has_no_separate_hash() -> None:
    baseline, forecast, draft = _fixture()
    del forecast["calculation_hash"]
    result = project_cockpit(baseline, forecast, draft)
    assert result["sources"]["forecast"]["calculation_hash"] == fingerprint(forecast["calculation"])


@pytest.mark.parametrize("field", ["snapshot", "calculation", "baseline_hash"])
def test_source_integrity_mismatch_fails_closed(field: str) -> None:
    baseline, forecast, draft = _fixture()
    if field == "baseline_hash":
        forecast[field] = "other"
    else:
        forecast[field]["tampered"] = True
    with pytest.raises(CockpitProjectionError):
        project_cockpit(baseline, forecast, draft)


def test_actuals_require_quarter_reconciliation_and_missing_is_not_zero() -> None:
    baseline, forecast, draft = _fixture()
    forecast["snapshot"]["actual_totals"]["秋季"]["actual_revenue"] = "9"
    forecast["snapshot_hash"] = fingerprint(forecast["snapshot"])
    result = project_cockpit(baseline, forecast, draft)
    assert result["totals"]["actual"] is None
    assert result["totals"]["forecast"] == "360"
    assert any("秋季" in note for note in result["notes"])


def test_explicit_mapping_preserves_unbudgeted_and_negative_actuals() -> None:
    baseline, forecast, draft = _fixture()
    source = {**baseline["rows"][0], "campus": "旧甲名"}
    forecast["snapshot"]["rows"][0]["campus"] = "旧甲名"
    forecast["assumptions"]["mappings"] = [
        {"source": source, "target": baseline["rows"][0], "reason": "保存别名"}
    ]
    forecast["snapshot_hash"] = fingerprint(forecast["snapshot"])
    result = project_cockpit(baseline, forecast, draft)
    assert len(result["rows"]) == 4
    assert result["totals"]["actual"] == "39"
    assert any(row["actual"] == "-2" for row in result["rows"])


def test_no_sources_and_next_year_only_coverage_never_become_current_year_zeros() -> None:
    baseline, _, draft = _fixture()
    draft["calculation"]["rows"][0]["campus"] = "新校区"
    result = project_cockpit(baseline, None, draft)
    assert result["totals"]["actual"] is None and result["totals"]["forecast"] is None
    new = next(row for row in result["rows"] if row["campus"] == "新校区")
    assert not new["covered"]["actual"] and new["actual"] is None
    assert new["next_budget"] == "120"


def test_explicitly_excluded_closed_actual_rows_are_not_forecast_zero_or_unknown() -> None:
    baseline, forecast, draft = _fixture()
    forecast["calculation"]["rows"].append(
        {
            **baseline["rows"][0],
            "campus": "空预算",
            "forecast_revenue": None,
            "included_in_closed_actual_total": False,
        }
    )
    forecast["calculation_hash"] = fingerprint(forecast["calculation"])
    result = project_cockpit(baseline, forecast, draft)
    assert Decimal(result["totals"]["forecast"]) == Decimal(360)
    assert not next(row for row in result["rows"] if row["campus"] == "空预算")["covered"]["forecast"]


def test_mismatched_fiscal_year_or_invalid_quarter_cannot_be_silently_dropped() -> None:
    baseline, forecast, draft = _fixture()
    draft["inputs"]["fiscal_year"] = 2029
    with pytest.raises(CockpitProjectionError):
        project_cockpit(baseline, forecast, draft)
    draft["inputs"]["fiscal_year"] = 2028
    draft["calculation"]["rows"][0]["season"] = "未知季度"
    with pytest.raises(CockpitProjectionError):
        project_cockpit(baseline, forecast, draft)


def test_excluded_forecast_rows_still_require_valid_quarter() -> None:
    baseline, forecast, draft = _fixture()
    forecast["calculation"]["rows"].append(
        {
            **baseline["rows"][0],
            "season": "无效季度",
            "forecast_revenue": None,
            "included_in_closed_actual_total": False,
        }
    )
    forecast["calculation_hash"] = fingerprint(forecast["calculation"])
    with pytest.raises(CockpitProjectionError):
        project_cockpit(baseline, forecast, draft)
