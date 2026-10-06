from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api.routes import forecast as route
from packages.domain.identity.models import Actor, Role
from packages.forecast.store import ForecastStore


def _event_bytes(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.glob("event-*.json")}


@pytest.fixture
def setup_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Request, Actor]:
    baseline = {"sha256": "immutable", "rows": [], "annual_revenue": "113004618.59"}

    async def access(_: Request, actor: Actor) -> dict[str, Any]:
        if actor.department_id != "department":
            raise HTTPException(403, "other department")
        return deepcopy(baseline)

    async def extract(_: Any) -> dict[str, Any]:
        return {"as_of": "2026-10-03", "rows": [], "internal_evidence": "not a UI payload"}

    monkeypatch.setattr(route, "_access", access)
    monkeypatch.setattr(route, "extract_forecast_source", extract)
    monkeypatch.setattr(route, "calculate_forecast", lambda *_: {"ready": True, "annual": {"gap": "999999"}})
    monkeypatch.setattr(route, "store", ForecastStore(tmp_path))
    actor = Actor("owner", "department", frozenset({Role.DATA_MAINTAINER, Role.DEPARTMENT_BUDGET_OWNER}))
    request = Request({"type": "http", "headers": []})
    return request, actor


@pytest.mark.asyncio
async def test_refresh_revision_confirm_and_idempotency(setup_route: tuple[Request, Actor]) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    assert "snapshot" not in first["version"]
    version_id = first["version"]["id"]
    confirmed = await route.confirm(
        request, version_id, route.Confirmation(reason="reviewed"), actor, "confirm-1"
    )
    assert confirmed["version"]["status"] == "CONFIRMED"
    assumptions = route.Assumptions.model_validate(first["version"]["assumptions"])
    assumptions.adjustments = [
        route.Adjustment(season="秋季", campus="example", kind="additional", value=1, reason="revised")
    ]
    second = await route.revise(
        request, route.Revise(parent_id=version_id, reason="new", assumptions=assumptions), actor, "request-2"
    )
    assert second["version"]["status"] == "DRAFT"
    assert second["version"]["id"] != version_id
    assert second["version"]["as_of"] == "2026-10-03"
    assert (await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")) == first


@pytest.mark.asyncio
async def test_changed_archive_blocks_confirmation_without_appending_an_event(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    historical_events = _event_bytes(tmp_path)

    async def changed_access(_: Request, __: Actor) -> dict[str, Any]:
        return {"sha256": "changed archive", "rows": []}

    monkeypatch.setattr(route, "_access", changed_access)
    with pytest.raises(HTTPException) as current_error:
        await route.current(request, actor)
    assert current_error.value.status_code == 409
    with pytest.raises(HTTPException) as confirmation_error:
        await route.confirm(
            request, first["version"]["id"], route.Confirmation(reason="reviewed"), actor, "confirm-1"
        )
    assert confirmation_error.value.status_code == 409
    assert "预测基准不一致" in confirmation_error.value.detail
    assert route.store.current(actor.department_id)["version"]["status"] == "DRAFT"
    assert _event_bytes(tmp_path) == historical_events


@pytest.mark.asyncio
async def test_completed_confirmation_replay_is_idempotent_even_if_archive_later_changes(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    version_id = first["version"]["id"]
    body = route.Confirmation(reason="reviewed")
    confirmed = await route.confirm(request, version_id, body, actor, "confirm-1")
    historical_events = _event_bytes(tmp_path)

    async def changed_access(_: Request, __: Actor) -> dict[str, Any]:
        return {"sha256": "changed archive", "rows": []}

    monkeypatch.setattr(route, "_access", changed_access)
    assert await route.confirm(request, version_id, body, actor, "confirm-1") == confirmed
    with pytest.raises(HTTPException) as new_request_error:
        await route.confirm(request, version_id, body, actor, "confirm-2")
    assert new_request_error.value.status_code == 409
    with pytest.raises(HTTPException) as reused_key_error:
        await route.confirm(request, version_id, route.Confirmation(reason="different"), actor, "confirm-1")
    assert reused_key_error.value.status_code == 409
    assert "同一请求编号" in reused_key_error.value.detail
    assert _event_bytes(tmp_path) == historical_events


@pytest.mark.asyncio
async def test_confirmation_cannot_target_an_old_parent_version(setup_route: tuple[Request, Actor]) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    await route.refresh(request, route.Refresh(reason="second"), actor, "request-2")
    with pytest.raises(HTTPException) as exc:
        await route.confirm(
            request, first["version"]["id"], route.Confirmation(reason="old version"), actor, "confirm-1"
        )
    assert exc.value.status_code == 409
    assert "只能确认当前" in exc.value.detail


@pytest.mark.asyncio
async def test_permissions_and_finalized_state_cannot_be_reopened(setup_route: tuple[Request, Actor]) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    assumptions = route.Assumptions.model_validate(first["version"]["assumptions"])
    assumptions.states["暑假"] = "OPEN"
    with pytest.raises(HTTPException) as exc:
        await route.revise(
            request,
            route.Revise(parent_id=first["version"]["id"], reason="reopen", assumptions=assumptions),
            actor,
            "request-2",
        )
    assert exc.value.status_code == 409
    owner_only = Actor("owner", "department", frozenset({Role.DEPARTMENT_BUDGET_OWNER}))
    with pytest.raises(HTTPException) as exc:
        await route.refresh(request, route.Refresh(reason="refresh"), owner_only, "request-3")
    assert exc.value.status_code == 403
    maintainer_only = Actor("maintainer", "department", frozenset({Role.DATA_MAINTAINER}))
    with pytest.raises(HTTPException) as exc:
        await route.confirm(
            request,
            first["version"]["id"],
            route.Confirmation(reason="approve"),
            maintainer_only,
            "confirm-2",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["refresh", "revise"])
@pytest.mark.parametrize("open_state", ["OPEN", "ENDED"])
async def test_finalization_retires_inherited_adjustments_without_changing_history(
    setup_route: tuple[Request, Actor], tmp_path: Path, command: str, open_state: str
) -> None:
    request, actor = setup_route
    states = {**route.DEFAULT_STATES, "秋季": open_state, "寒假": "OPEN"}
    first = await route.refresh(
        request, route.Refresh.model_validate({"reason": "first", "states": states}), actor, "request-1"
    )
    assumptions = route.Assumptions.model_validate(first["version"]["assumptions"])
    inherited = [
        route.Adjustment(season="秋季", campus="example", kind=kind, value=10, reason="approved estimate")
        for kind in ("additional", "new_withdrawal", "old_withdrawal")
    ]
    remaining = route.Adjustment(
        season="寒假", campus="example", kind="additional", value=5, reason="still open"
    )
    assumptions.adjustments = [*inherited, remaining]
    parent = await route.revise(
        request,
        route.Revise(parent_id=first["version"]["id"], reason="estimate", assumptions=assumptions),
        actor,
        "request-2",
    )
    await route.confirm(
        request, parent["version"]["id"], route.Confirmation(reason="reviewed"), actor, "confirm-1"
    )
    historical_events = _event_bytes(tmp_path)
    parent_assumptions = deepcopy(parent["version"]["assumptions"])
    states["秋季"] = "FINALIZED"
    if command == "refresh":
        result = await route.refresh(
            request,
            route.Refresh.model_validate({"reason": "freeze actuals", "states": states}),
            actor,
            "request-3",
        )
    else:
        assumptions = route.Assumptions.model_validate({**parent_assumptions, "states": states})
        result = await route.revise(
            request,
            route.Revise(parent_id=parent["version"]["id"], reason="freeze actuals", assumptions=assumptions),
            actor,
            "request-3",
        )
    assert result["version"]["states"]["秋季"] == "FINALIZED"
    assert result["version"]["assumptions"]["adjustments"] == [remaining.model_dump()]
    assert result["version"]["status"] == "DRAFT"
    assert result["version"]["confirmation"] is None
    assert result["history"][1]["status"] == "CONFIRMED"
    assert parent["version"]["assumptions"] == parent_assumptions
    assert all(path.read_bytes() == original for path, original in historical_events.items())
    replay = await route.revise(
        request,
        route.Revise(
            parent_id=first["version"]["id"],
            reason="estimate",
            assumptions=route.Assumptions.model_validate(parent_assumptions),
        ),
        actor,
        "request-2",
    )
    assert replay["version"]["assumptions"]["adjustments"] == [
        *(adjustment.model_dump() for adjustment in inherited),
        remaining.model_dump(),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_finalization_rejects_new_or_changed_adjustments(
    setup_route: tuple[Request, Actor], tmp_path: Path, changed: bool
) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    assumptions = route.Assumptions.model_validate(first["version"]["assumptions"])
    original = route.Adjustment(
        season="秋季", campus="example", kind="additional", value=10, reason="approved estimate"
    )
    assumptions.adjustments = [original]
    parent = await route.revise(
        request,
        route.Revise(parent_id=first["version"]["id"], reason="estimate", assumptions=assumptions),
        actor,
        "request-2",
    )
    historical_events = _event_bytes(tmp_path)
    assumptions.states["秋季"] = "FINALIZED"
    if changed:
        assumptions.adjustments = [original.model_copy(update={"value": 11})]
    else:
        assumptions.adjustments.append(
            route.Adjustment(season="秋季", campus="new campus", kind="additional", value=1, reason="new")
        )
    with pytest.raises(HTTPException) as exc:
        await route.revise(
            request,
            route.Revise(parent_id=parent["version"]["id"], reason="freeze", assumptions=assumptions),
            actor,
            "request-3",
        )
    assert exc.value.status_code == 409
    assert route.store.current(actor.department_id)["version"]["id"] == parent["version"]["id"]
    assert _event_bytes(tmp_path) == historical_events


@pytest.mark.asyncio
async def test_open_to_ended_retains_manual_estimates(setup_route: tuple[Request, Actor]) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    assumptions = route.Assumptions.model_validate(first["version"]["assumptions"])
    adjustment = route.Adjustment(
        season="秋季", campus="example", kind="additional", value=10, reason="planned new students"
    )
    assumptions.adjustments = [adjustment]
    parent = await route.revise(
        request,
        route.Revise(parent_id=first["version"]["id"], reason="estimate", assumptions=assumptions),
        actor,
        "request-2",
    )
    states = {**route.DEFAULT_STATES, "秋季": "ENDED"}
    result = await route.refresh(
        request,
        route.Refresh.model_validate({"reason": "enrollment ended", "states": states}),
        actor,
        "request-3",
    )
    assert result["version"]["assumptions"]["adjustments"] == [adjustment.model_dump()]
    assert result["version"]["parent_id"] == parent["version"]["id"]


def test_every_dimension_and_integer_mandatory_and_unknown_inputs_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        route.Dimensions(track=" ", product="x", campus="x", grade="x", subject="x")
    with pytest.raises(ValidationError):
        route.Assumptions(states={"暑假": "FINALIZED"})
    with pytest.raises(ValidationError):
        route.Adjustment(season="秋季", campus="x", kind="additional", value=1.5, reason="x")
    with pytest.raises(ValidationError):
        route.Assumptions(minimum_sample=True)
    with pytest.raises(ValidationError):
        route.Assumptions.model_validate({"tax_factor": 1.06})


@pytest.mark.asyncio
async def test_trial_preparation_versions_same_snapshot_and_replays_original_result(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    original = _event_bytes(tmp_path)

    def prepare(_: Any, __: Any, prior: dict[str, Any]) -> dict[str, Any]:
        return {**deepcopy(prior), "trial_mode": True, "forecast_scopes": []}

    monkeypatch.setattr(route, "build_trial_assumptions", prepare)
    monkeypatch.setattr(
        route, "calculate_forecast", lambda *_: {"ready": False, "trial_ready": True, "annual": {"gap": "5"}}
    )
    body = route.PrepareTrial(parent_id=first["version"]["id"], reason="local trial")
    trial = await route.prepare_trial(request, body, actor, "trial-001")
    assert trial["version"]["status"] == "DRAFT"
    assert trial["version"]["parent_id"] == first["version"]["id"]
    assert trial["version"]["assumptions"]["trial_mode"] is True
    assert trial["version"]["calculation"]["trial_ready"] is True
    assert trial["version"]["snapshot_hash"] == first["version"]["snapshot_hash"]
    assert "snapshot" not in trial["version"]
    assert all(path.read_bytes() == value for path, value in original.items())
    assert await route.prepare_trial(request, body, actor, "trial-001") == trial
    with pytest.raises(HTTPException) as old:
        await route.prepare_trial(request, body, actor, "trial-002")
    assert old.value.status_code == 409
    with pytest.raises(HTTPException) as forbidden:
        await route.confirm(
            request, trial["version"]["id"], route.Confirmation(reason="not official"), actor, "confirm-trial"
        )
    assert "试用预测不能正式确认" in forbidden.value.detail


@pytest.mark.asyncio
async def test_trial_preparation_requires_maintainer_and_current_baseline(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    body = route.PrepareTrial(parent_id=first["version"]["id"], reason="trial")
    owner_only = Actor("owner", "department", frozenset({Role.DEPARTMENT_BUDGET_OWNER}))
    with pytest.raises(HTTPException) as denied:
        await route.prepare_trial(request, body, owner_only, "trial-001")
    assert denied.value.status_code == 403
    original = _event_bytes(tmp_path)

    async def changed(_: Request, __: Actor) -> dict[str, Any]:
        return {"sha256": "different", "rows": []}

    monkeypatch.setattr(route, "_access", changed)
    with pytest.raises(HTTPException) as mismatch:
        await route.prepare_trial(request, body, actor, "trial-002")
    assert mismatch.value.status_code == 409
    assert _event_bytes(tmp_path) == original


def test_trial_scope_validation_and_finalized_parameter_guard() -> None:
    from pydantic import ValidationError

    dims = {name: "具体值" for name in ("track", "product", "grade", "subject", "campus")}
    financial = {"price": "4665", "discount": ".85", "recognition": ".6875", "method": "MANUAL"}
    scope = {"dims": dims, "reason": "试用估计", "financial": {"秋季": financial}, "additional": {"秋季": 0}}
    assumptions = route.Assumptions.model_validate({"trial_mode": True, "forecast_scopes": [scope]})
    assert assumptions.forecast_scopes[0].financial["秋季"].renewal is None
    for bad in (
        {"trial_mode": False, "forecast_scopes": [scope]},
        {"trial_mode": True, "forecast_scopes": [scope, scope]},
        {"trial_mode": True, "forecast_scopes": [{**scope, "additional": {"秋季": -1}}]},
        {"trial_mode": True, "forecast_scopes": [{**scope, "additional": {"秋季": 1.5}}]},
        {
            "trial_mode": True,
            "forecast_scopes": [{**scope, "financial": {"秋季": {**financial, "share": 1}}}],
        },
        {
            "trial_mode": True,
            "forecast_scopes": [{**scope, "financial": {"秋季": {**financial, "price": "NaN"}}}],
        },
    ):
        with pytest.raises(ValidationError):
            route.Assumptions.model_validate(bad)
    actor = Actor("owner", "department", frozenset({Role.DATA_MAINTAINER}))
    summer = route.Assumptions.model_validate(
        {"trial_mode": True, "forecast_scopes": [{**scope, "financial": {"暑假": financial}}]}
    ).model_dump()
    with pytest.raises(HTTPException) as blocked:
        route._guard_change(None, summer, actor)
    assert blocked.value.status_code == 409


@pytest.mark.asyncio
async def test_forecast_access_reuses_existing_local_only_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.routes import historical_review

    async def session(_: Request) -> dict[str, str]:
        return {"departmentId": "department"}

    monkeypatch.setattr(historical_review, "trial_session", session)
    monkeypatch.setattr(historical_review, "read_review", lambda: {"sha256": "immutable"})
    actor = Actor("owner", "department", frozenset({Role.DEPARTMENT_BUDGET_OWNER}))
    for address in ("", "192.168.1.5", "unknown"):
        request = Request({"type": "http", "headers": [(b"x-budget-client-address", address.encode())]})
        with pytest.raises(HTTPException) as exc:
            await route._access(request, actor)
        assert exc.value.status_code == 403
