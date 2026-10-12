import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from apps.api.auth import current_actor
from apps.api.main import create_app
from apps.api.routes import forecast as forecast_routes
from apps.api.routes import historical_review
from apps.api.routes import next_year as route
from packages.domain.identity.models import Actor, Role
from packages.forecast.store import ForecastStore, fingerprint
from packages.next_year.store import NextYearStore

Json = dict[str, Any]
REAL_ACCESS = forecast_routes._access
SEASONS = ("暑假", "秋季", "寒假", "春季")
BASELINE_HASH = fingerprint("immutable FY27 archive")


def _inputs() -> Json:
    combinations: list[Json] = []
    for grade, entry in (("七年级", True), ("八年级", False)):
        dims = {
            "track": "智慧空间",
            "product": "普通",
            "grade": grade,
            "subject": "数学",
            "campus": "示例校区",
        }
        combinations.append(
            {
                "id": fingerprint(tuple(dims.values()))[:24],
                **dims,
                "entry": entry,
                "enabled": True,
                "source_entry_weight": 10 if entry else 0,
                "source_new_by_season": {season: 10 for season in SEASONS},
                "opening_carryover_yuan": "0",
            }
        )
    rates = [
        {
            "id": f"rate-{index}-{season}",
            "season": season,
            **{key: combo[key] for key in ("track", "product", "grade", "campus")},
            "renewal_ppm": 800000,
            "share_ppm": 200000,
        }
        for index, combo in enumerate(combinations)
        for season in SEASONS
    ]
    return {
        "fiscal_year": 2028,
        "source_as_of": "2026-10-07",
        "target_revenue_yuan": "10000",
        "track_targets": {"智慧空间": "10000"},
        "entry_total": 10,
        "tax_factor": "1.06",
        "combinations": combinations,
        "rates": rates,
        "finance": [
            {
                "id": f"finance-{index}-{season}",
                "combination_id": combo["id"],
                "season": season,
                "price_yuan": "100",
                "discount_ppm": 900000,
                "recognition_ppm": 800000,
            }
            for index, combo in enumerate(combinations)
            for season in SEASONS
        ],
        "class_sizes": [{"track": "智慧空间", "product": "普通", "minimum": 5}],
        "plans": [
            {"combination_id": combo["id"], "season": season, "students": 10}
            for combo in combinations
            for season in SEASONS
        ],
        "opening_sources": [
            {
                "track": "智慧空间",
                "product": "普通",
                "grade": "七年级",
                "subject": "数学",
                "campus": "示例校区",
                "students": 100,
                "target_id": combinations[1]["id"],
                "exclusion": None,
            }
        ],
        "notes": [],
    }


def _bytes(root: Path) -> dict[str, bytes]:
    return {file.name: file.read_bytes() for file in root.glob("event-*.json")}


def _forecast(actor: Actor, *, parent: str | None = None, sequence: int = 1, confirmed: bool = True) -> Json:
    result = forecast_routes.store.append_version(
        department=actor.department_id,
        actor=actor.user_id,
        request_id=f"forecast-{sequence}",
        request_hash=f"forecast-content-{sequence}",
        parent_id=parent,
        baseline_hash=BASELINE_HASH,
        snapshot={"as_of": "2026-10-07", "rows": [], "private": "source evidence"},
        assumptions={"trial_mode": False, "states": {season: "FUTURE" for season in SEASONS}},
        calculation={
            "ready": True,
            "trial_mode": False,
            "issues": [],
            "rows": [],
            "annual": {"forecast_revenue": str(10000 + sequence), "forecast_total": 100},
        },
        reason="fixture forecast",
    )
    if confirmed:
        result = forecast_routes.store.confirm(
            department=actor.department_id,
            actor=actor.user_id,
            version_id=result["version"]["id"],
            request_id=f"confirm-{sequence}",
            request_hash=f"confirm-content-{sequence}",
            reason="fixture reviewed",
        )
    return result["version"]


