from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api.routes import cockpit as route
from packages.domain.identity.models import Actor
from tests.contract import test_next_year_api as fixtures
from tests.contract.test_next_year_api import _forecast, _prepare

setup_route = fixtures.setup_route


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*.json")}


async def test_current_and_pinned_reads_do_not_append_or_change_any_ledger(
    setup_route: tuple[Request, Actor, dict[str, Any]],
    tmp_path: Path,
) -> None:
    request, actor, _ = setup_route
    prepared = await _prepare(request, actor)
    new = _forecast(actor, parent=prepared["draft"]["source"]["id"], sequence=2, confirmed=False)
    before = _files(tmp_path)
    result = await route.cockpit(request, actor)
    assert result["read_only"] is True
    assert result["sources"]["forecast"]["id"] == new["id"]
    assert result["sources"]["next_budget"]["id"] == prepared["draft"]["id"]
    exact = await route.cockpit(
        request,
        actor,
        forecast_version=prepared["draft"]["source"]["id"],
        next_year_version=prepared["draft"]["id"],
    )
    assert exact["sources"]["forecast"]["id"] != new["id"]
    assert _files(tmp_path) == before


async def test_unknown_exact_ids_fail_instead_of_falling_back_and_none_stays_missing(
    setup_route: tuple[Request, Actor, dict[str, Any]],
) -> None:
    request, actor, _ = setup_route
    await _prepare(request, actor)
    for kwargs in ({"forecast_version": "missing"}, {"next_year_version": "missing"}):
        with pytest.raises(HTTPException) as error:
            await route.cockpit(request, actor, **kwargs)
        assert error.value.status_code == 404
    result = await route.cockpit(request, actor, forecast_version="none", next_year_version="none")
    assert result["sources"]["forecast"] is None
    assert result["sources"]["next_budget"] is None


async def test_cockpit_keeps_existing_department_access_guard(
    setup_route: tuple[Request, Actor, dict[str, Any]],
) -> None:
    request, actor, _ = setup_route
    with pytest.raises(HTTPException) as error:
        await route.cockpit(request, Actor(actor.user_id, "other", actor.roles))
    assert error.value.status_code == 403
