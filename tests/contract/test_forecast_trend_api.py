from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from apps.api.auth import current_actor
from apps.api.main import create_app
from apps.api.routes import forecast as route
from packages.domain.identity.models import Actor, Role
from packages.forecast.store import ForecastStore, fingerprint

Json = dict[str, Any]


@pytest.fixture
def trend_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Request, Actor, ForecastStore]:
    async def access(_: Request, actor: Actor) -> Json:
        return {"sha256": "baseline"}

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("trend reads must not query BI or recalculate")

    ledger = ForecastStore(tmp_path)
    monkeypatch.setattr(route, "store", ledger)
    monkeypatch.setattr(route, "_access", access)
    monkeypatch.setattr(route, "extract_forecast_source", forbidden)
    monkeypatch.setattr(route, "calculate_forecast", forbidden)
    return (
        Request({"type": "http", "headers": []}),
        Actor("owner", "test-department", frozenset({Role.DATA_MAINTAINER})),
        ledger,
    )


def seed(ledger: ForecastStore, actor: Actor, parent: str | None = None, number: int = 1) -> Json:
    view = ledger.append_version(
        department=actor.department_id,
        actor=actor.user_id,
        request_id=f"test-{number}",
        request_hash=fingerprint(number),
        parent_id=parent,
        baseline_hash="baseline",
        snapshot={
            "as_of": "2026-10-15",
            "actual_totals": {season: {"actual_revenue": "0"} for season in route.SEASONS},
            "source_data": {"private_marker": "must-not-be-in-trend"},
        },
        assumptions={"states": {}},
        calculation={
            "ready": True,
            "annual": {"forecast_revenue": "20000.123456789"},
            "quarters": [{"season": s, "forecast_revenue": "5000"} for s in route.SEASONS],
        },
        reason="synthetic complete snapshot",
    )
    return cast(Json, view["version"])


async def test_trend_is_read_only_compact_exact_and_does_not_query_sources(
    trend_route: tuple[Request, Actor, ForecastStore],
) -> None:
    request, actor, ledger = trend_route
    first = seed(ledger, actor)
    ledger.confirm(
        department=actor.department_id,
        actor=actor.user_id,
        version_id=first["id"],
        request_id="confirm-first",
        request_hash=fingerprint("confirmation"),
        reason="reviewed",
    )
    latest = seed(ledger, actor, first["id"], 2)
    before = deepcopy(ledger._events(actor.department_id))
    result = await route.revenue_trend(request, actor, days="all", through_version=first["id"])
    assert result["points"][0]["version_id"] == first["id"]
    assert result["points"][0]["status"] == "CONFIRMED"
    assert result["points"][0]["periods"]["全年"]["forecast_revenue"] == "20000.123456789"
    assert result["points"][0]["periods"]["全年"]["actual_revenue"] == "0"
    assert "must-not-be-in-trend" not in str(result)
    assert "created_by" not in result["points"][0]
    assert "snapshot" not in result["points"][0]
    assert result["points"][0]["previous_version"] is None
    current_trend = await route.revenue_trend(request, actor, days="all")
    previous = current_trend["points"][-1]["previous_version"]
    assert previous["version_id"] == first["id"]
    assert previous["sequence"] == first["sequence"]
    assert "snapshot" not in previous
    assert "must-not-be-in-trend" not in str(previous)
    assert ledger.current(actor.department_id)["version"]["id"] == latest["id"]
    assert ledger._events(actor.department_id) == before


async def test_trend_does_not_reveal_another_departments_version(
    trend_route: tuple[Request, Actor, ForecastStore],
) -> None:
    request, actor, ledger = trend_route
    first = seed(ledger, actor)
    other = Actor("other", "other-department", actor.roles)
    with pytest.raises(HTTPException) as exc:
        await route.revenue_trend(request, other, through_version=first["id"])
    assert exc.value.status_code == 404


async def test_trend_runs_existing_local_department_access_guard_before_store_reads(
    trend_route: tuple[Request, Actor, ForecastStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, actor, _ = trend_route

    async def denied(_: Request, __: Actor) -> Json:
        raise HTTPException(403, "not allowed")

    monkeypatch.setattr(route, "_access", denied)
    with pytest.raises(HTTPException) as exc:
        await route.revenue_trend(request, actor)
    assert exc.value.status_code == 403


async def test_trend_storage_error_is_safe_and_retryable(
    trend_route: tuple[Request, Actor, ForecastStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, actor, ledger = trend_route

    def failed(*_: Any, **__: Any) -> Json:
        raise OperationalError("SELECT", {"private": "must-not-leak"}, Exception("secret-host"))

    monkeypatch.setattr(ledger, "trend", failed)
    with pytest.raises(HTTPException) as exc:
        await route.revenue_trend(request, actor)
    assert exc.value.status_code == 503
    assert "must-not-leak" not in str(exc.value.detail)
    assert "secret-host" not in str(exc.value.detail)


async def test_trend_rejects_baseline_changes(
    trend_route: tuple[Request, Actor, ForecastStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, ledger = trend_route
    seed(ledger, actor)

    async def changed(_: Request, __: Actor) -> Json:
        return {"sha256": "different"}

    monkeypatch.setattr(route, "_access", changed)
    with pytest.raises(HTTPException) as exc:
        await route.revenue_trend(request, actor)
    assert exc.value.status_code == 409


async def test_trend_http_accepts_only_bounded_windows_and_get_reads(
    trend_route: tuple[Request, Actor, ForecastStore],
) -> None:
    _, actor, ledger = trend_route
    seed(ledger, actor)
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: actor
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for days in ("30", "90", "all"):
            response = await client.get(f"/api/v1/forecasts/fy27/trend?days={days}")
            assert response.status_code == 200
            assert response.json()["points"]
        for days in ("0", "-1", "365000", "everything"):
            assert (await client.get(f"/api/v1/forecasts/fy27/trend?days={days}")).status_code == 422
        assert (await client.post("/api/v1/forecasts/fy27/trend", json={})).status_code == 405