@pytest.fixture
def setup_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Request, Actor, Json]:
    baseline = {"fiscal_year": 2027, "sha256": BASELINE_HASH, "rows": [], "annual_revenue": "10000"}

    async def access(_: Request, actor: Actor) -> Json:
        if actor.department_id != "department":
            raise HTTPException(403, "other department")
        return deepcopy(baseline)

    monkeypatch.setattr(forecast_routes, "_access", access)
    monkeypatch.setattr(forecast_routes, "store", ForecastStore(tmp_path / "forecast"))
    monkeypatch.setattr(route, "store", NextYearStore(tmp_path / "next-year"))
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(_inputs()))
    actor = Actor("owner", "department", frozenset({Role.DATA_MAINTAINER, Role.DEPARTMENT_BUDGET_OWNER}))
    request = Request({"type": "http", "headers": [(b"x-budget-client-address", b"127.0.0.1")]})
    return request, actor, baseline


async def _prepare(request: Request, actor: Actor) -> Json:
    source = _forecast(actor)
    return await route.prepare(
        request, route.Prepare(forecast_id=source["id"], reason="create draft"), actor, "prepare-1"
    )


@pytest.mark.asyncio
async def test_empty_state_and_latest_confirmed_source_not_latest_forecast_draft(setup_route: tuple) -> None:
    request, actor, _ = setup_route
    assert await route.current(request, actor) == {
        "source": None,
        "draft": None,
        "source_changed": False,
        "history": [],
    }
    first = _forecast(actor)
    latest_draft = _forecast(actor, parent=first["id"], sequence=2, confirmed=False)
    result = await route.current(request, actor)
    assert result["source"]["id"] == first["id"] != latest_draft["id"]
    assert result["source"]["status"] == "CONFIRMED"
    assert result["draft"] is None


@pytest.mark.asyncio
async def test_prepare_preview_revise_keep_source_data_and_only_add_draft_events(setup_route: tuple) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before_forecast = _bytes(forecast_routes.store.root)
    before_draft = _bytes(route.store.root)
    assert set(first["draft"]) == route.DRAFT_FIELDS
    assert first["draft"]["status"] == "DRAFT"
    assert "source_snapshot" not in str(first)
    assert "source evidence" not in str(first)
    assert first["draft"]["calculation"]["rows"]
    assert first["draft"]["calculation"]["annual"]["old_students"] > 0
    inputs = deepcopy(first["draft"]["inputs"])
    inputs["target_revenue_yuan"] = "20000"
    inputs["track_targets"]["智慧空间"] = "20000"
    preview = await route.preview(
        request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor
    )
    assert set(preview) == {"source", "calculation"}
    assert preview["source"] == first["draft"]["source"]
    assert _bytes(route.store.root) == before_draft
    body = route.Revise(parent_id=first["draft"]["id"], reason="raise target", inputs=inputs)
    second = await route.revise(request, body, actor, "revise-2")
    assert second["draft"]["inputs"] == inputs
    assert second["draft"]["source"] == first["draft"]["source"]
    assert second["draft"]["parent_id"] == first["draft"]["id"]
    assert second["draft"]["calculation"] == preview["calculation"]
    assert _bytes(forecast_routes.store.root) == before_forecast
    assert all(_bytes(route.store.root)[name] == data for name, data in before_draft.items())


@pytest.mark.asyncio
async def test_unset_target_can_be_prepared_read_and_saved_without_using_forecast_income(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, _ = setup_route
    inputs = _inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(inputs))
    first = await _prepare(request, actor)
    before_forecast = _bytes(forecast_routes.store.root)
    draft = first["draft"]
    assert draft["source"]["forecast_revenue"] == "10001"
    assert draft["inputs"]["target_revenue_yuan"] is None
    assert draft["calculation"]["target_status"] == "NOT_SET"
    assert draft["calculation"]["annual"]["gap_yuan"] is None
    assert draft["calculation"]["annual"]["total_students"] > 0
    assert (await route.current(request, actor))["draft"] == draft
    revised = await route.revise(
        request,
        route.Revise(parent_id=draft["id"], inputs=inputs, reason="save planning before target is set"),
        actor,
        "revise-no-target",
    )
    assert revised["draft"]["inputs"] == inputs
    assert revised["draft"]["calculation"] == draft["calculation"]
    assert (await route.current(request, actor))["draft"] == revised["draft"]
    assert _bytes(forecast_routes.store.root) == before_forecast


