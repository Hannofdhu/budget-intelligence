from copy import deepcopy
from typing import Any

import pytest

from packages.forecast.store import ForecastStoreError, fingerprint
from packages.forecast.trend import TrendVersionNotFound, build_forecast_trend

Json = dict[str, Any]
SEASONS = ("暑假", "秋季", "寒假", "春季")


def saved(sequence: int, at: str, *, ready: bool = True, automatic: bool = False) -> Json:
    snapshot: Json = {
        "as_of": "2026-10-14",
        "actual_totals": {season: {"actual_revenue": str(index + 1)} for index, season in enumerate(SEASONS)},
        "provenance": {"refresh_mode": "AUTOMATIC" if automatic else "MANUAL"},
    }
    return {
        "id": f"version-{sequence}",
        "parent_id": f"version-{sequence - 1}" if sequence > 1 else None,
        "sequence": sequence,
        "created_at": at,
        "created_by": "forecast-daily-scheduler" if automatic else "synthetic-owner",
        "as_of": snapshot["as_of"],
        "baseline_hash": "baseline",
        "snapshot": snapshot,
        "snapshot_hash": fingerprint(snapshot),
        "status": "DRAFT",
        "calculation": {
            "ready": ready,
            "annual": {"forecast_revenue": f"{sequence}00000.123456789"},
            "quarters": [{"season": season, "forecast_revenue": "10000.123456789"} for season in SEASONS],
        },
    }


def refresh_hash(version: Json) -> None:
    version["snapshot_hash"] = fingerprint(version["snapshot"])


def test_uses_last_successful_version_per_shanghai_day_not_source_partition() -> None:
    first = saved(1, "2026-10-14T17:00:00+00:00")  # Oct 15 Shanghai
    latest = saved(2, "2026-10-15T15:59:00+00:00", automatic=True)
    incomplete = saved(3, "2026-10-15T15:59:59+00:00", ready=False)
    next_day = saved(4, "2026-10-15T16:00:00+00:00")
    original = deepcopy([next_day, incomplete, latest, first])
    result = build_forecast_trend(original, baseline_hash="baseline", days="all")
    assert [p["date"] for p in result["points"]] == ["2026-10-15", "2026-10-16"]
    assert [p["sequence"] for p in result["points"]] == [2, 4]
    assert result["points"][0]["generation"] == "AUTOMATIC"
    assert result["points"][1]["generation"] == "MANUAL"
    assert result["points"][0]["as_of"] == "2026-10-14"
    assert result["omitted_incomplete_count"] == 1
    assert original == [next_day, incomplete, latest, first]


def test_historical_cutoff_precedes_daily_selection_and_later_versions_never_leak() -> None:
    first = saved(1, "2026-10-15T09:00:00+08:00")
    later_same_day = saved(2, "2026-10-15T10:00:00+08:00")
    later_day = saved(3, "2026-10-16T09:30:00+08:00")
    result = build_forecast_trend(
        [later_day, later_same_day, first], baseline_hash="baseline", through_version="version-1"
    )
    assert [p["sequence"] for p in result["points"]] == [1]


def test_manual_revision_of_an_automatic_snapshot_is_still_a_manual_version() -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00", automatic=True)
    version["created_by"] = "synthetic-owner"
    assert build_forecast_trend([version], baseline_hash="baseline")["points"][0]["generation"] == "MANUAL"


def test_annual_actual_preserves_widely_separated_decimal_exponents() -> None:
    from decimal import Decimal, localcontext

    version = saved(1, "2026-10-15T09:30:00+08:00")
    amounts = ("1E+100", "1E-100", "0", "0")
    for season, amount in zip(SEASONS, amounts, strict=True):
        version["snapshot"]["actual_totals"][season]["actual_revenue"] = amount
    refresh_hash(version)
    with localcontext() as context:
        context.prec = 220
        expected = sum((Decimal(value) for value in amounts), Decimal(0))
    actual = build_forecast_trend([version], baseline_hash="baseline")["points"][0]["periods"]["全年"][
        "actual_revenue"
    ]
    assert Decimal(actual) == expected


