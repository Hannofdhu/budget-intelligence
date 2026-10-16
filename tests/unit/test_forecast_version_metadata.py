"""Read-only version presentation metadata must not alter the saved ledger."""

from copy import deepcopy
from typing import Any

import pytest

from packages.forecast.database_store import PostgreSQLForecastStore
from packages.forecast.store import ForecastStore, fingerprint, forecast_generation, forecast_version_summary
from packages.forecast.trend import project_forecast_versions

Json = dict[str, Any]


def saved_event(sequence: int, *, actor: str | None = "budget-owner", automatic_source: bool = False) -> Json:
    snapshot = {
        "provenance": {"refresh_mode": "AUTOMATIC" if automatic_source else "MANUAL"},
        "source_data": {"sensitive_rows": ["never include in history"]},
    }
    version: Json = {
        "id": f"version-{sequence}",
        "sequence": sequence,
        "baseline_hash": "baseline",
        "as_of": "2026-10-15",
        "created_at": f"2026-10-16T0{sequence}:07:54+00:00",
        "created_by": actor,
        "reason": f"saved reason {sequence}",
        "snapshot": snapshot,
        "snapshot_hash": fingerprint(snapshot),
        "calculation": {"ready": True, "annual": {"forecast_revenue": "100.001"}},
        "assumptions": {"states": {}},
    }
    return {"kind": "VERSION", "version": version}


@pytest.mark.parametrize("database", [False, True])
def test_summary_preserves_original_events_hashes_and_saved_version_identity(database: bool) -> None:
    first = saved_event(1)
    confirmation: Json = {
        "kind": "CONFIRMATION",
        "version_id": "version-1",
        "calculation_hash": fingerprint(first["version"]["calculation"]),
        "actor": "confirmation-owner",
        "created_at": "2026-10-16T02:00:00+00:00",
        "reason": "reviewed the saved prediction",
    }
    latest = saved_event(2, actor="forecast-daily-scheduler", automatic_source=True)
    events = [first, confirmation, latest]
    original, original_hash = deepcopy(events), fingerprint(events)
    project = PostgreSQLForecastStore._view if database else ForecastStore()._view

    view = project(events)

    assert events == original
    assert fingerprint(events) == original_hash
    assert view["latest_version_id"] == "version-2"
    assert view["version"]["id"] == "version-2"
    assert [item["sequence"] for item in view["history"]] == [2, 1]
    latest_summary, confirmed_summary = view["history"]
    assert latest_summary["status"] == "DRAFT"
    assert latest_summary["generation"] == "AUTOMATIC"
    assert latest_summary["confirmation"] is None
    assert confirmed_summary["status"] == "CONFIRMED"
    assert confirmed_summary["id"] == first["version"]["id"]
    assert confirmed_summary["sequence"] == first["version"]["sequence"]
    assert confirmed_summary["created_at"] == first["version"]["created_at"]
    assert confirmed_summary["reason"] == first["version"]["reason"]
    assert confirmed_summary["created_by"] == first["version"]["created_by"]
    assert confirmed_summary["confirmation"] == {
        "actor": confirmation["actor"],
        "at": confirmation["created_at"],
        "reason": confirmation["reason"],
    }
    assert confirmed_summary["generation"] == "MANUAL"


def test_history_whitelist_never_includes_raw_snapshot_source_data_or_calculation() -> None:
    event = saved_event(1)
    event["version"]["source_data"] = {"private": "no raw data in history"}
    event["version"]["unrelated_secret"] = "never include"
    summary = ForecastStore()._view([event])["history"][0]
    assert set(summary) == {
        "id",
        "sequence",
        "status",
        "as_of",
        "created_at",
        "reason",
        "created_by",
        "confirmation",
        "generation",
    }
    assert "never include" not in str(summary)
    assert "snapshot" not in summary
    assert "source_data" not in summary
    assert "calculation" not in summary


def test_generation_uses_version_author_not_automatic_source_reused_by_manual_revision() -> None:
    version = saved_event(1, actor="budget-owner", automatic_source=True)["version"]
    original_hash = fingerprint(version)
    assert forecast_generation(version) == "MANUAL"
    assert fingerprint(version) == original_hash


@pytest.mark.parametrize(
    "actor,expected", [("forecast-daily-scheduler", "AUTOMATIC"), ("owner", "MANUAL"), (None, None)]
)
def test_trend_and_history_share_the_same_saved_author_semantics(
    actor: str | None, expected: str | None
) -> None:
    event = saved_event(1, actor=actor, automatic_source=True)
    history = ForecastStore()._view([event])["history"][0]
    version = {**event["version"], "status": "DRAFT"}
    points = project_forecast_versions([version], baseline_hash="baseline")
    assert history["generation"] == points[0]["generation"] == expected


@pytest.mark.parametrize(
    "actor,snapshot",
    [
        (None, {"provenance": {"refresh_mode": "AUTOMATIC"}}),
        ("", {"provenance": {"refresh_mode": "AUTOMATIC"}}),
        ("  ", {"provenance": {"refresh_mode": "AUTOMATIC"}}),
        ("forecast-daily-scheduler", None),
        ("forecast-daily-scheduler", {}),
        ("forecast-daily-scheduler", {"provenance": None}),
        ("forecast-daily-scheduler", {"provenance": {"refresh_mode": "MANUAL"}}),
    ],
)
def test_unknown_generation_is_not_fabricated_as_manual(actor: str | None, snapshot: Any) -> None:
    assert forecast_generation({"created_by": actor, "snapshot": snapshot}) is None


def test_old_records_without_optional_metadata_still_project_with_explicit_unknowns() -> None:
    event = saved_event(1)
    for key in ("created_by", "reason", "snapshot"):
        del event["version"][key]
    summary = ForecastStore()._view([event])["history"][0]
    assert summary["created_by"] is None
    assert summary["reason"] is None
    assert summary["generation"] is None
    assert summary["confirmation"] is None


def test_confirmation_projection_is_whitelisted_and_does_not_infer_missing_metadata() -> None:
    version = saved_event(1)["version"]
    version["status"] = "CONFIRMED"
    version["confirmation"] = {"at": "2026-10-16T02:00:00+00:00", "source_data": {"private": "secret"}}
    summary = forecast_version_summary(version)
    assert summary["confirmation"] == {"actor": None, "at": "2026-10-16T02:00:00+00:00", "reason": None}
    summary["confirmation"]["at"] = "changed only projected metadata"
    assert version["confirmation"]["at"] == "2026-10-16T02:00:00+00:00"


def test_invalid_optional_metadata_does_not_pass_nested_objects_into_history() -> None:
    event = saved_event(1)
    event["version"].update(reason={"source_data": "secret"}, created_by=["not a recorded identity"])
    summary = ForecastStore()._view([event])["history"][0]
    assert summary["reason"] is None
    assert summary["created_by"] is None
    assert summary["generation"] is None


def test_empty_history_has_explicit_empty_latest_pointer() -> None:
    view = ForecastStore()._view([])
    assert view["latest_version_id"] is None
    assert view["version"] is None
    assert view["history"] == []