@pytest.mark.asyncio
async def test_existing_draft_can_clear_preview_save_then_accept_manually_specified_target(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)  # Simulates older prepared inputs with an inherited target.
    before_draft = _bytes(route.store.root)
    before_forecast = _bytes(forecast_routes.store.root)
    original = first["draft"]["inputs"]
    inputs = deepcopy(original)
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    preview = await route.preview(
        request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor
    )
    assert _bytes(route.store.root) == before_draft
    assert preview["calculation"]["rows"] == first["draft"]["calculation"]["rows"]
    assert preview["calculation"]["annual"]["gap_yuan"] is None
    cleared = await route.revise(
        request,
        route.Revise(parent_id=first["draft"]["id"], inputs=inputs, reason="clear inherited target"),
        actor,
        "clear-old-target",
    )
    assert cleared["draft"]["calculation"] == preview["calculation"]
    assert (await route.current(request, actor))["draft"] == cleared["draft"]
    inputs["target_revenue_yuan"] = "20000"
    # Department target can be specified before the user decides its track split.
    manual = await route.revise(
        request,
        route.Revise(parent_id=cleared["draft"]["id"], inputs=inputs, reason="set FY28 business target"),
        actor,
        "manual-target",
    )
    assert manual["draft"]["calculation"]["annual"]["target_revenue_yuan"] == "20000.000000"
    assert manual["draft"]["calculation"]["annual"]["gap_yuan"] is not None
    assert manual["draft"]["calculation"]["target_status"] == "TRACK_TARGETS_NOT_SET"
    assert manual["draft"]["calculation"]["tracks"][0]["target_revenue_yuan"] is None
    assert manual["draft"]["calculation"]["rows"] == first["draft"]["calculation"]["rows"]
    inputs["track_targets"]["智慧空间"] = "20000"
    allocated = await route.revise(
        request,
        route.Revise(parent_id=manual["draft"]["id"], inputs=inputs, reason="allocate track target"),
        actor,
        "allocate-target",
    )
    assert allocated["draft"]["calculation"]["tracks"][0]["target_revenue_yuan"] == "20000.000000"
    assert (await route.current(request, actor))["draft"] == allocated["draft"]
    assert _bytes(forecast_routes.store.root) == before_forecast
    assert all(_bytes(route.store.root)[name] == data for name, data in before_draft.items())


@pytest.mark.asyncio
async def test_new_confirmed_source_only_marks_changed_until_explicit_prepare(setup_route: tuple) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    old_source = first["draft"]["source"]
    latest = _forecast(actor, parent=old_source["id"], sequence=2)
    view = await route.current(request, actor)
    assert view["source_changed"] is True
    assert view["source"]["id"] == latest["id"]
    assert view["draft"] == first["draft"]
    revised = await route.revise(
        request,
        route.Revise(
            parent_id=first["draft"]["id"], reason="keep frozen source", inputs=first["draft"]["inputs"]
        ),
        actor,
        "revise-2",
    )
    assert revised["source_changed"] is True
    assert revised["draft"]["source"] == old_source
    replaced = await route.prepare(
        request,
        route.Prepare(forecast_id=latest["id"], parent_id=revised["draft"]["id"], reason="new source"),
        actor,
        "prepare-3",
    )
    assert replaced["source_changed"] is False
    assert replaced["draft"]["source"]["id"] == latest["id"]