@pytest.mark.parametrize("days,expected", [("30", [3, 4]), ("90", [1, 2, 3, 4]), ("all", [1, 2, 3, 4])])
def test_window_ends_at_archive_date_not_current_clock(days: Any, expected: list[int]) -> None:
    versions = [
        saved(1, "2020-08-31T09:30:00+08:00"),
        saved(2, "2020-09-16T09:30:00+08:00"),
        saved(3, "2020-09-17T09:30:00+08:00"),
        saved(4, "2020-10-16T09:30:00+08:00"),
    ]
    assert [
        p["sequence"] for p in build_forecast_trend(versions, baseline_hash="baseline", days=days)["points"]
    ] == expected


def test_numbers_remain_exact_signed_raw_yuan_and_actual_is_not_final_forecast() -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00")
    version["snapshot"]["actual_totals"]["暑假"]["actual_revenue"] = "-0.123456789012345678901234567890"
    version["snapshot"]["actual_totals"]["秋季"]["actual_revenue"] = "99999999.999999999999999999999999"
    version["status"] = "CONFIRMED"
    refresh_hash(version)
    point = build_forecast_trend([version], baseline_hash="baseline")["points"][0]
    assert point["status"] == "CONFIRMED"
    assert point["periods"]["暑假"]["actual_revenue"] == "-0.123456789012345678901234567890"
    assert point["periods"]["暑假"]["forecast_revenue"] == "10000.123456789"
    assert point["periods"]["全年"]["actual_revenue"] == "100000006.876543210987654321098764432110"
    assert point["periods"]["全年"]["forecast_revenue"] == "100000.123456789"


@pytest.mark.parametrize("invalid", [None, "", "-", "NaN", "Infinity", "-Infinity", True, False])
def test_missing_or_invalid_actual_is_a_gap_not_zero_or_an_invented_annual_sum(invalid: object) -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00")
    version["snapshot"]["actual_totals"]["秋季"]["actual_revenue"] = invalid
    refresh_hash(version)
    periods = build_forecast_trend([version], baseline_hash="baseline")["points"][0]["periods"]
    assert periods["秋季"]["actual_revenue"] is None
    assert periods["秋季"]["actual_status"] == "UNAVAILABLE"
    assert periods["全年"]["actual_revenue"] is None
    assert periods["全年"]["forecast_revenue"] is not None


def test_explicit_real_zero_is_available_but_no_snapshot_field_cannot_be_backfilled() -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00")
    version["snapshot"]["actual_totals"]["秋季"]["actual_revenue"] = "0"
    refresh_hash(version)
    periods = build_forecast_trend([version], baseline_hash="baseline")["points"][0]["periods"]
    assert periods["秋季"] == {
        "actual_revenue": "0",
        "actual_status": "AVAILABLE",
        "forecast_revenue": "10000.123456789",
    }
    del version["snapshot"]["actual_totals"]
    refresh_hash(version)
    periods = build_forecast_trend([version], baseline_hash="baseline")["points"][0]["periods"]
    assert all(value["actual_revenue"] is None for value in periods.values())


@pytest.mark.parametrize(
    "issue",
    ["FINANCIAL_PARTITION_MISMATCH", "FINANCIAL_DEPARTMENT_MISMATCH", "SUMMARY_ACTUAL_REVENUE_INVALID"],
)
def test_failed_actual_reconciliation_cannot_become_a_trusted_observation(issue: str) -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00")
    version["snapshot"]["issues"] = [{"code": issue, "season": "秋季"}]
    refresh_hash(version)
    periods = build_forecast_trend([version], baseline_hash="baseline")["points"][0]["periods"]
    assert periods["秋季"]["actual_revenue"] is None
    assert periods["暑假"]["actual_revenue"] == "1"
    assert periods["全年"]["actual_revenue"] is None


def test_failed_recorded_department_proof_invalidates_only_its_season() -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00")
    version["snapshot"]["provenance"]["count_proof"] = {
        "department_financial_reconciliation": {"春季": {"matched": False}}
    }
    refresh_hash(version)
    periods = build_forecast_trend([version], baseline_hash="baseline")["points"][0]["periods"]
    assert periods["春季"]["actual_revenue"] is None
    assert periods["寒假"]["actual_revenue"] == "3"


