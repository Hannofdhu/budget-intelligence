import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api.routes import forecast as route
from packages.domain.identity.models import Actor, Role
from packages.forecast.rules import FIXED_RULES_VERSION
from packages.forecast.store import ForecastStore
from packages.source_snapshot.adapter import SourceAdapterError


def _event_bytes(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.glob("event-*.json")}


def _ledger() -> ForecastStore:
    """These contract tests install the synchronous file-backed fixture store."""
    ledger = route.store
    assert isinstance(ledger, ForecastStore)
    return ledger


def _legacy_version(actor: Actor, *, trial: bool = False) -> dict[str, Any]:
    """Historical versions predate automatic fixed-policy refreshes."""
    return _ledger().append_version(
        department=actor.department_id,
        actor=actor.user_id,
        request_id="legacy-1",
        request_hash="legacy",
        parent_id=None,
        baseline_hash="immutable",
        snapshot={"as_of": "2026-10-03", "rows": []},
        assumptions=route.Assumptions(trial_mode=trial).model_dump(mode="json"),
        calculation={"ready": not trial, "trial_ready": trial, "annual": {"gap": "999999"}},
        reason="historical",
    )


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
    assert _ledger().current(actor.department_id)["version"]["status"] == "DRAFT"
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
    assert _ledger().current(actor.department_id)["version"]["id"] == parent["version"]["id"]
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
    first = _legacy_version(actor)
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

    baseline = {"sha256": "immutable", "source_file_sha256": "accepted-current", "rows": []}
    reads: list[str] = []

    async def database_review(department_id: str) -> dict[str, Any]:
        reads.append(department_id)
        return deepcopy(baseline)

    monkeypatch.setattr(historical_review, "trial_session", session)
    monkeypatch.setattr(historical_review, "read_database_review", database_review)
    actor = Actor("owner", "department", frozenset({Role.DEPARTMENT_BUDGET_OWNER}))
    for address in ("", "192.168.1.5", "unknown"):
        request = Request({"type": "http", "headers": [(b"x-budget-client-address", address.encode())]})
        with pytest.raises(HTTPException) as exc:
            await route._access(request, actor)
        assert exc.value.status_code == 403
    assert reads == []
    local = Request({"type": "http", "headers": [(b"x-budget-client-address", b"127.0.0.1")]})
    result = await route._access(local, actor)
    assert result == baseline
    assert result["sha256"] == "immutable"
    assert reads == ["department"]