@pytest.mark.asyncio
async def test_idempotency_retains_original_response_after_forecast_and_budget_change(
    setup_route: tuple,
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    source_id = first["draft"]["source"]["id"]
    _forecast(actor, parent=source_id, sequence=2)
    second = await route.revise(
        request,
        route.Revise(parent_id=first["draft"]["id"], reason="second", inputs=first["draft"]["inputs"]),
        actor,
        "revise-2",
    )
    before = _bytes(route.store.root)
    assert (
        await route.prepare(
            request, route.Prepare(forecast_id=source_id, reason="create draft"), actor, "prepare-1"
        )
        == first
    )
    assert (
        await route.revise(
            request,
            route.Revise(parent_id=first["draft"]["id"], reason="second", inputs=first["draft"]["inputs"]),
            actor,
            "revise-2",
        )
        == second
    )
    assert _bytes(route.store.root) == before
    with pytest.raises(HTTPException) as exc:
        await route.prepare(
            request, route.Prepare(forecast_id=source_id, reason="changed"), actor, "prepare-1"
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["prepare", "revise", "preview"])
async def test_stale_parent_never_overwrites_draft(setup_route: tuple, command: str) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)
    with pytest.raises(HTTPException) as exc:
        if command == "prepare":
            await route.prepare(
                request,
                route.Prepare(forecast_id=first["draft"]["source"]["id"], reason="missing parent"),
                actor,
                "prepare-2",
            )
        elif command == "revise":
            await route.revise(
                request,
                route.Revise(parent_id="stale", reason="stale", inputs=first["draft"]["inputs"]),
                actor,
                "revise-2",
            )
        else:
            await route.preview(
                request, route.Preview(parent_id="stale", inputs=first["draft"]["inputs"]), actor
            )
    assert exc.value.status_code == 409
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["revise", "preview"])
@pytest.mark.parametrize(
    "mutation",
    [
        "fiscal_year",
        "source_as_of",
        "dimensions",
        "id",
        "entry_weight",
        "new_weights",
        "carryover",
        "opening",
        "notes",
        "coverage",
    ],
)
async def test_frozen_inputs_cannot_be_mutated(setup_route: tuple, command: str, mutation: str) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    if mutation == "fiscal_year":
        inputs["fiscal_year"] = 2029
    elif mutation == "source_as_of":
        inputs["source_as_of"] = "2026-10-08"
    elif mutation == "dimensions":
        inputs["combinations"][0]["campus"] = "other"
    elif mutation == "id":
        inputs["rates"][0]["id"] = "other"
    elif mutation == "entry_weight":
        inputs["combinations"][0]["source_entry_weight"] = 1
    elif mutation == "new_weights":
        inputs["combinations"][0]["source_new_by_season"]["秋季"] = 1
    elif mutation == "carryover":
        inputs["combinations"][0]["opening_carryover_yuan"] = "1"
    elif mutation == "opening":
        inputs["opening_sources"][0]["students"] = 1
    elif mutation == "notes":
        inputs["notes"].append({"hidden_override": True})
    else:
        inputs["plans"].pop()
    before = _bytes(route.store.root)
    with pytest.raises(HTTPException) as exc:
        if command == "revise":
            await route.revise(
                request,
                route.Revise(parent_id=first["draft"]["id"], reason="mutate", inputs=inputs),
                actor,
                "mutate-2",
            )
        else:
            await route.preview(request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor)
    assert exc.value.status_code == 422
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_total", True),
        ("entry_total", 1.5),
        ("entry_total", -1),
        ("renewal_ppm", True),
        ("renewal_ppm", 800001),
        ("share_ppm", 1000000),
        ("discount_ppm", 999999),
        ("price_yuan", "NaN"),
        ("tax_factor", "Infinity"),
        ("tax_factor", "0"),
        ("students", True),
        ("minimum", 0),
        ("extra", "override"),
        ("entry_total", float("inf")),
    ],
)
async def test_invalid_numeric_bounds_and_extra_fields_are_rejected(
    setup_route: tuple, field: str, value: Any
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    if field in {"renewal_ppm", "share_ppm"}:
        inputs["rates"][0][field] = value
    elif field in {"discount_ppm", "price_yuan"}:
        inputs["finance"][0][field] = value
    elif field == "students":
        inputs["plans"][0][field] = value
    elif field == "minimum":
        inputs["class_sizes"][0][field] = value
    else:
        inputs[field] = value
    before = _bytes(route.store.root)
    with pytest.raises(HTTPException) as exc:
        await route.revise(
            request,
            route.Revise(parent_id=first["draft"]["id"], reason="invalid", inputs=inputs),
            actor,
            "invalid-2",
        )
    assert exc.value.status_code == 422
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "draft",
        "no_confirmation",
        "trial",
        "calculation_trial",
        "not_ready",
        "blocking",
        "baseline",
        "snapshot",
    ],
)
async def test_prepare_rejects_ineligible_forecast_without_appending(
    setup_route: tuple, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    request, actor, _ = setup_route
    version = _forecast(actor)
    invalid = deepcopy(version)
    if mutation == "draft":
        invalid["status"] = "DRAFT"
    elif mutation == "no_confirmation":
        invalid["confirmation"] = None
    elif mutation == "trial":
        invalid["assumptions"]["trial_mode"] = True
    elif mutation == "calculation_trial":
        invalid["calculation"]["trial_mode"] = True
    elif mutation == "not_ready":
        invalid["calculation"]["ready"] = False
    elif mutation == "blocking":
        invalid["calculation"]["issues"] = [{"severity": "BLOCKING", "code": "source missing"}]
    elif mutation == "baseline":
        invalid["baseline_hash"] = "changed"
    else:
        invalid["snapshot_hash"] = "changed"

    async def invalid_forecasts(_: Actor) -> list[Json]:
        return [invalid]

    monkeypatch.setattr(route, "_forecasts", invalid_forecasts)
    with pytest.raises(HTTPException) as exc:
        await route.prepare(
            request, route.Prepare(forecast_id=invalid["id"], reason="invalid"), actor, "prepare-1"
        )
    assert exc.value.status_code == 409
    assert _bytes(route.store.root) == {}


@pytest.mark.asyncio
async def test_unknown_or_other_department_source_and_draft_are_inaccessible(setup_route: tuple) -> None:
    request, actor, _ = setup_route
    other = Actor("other", "other department", actor.roles)
    other_version = _forecast(other)
    with pytest.raises(HTTPException) as source_error:
        await route.prepare(
            request, route.Prepare(forecast_id=other_version["id"], reason="other"), actor, "prepare-1"
        )
    assert source_error.value.status_code == 404
    with pytest.raises(HTTPException) as access_error:
        await route.current(request, other)
    assert access_error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.DATA_MAINTAINER, Role.DEPARTMENT_BUDGET_OWNER])
