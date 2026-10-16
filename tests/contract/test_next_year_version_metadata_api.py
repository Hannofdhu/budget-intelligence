"""Presentation-only year metadata must not mutate a saved forecast basis."""

from copy import deepcopy
from typing import Any

import pytest

from apps.api.routes import next_year as route
from packages.forecast.store import fingerprint

Json = dict[str, Any]


def legacy_draft(year: int) -> Json:
    calculation = {"annual": {"forecast_revenue": "10", "forecast_total": 1}}
    source: Json = {
        "id": "forecast-17",
        "sequence": 17,
        "status": "CONFIRMED",
        "as_of": "2026-10-15",
        "baseline_hash": "saved-baseline",
        "snapshot_hash": "saved-snapshot",
        "calculation_hash": fingerprint(calculation),
    }
    return {
        "id": "budget-12",
        "sequence": 12,
        "source": source,
        "source_hash": fingerprint(source),
        "source_snapshot": {
            "baseline": {"sha256": "saved-baseline", "fiscal_year": year},
            "forecast": {**source, "calculation": calculation},
        },
        "inputs": {"fiscal_year": 2029},
    }


@pytest.mark.parametrize("year", [2025, 2027, 2030])
def test_safe_draft_exposes_a_separate_verified_source_year_without_rewriting_the_basis(year: int) -> None:
    draft = legacy_draft(year)
    original, original_hash = deepcopy(draft), fingerprint(draft)

    safe = route._safe_view({"draft": draft})["draft"]

    assert safe["source_fiscal_year"] == year
    assert safe["source"] == original["source"]
    assert "fiscal_year" not in safe["source"]
    assert "source_snapshot" not in safe
    assert fingerprint(draft) == original_hash
    assert draft == original


def test_safe_draft_does_not_borrow_year_from_an_unrelated_snapshot_or_planning_year() -> None:
    draft = legacy_draft(2027)
    draft["source_snapshot"]["forecast"]["id"] = "another-forecast"
    assert route._safe_view({"draft": draft})["draft"]["source_fiscal_year"] is None


@pytest.mark.parametrize("year", [2025, 2027, 2030])
def test_read_source_year_uses_the_validated_matching_baseline_only_in_the_response(year: int) -> None:
    source = legacy_draft(year)["source"]
    baseline = {"sha256": source["baseline_hash"], "fiscal_year": year}
    view = {"source": source, "draft": None}
    original, original_baseline = deepcopy(view), deepcopy(baseline)

    safe = route._safe_view(view, baseline)

    assert safe["source"]["fiscal_year"] == year
    assert view == original and baseline == original_baseline
    assert "fiscal_year" not in source
    # Calling the serializer without verified baseline evidence cannot infer a year.
    assert "fiscal_year" not in route._safe_view(view)["source"]


def test_read_source_year_is_unknown_when_the_current_baseline_is_unrelated() -> None:
    source = legacy_draft(2027)["source"]
    safe = route._safe_view({"source": source}, {"sha256": "another-baseline", "fiscal_year": 2027})
    assert "fiscal_year" not in safe["source"]
