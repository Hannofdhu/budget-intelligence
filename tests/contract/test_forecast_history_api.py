from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from apps.api.routes import forecast as route
from packages.domain.identity.models import Actor, Role
from packages.forecast.store import ForecastStore, fingerprint
from packages.source_snapshot.adapter import SourceAdapterError


@pytest.fixture
def history_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Request, Actor, ForecastStore]:
    async def access(_: Request, actor: Actor) -> dict[str, Any]:
        return {"sha256": "accepted-budget", "rows": []}

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("history must not query BI or recalculate")

    ledger = ForecastStore(tmp_path)
    monkeypatch.setattr(route, "store", ledger)
    monkeypatch.setattr(route, "_access", access)
    monkeypatch.setattr(route, "extract_forecast_source", forbidden)
    monkeypatch.setattr(route, "calculate_forecast", forbidden)
    request = Request({"type": "http", "headers": []})
    actor = Actor("owner", "local-department", frozenset({Role.DATA_MAINTAINER}))
    return request, actor, ledger


def seed(ledger: ForecastStore, actor: Actor, parent: str | None = None, index: int = 1) -> dict[str, Any]:
    return ledger.append_version(
        department=actor.department_id,
        actor=actor.user_id,
        request_id=f"request-{index}",
        request_hash=fingerprint(index),
        parent_id=parent,
        baseline_hash="accepted-budget",
        snapshot={"as_of": "2026-10-05", "rows": [{"amount": "1.234567890123456789"}]},
        assumptions=route.Assumptions().model_dump(mode="json"),
        calculation={"ready": True, "rows": [{"forecast_revenue": str(index)}], "issues": []},
        reason="verified source",
    )


async def test_history_reads_exact_frozen_results_and_confirmation_without_new_events(
    history_route: tuple,
) -> None:
    request, actor, ledger = history_route
    first = seed(ledger, actor)["version"]
    ledger.confirm(
        department=actor.department_id,
        actor=actor.user_id,
        version_id=first["id"],
        request_id="confirm-1",
        request_hash=fingerprint("confirmation"),
        reason="reviewed",
    )
    latest = seed(ledger, actor, first["id"], 2)["version"]
    events = deepcopy(ledger._events(actor.department_id))
    history = await route.saved_version(request, first["id"], actor)
    assert history["version"]["id"] == first["id"]
    assert history["version"]["status"] == "CONFIRMED"
    assert history["version"]["calculation"] == first["calculation"]
    assert history["latest_version_id"] == latest["id"]
    assert "snapshot" not in history["version"]
    exported = await route.saved_snapshot(request, first["id"], actor)
    assert exported["snapshot_hash"] == fingerprint(first["snapshot"])
    assert exported["calculation_hash"] == fingerprint(first["calculation"])
    assert exported["snapshot"]["rows"][0]["amount"] == "1.234567890123456789"
    assert exported["confirmation"]["reason"] == "reviewed"
    assert exported["version"]["created_by"] == actor.user_id
    assert exported["version"]["reason"] == "verified source"
    assert exported["version"]["parent_id"] is None
    assert exported["version"]["baseline_kind"] == "APPROVED_FY27_HISTORICAL_ARCHIVE"
    assert ledger._events(actor.department_id) == events


async def test_other_department_cannot_read_or_export_saved_forecast(history_route: tuple) -> None:
    request, actor, ledger = history_route
    first = seed(ledger, actor)["version"]
    other = Actor("other", "other-department", frozenset({Role.DATA_MAINTAINER}))
    for reader in (route.saved_version, route.saved_snapshot):
        with pytest.raises(HTTPException) as exc:
            await reader(request, first["id"], other)
        assert exc.value.status_code == 404


async def test_failed_refresh_retains_current_confirmed_snapshot_and_sanitized_error(
    history_route: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, actor, ledger = history_route
    first = seed(ledger, actor)["version"]
    ledger.confirm(
        department=actor.department_id,
        actor=actor.user_id,
        version_id=first["id"],
        request_id="confirm-1",
        request_hash=fingerprint("confirmation"),
        reason="reviewed",
    )
    prior = deepcopy(ledger.current(actor.department_id))

    async def fail(_: Any) -> dict[str, Any]:
        raise SourceAdapterError("SOURCE_NETWORK_ERROR", "观远连接中断，请重试更新")

    monkeypatch.setattr(route, "extract_forecast_source", fail)
    with pytest.raises(HTTPException) as exc:
        await route.refresh(request, route.Refresh(reason="retry update"), actor, "refresh-error")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "SOURCE_NETWORK_ERROR"
    assert exc.value.detail["retained_version"]["id"] == first["id"]
    assert exc.value.detail["retained_version"]["status"] == "CONFIRMED"
    assert ledger.current(actor.department_id) == prior


async def test_storage_failure_is_safe_and_retryable(
    history_route: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, actor, ledger = history_route

    def failed(*_: Any) -> None:
        raise OperationalError("SELECT", {"private_snapshot": "must-not-leak"}, Exception("secret host"))

    monkeypatch.setattr(ledger, "current", failed)
    with pytest.raises(HTTPException) as exc:
        await route._current(actor)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "FORECAST_STORAGE_UNAVAILABLE"
    assert exc.value.detail["retryable"] is True
    assert "must-not-leak" not in str(exc.value.detail)
    assert "secret host" not in str(exc.value.detail)


async def test_changed_baseline_does_not_silently_rebase_a_historical_snapshot(
    history_route: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, actor, ledger = history_route
    first = seed(ledger, actor)["version"]

    async def changed(_: Request, __: Actor) -> dict[str, Any]:
        return {"sha256": "different-baseline"}

    monkeypatch.setattr(route, "_access", changed)
    with pytest.raises(HTTPException) as exc:
        await route.saved_snapshot(request, first["id"], actor)
    assert exc.value.status_code == 409
