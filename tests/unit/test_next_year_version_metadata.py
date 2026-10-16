"""A planning version's source year comes only from its frozen source metadata."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from packages.forecast.store import fingerprint
from packages.next_year.store import NextYearStore, source_fiscal_year

Json = dict[str, Any]


def append_source(
    store: NextYearStore, source: Json, parent_id: str | None = None, frozen: Json | None = None
) -> Json:
    return store.append_draft(
        department="metadata-test-department",
        actor="test-owner",
        request_id=source["id"],
        request_hash=fingerprint(source),
        parent_id=parent_id,
        source=source,
        inputs={"fiscal_year": 2029, "target_revenue_yuan": None},
        calculation={"annual": {"revenue_yuan": "10.001"}},
        reason="prepare with an explicitly saved source",
        operation="PREPARE",
        source_snapshot=frozen or {"forecast": {"id": source["id"]}, "baseline": {"fiscal_year": 2099}},
        observed_source={"id": "latest-forecast", "calculation_hash": "observed", "fiscal_year": 2098},
    )


def saved_files(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in root.glob("event-*.json")}


def legacy_basis(year: int) -> tuple[Json, Json]:
    baseline_hash = fingerprint(f"baseline-for-{year}")
    forecast = {
        "id": f"forecast-{year}",
        "sequence": 17,
        "status": "CONFIRMED",
        "as_of": "2026-10-15",
        "baseline_hash": baseline_hash,
        "snapshot_hash": fingerprint("a frozen snapshot"),
        "calculation": {"annual": {"forecast_revenue": "10.001", "forecast_total": 12}},
    }
    source = {
        key: forecast[key] for key in ("id", "sequence", "status", "as_of", "baseline_hash", "snapshot_hash")
    }
    source["calculation_hash"] = fingerprint(forecast["calculation"])
    frozen = {"baseline": {"sha256": baseline_hash, "fiscal_year": year}, "forecast": forecast}
    return source, frozen


@pytest.mark.parametrize("source_year", [2025, 2027, 2030, None])
def test_history_uses_frozen_source_year_without_inference_or_mutation(
    tmp_path: Path, source_year: int | None
) -> None:
    store = NextYearStore(tmp_path)
    source: Json = {"id": "source-first", "calculation_hash": "calculation-first", "sequence": 17}
    if source_year is not None:
        source["fiscal_year"] = source_year
    original_source = deepcopy(source)
    result = append_source(store, source)
    original_draft = deepcopy(result["draft"])
    events = deepcopy(store._events("metadata-test-department"))
    original_hash, original_files = fingerprint(events), saved_files(tmp_path)

    view = store.current("metadata-test-department")

    assert view["history"][0]["source_fiscal_year"] == source_year
    assert view["history"][0]["fiscal_year"] == 2029
    assert source == original_source
    assert view["draft"] == original_draft
    assert store._events("metadata-test-department") == events
    assert fingerprint(store._events("metadata-test-department")) == original_hash
    assert saved_files(tmp_path) == original_files
    if source_year is None:
        assert "fiscal_year" not in view["draft"]["source"]
        assert view["history"][0]["source_fiscal_year"] is None


def test_each_history_row_keeps_its_own_source_year_not_the_latest_source_year(tmp_path: Path) -> None:
    store = NextYearStore(tmp_path)
    first = append_source(
        store, {"id": "source-first", "calculation_hash": "calculation-first", "fiscal_year": 2025}
    )["draft"]
    original_files = saved_files(tmp_path)
    append_source(
        store,
        {"id": "source-second", "calculation_hash": "calculation-second", "fiscal_year": 2027},
        first["id"],
    )
    view = store.current("metadata-test-department")
    assert [(item["source_id"], item["source_fiscal_year"]) for item in view["history"]] == [
        ("source-second", 2027),
        ("source-first", 2025),
    ]
    assert all(saved_files(tmp_path)[name] == content for name, content in original_files.items())


@pytest.mark.parametrize("year", [2025, 2027, 2030])
def test_legacy_history_projects_its_verified_frozen_year_without_changing_source_hashes(
    tmp_path: Path, year: int
) -> None:
    store = NextYearStore(tmp_path)
    source, frozen = legacy_basis(year)
    original_source, original_frozen = deepcopy(source), deepcopy(frozen)
    draft = append_source(store, source, frozen=frozen)["draft"]
    original_draft = deepcopy(draft)
    original_events, original_files = (
        deepcopy(store._events("metadata-test-department")),
        saved_files(tmp_path),
    )

    view = store.current("metadata-test-department")

    assert view["history"][0]["source_fiscal_year"] == year
    assert source_fiscal_year(view["draft"]) == year
    assert view["draft"] == original_draft
    assert view["draft"]["source_hash"] == fingerprint(original_source)
    assert "fiscal_year" not in view["draft"]["source"]
    assert source == original_source and frozen == original_frozen
    assert store._events("metadata-test-department") == original_events
    assert saved_files(tmp_path) == original_files


@pytest.mark.parametrize(
    "field",
    ["id", "sequence", "status", "as_of", "baseline_hash", "snapshot_hash", "calculation"],
)
def test_legacy_year_remains_unknown_when_frozen_forecast_no_longer_matches_basis(field: str) -> None:
    source, frozen = legacy_basis(2027)
    frozen["forecast"][field] = {"changed": True} if field == "calculation" else "not the bound source"
    original = deepcopy({"source": source, "source_snapshot": frozen})
    assert source_fiscal_year(original) is None
    assert original == {"source": source, "source_snapshot": frozen}


@pytest.mark.parametrize("baseline", [{}, {"sha256": "different"}, {"fiscal_year": 2027}])
def test_legacy_year_requires_a_matching_nonempty_frozen_baseline_hash(baseline: Json) -> None:
    source, frozen = legacy_basis(2027)
    frozen["baseline"] = baseline
    assert source_fiscal_year({"source": source, "source_snapshot": frozen}) is None


@pytest.mark.parametrize("year", [None, "2027", True, 1999, 2101])
def test_legacy_year_is_not_inferred_when_frozen_baseline_year_is_unknown_or_invalid(year: Any) -> None:
    source, frozen = legacy_basis(2027)
    frozen["baseline"]["fiscal_year"] = year
    assert (
        source_fiscal_year({"source": source, "source_snapshot": frozen, "inputs": {"fiscal_year": 2028}})
        is None
    )