async def test_either_business_write_role_can_prepare(setup_route: tuple, role: Role) -> None:
    request, actor, _ = setup_route
    version = _forecast(actor)
    single = Actor("single", actor.department_id, frozenset({role}))
    result = await route.prepare(
        request, route.Prepare(forecast_id=version["id"], reason="authorized"), single, "prepare-1"
    )
    assert result["draft"]["created_by"] == "single"


@pytest.mark.asyncio
async def test_permission_admin_alone_cannot_write_even_on_replay(setup_route: tuple) -> None:
    request, actor, _ = setup_route
    result = await _prepare(request, actor)
    admin = Actor(actor.user_id, actor.department_id, frozenset({Role.PERMISSION_ADMIN}))
    with pytest.raises(HTTPException) as exc:
        await route.prepare(
            request,
            route.Prepare(forecast_id=result["draft"]["source"]["id"], reason="create draft"),
            admin,
            "prepare-1",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["current", "prepare", "revise", "preview"])
async def test_every_route_reuses_real_local_only_historical_guard(
    setup_route: tuple, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    _, actor, baseline = setup_route

    async def trial(_: Request) -> Json:
        return {"departmentId": "department"}

    reads: list[str] = []

    async def database_review(department_id: str) -> Json:
        reads.append(department_id)
        return deepcopy(baseline)

    monkeypatch.setattr(forecast_routes, "_access", REAL_ACCESS)
    monkeypatch.setattr(historical_review, "trial_session", trial)
    monkeypatch.setattr(historical_review, "read_database_review", database_review)
    external = Request({"type": "http", "headers": [(b"x-budget-client-address", b"192.168.1.10")]})
    with pytest.raises(HTTPException) as exc:
        if command == "current":
            await route.current(external, actor)
        elif command == "prepare":
            await route.prepare(
                external, route.Prepare(forecast_id="forecast", reason="test"), actor, "prepare-1"
            )
        elif command == "revise":
            await route.revise(
                external, route.Revise(parent_id="draft", reason="test", inputs=_inputs()), actor, "revise-1"
            )
        else:
            await route.preview(external, route.Preview(parent_id="draft", inputs=_inputs()), actor)
    assert exc.value.status_code == 403
    assert "仅在本机" in exc.value.detail
    assert reads == []
    assert _bytes(route.store.root) == _bytes(forecast_routes.store.root) == {}


@pytest.mark.asyncio
async def test_changed_archive_blocks_new_revision_and_preview(
    setup_route: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)

    async def changed(_: Request, __: Actor) -> Json:
        return {"fiscal_year": 2027, "sha256": "new immutable archive", "rows": []}

    monkeypatch.setattr(forecast_routes, "_access", changed)
    for operation in ("current", "revise", "preview"):
        with pytest.raises(HTTPException) as exc:
            if operation == "current":
                await route.current(request, actor)
            elif operation == "revise":
                await route.revise(
                    request,
                    route.Revise(
                        parent_id=first["draft"]["id"], reason="changed", inputs=first["draft"]["inputs"]
                    ),
                    actor,
                    "revise-2",
                )
            else:
                await route.preview(
                    request,
                    route.Preview(parent_id=first["draft"]["id"], inputs=first["draft"]["inputs"]),
                    actor,
                )
        assert exc.value.status_code == 409
    assert _bytes(route.store.root) == before


def test_http_wiring_strict_bodies_and_no_publish_interface(setup_route: tuple) -> None:
    _, actor, _ = setup_route
    version = _forecast(actor)
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: actor
    with TestClient(app) as client:
        assert client.get("/api/v1/next-year-budgets/fy28/current").status_code == 200
        assert (
            client.post(
                "/api/v1/next-year-budgets/fy28/prepare",
                json={"forecast_id": version["id"], "reason": "HTTP test"},
            ).status_code
            == 422
        )
        response = client.post(
            "/api/v1/next-year-budgets/fy28/prepare",
            headers={"Idempotency-Key": "prepare-http"},
            json={"forecast_id": version["id"], "reason": "HTTP test"},
        )
        assert response.status_code == 201
        draft = response.json()["draft"]
        assert draft["status"] == "DRAFT"
        assert client.post("/api/v1/next-year-budgets/fy28/confirm", json={}).status_code == 404
        assert client.post("/api/v1/next-year-budgets/fy28/publish", json={}).status_code == 404
        assert (
            client.post(
                "/api/v1/next-year-budgets/fy28/revise",
                headers={"Idempotency-Key": "revise-http"},
                json={
                    "parent_id": draft["id"],
                    "reason": "cannot forge",
                    "inputs": draft["inputs"],
                    "source": {"id": "other"},
                },
            ).status_code
            == 422
        )
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "model,payload",
    [
        (route.Prepare, {"forecast_id": "forecast", "reason": "  "}),
        (route.Revise, {"parent_id": "draft", "reason": "  ", "inputs": {}}),
        (route.Preview, {"parent_id": "draft", "inputs": {}, "reason": "unexpected"}),
    ],
)
def test_blank_reason_and_unknown_body_fields_fail_before_commands(model: Any, payload: Json) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["current", "preview", "revise"])
async def test_rehashed_frozen_input_tampering_is_not_adopted_as_new_original(
    setup_route: tuple, command: str
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    path = next(route.store.root.glob("event-*.json"))
    envelope = json.loads(path.read_text())
    draft = envelope["event"]["draft"]
    draft["inputs"]["combinations"][0]["source_entry_weight"] = 123
    draft["inputs_hash"] = fingerprint(draft["inputs"])
    envelope["sha256"] = fingerprint(envelope["event"])
    path.write_text(json.dumps(envelope))
    before = _bytes(route.store.root)
    with pytest.raises(HTTPException) as exc:
        if command == "current":
            await route.current(request, actor)
        elif command == "preview":
            await route.preview(
                request, route.Preview(parent_id=first["draft"]["id"], inputs=draft["inputs"]), actor
            )
        else:
            await route.revise(
                request,
                route.Revise(
                    parent_id=first["draft"]["id"], reason="do not adopt tamper", inputs=draft["inputs"]
                ),
                actor,
                "tamper-2",
            )
    assert exc.value.status_code == 409
    assert _bytes(route.store.root) == before