@pytest.mark.asyncio
async def test_adopt_fixed_rules_preserves_same_snapshot_and_history_without_refresh_or_confirmation(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request, actor = setup_route
    parent = _legacy_version(actor, trial=True)
    original = _event_bytes(tmp_path)

    async def forbidden_extraction(_: Any) -> dict[str, Any]:
        raise AssertionError("same-snapshot adoption must not refresh BI")

    monkeypatch.setattr(route, "extract_forecast_source", forbidden_extraction)
    monkeypatch.setattr(
        route,
        "calculate_forecast",
        lambda *_: {
            "ready": False,
            "forecast_complete": True,
            "issues": [{"code": "FINALIZED_ACTUAL_MISSING"}],
        },
    )
    body = route.AdoptFixedRules(parent_id=parent["version"]["id"], reason="adopt current policy")
    adopted = await route.adopt_fixed_rules(request, body, actor, "adopt-001")
    version = adopted["version"]
    assert version["assumptions"]["fixed_rules_version"] == FIXED_RULES_VERSION
    assert version["assumptions"]["trial_mode"] is False
    assert version["snapshot_hash"] == parent["version"]["snapshot_hash"]
    assert version["baseline_hash"] == parent["version"]["baseline_hash"]
    assert version["status"] == "DRAFT" and version["confirmation"] is None
    assert version["parent_id"] == parent["version"]["id"]
    assert all(path.read_bytes() == value for path, value in original.items())
    assert await route.adopt_fixed_rules(request, body, actor, "adopt-001") == adopted
    with pytest.raises(HTTPException) as blocked:
        await route.confirm(request, version["id"], route.Confirmation(reason="confirm"), actor, "confirm-1")
    assert "数据阻断" in blocked.value.detail
    with pytest.raises(HTTPException) as stale:
        await route.adopt_fixed_rules(request, body, actor, "adopt-002")
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_adoption_requires_maintainer_and_matching_current_baseline(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request, actor = setup_route
    parent = _legacy_version(actor, trial=True)
    body = route.AdoptFixedRules(parent_id=parent["version"]["id"], reason="adopt")
    original = _event_bytes(tmp_path)
    owner_only = Actor("owner", "department", frozenset({Role.DEPARTMENT_BUDGET_OWNER}))
    with pytest.raises(HTTPException) as denied:
        await route.adopt_fixed_rules(request, body, owner_only, "adopt-001")
    assert denied.value.status_code == 403

    async def changed(_: Request, __: Actor) -> dict[str, Any]:
        return {"sha256": "different", "rows": []}

    monkeypatch.setattr(route, "_access", changed)
    with pytest.raises(HTTPException) as mismatch:
        await route.adopt_fixed_rules(request, body, actor, "adopt-002")
    assert mismatch.value.status_code == 409
    assert _event_bytes(tmp_path) == original


@pytest.mark.asyncio
async def test_refresh_and_revise_automatically_inherit_fixed_rules_and_manual_values(
    setup_route: tuple[Request, Actor], tmp_path: Path
) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    assert first["version"]["assumptions"]["fixed_rules_version"] == FIXED_RULES_VERSION
    assert first["version"]["assumptions"]["trial_mode"] is False
    assumptions = route.Assumptions.model_validate(first["version"]["assumptions"])
    assumptions.adjustments = [
        route.Adjustment(season="秋季", campus="example", kind="additional", value=7, reason="manual")
    ]
    assumptions.forecast_scopes = [
        route.ForecastScope.model_validate(
            {
                "dims": {name: "specific" for name in ("track", "product", "grade", "subject", "campus")},
                "reason": "independent business",
                "additional": {"秋季": 5},
                "financial": {
                    "秋季": {
                        "price": "106.00123456789",
                        "discount": ".9123456789012",
                        "recognition": ".6875",
                        "renewal": ".6123456789012",
                        "share": ".25",
                        "method": "MANUAL",
                    }
                },
            }
        )
    ]
    owner_only = Actor("owner", "department", frozenset({Role.DEPARTMENT_BUDGET_OWNER}))
    body = route.Revise(parent_id=first["version"]["id"], reason="manual", assumptions=assumptions)
    revised = await route.revise(request, body, owner_only, "revise-001")
    saved = deepcopy(revised["version"]["assumptions"])
    assert saved == assumptions.model_dump(mode="json")
    assert await route.revise(request, body, owner_only, "revise-001") == revised
    await route.confirm(
        request, revised["version"]["id"], route.Confirmation(reason="reviewed"), actor, "confirm-1"
    )
    original = _event_bytes(tmp_path)
    refreshed = await route.refresh(request, route.Refresh(reason="new snapshot"), actor, "refresh-001")
    assert refreshed["version"]["assumptions"] == saved
    assert refreshed["version"]["status"] == "DRAFT" and refreshed["version"]["confirmation"] is None
    assert all(path.read_bytes() == value for path, value in original.items())
    assert (
        await route.refresh(request, route.Refresh(reason="new snapshot"), actor, "refresh-001") == refreshed
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("trial", [False, True])
async def test_fixed_rules_cannot_be_downgraded_in_revision_or_legacy_preparation(
    setup_route: tuple[Request, Actor], tmp_path: Path, trial: bool
) -> None:
    request, actor = setup_route
    parent = await route.refresh(request, route.Refresh(reason="first"), actor, "request-1")
    original = _event_bytes(tmp_path)
    assumptions = route.Assumptions.model_validate(parent["version"]["assumptions"])
    assumptions.fixed_rules_version = None
    assumptions.trial_mode = trial
    with pytest.raises(HTTPException) as blocked:
        await route.revise(
            request,
            route.Revise(parent_id=parent["version"]["id"], reason="downgrade", assumptions=assumptions),
            actor,
            "revise-001",
        )
    assert blocked.value.status_code == 409
    with pytest.raises(HTTPException) as preparation:
        await route.prepare_trial(
            request,
            route.PrepareTrial(parent_id=parent["version"]["id"], reason="trial"),
            actor,
            "prepare-001",
        )
    assert preparation.value.status_code == 409
    assert _event_bytes(tmp_path) == original


def test_fixed_scope_contract_rejects_unknown_policy_conflict_and_closed_actual_changes() -> None:
    from pydantic import ValidationError

    for invalid in (
        {"fixed_rules_version": "unknown"},
        {"fixed_rules_version": FIXED_RULES_VERSION, "trial_mode": True},
    ):
        with pytest.raises(ValidationError):
            route.Assumptions.model_validate(invalid)
    fixed = route.Assumptions(fixed_rules_version=FIXED_RULES_VERSION)
    actor = Actor("owner", "department", frozenset({Role.DATA_MAINTAINER}))
    fixed.forecast_scopes = [
        route.ForecastScope.model_validate(
            {
                "dims": {name: "specific" for name in ("track", "product", "grade", "subject", "campus")},
                "reason": "independent",
                "financial": {"暑假": {"price": 106, "discount": 1, "recognition": 1, "method": "MANUAL"}},
            }
        )
    ]
    with pytest.raises(HTTPException) as blocked:
        route._guard_change(None, fixed.model_dump(mode="json"), actor)
    assert blocked.value.status_code == 409


@pytest.mark.parametrize("duplicate_key", [True, False])
async def test_refresh_rejects_same_department_inflight_without_queuing_duplicate_bi(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch, duplicate_key: bool
) -> None:
    request, actor = setup_route
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def blocked_extract(_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"as_of": "2026-10-03", "rows": []}

    monkeypatch.setattr(route, "extract_forecast_source", blocked_extract)
    body = route.Refresh(reason="one refresh intent")
    original = asyncio.create_task(route.refresh(request, body, actor, "request-1"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(HTTPException) as busy:
            await route.refresh(request, body, actor, "request-1" if duplicate_key else "request-2")
        assert busy.value.status_code == 409
        detail: object = busy.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == "FORECAST_REFRESH_IN_PROGRESS"
        assert detail["retryable"] is True
        assert busy.value.headers == {"Retry-After": "3"}
        assert calls == 1
    finally:
        release.set()
        saved = await original
    assert len(_ledger().current(actor.department_id)["history"]) == 1
    assert actor.department_id not in route._refresh_inflight
    assert await route.refresh(request, body, actor, "request-1") == saved
    assert calls == 1


async def test_failed_refresh_releases_inflight_and_same_intent_can_retry(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor = setup_route
    calls = 0

    async def transient_extract(_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SourceAdapterError("SOURCE_NETWORK_ERROR", "连接中断", retryable=True)
        return {"as_of": "2026-10-03", "rows": []}

    monkeypatch.setattr(route, "extract_forecast_source", transient_extract)
    body = route.Refresh(reason="retry same intent")
    with pytest.raises(HTTPException) as failed:
        await route.refresh(request, body, actor, "request-1")
    detail: object = failed.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "SOURCE_NETWORK_ERROR"
    assert actor.department_id not in route._refresh_inflight
    assert _ledger().current(actor.department_id)["version"] is None
    saved = await route.refresh(request, body, actor, "request-1")
    assert saved["version"]["sequence"] == 1 and calls == 2


async def test_cancelled_refresh_releases_inflight_without_saving_a_partial_version(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor = setup_route
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked_extract(_: Any) -> dict[str, Any]:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("cancelled query must not finish")

    monkeypatch.setattr(route, "extract_forecast_source", blocked_extract)
    body = route.Refresh(reason="cancelled intent")
    operation = asyncio.create_task(route.refresh(request, body, actor, "request-1"))
    await asyncio.wait_for(started.wait(), timeout=2)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert cancelled.is_set() and actor.department_id not in route._refresh_inflight
    assert _ledger().current(actor.department_id)["version"] is None

    async def available_extract(_: Any) -> dict[str, Any]:
        return {"as_of": "2026-10-03", "rows": []}

    monkeypatch.setattr(route, "extract_forecast_source", available_extract)
    assert (await route.refresh(request, body, actor, "request-1"))["version"]["sequence"] == 1


async def test_completed_refresh_replay_does_not_wait_for_a_newer_inflight_update(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor = setup_route
    first_body = route.Refresh(reason="first completed intent")
    first = await route.refresh(request, first_body, actor, "request-1")
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked_extract(_: Any) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"as_of": "2026-10-04", "rows": []}

    monkeypatch.setattr(route, "extract_forecast_source", blocked_extract)
    operation = asyncio.create_task(route.refresh(request, route.Refresh(reason="next"), actor, "request-2"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert await route.refresh(request, first_body, actor, "request-1") == first
    finally:
        release.set()
        await operation
    assert len(_ledger().current(actor.department_id)["history"]) == 2


async def test_refresh_rechecks_replay_after_initial_lookup_races_with_a_committed_request(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor = setup_route
    body = route.Refresh(reason="one refresh intent")
    original_replay = route._replay
    first_lookup = True
    calls = 0
    committed = None

    async def stale_first_lookup(person: Actor, request_id: str, digest: str) -> dict[str, Any] | None:
        nonlocal first_lookup, committed
        if first_lookup:
            first_lookup = False
            # Model an awaited lookup that started before the same intent was
            # committed, then resumes with its stale "not found" result.
            committed = await route.refresh(request, body, actor, "request-1")
            return None
        return await original_replay(person, request_id, digest)

    async def extract(_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"as_of": "2026-10-03", "rows": []}

    monkeypatch.setattr(route, "_replay", stale_first_lookup)
    monkeypatch.setattr(route, "extract_forecast_source", extract)
    result = await route.refresh(request, body, actor, "request-1")
    assert result == committed and calls == 1
    assert len(_ledger().current(actor.department_id)["history"]) == 1
    assert actor.department_id not in route._refresh_inflight


async def test_inflight_guard_is_department_scoped(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor = setup_route
    other = Actor("other", "another-department", actor.roles)
    both_started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def access(_: Request, __: Actor) -> dict[str, Any]:
        return {"sha256": "immutable", "rows": [], "annual_revenue": "113004618.59"}

    async def blocked_extract(_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            both_started.set()
        await release.wait()
        return {"as_of": "2026-10-03", "rows": []}

    monkeypatch.setattr(route, "_access", access)
    monkeypatch.setattr(route, "extract_forecast_source", blocked_extract)
    operations = [
        asyncio.create_task(route.refresh(request, route.Refresh(reason="update"), person, "request-1"))
        for person in (actor, other)
    ]
    try:
        await asyncio.wait_for(both_started.wait(), timeout=2)
        assert calls == 2 and {actor.department_id, other.department_id} <= route._refresh_inflight
    finally:
        release.set()
        await asyncio.gather(*operations)
    assert len(_ledger().current(actor.department_id)["history"]) == 1
    assert len(_ledger().current(other.department_id)["history"]) == 1
    assert (
        actor.department_id not in route._refresh_inflight
        and other.department_id not in route._refresh_inflight
    )


async def test_refresh_still_rejects_stale_parent_if_user_revises_during_extraction(
    setup_route: tuple[Request, Actor], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor = setup_route
    first = await route.refresh(request, route.Refresh(reason="initial"), actor, "request-1")
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked_extract(_: Any) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"as_of": "2026-10-04", "rows": []}

    monkeypatch.setattr(route, "extract_forecast_source", blocked_extract)
    operation = asyncio.create_task(
        route.refresh(request, route.Refresh(reason="update"), actor, "request-2")
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        revised = await route.revise(
            request,
            route.Revise(
                parent_id=first["version"]["id"],
                reason="user revision",
                assumptions=route.Assumptions.model_validate(first["version"]["assumptions"]),
            ),
            actor,
            "revise-001",
        )
    finally:
        release.set()
    with pytest.raises(HTTPException, match="已变化") as conflict:
        await operation
    assert conflict.value.status_code == 409
    current = _ledger().current(actor.department_id)
    assert current["version"]["id"] == revised["version"]["id"]
    assert len(current["history"]) == 2 and actor.department_id not in route._refresh_inflight
