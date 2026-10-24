import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

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
from packages.next_year.calculation import (
    calculate_plan,
    forecast_basis,
    prepare_inputs,
    validate_plan_inputs,
)
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


def _forecast_store() -> ForecastStore:
    # The route fixture deliberately replaces the production async store with
    # this synchronous, temporary-file store; never access the live database.
    store = cast(ForecastStore, forecast_routes.store)
    assert isinstance(store, ForecastStore)
    return store


def _forecast(
    actor: Actor,
    *,
    parent: str | None = None,
    sequence: int = 1,
    confirmed: bool = True,
    snapshot_marker: str = "source evidence",
) -> Json:
    store = _forecast_store()
    result = store.append_version(
        department=actor.department_id,
        actor=actor.user_id,
        request_id=f"forecast-{sequence}",
        request_hash=f"forecast-content-{sequence}",
        parent_id=parent,
        baseline_hash=BASELINE_HASH,
        snapshot={"as_of": "2026-10-07", "rows": [], "private": snapshot_marker},
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
        result = store.confirm(
            department=actor.department_id,
            actor=actor.user_id,
            version_id=result["version"]["id"],
            request_id=f"confirm-{sequence}",
            request_hash=f"confirm-content-{sequence}",
            reason="fixture reviewed",
        )
    return cast(Json, result["version"])


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
async def test_empty_state_and_latest_confirmed_source_not_latest_forecast_draft(
    setup_route: tuple[Request, Actor, Json],
) -> None:
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
async def test_prepare_preview_revise_keep_source_data_and_only_add_draft_events(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before_forecast = _bytes(_forecast_store().root)
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
    assert _bytes(_forecast_store().root) == before_forecast
    assert all(_bytes(route.store.root)[name] == data for name, data in before_draft.items())


@pytest.mark.asyncio
async def test_basis_preview_is_read_only_and_matches_explicit_prepare(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, baseline = setup_route
    source = _forecast(actor)
    before_forecast = _bytes(_forecast_store().root)
    before_baseline = deepcopy(baseline)
    append = route.store.append_draft

    def forbidden_append(**_: Any) -> Json:
        pytest.fail("read-only basis preview must not append a draft")

    monkeypatch.setattr(route.store, "append_draft", forbidden_append)
    result = await route.prepare_preview(request, route.PreparePreview(forecast_id=source["id"]), actor)
    assert set(result) == {
        "read_only",
        "fiscal_year",
        "parent_id",
        "source_id",
        "source",
        "inputs",
        "calculation",
    }
    assert result["read_only"] is True
    assert result["fiscal_year"] == 2028
    assert result["parent_id"] is None
    assert result["source_id"] == source["id"] == result["source"]["id"]
    assert result["calculation"] == calculate_plan(result["inputs"])
    assert _bytes(route.store.root) == {}
    assert (await route.current(request, actor))["draft"] is None
    assert _bytes(_forecast_store().root) == before_forecast
    assert baseline == before_baseline
    assert "source_snapshot" not in str(result)
    assert "source evidence" not in str(result)
    monkeypatch.setattr(route.store, "append_draft", append)
    saved = await route.prepare(
        request,
        route.Prepare(forecast_id=source["id"], reason="explicit create after preview"),
        actor,
        "prepare-after-preview",
    )
    assert saved["draft"]["inputs"] == result["inputs"]
    assert saved["draft"]["calculation"] == result["calculation"]
    assert saved["draft"]["source"] == result["source"]


@pytest.mark.asyncio
async def test_basis_preview_rebuilds_inputs_without_inheriting_saved_manual_changes(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, _ = setup_route
    initial = await _prepare(request, actor)
    changed = deepcopy(initial["draft"]["inputs"])
    changed["entry_total"] = 999
    changed["target_revenue_yuan"] = "123456"
    changed["track_targets"] = {"智慧空间": "123456"}
    revised = await route.revise(
        request,
        route.Revise(parent_id=initial["draft"]["id"], inputs=changed, reason="manual changes"),
        actor,
        "revise-before-basis-preview",
    )
    new_source = _forecast(actor, parent=initial["draft"]["source"]["id"], sequence=2)
    fresh = _inputs()
    fresh["entry_total"] = 12
    fresh["target_revenue_yuan"] = None
    fresh["track_targets"] = {"智慧空间": None}
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(fresh))
    before_drafts, before_forecasts = _bytes(route.store.root), _bytes(_forecast_store().root)
    result = await route.prepare_preview(
        request, route.PreparePreview(forecast_id=new_source["id"], parent_id=revised["draft"]["id"]), actor
    )
    assert result["inputs"] == fresh != changed
    assert result["calculation"]["annual"]["target_revenue_yuan"] is None
    assert result["calculation"]["annual"]["gap_yuan"] is None
    assert result["source_id"] == new_source["id"]
    assert result["parent_id"] == revised["draft"]["id"]
    assert (await route.current(request, actor))["draft"] == revised["draft"]
    assert _bytes(route.store.root) == before_drafts
    assert _bytes(_forecast_store().root) == before_forecasts
    saved = await route.prepare(
        request,
        route.Prepare(forecast_id=new_source["id"], parent_id=result["parent_id"], reason="use basis"),
        actor,
        "prepare-reviewed-basis",
    )
    assert saved["draft"]["inputs"] == result["inputs"]
    assert saved["draft"]["calculation"] == result["calculation"]


@pytest.mark.asyncio
async def test_basis_preview_and_prepare_use_real_preparation_and_calculation_engines(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, baseline = setup_route
    version = _forecast(actor)
    scopes = [
        {key: combo[key] for key in ("track", "product", "grade", "subject", "campus")}
        for combo in _inputs()["combinations"]
    ]
    baseline["rows"] = [
        {
            **dims,
            "season": season,
            "entry": "是" if dims["grade"] == "七年级" else "否",
            "price": "106",
            "discount": "1",
            "recognition": "0.8",
            "renewal": "0.8",
            "share": "0.2",
            "minimum": 5,
            "old": 80,
            "new": 20,
            "total": 100,
            "revenue": "1000",
        }
        for dims in scopes
        for season in SEASONS
    ]
    version["calculation"]["rows"] = [
        {
            **dims,
            "season": season,
            "forecast_old": 80,
            "forecast_new": 20,
            "forecast_total": 100,
            "forecast_revenue": "1000",
            "forecast_carryout": "0",
            "counts_not_applicable": False,
        }
        for dims in scopes
        for season in SEASONS
    ]
    version["states"] = {"暑假": "FINALIZED", "秋季": "FUTURE", "寒假": "FUTURE", "春季": "FUTURE"}
    version["assumptions"]["states"] = version["states"]
    version["snapshot"]["rows"] = [
        {
            **dims,
            "season": "暑假",
            "enrollment": 100,
            "new_business": 13,
            "new_base": 20,
            "old_base": 80,
            "actual_revenue": "1000",
            "actual_carryout": "0",
        }
        for dims in scopes
    ]
    version["snapshot_hash"] = fingerprint(version["snapshot"])

    async def selected_forecasts(_: Actor) -> list[Json]:
        return [deepcopy(version)]

    monkeypatch.setattr(route, "prepare_inputs", prepare_inputs)
    monkeypatch.setattr(route, "_forecasts", selected_forecasts)
    before_source = fingerprint((baseline, version))
    before_ledger = _bytes(_forecast_store().root)
    preview = await route.prepare_preview(request, route.PreparePreview(forecast_id=version["id"]), actor)
    assert preview["inputs"] == prepare_inputs(baseline, version)
    assert preview["inputs"]["entry_total"] == 13
    assert preview["inputs"]["target_revenue_yuan"] is None
    assert preview["calculation"] == calculate_plan(preview["inputs"])
    assert preview["calculation"]["complete"] is True
    assert preview["calculation"]["annual"]["revenue_yuan"] is not None
    assert _bytes(route.store.root) == {}
    saved = await route.prepare(
        request,
        route.Prepare(forecast_id=version["id"], reason="real adapter parity"),
        actor,
        "prepare-real-parity",
    )
    assert saved["draft"]["inputs"] == preview["inputs"]
    assert saved["draft"]["calculation"] == preview["calculation"]
    assert fingerprint((baseline, version)) == before_source
    assert _bytes(_forecast_store().root) == before_ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["bound_source", "bound_baseline", "parent_year", "input_year"])
async def test_basis_preview_rejects_mismatched_frozen_parent_and_prepared_year(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    request, actor, _ = setup_route
    saved = await _prepare(request, actor)
    parent = route.store.current(actor.department_id)
    if mutation == "bound_source":
        parent["draft"]["source"]["id"] = "tampered-source"
    elif mutation == "bound_baseline":
        parent["draft"]["source_snapshot"]["baseline"]["sha256"] = "other-baseline"
    elif mutation == "parent_year":
        parent["draft"]["inputs"]["fiscal_year"] = 2029
    else:
        incompatible = _inputs()
        incompatible["fiscal_year"] = 2029
        monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(incompatible))
    monkeypatch.setattr(route.store, "current", lambda _: deepcopy(parent))
    before = _bytes(route.store.root)
    with pytest.raises(HTTPException) as error:
        await route.prepare_preview(
            request,
            route.PreparePreview(forecast_id=saved["draft"]["source"]["id"], parent_id=saved["draft"]["id"]),
            actor,
        )
    assert error.value.status_code == 409
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_kind", ["missing", "stale", "other_department"])
async def test_basis_preview_rejects_wrong_parent_without_writing(
    setup_route: tuple[Request, Actor, Json], parent_kind: str
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)
    parent_id = None if parent_kind == "missing" else "not-the-current-draft"
    if parent_kind == "other_department":
        other = Actor("other", "other department", actor.roles)
        other_source = _forecast(other)
        other_draft = route.store.append_draft(
            department=other.department_id,
            actor=other.user_id,
            request_id="other-department-draft",
            request_hash="other-department-content",
            parent_id=None,
            source=forecast_basis(other_source),
            inputs=_inputs(),
            calculation=calculate_plan(_inputs()),
            reason="other department",
            operation="PREPARE",
            source_snapshot={"baseline": setup_route[2], "forecast": other_source},
        )
        parent_id = other_draft["draft"]["id"]
        before = _bytes(route.store.root)
    with pytest.raises(HTTPException) as error:
        await route.prepare_preview(
            request,
            route.PreparePreview(forecast_id=first["draft"]["source"]["id"], parent_id=parent_id),
            actor,
        )
    assert error.value.status_code == 409
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("year", ["fy29", "2029", "fy27", "bad-year"])
async def test_basis_preview_rejects_cross_year_source(
    setup_route: tuple[Request, Actor, Json], year: str
) -> None:
    request, actor, _ = setup_route
    source = _forecast(actor)
    with pytest.raises(HTTPException) as error:
        await route.prepare_preview(request, route.PreparePreview(forecast_id=source["id"]), actor, year)
    assert error.value.status_code == (422 if year in {"fy27", "bad-year"} else 409)
    assert _bytes(route.store.root) == {}
    assert _bytes(route.store.root / "fy2029") == {}


@pytest.mark.asyncio
async def test_basis_preview_rejects_unknown_source_and_unauthorized_actor(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    source = _forecast(actor)
    for caller, source_id, status in (
        (actor, "unknown-forecast", 404),
        (Actor("admin", actor.department_id, frozenset({Role.PERMISSION_ADMIN})), source["id"], 403),
        (Actor("other", "other department", actor.roles), source["id"], 403),
    ):
        with pytest.raises(HTTPException) as error:
            await route.prepare_preview(request, route.PreparePreview(forecast_id=source_id), caller)
        assert error.value.status_code == status
    assert _bytes(route.store.root) == {}


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
    before_forecast = _bytes(_forecast_store().root)
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
    assert _bytes(_forecast_store().root) == before_forecast


@pytest.mark.asyncio
async def test_existing_draft_can_clear_preview_save_then_accept_manually_specified_target(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)  # Simulates older prepared inputs with an inherited target.
    before_draft = _bytes(route.store.root)
    before_forecast = _bytes(_forecast_store().root)
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
    assert _bytes(_forecast_store().root) == before_forecast
    assert all(_bytes(route.store.root)[name] == data for name, data in before_draft.items())


@pytest.mark.asyncio
async def test_new_confirmed_source_only_marks_changed_until_explicit_prepare(
    setup_route: tuple[Request, Actor, Json],
) -> None:
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
    setup_route: tuple[Request, Actor, Json],
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
async def test_stale_parent_never_overwrites_draft(
    setup_route: tuple[Request, Actor, Json], command: str
) -> None:
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
async def test_frozen_inputs_cannot_be_mutated(
    setup_route: tuple[Request, Actor, Json], command: str, mutation: str
) -> None:
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
    setup_route: tuple[Request, Actor, Json], field: str, value: Any
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
@pytest.mark.parametrize("command", ["prepare", "prepare_preview"])
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
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch, mutation: str, command: str
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
        if command == "prepare":
            await route.prepare(
                request, route.Prepare(forecast_id=invalid["id"], reason="invalid"), actor, "prepare-1"
            )
        else:
            await route.prepare_preview(request, route.PreparePreview(forecast_id=invalid["id"]), actor)
    assert exc.value.status_code == 409
    assert _bytes(route.store.root) == {}


@pytest.mark.asyncio
async def test_unknown_or_other_department_source_and_draft_are_inaccessible(
    setup_route: tuple[Request, Actor, Json],
) -> None:
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
async def test_either_business_write_role_can_prepare(
    setup_route: tuple[Request, Actor, Json], role: Role
) -> None:
    request, actor, _ = setup_route
    version = _forecast(actor)
    single = Actor("single", actor.department_id, frozenset({role}))
    result = await route.prepare(
        request, route.Prepare(forecast_id=version["id"], reason="authorized"), single, "prepare-1"
    )
    assert result["draft"]["created_by"] == "single"


@pytest.mark.asyncio
async def test_permission_admin_alone_cannot_write_even_on_replay(
    setup_route: tuple[Request, Actor, Json],
) -> None:
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
@pytest.mark.parametrize("command", ["current", "prepare", "prepare_preview", "revise", "preview"])
async def test_every_route_reuses_real_local_only_historical_guard(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch, command: str
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
        elif command == "prepare_preview":
            await route.prepare_preview(external, route.PreparePreview(forecast_id="forecast"), actor)
        else:
            await route.preview(external, route.Preview(parent_id="draft", inputs=_inputs()), actor)
    assert exc.value.status_code == 403
    assert "仅在本机" in exc.value.detail
    assert reads == []
    assert _bytes(route.store.root) == _bytes(_forecast_store().root) == {}


@pytest.mark.asyncio
async def test_changed_archive_blocks_new_revision_and_preview(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
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


def test_http_wiring_strict_bodies_and_no_publish_interface(setup_route: tuple[Request, Actor, Json]) -> None:
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


def test_http_basis_preview_requires_no_write_token_and_rejects_forged_inputs(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    _, actor, _ = setup_route
    source = _forecast(actor)
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: actor
    before_forecast = _bytes(_forecast_store().root)
    try:
        with TestClient(app) as client:
            endpoint = "/api/v1/next-year-budgets/fy28/prepare-preview"
            result = client.post(endpoint, json={"forecast_id": source["id"], "parent_id": None})
            assert result.status_code == 200
            assert result.json()["read_only"] is True
            assert result.json()["source_id"] == source["id"]
            for extra in ({"inputs": _inputs()}, {"reason": "fake-save"}, {"source": {"id": "forged"}}):
                assert client.post(endpoint, json={"forecast_id": source["id"], **extra}).status_code == 422
            assert client.post(endpoint, json={"forecast_id": ""}).status_code == 422
            assert (
                client.post(endpoint, json={"forecast_id": source["id"], "parent_id": "stale"}).status_code
                == 409
            )
            assert client.get("/api/v1/next-year-budgets/fy28/current").json()["draft"] is None
        assert _bytes(route.store.root) == {}
        assert _bytes(_forecast_store().root) == before_forecast
    finally:
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
@pytest.mark.parametrize("command", ["current", "preview", "revise", "class-repair"])
async def test_rehashed_frozen_input_tampering_is_not_adopted_as_new_original(
    setup_route: tuple[Request, Actor, Json], command: str
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
        elif command == "class-repair":
            await route.class_repair(
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


@pytest.mark.asyncio
async def test_catalog_lists_real_confirmed_sources_and_keeps_old_source_selectable(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = _forecast(actor)
    latest = _forecast(actor, parent=first["id"], sequence=2)
    view = await route.catalog(request, actor)
    assert view["source_fiscal_year"] == 2027
    assert view["default_fiscal_year"] == 2028
    assert view["latest_draft"] is None
    assert [item["id"] for item in view["sources"]] == [latest["id"], first["id"]]
    assert view["fiscal_years"] == [
        {
            "fiscal_year": 2028,
            "has_draft": False,
            "latest_sequence": None,
            "latest_draft_id": None,
            "source_fiscal_year": 2027,
            "can_prepare": True,
        }
    ]
    prepared = await route.prepare(
        request,
        route.Prepare(forecast_id=first["id"], reason="use earlier confirmed forecast"),
        actor,
        "prepare-earlier",
    )
    assert prepared["draft"]["source"]["id"] == first["id"]
    assert prepared["source_changed"] is True
    assert (await route.catalog(request, actor))["fiscal_years"][0]["has_draft"] is True


@pytest.mark.asyncio
async def test_catalog_explains_latest_draft_without_confirming_or_selecting_it(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    confirmed = _forecast(actor)
    older_draft = _forecast(actor, parent=confirmed["id"], sequence=2, confirmed=False)
    latest = _forecast(actor, parent=older_draft["id"], sequence=3, confirmed=False)
    before_forecast, before_budget = _bytes(_forecast_store().root), _bytes(route.store.root)

    view = await route.catalog(request, actor)

    assert view["latest_draft"] == {
        **{key: latest[key] for key in ("id", "sequence", "as_of", "status")},
        "fiscal_year": 2027,
    }
    assert [item["id"] for item in view["sources"]] == [confirmed["id"]]
    assert "source evidence" not in str(view)
    assert "calculation" not in view["latest_draft"]
    assert _bytes(_forecast_store().root) == before_forecast
    assert _bytes(route.store.root) == before_budget
    current = await route.current(request, actor)
    assert current["source"]["id"] == confirmed["id"]
    assert current["draft"] is None

    # A real confirmation updates the catalog; reading the notice never does.
    _forecast_store().confirm(
        department=actor.department_id,
        actor=actor.user_id,
        version_id=latest["id"],
        request_id="confirm-latest-for-catalog",
        request_hash="catalog-confirmed-fixture",
        reason="fixture reviewed",
    )
    updated = await route.catalog(request, actor)
    assert updated["sources"][0]["id"] == latest["id"]
    assert updated["latest_draft"]["id"] == older_draft["id"]


@pytest.mark.asyncio
async def test_catalog_can_explain_a_draft_before_any_forecast_is_confirmed(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    assert (await route.catalog(request, actor))["latest_draft"] is None
    latest = _forecast(actor, confirmed=False)
    before = _bytes(_forecast_store().root)
    view = await route.catalog(request, actor)
    assert view["sources"] == []
    assert view["latest_draft"]["id"] == latest["id"]
    assert view["latest_draft"]["status"] == "DRAFT"
    assert view["fiscal_years"][0]["can_prepare"] is False
    assert _bytes(_forecast_store().root) == before


@pytest.mark.asyncio
async def test_read_compare_restore_are_immutable_and_hide_frozen_source_evidence(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    inputs["entry_total"] = 20
    second = await route.revise(
        request,
        route.Revise(parent_id=first["draft"]["id"], reason="raise entry", inputs=inputs),
        actor,
        "revise-second",
    )
    before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    selected = await route.version(request, first["draft"]["id"], actor)
    assert selected["draft"] == first["draft"]
    assert selected["latest_draft_id"] == second["draft"]["id"]
    assert selected["read_only"] is True
    assert "source_snapshot" not in str(selected)
    assert "source evidence" not in str(selected)
    compared = await route.compare(request, first["draft"]["id"], second["draft"]["id"], actor)
    assert compared["before"] == first["draft"]
    assert compared["after"] == second["draft"]
    assert compared["impact"]["annual"]["delta"]["total_students"] > 0
    assert compared["input_changes"]["fields"] == [{"field": "entry_total", "before": 10, "after": 20}]
    assert _bytes(route.store.root) == before
    body = route.Restore(
        parent_id=second["draft"]["id"], version_id=first["draft"]["id"], reason="recover earlier planning"
    )
    restored = await route.restore(request, body, actor, "restore-third")
    assert restored["draft"]["operation"] == "RESTORE"
    assert restored["draft"]["restored_from_id"] == first["draft"]["id"]
    assert restored["draft"]["sequence"] == 3
    assert restored["draft"]["inputs"] == first["draft"]["inputs"]
    assert restored["draft"]["calculation"] == first["draft"]["calculation"]
    assert all(_bytes(route.store.root)[name] == data for name, data in before.items())
    assert _bytes(_forecast_store().root) == forecast_before
    assert await route.restore(request, body, actor, "restore-third") == restored
    assert len(_bytes(route.store.root)) == 3


@pytest.mark.asyncio
async def test_impact_previews_input_and_result_changes_without_saving(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    inputs["entry_total"] = 15
    before = _bytes(route.store.root)
    result = await route.impact(request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor)
    assert set(result) == {"source", "calculation", "impact", "input_changes"}
    assert result["impact"]["annual"]["before"] == first["draft"]["calculation"]["annual"]
    assert result["impact"]["annual"]["after"] == result["calculation"]["annual"]
    assert result["input_changes"]["count"] == 1
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
async def test_legacy_class_size_is_frozen_until_explicit_preview_and_revision(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, _ = setup_route
    inputs = _inputs()
    inputs["entry_total"] = 333
    inputs["class_sizes"][0].update(minimum=24, planned_class_size=30)
    frozen_calculation = calculate_plan(inputs)
    frozen_calculation["rule_versions"].pop("class_count")
    for row in frozen_calculation["rows"]:
        row.pop("class_average")
        row["planned_class_size"] = 30
        row["class_count"] = (row["total_students"] + 29) // 30
    frozen_calculation["annual"]["class_count"] = sum(
        row["class_count"] for row in frozen_calculation["rows"]
    )
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(inputs))
    monkeypatch.setattr(route, "calculate_plan", lambda _: deepcopy(frozen_calculation))
    saved = await _prepare(request, actor)
    monkeypatch.setattr(route, "calculate_plan", calculate_plan)
    before_budget, before_forecast = _bytes(route.store.root), _bytes(_forecast_store().root)
    current = await route.current(request, actor)
    assert current["draft"]["calculation"] == frozen_calculation
    preview = await route.preview(
        request, route.Preview(parent_id=saved["draft"]["id"], inputs=inputs), actor
    )
    summer = next(
        row
        for row in preview["calculation"]["rows"]
        if row["combination_id"] == inputs["combinations"][0]["id"] and row["season"] == "暑假"
    )
    assert (summer["total_students"], summer["class_average"], summer["class_count"]) == (333, 24, 14)
    for field in ("revenue_yuan", "total_students", "old_students", "new_students"):
        assert preview["calculation"]["annual"][field] == frozen_calculation["annual"][field]
    assert _bytes(route.store.root) == before_budget
    assert _bytes(_forecast_store().root) == before_forecast
    revised = await route.revise(
        request,
        route.Revise(parent_id=saved["draft"]["id"], inputs=inputs, reason="统一班均口径"),
        actor,
        "class-average-explicit-revision",
    )
    assert revised["draft"]["sequence"] == 2
    assert revised["draft"]["calculation"] == preview["calculation"]
    assert all(_bytes(route.store.root)[name] == content for name, content in before_budget.items())
    assert _bytes(_forecast_store().root) == before_forecast


@pytest.mark.asyncio
@pytest.mark.parametrize("has_proposal", [True, False])
async def test_balance_is_only_a_preview_and_handles_no_proposal(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch, has_proposal: bool
) -> None:
    from packages.next_year import planning

    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    proposed = deepcopy(inputs)
    proposed["entry_total"] = 11

    def solve(_: Json, options: Json) -> Json:
        assert options == {"adjust_entry": True}
        return {
            "status": "FEASIBLE" if has_proposal else "INFEASIBLE",
            "proposed_inputs": proposed if has_proposal else None,
            "calculation": calculate_plan(proposed if has_proposal else inputs),
            "impact": {},
            "attempts": 1,
            "constraints": [],
        }

    monkeypatch.setattr(planning, "balance_plan", solve)
    before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    result = await route.balance(
        request,
        route.Balance(parent_id=first["draft"]["id"], inputs=inputs, options={"adjust_entry": True}),
        actor,
    )
    assert result["proposed_inputs"] == (proposed if has_proposal else None)
    assert result["input_changes"]["count"] == int(has_proposal)
    assert _bytes(route.store.root) == before
    assert _bytes(_forecast_store().root) == forecast_before


@pytest.mark.asyncio
@pytest.mark.parametrize("fiscal_year", ["fy27", "../../other", "fy2028/other", "Infinity", "fy2101"])
async def test_invalid_year_cannot_choose_a_ledger_namespace(
    setup_route: tuple[Request, Actor, Json], fiscal_year: str
) -> None:
    request, actor, _ = setup_route
    before = _bytes(route.store.root)
    with pytest.raises(HTTPException) as error:
        await route.current(request, actor, fiscal_year)
    assert error.value.status_code == 422
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
async def test_other_fiscal_year_does_not_borrow_fy27_source_or_overwrite_fy28(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)
    future = await route.current(request, actor, "fy29")
    assert future == {"source": None, "draft": None, "source_changed": False, "history": []}
    with pytest.raises(HTTPException) as error:
        await route.prepare(
            request,
            route.Prepare(forecast_id=first["draft"]["source"]["id"], reason="wrong source year"),
            actor,
            "future-prepare",
            "fy29",
        )
    assert error.value.status_code == 409
    assert _bytes(route.store.root) == before
    assert _bytes(route.store.root / "fy2029") == {}
    assert (await route.current(request, actor, "2028"))["draft"] == first["draft"]


@pytest.mark.asyncio
async def test_restore_cannot_use_stale_parent_missing_version_or_permission_admin(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)
    admin = Actor("admin", actor.department_id, frozenset({Role.PERMISSION_ADMIN}))
    for parent, version_id, caller, status in (
        ("stale", first["draft"]["id"], actor, 409),
        (first["draft"]["id"], "absent", actor, 404),
        (first["draft"]["id"], first["draft"]["id"], admin, 403),
    ):
        with pytest.raises(HTTPException) as error:
            await route.restore(
                request,
                route.Restore(parent_id=parent, version_id=version_id, reason="restore"),
                caller,
                "restore-rejected",
            )
        assert error.value.status_code == status
    assert _bytes(route.store.root) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "catalog",
        "versions",
        "version",
        "compare",
        "restore",
        "impact",
        "balance",
        "class-readiness",
        "class-repair",
    ],
)
async def test_new_planning_routes_keep_real_local_only_source_guard(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch, command: str
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
    with pytest.raises(HTTPException) as error:
        if command == "catalog":
            await route.catalog(external, actor)
        elif command == "versions":
            await route.versions(external, actor)
        elif command == "version":
            await route.version(external, "draft", actor)
        elif command == "compare":
            await route.compare(external, "before", "after", actor)
        elif command == "restore":
            await route.restore(
                external,
                route.Restore(parent_id="draft", version_id="history", reason="restore"),
                actor,
                "restore-external",
            )
        elif command == "impact":
            await route.impact(external, route.Preview(parent_id="draft", inputs=_inputs()), actor)
        elif command == "class-readiness":
            await route.class_readiness(external, route.Preview(parent_id="draft", inputs=_inputs()), actor)
        elif command == "class-repair":
            await route.class_repair(external, route.Preview(parent_id="draft", inputs=_inputs()), actor)
        else:
            await route.balance(external, route.Balance(parent_id="draft", inputs=_inputs()), actor)
    assert error.value.status_code == 403
    assert reads == []
    assert _bytes(route.store.root) == _bytes(_forecast_store().root) == {}


def test_http_new_version_routes_and_impact_keep_existing_preview_contract(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    _, actor, _ = setup_route
    source = _forecast(actor)
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: actor
    with TestClient(app) as client:
        assert client.get("/api/v1/next-year-budgets/catalog").status_code == 200
        response = client.post(
            "/api/v1/next-year-budgets/2028/prepare",
            headers={"Idempotency-Key": "prepare-new-http"},
            json={"forecast_id": source["id"], "reason": "HTTP version test"},
        )
        assert response.status_code == 201
        draft = response.json()["draft"]
        body = {"parent_id": draft["id"], "inputs": draft["inputs"]}
        preview = client.post("/api/v1/next-year-budgets/fy28/preview", json=body)
        assert set(preview.json()) == {"source", "calculation"}
        impact = client.post("/api/v1/next-year-budgets/fy28/impact", json=body)
        assert impact.status_code == 200
        assert impact.json()["input_changes"]["count"] == 0
        selected = client.get(f"/api/v1/next-year-budgets/fy28/versions/{draft['id']}")
        assert selected.status_code == 200
        assert selected.json()["read_only"] is True
        assert "source_snapshot" not in selected.text
        restored = client.post(
            "/api/v1/next-year-budgets/fy28/restore",
            headers={"Idempotency-Key": "restore-new-http"},
            json={"parent_id": draft["id"], "version_id": draft["id"], "reason": "new copy from history"},
        )
        assert restored.status_code == 201
        assert restored.json()["draft"]["sequence"] == 2
        assert client.post("/api/v1/next-year-budgets/fy28/publish", json={}).status_code == 404
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_real_balance_reuses_solver_without_saving_or_changing_frozen_data(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    inputs["target_revenue_yuan"] = first["draft"]["calculation"]["annual"]["revenue_yuan"]
    inputs["track_targets"] = {"智慧空间": inputs["target_revenue_yuan"]}
    before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    result = await route.balance(
        request,
        route.Balance(parent_id=first["draft"]["id"], inputs=inputs, options={"maximum_seconds": 1}),
        actor,
    )
    assert isinstance(result["attempts"], list)
    assert result["constraints"]["entry_total_fixed"] is True
    assert result["constraints"]["manual_locks_preserved"] is True
    if result["proposed_inputs"] is not None:
        assert result["proposed_inputs"]["entry_total"] == inputs["entry_total"]
        validate_plan_inputs(result["proposed_inputs"], first["draft"]["inputs"])
        assert calculate_plan(result["proposed_inputs"]) == result["calculation"]
    assert _bytes(route.store.root) == before
    assert _bytes(_forecast_store().root) == forecast_before


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["preview", "impact", "balance", "class-readiness", "class-repair"])
@pytest.mark.parametrize("failure", ["none", "immutable_input", "stale_parent", "other_year"])
async def test_planning_previews_never_append_on_success_or_rejection(
    setup_route: tuple[Request, Actor, Json],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    failure: str,
) -> None:
    from packages.next_year import class_repair, planning

    request, actor, baseline = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    inputs["entry_total"] = 12
    parent = "stale-parent" if failure == "stale_parent" else first["draft"]["id"]
    year = "fy29" if failure == "other_year" else "fy28"
    if failure == "immutable_input":
        inputs["notes"] = [{"message": "cannot replace frozen source notes"}]

    def solve(candidate: Json, _: Json) -> Json:
        return {
            "status": "FEASIBLE",
            "proposed_inputs": deepcopy(candidate),
            "calculation": calculate_plan(candidate),
        }

    monkeypatch.setattr(planning, "balance_plan", solve)
    monkeypatch.setattr(class_repair, "repair_class_plan", lambda candidate: solve(candidate, {}))
    draft_before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    original = fingerprint((first, baseline))

    async def invoke() -> Json:
        if command == "balance":
            return await route.balance(request, route.Balance(parent_id=parent, inputs=inputs), actor, year)
        body = route.Preview(parent_id=parent, inputs=inputs)
        if command == "class-readiness":
            return await route.class_readiness(request, body, actor, year)
        if command == "class-repair":
            return await route.class_repair(request, body, actor, year)
        if command == "preview":
            return await route.preview(request, body, actor, year)
        return await route.impact(request, body, actor, year)

    if failure == "none":
        result = await invoke()
        assert result["source"] == first["draft"]["source"]
        if command == "class-readiness":
            assert result["calculation_complete"] is True
            assert result["issue_count"] == 0
        else:
            assert result["calculation"]["annual"]["new_students"] > 0
    else:
        with pytest.raises(HTTPException) as error:
            await invoke()
        assert (
            error.value.status_code
            == {
                "immutable_input": 422,
                "stale_parent": 409,
                "other_year": 404,
            }[failure]
        )
    assert _bytes(route.store.root) == draft_before
    assert _bytes(route.store.root / "fy2029") == {}
    assert _bytes(_forecast_store().root) == forecast_before
    assert fingerprint((first, baseline)) == original


@pytest.mark.asyncio
async def test_restore_uses_selected_frozen_source_not_latest_forecast_or_recalculation(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    selected_view = route.store.version(actor.department_id, first["draft"]["id"])
    assert selected_view is not None
    selected = selected_view["draft"]
    latest_source = _forecast(
        actor, parent=first["draft"]["source"]["id"], sequence=2, snapshot_marker="new source evidence"
    )
    changed_inputs = _inputs()
    changed_inputs["entry_total"] = 23
    changed_inputs["notes"] = [{"message": "new source prepared assumptions"}]
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(changed_inputs))
    latest = await route.prepare(
        request,
        route.Prepare(forecast_id=latest_source["id"], parent_id=first["draft"]["id"], reason="new basis"),
        actor,
        "prepare-new-source",
    )
    latest_private = route.store.current(actor.department_id)["draft"]
    assert selected["inputs"] != latest_private["inputs"]
    assert selected["source_snapshot"] != latest_private["source_snapshot"]
    draft_before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)

    def forbidden_recalculation(_: Json) -> Json:
        raise AssertionError("restoring history must copy its frozen result, not run today's kernels")

    monkeypatch.setattr(route, "calculate_plan", forbidden_recalculation)
    result = await route.restore(
        request,
        route.Restore(
            parent_id=latest["draft"]["id"], version_id=first["draft"]["id"], reason="restore old basis"
        ),
        actor,
        "restore-selected-source",
    )
    restored = route.store.current(actor.department_id)["draft"]
    assert restored["sequence"] == 3
    assert restored["parent_id"] == latest["draft"]["id"]
    assert restored["restored_from_id"] == selected["id"]
    for field in ("source", "inputs", "prepared_inputs", "source_snapshot", "calculation"):
        assert restored[field] == selected[field]
    assert result["source"]["id"] == latest_source["id"]
    assert result["draft"]["source"]["id"] == first["draft"]["source"]["id"]
    assert result["source_changed"] is True
    assert "source_snapshot" not in str(result)
    assert len(_bytes(route.store.root)) == len(draft_before) + 1
    assert all(_bytes(route.store.root)[name] == value for name, value in draft_before.items())
    assert _bytes(_forecast_store().root) == forecast_before


@pytest.mark.asyncio
async def test_empty_future_year_namespaces_do_not_become_catalog_or_cross_year_versions(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)
    for year in ("fy29", "2030", "2100"):
        assert (await route.current(request, actor, year))["draft"] is None
        assert await route.versions(request, actor, year) == {"history": [], "latest_draft_id": None}
        with pytest.raises(HTTPException) as error:
            await route.version(request, first["draft"]["id"], actor, year)
        assert error.value.status_code == 404
        with pytest.raises(HTTPException) as error:
            await route.compare(request, first["draft"]["id"], first["draft"]["id"], actor, year)
        assert error.value.status_code == 404
    assert [row["fiscal_year"] for row in (await route.catalog(request, actor))["fiscal_years"]] == [2028]
    assert _bytes(route.store.root) == before
    assert not list(route.store.root.glob("fy*/event-*.json"))


def test_http_year_aliases_share_one_chain_and_invalid_routes_cannot_write(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    _, actor, _ = setup_route
    source = _forecast(actor)
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: actor
    try:
        with TestClient(app) as client:
            prepared = client.post(
                "/api/v1/next-year-budgets/FY2028/prepare",
                headers={"Idempotency-Key": "prepare-alias-http"},
                json={"forecast_id": source["id"], "reason": "year alias"},
            )
            assert prepared.status_code == 201
            draft = prepared.json()["draft"]
            before = _bytes(route.store.root)
            for year in ("fy28", "FY28", "fy2028", "2028", "28"):
                assert client.get(f"/api/v1/next-year-budgets/{year}/current").json()["draft"] == draft
            for year in ("fy27", "fy2101", "fy028", "NaN"):
                assert client.get(f"/api/v1/next-year-budgets/{year}/current").status_code == 422
                assert (
                    client.post(
                        f"/api/v1/next-year-budgets/{year}/restore",
                        headers={"Idempotency-Key": "invalid-year-http"},
                        json={"parent_id": draft["id"], "version_id": draft["id"], "reason": "invalid year"},
                    ).status_code
                    == 422
                )
            assert client.get("/api/v1/next-year-budgets/fy28/versions/unknown").status_code == 404
            assert client.get("/api/v1/next-year-budgets/fy29/current").json()["source"] is None
            assert _bytes(route.store.root) == before
            assert not list(route.store.root.glob("fy*/event-*.json"))
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_class_readiness_has_no_target_requirement_and_keeps_every_frozen_value(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, baseline = setup_route
    inputs = _inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    inputs["class_sizes"][0]["minimum"] = 24
    inputs["entry_total"] = 15
    inputs["combinations"][0]["opening_carryover_yuan"] = "123.45"
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(inputs))
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    frozen = fingerprint((inputs, first, baseline))
    result = await route.class_readiness(
        request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor
    )
    assert result["source"] == first["draft"]["source"]
    assert result["issue_count"] == 4
    assert result["combination_count"] == result["carry_protected_count"] == 1
    assert result["closure_allowed_count"] == 0
    assert all(row["carryover_yuan"] == "123.45" for row in result["rows"])
    assert not {"source_snapshot", "prepared_inputs", "proposed_inputs"} & result.keys()
    assert _bytes(route.store.root) == before
    assert _bytes(_forecast_store().root) == forecast_before
    assert fingerprint((inputs, first, baseline)) == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["minimum", "frozen_source", "other_department"])
async def test_class_readiness_rejects_invalid_parameters_and_source_without_writes(
    setup_route: tuple[Request, Actor, Json], invalid: str
) -> None:
    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    caller = actor
    if invalid == "minimum":
        inputs["class_sizes"][0]["minimum"] = 0
    elif invalid == "frozen_source":
        inputs["opening_sources"][0]["students"] += 1
    else:
        caller = Actor("other", "other-department", actor.roles)
    before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    with pytest.raises(HTTPException) as error:
        await route.class_readiness(
            request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), caller
        )
    assert error.value.status_code == (403 if invalid == "other_department" else 422)
    assert _bytes(route.store.root) == before
    assert _bytes(_forecast_store().root) == forecast_before


def test_class_readiness_http_reuses_preview_body_and_rejects_unsupported_options(
    setup_route: tuple[Request, Actor, Json],
) -> None:
    _, actor, _ = setup_route
    source = _forecast(actor)
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: actor
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/next-year-budgets/fy28/prepare",
                headers={"Idempotency-Key": "prepare-readiness-http"},
                json={"forecast_id": source["id"], "reason": "readiness contract"},
            )
            draft = response.json()["draft"]
            inputs = deepcopy(draft["inputs"])
            inputs["target_revenue_yuan"] = None
            inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
            body = {"parent_id": draft["id"], "inputs": inputs}
            before = _bytes(route.store.root)
            endpoint = "/api/v1/next-year-budgets/fy28/class-readiness"
            result = client.post(endpoint, json=body)
            assert result.status_code == 200
            assert result.json()["calculation_complete"] is True
            assert client.post(endpoint, json={**body, "options": {"adjust_entry": True}}).status_code == 422
            assert client.post("/api/v1/next-year-budgets/fy29/class-readiness", json=body).status_code == 404
            assert client.post("/api/v1/next-year-budgets/fy27/class-readiness", json=body).status_code == 422
            assert _bytes(route.store.root) == before
            assert not list(route.store.root.glob("fy*/event-*.json"))
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["READY", "PARTIAL"])
async def test_class_repair_only_generates_a_proposal_and_save_still_appends_a_new_draft(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    from packages.next_year import class_repair
    from packages.next_year.planning import compare_calculations

    request, actor, baseline = setup_route
    inputs = _inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(inputs))
    first = await _prepare(request, actor)
    body = route.Preview(parent_id=first["draft"]["id"], inputs=deepcopy(inputs))

    def solve(candidate: Json) -> Json:
        proposed = deepcopy(candidate)
        proposed["plans"][-1]["students"] += 1
        calculation = calculate_plan(proposed)
        return {
            "status": status,
            "message": "proposal only",
            "proposed_inputs": proposed,
            "calculation": calculation,
            "impact": compare_calculations(calculate_plan(candidate), calculation),
            "issue_count": {"before": 1, "after": int(status == "PARTIAL")},
            "constraints": {"operating_target_unchanged": True},
        }

    monkeypatch.setattr(class_repair, "repair_class_plan", solve)
    draft_before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    original = fingerprint((body.model_dump(), baseline, first))
    reader = Actor("reader", actor.department_id, frozenset())
    result = await route.class_repair(request, body, reader)
    assert result["status"] == status
    assert result["source"] == first["draft"]["source"]
    assert result["input_changes"]["count"] == 1
    assert result["proposed_inputs"]["target_revenue_yuan"] is None
    assert result["calculation"]["target_status"] == "NOT_SET"
    assert result["calculation"] == calculate_plan(result["proposed_inputs"])
    assert result["impact"]["annual"]["before"] == calculate_plan(inputs)["annual"]
    assert result["impact"]["annual"]["after"] == result["calculation"]["annual"]
    assert not {"source_snapshot", "prepared_inputs"} & result.keys()
    assert fingerprint((body.model_dump(), baseline, first)) == original
    assert _bytes(route.store.root) == draft_before
    assert _bytes(_forecast_store().root) == forecast_before

    save_body = route.Revise(
        parent_id=first["draft"]["id"],
        inputs=result["proposed_inputs"],
        reason="reviewed whole class proposal",
    )
    with pytest.raises(HTTPException) as denied:
        await route.revise(request, save_body, reader, "repair-reader-save")
    assert denied.value.status_code == 403
    assert _bytes(route.store.root) == draft_before

    saved = await route.revise(request, save_body, actor, "repair-owner-save")
    assert saved["draft"]["sequence"] == 2
    assert saved["draft"]["parent_id"] == first["draft"]["id"]
    assert saved["draft"]["status"] == "DRAFT"
    assert saved["draft"]["inputs"] == result["proposed_inputs"]
    assert saved["draft"]["calculation"] == result["calculation"]
    assert len(_bytes(route.store.root)) == len(draft_before) + 1
    assert all(_bytes(route.store.root)[name] == value for name, value in draft_before.items())
    assert _bytes(_forecast_store().root) == forecast_before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["notes", "carryover", "missing_season"])
async def test_class_repair_validates_generated_inputs_against_the_frozen_original(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    from packages.next_year import class_repair

    request, actor, _ = setup_route
    first = await _prepare(request, actor)
    inputs = deepcopy(first["draft"]["inputs"])
    # User-editable changes in the request must not become a new historical basis.
    inputs["plans"][-1]["students"] += 1

    def corrupt_proposal(candidate: Json) -> Json:
        proposed = deepcopy(candidate)
        if mutation == "notes":
            proposed["notes"] = [{"message": "spoofed historical evidence"}]
        elif mutation == "carryover":
            proposed["combinations"][0]["opening_carryover_yuan"] = "1"
        else:
            proposed["finance"].pop()
        return {"status": "READY", "proposed_inputs": proposed}

    monkeypatch.setattr(class_repair, "repair_class_plan", corrupt_proposal)
    before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    with pytest.raises(HTTPException) as error:
        await route.class_repair(request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor)
    assert error.value.status_code == 422
    assert _bytes(route.store.root) == before
    assert _bytes(_forecast_store().root) == forecast_before


@pytest.mark.asyncio
async def test_real_class_repair_preserves_user_locks_and_targets_minimum_carryover(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    request, actor, _ = setup_route
    inputs = _inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    inputs["class_sizes"][0]["minimum"] = 24
    inputs["combinations"][0].update(
        {"entry_locked": True, "entry_students": 10, "opening_carryover_yuan": "123.45"}
    )
    inputs["plans"][0].update({"students": 1, "locked": True, "reason": "explicit user lock"})
    inputs["rates"][0]["locked"] = True
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(inputs))
    first = await _prepare(request, actor)
    before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    original = fingerprint(inputs)
    result = await route.class_repair(
        request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor
    )
    proposed = result["proposed_inputs"]
    assert result["status"] == "PARTIAL"
    assert result["issue_count"]["before"] >= result["issue_count"]["after"] > 0
    assert proposed["target_revenue_yuan"] is None
    for field in (
        "fiscal_year",
        "entry_total",
        "track_targets",
        "class_sizes",
        "finance",
        "opening_sources",
        "notes",
    ):
        assert proposed[field] == inputs[field]
    assert proposed["combinations"][0]["entry_students"] == 10
    assert proposed["combinations"][0]["entry_locked"] is True
    assert proposed["plans"][0] == inputs["plans"][0]
    assert proposed["rates"][0] == inputs["rates"][0]
    assert all(row["enabled"] is True for row in proposed["combinations"])
    assert [row["opening_carryover_yuan"] for row in proposed["combinations"]] == ["123.45", "0"]
    assert all(result["constraints"].values())
    validate_plan_inputs(proposed, first["draft"]["inputs"])
    assert result["calculation"] == calculate_plan(proposed)
    assert result["impact"]["annual"]["before"] == calculate_plan(inputs)["annual"]
    assert result["impact"]["annual"]["after"] == result["calculation"]["annual"]
    assert fingerprint(inputs) == original
    assert _bytes(route.store.root) == before
    assert _bytes(_forecast_store().root) == forecast_before


@pytest.mark.asyncio
async def test_real_class_repair_contract_reports_stability_scope_and_all_parameter_changes(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    from packages.next_year.planning import _rate_bounds

    request, actor, _ = setup_route
    inputs = _inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    inputs["entry_total"] = 100
    inputs["opening_sources"][0]["students"] = 25
    inputs["class_sizes"][0]["minimum"] = 25
    locked = next(
        row
        for row in inputs["plans"]
        if row["combination_id"] == inputs["combinations"][1]["id"] and row["season"] == "秋季"
    )
    locked.update(students=0, locked=True, reason="business-approved absolute new-student target")
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(inputs))
    first = await _prepare(request, actor)
    draft_before = _bytes(route.store.root)
    forecast_before = _bytes(_forecast_store().root)
    original = deepcopy(inputs)

    result = await route.class_repair(
        request, route.Preview(parent_id=first["draft"]["id"], inputs=inputs), actor
    )

    assert result["status"] == "READY"
    assert result["adjustable_range_class_safe"] is True
    assert result["stability_unresolved"] == []
    assert result["stability_scope"] == {
        "entry_allocation_fixed": True,
        "rate_bounds": "EXPLICIT_OR_DEFAULT_PLUS_MINUS_5PP",
        "user_and_zero_old_seed_rate_locks_preserved": True,
        "integer_rounding_guard": "SEPARATE_OLD_NEW_FLOOR_AND_CEILING",
        "not_a_revenue_target_reachability_proof": True,
    }
    proposed = result["proposed_inputs"]
    rates_before = {row["id"]: row for row in inputs["rates"]}
    rate_fields = (
        "renewal_ppm",
        "renewal_lower_ppm",
        "renewal_upper_ppm",
        "share_lower_ppm",
        "share_upper_ppm",
    )
    changes = 0
    for rate in proposed["rates"]:
        old = rates_before[rate["id"]]
        assert rate["share_ppm"] == old["share_ppm"]
        for field in rate_fields:
            if old.get(field) == rate.get(field):
                continue
            before = old.get(field)
            if before is None:
                kind, side = field.split("_")[:2]
                before = _rate_bounds(old, kind, 50_000)[0 if side == "lower" else 1]
            actions = [
                row for row in result["actions"] if row.get("rate_id") == rate["id"] and row["field"] == field
            ]
            assert len(actions) == 1
            assert (actions[0]["before"], actions[0]["after"]) == (before, rate[field])
            changes += 1
    assert changes > 0
    assert any(row["kind"] == "RENEWAL_ADJUSTMENT" for row in result["actions"])
    assert any(row["field"] == "renewal_lower_ppm" for row in result["actions"])
    assert (
        next(
            row
            for row in proposed["plans"]
            if (row["combination_id"], row["season"]) == (locked["combination_id"], locked["season"])
        )
        == locked
    )
    assert result["input_changes"] == route._input_changes(inputs, proposed)
    assert result["source"] == first["draft"]["source"]
    assert proposed["target_revenue_yuan"] is None and all(result["constraints"].values())
    assert inputs == original
    assert _bytes(route.store.root) == draft_before
    assert _bytes(_forecast_store().root) == forecast_before


def test_class_repair_http_has_no_target_or_options_and_keeps_year_and_input_boundaries(
    setup_route: tuple[Request, Actor, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _inputs()
    inputs["target_revenue_yuan"] = None
    inputs["track_targets"] = {key: None for key in inputs["track_targets"]}
    monkeypatch.setattr(route, "prepare_inputs", lambda *_: deepcopy(inputs))
    _, actor, _ = setup_route
    source = _forecast(actor)
    app = create_app()
    app.dependency_overrides[current_actor] = lambda: actor
    try:
        with TestClient(app) as client:
            prepared = client.post(
                "/api/v1/next-year-budgets/fy28/prepare",
                headers={"Idempotency-Key": "prepare-class-repair-http"},
                json={"forecast_id": source["id"], "reason": "class repair HTTP"},
            )
            draft = prepared.json()["draft"]
            body = {"parent_id": draft["id"], "inputs": draft["inputs"]}
            endpoint = "/api/v1/next-year-budgets/fy28/class-repair"
            before = _bytes(route.store.root)
            forecast_before = _bytes(_forecast_store().root)
            result = client.post(endpoint, json=body)
            assert result.status_code == 200
            assert result.json()["proposed_inputs"]["target_revenue_yuan"] is None
            assert client.post(endpoint, json={**body, "options": {}}).status_code == 422
            assert client.post(endpoint, json={**body, "parent_id": "old-version"}).status_code == 409
            invalid = deepcopy(body)
            invalid["inputs"]["class_sizes"][0]["minimum"] = 0
            assert client.post(endpoint, json=invalid).status_code == 422
            invalid["inputs"]["class_sizes"][0]["minimum"] = 5
            invalid["inputs"]["notes"] = [{"message": "replaced historical note"}]
            assert client.post(endpoint, json=invalid).status_code == 422
            assert client.post("/api/v1/next-year-budgets/fy29/class-repair", json=body).status_code == 404
            assert client.post("/api/v1/next-year-budgets/fy27/class-repair", json=body).status_code == 422
            assert client.post("/api/v1/next-year-budgets/fy28/publish", json={}).status_code == 404
            assert _bytes(route.store.root) == before
            assert _bytes(_forecast_store().root) == forecast_before
            assert not list(route.store.root.glob("fy*/event-*.json"))
    finally:
        app.dependency_overrides.clear()
