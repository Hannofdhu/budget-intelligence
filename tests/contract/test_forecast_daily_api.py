from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api.routes import forecast as route
from packages.domain.identity.models import Actor, Role
from packages.forecast.store import ForecastStore


def _event_bytes(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in root.glob("event-*.json")}


async def test_schedule_get_is_authorized_same_department_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def access(request: Request, actor: Actor) -> dict[str, Any]:
        seen.append("access")
        if actor.department_id != "allowed":
            raise HTTPException(403, "not allowed")
        return {}

    async def status(settings: Any, department: str) -> dict[str, Any]:
        seen.append(department)
        return {"enabled": False, "time": "09:30", "timezone": "Asia/Shanghai", "last_run": None}

    monkeypatch.setattr(route, "_access", access)
    monkeypatch.setattr(route, "schedule_status", status)
    request = Request({"type": "http", "headers": []})
    allowed = Actor("test", "allowed", frozenset({Role.DATA_MAINTAINER}))
    result = await route.daily_schedule(request, allowed)
    assert result["time"] == "09:30" and seen == ["access", "allowed"]
    seen.clear()
    with pytest.raises(HTTPException) as exc:
        await route.daily_schedule(request, Actor("test", "other", allowed.roles))
    assert exc.value.status_code == 403 and seen == ["access"]


@pytest.mark.parametrize("ready", [False, True])
async def test_automatic_refresh_does_not_replace_good_version_with_incomplete_data_or_stale_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ready: bool,
) -> None:
    ledger = ForecastStore(tmp_path)
    department = "synthetic-auto-contract"
    assumptions = route.Assumptions().model_dump(mode="json")
    assumptions["states"]["秋季"] = "ENDED"
    first = ledger.append_version(
        department=department,
        actor="test",
        request_id="first-test",
        request_hash="first",
        parent_id=None,
        baseline_hash="immutable",
        snapshot={"as_of": "2026-10-15", "rows": []},
        assumptions=assumptions,
        calculation={"ready": True},
        reason="synthetic",
    )
    ledger.confirm(
        department=department,
        actor="test",
        version_id=first["version"]["id"],
        request_id="confirm-first",
        request_hash="confirm",
        reason="synthetic",
    )
    original = _event_bytes(tmp_path)
    seen: list[dict[str, Any]] = []

    async def source(_settings: Any) -> dict[str, Any]:
        return {"as_of": "2026-10-15", "rows": []}

    def calculate(_baseline: Any, _snapshot: Any, effective: dict[str, Any]) -> dict[str, Any]:
        seen.append(effective)
        return {"ready": ready}

    monkeypatch.setattr(route, "store", ledger)
    monkeypatch.setattr(route, "extract_forecast_source", source)
    monkeypatch.setattr(route, "calculate_forecast", calculate)
    monkeypatch.setattr(route, "build_fixed_assumptions", lambda _b, _s, assumptions: assumptions)
    actor = Actor("forecast-daily-scheduler", department, frozenset({Role.DATA_MAINTAINER}))
    body = route.Refresh(reason="synthetic background", states=route.DEFAULT_STATES)
    if ready:
        result = await route._refresh_from_source(
            {"sha256": "immutable"}, body, actor, "daily-test", "digest", automatic=True
        )
        assert result["version"]["status"] == "DRAFT"
        saved = ledger.current(department)["version"]
        assert saved["snapshot"]["provenance"]["refresh_mode"] == "AUTOMATIC"
        assert (
            saved["snapshot"]["provenance"]["verification_mode"]
            == "AUTOMATED_SOURCE_VALIDATION_NO_HUMAN_CONFIRMATION"
        )
    else:
        with pytest.raises(HTTPException) as exc:
            await route._refresh_from_source(
                {"sha256": "immutable"}, body, actor, "daily-test", "digest", automatic=True
            )
        assert isinstance(exc.value.detail, dict)
        assert exc.value.detail["code"] == "FORECAST_DAILY_DATA_INCOMPLETE"
        assert ledger.current(department)["version"]["status"] == "CONFIRMED"
        assert _event_bytes(tmp_path) == original
    assert seen[0]["states"]["秋季"] == "ENDED"  # never reverted to stale job body OPEN
