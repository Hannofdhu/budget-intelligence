"""Version metadata is a projection of saved results, never a new calculation."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from apps.api.routes import forecast as route
from packages.domain.identity.models import Actor, Role
from packages.forecast.store import ForecastStore, fingerprint


def saved_files(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in root.glob("event-*.json")}


async def test_current_and_historical_reads_share_safe_metadata_without_changing_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def access(_: Request, __: Actor) -> dict[str, Any]:
        return {"sha256": "accepted-budget"}

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("version browsing must not query BI, recalculate or append ledger events")

    actor = Actor("owner", "metadata-test-department", frozenset({Role.DATA_MAINTAINER}))
    ledger = ForecastStore(tmp_path)
    first = ledger.append_version(
        department=actor.department_id,
        actor=actor.user_id,
        request_id="metadata-first",
        request_hash=fingerprint("metadata-first"),
        parent_id=None,
        baseline_hash="accepted-budget",
        snapshot={"as_of": "2026-10-15", "source_data": {"private": "not for a version list"}},
        assumptions={"states": {}},
        calculation={"ready": True, "issues": []},
        reason="first saved reason",
    )["version"]
    ledger.confirm(
        department=actor.department_id,
        actor=actor.user_id,
        version_id=first["id"],
        request_id="metadata-confirm",
        request_hash=fingerprint("metadata-confirm"),
        reason="review completed",
    )
    latest = ledger.append_version(
        department=actor.department_id,
        actor="forecast-daily-scheduler",
        request_id="metadata-latest",
        request_hash=fingerprint("metadata-latest"),
        parent_id=first["id"],
        baseline_hash="accepted-budget",
        snapshot={"as_of": "2026-10-15", "provenance": {"refresh_mode": "AUTOMATIC"}},
        assumptions={"states": {}},
        calculation={"ready": True, "issues": []},
        reason="scheduled refresh",
    )["version"]
    original_events = deepcopy(ledger._events(actor.department_id))
    original_files = saved_files(tmp_path)
    monkeypatch.setattr(route, "store", ledger)
    monkeypatch.setattr(route, "_access", access)
    monkeypatch.setattr(route, "extract_forecast_source", forbidden)
    monkeypatch.setattr(route, "calculate_forecast", forbidden)
    monkeypatch.setattr(ledger, "_append", forbidden)
    request = Request({"type": "http", "headers": []})

    current = await route.current(request, actor)
    historical = await route.saved_version(request, first["id"], actor)

    assert current["latest_version_id"] == historical["latest_version_id"] == latest["id"]
    assert current["version"]["id"] == latest["id"]
    assert historical["version"]["id"] == first["id"]
    assert current["history"] == historical["history"]
    automatic, confirmed = historical["history"]
    assert automatic["generation"] == "AUTOMATIC"
    assert automatic["status"] == "DRAFT"
    assert confirmed["generation"] == "MANUAL"
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["reason"] == "first saved reason"
    assert confirmed["created_at"] == first["created_at"]
    assert confirmed["sequence"] == first["sequence"]
    assert confirmed["confirmation"]["reason"] == "review completed"
    for result in (current, historical):
        assert "snapshot" not in result["version"]
        for summary in result["history"]:
            assert not {"snapshot", "source_data", "calculation"}.intersection(summary)
    assert ledger._events(actor.department_id) == original_events
    assert saved_files(tmp_path) == original_files