def test_duplicate_quarters_are_unknown_not_arbitrary_first_match() -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00")
    version["calculation"]["quarters"].append({"season": "秋季", "forecast_revenue": "999"})
    periods = build_forecast_trend([version], baseline_hash="baseline")["points"][0]["periods"]
    assert periods["秋季"]["forecast_revenue"] is None


def test_cutoff_and_baseline_and_snapshot_integrity_are_enforced() -> None:
    version = saved(1, "2026-10-15T09:30:00+08:00")
    with pytest.raises(TrendVersionNotFound):
        build_forecast_trend([version], baseline_hash="baseline", through_version="other-department")
    with pytest.raises(ForecastStoreError, match="基准不一致"):
        build_forecast_trend([version], baseline_hash="different")
    version["snapshot"]["actual_totals"]["暑假"]["actual_revenue"] = "2"
    with pytest.raises(ForecastStoreError, match="完整性"):
        build_forecast_trend([version], baseline_hash="baseline")


def test_empty_or_all_incomplete_never_fabricates_points() -> None:
    assert build_forecast_trend([], baseline_hash="baseline")["points"] == []
    version = saved(1, "2026-10-15T09:30:00+08:00", ready=False)
    result = build_forecast_trend([version], baseline_hash="baseline")
    assert result["points"] == []
    assert result["omitted_incomplete_count"] == 1


@pytest.mark.parametrize("at", ["", "2026-10-15T09:30:00", "not-a-date"])
def test_bad_saved_time_is_reported_not_replaced_by_today(at: str) -> None:
    with pytest.raises(ForecastStoreError, match="保存时间"):
        build_forecast_trend([saved(1, at)], baseline_hash="baseline")


def test_previous_version_is_exact_intraday_parent_not_previous_daily_point() -> None:
    versions = [
        saved(1, "2026-10-14T09:30:00+08:00"),
        saved(2, "2026-10-15T09:30:00+08:00"),
        saved(3, "2026-10-15T10:30:00+08:00"),
    ]
    before = deepcopy(versions)
    points = build_forecast_trend(list(reversed(versions)), baseline_hash="baseline")["points"]
    assert [point["sequence"] for point in points] == [1, 3]
    assert points[0]["previous_version"] is None
    previous = points[1]["previous_version"]
    assert previous["version_id"] == "version-2"
    assert previous["sequence"] == 2
    assert previous["periods"]["全年"]["forecast_revenue"] == "200000.123456789"
    assert set(previous) == {"version_id", "sequence", "created_at", "as_of", "periods"}
    assert versions == before


def test_previous_incomplete_version_is_not_replaced_with_an_older_complete_version() -> None:
    versions = [
        saved(1, "2026-10-14T09:30:00+08:00"),
        saved(2, "2026-10-15T09:30:00+08:00", ready=False),
        saved(3, "2026-10-16T09:30:00+08:00"),
    ]
    points = build_forecast_trend(versions, baseline_hash="baseline")["points"]
    assert [point["sequence"] for point in points] == [1, 3]
    assert points[1]["previous_version"]["sequence"] == 2
    assert points[1]["previous_version"]["periods"] is None


def test_previous_version_before_window_remains_exact_and_never_looks_ahead() -> None:
    versions = [
        saved(1, "2026-08-31T09:30:00+08:00"),
        saved(2, "2026-10-15T09:30:00+08:00"),
        saved(3, "2026-10-15T10:30:00+08:00"),
    ]
    points = build_forecast_trend(versions, baseline_hash="baseline", days="30", through_version="version-2")[
        "points"
    ]
    assert [point["sequence"] for point in points] == [2]
    assert points[0]["previous_version"]["sequence"] == 1
    assert "version-3" not in str(points)


@pytest.mark.parametrize("parent", [None, "unknown", "version-3", "version-1"])
def test_missing_future_or_nonadjacent_parent_never_fabricates_comparison(parent: str | None) -> None:
    versions = [saved(index, "2026-10-15T09:30:00+08:00") for index in (1, 2, 3)]
    versions[2]["parent_id"] = parent
    point = build_forecast_trend(versions, baseline_hash="baseline")["points"][0]
    assert point["sequence"] == 3
    assert point["previous_version"] is None
