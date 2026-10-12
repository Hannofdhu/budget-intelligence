"""Desktop-only FY28 preparation; no annual-budget writes or publication route."""

from copy import deepcopy
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import Field

from apps.api.auth import current_actor
from apps.api.routes import forecast as forecast_routes
from apps.api.routes.forecast import StrictBody
from packages.domain.identity.models import Actor, Role
from packages.forecast.store import ForecastStoreError, fingerprint
from packages.next_year.calculation import (
    NextYearError,
    calculate_plan,
    forecast_basis,
    prepare_inputs,
    validate_plan_inputs,
)
from packages.next_year.store import NextYearStore

router = APIRouter()
store = NextYearStore()
Json = dict[str, Any]
ActorDep = Annotated[Actor, Depends(current_actor)]
RequestId = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
DRAFT_FIELDS = {
    "id",
    "sequence",
    "parent_id",
    "source",
    "inputs",
    "calculation",
    "created_at",
    "created_by",
    "reason",
    "status",
}


class Prepare(StrictBody):
    forecast_id: str = Field(min_length=1, max_length=128)
    parent_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


class Revise(StrictBody):
    parent_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)
    inputs: Json


class Preview(StrictBody):
    parent_id: str = Field(min_length=1, max_length=128)
    inputs: Json


def _safe_view(view: Json) -> Json:
    draft = view.get("draft")
    return deepcopy(
        {
            "source": view.get("source"),
            "draft": {key: value for key, value in draft.items() if key in DRAFT_FIELDS} if draft else None,
            "source_changed": view.get("source_changed", False),
            "history": view.get("history", []),
        }
    )


def _require_write(actor: Actor) -> None:
    if not actor.roles.intersection({Role.DATA_MAINTAINER, Role.DEPARTMENT_BUDGET_OWNER}):
        raise HTTPException(403, "只有数据维护人员或部门预算负责人可以准备下一年预算")


def _validate_forecast(version: Json, baseline: Json) -> None:
    if version.get("baseline_hash") != baseline.get("sha256"):
        raise HTTPException(409, "FY27 定版档案与预测基准不一致，不能用于下一年预算")
    calculation = version.get("calculation", {})
    if (
        version.get("status") != "CONFIRMED"
        or not version.get("confirmation")
        or version.get("assumptions", {}).get("trial_mode")
        or calculation.get("trial_mode")
        or calculation.get("ready") is not True
        or any(issue.get("severity") == "BLOCKING" for issue in calculation.get("issues", []))
    ):
        raise HTTPException(409, "必须使用已确认、非试用且无数据阻断的 FY27 预测")
    if fingerprint(version.get("snapshot")) != version.get("snapshot_hash"):
        raise HTTPException(409, "FY27 预测来源快照指纹不一致")


async def _forecasts(actor: Actor) -> list[Json]:
    # Unlike /current, this selects confirmed versions even when a newer draft
    # exists. Reading this ledger never refreshes sources or appends an event.
    return list(reversed(await forecast_routes._store_call("versions", actor.department_id)))


def _latest_source(versions: list[Json], baseline: Json) -> Json | None:
    latest = next((version for version in reversed(versions) if version.get("status") == "CONFIRMED"), None)
    if latest is None:
        return None
    _validate_forecast(latest, baseline)
    return forecast_basis(latest)


def _digest(command: str, body: StrictBody) -> str:
    try:
        return fingerprint({"command": command, "body": body.model_dump(mode="json")})
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "预算参数必须是有限数值和有效 JSON") from exc


def _replay(actor: Actor, request_id: str, digest: str) -> Json | None:
    result = store.request_result(actor.department_id, request_id, digest)
    return _safe_view(result) if result is not None else None


def _parent(actor: Actor, parent_id: str) -> Json:
    current = store.current(actor.department_id)["draft"]
    if current is None:
        raise HTTPException(404, "请先准备 FY28 预算草稿")
    if current["id"] != parent_id:
        raise HTTPException(409, "预算准备草稿已变化，请重新读取后操作")
    return cast(Json, current)


def _validate_bound_source(draft: Json, baseline: Json) -> None:
    frozen = draft["source_snapshot"]
    forecast = frozen.get("forecast")
    saved_baseline = frozen.get("baseline")
    if not isinstance(forecast, dict) or not isinstance(saved_baseline, dict):
        raise HTTPException(409, "预算准备草稿缺少冻结的 FY27 来源")
    _validate_forecast(forecast, baseline)
    if saved_baseline.get("sha256") != baseline.get("sha256") or forecast_basis(forecast) != draft["source"]:
        raise HTTPException(409, "预算准备草稿与冻结来源不一致")
    try:
        validate_plan_inputs(draft["inputs"], draft["prepared_inputs"])
    except NextYearError as exc:
        raise HTTPException(409, "预算准备草稿输入与初次冻结来源不一致") from exc


@router.get("/next-year-budgets/fy28/current")
async def current(request: Request, actor: ActorDep) -> Json:
    baseline = await forecast_routes._access(request, actor)
    try:
        view = store.current(actor.department_id)
        source = _latest_source(await _forecasts(actor), baseline)
        draft = view["draft"]
        if draft:
            _validate_bound_source(draft, baseline)
        changed = bool(
            draft
            and (
                source is None
                or any(draft["source"].get(key) != source.get(key) for key in ("id", "calculation_hash"))
            )
        )
        return _safe_view({**view, "source": source, "source_changed": changed})
    except (ForecastStoreError, NextYearError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/next-year-budgets/fy28/prepare", status_code=201)
async def prepare(request: Request, body: Prepare, actor: ActorDep, request_id: RequestId) -> Json:
    baseline = await forecast_routes._access(request, actor)
    _require_write(actor)
    digest = _digest("prepare", body)
    try:
        if replay := _replay(actor, request_id, digest):
            return replay
        versions = await _forecasts(actor)
        version = next((item for item in versions if item["id"] == body.forecast_id), None)
        if version is None:
            raise HTTPException(404, "指定的 FY27 已确认预测不存在")
        _validate_forecast(version, baseline)
        source = forecast_basis(version)
        inputs = prepare_inputs(baseline, version)
        result = store.append_draft(
            department=actor.department_id,
            actor=actor.user_id,
            request_id=request_id,
            request_hash=digest,
            parent_id=body.parent_id,
            source=source,
            inputs=inputs,
            calculation=calculate_plan(inputs),
            reason=body.reason,
            operation="PREPARE",
            source_snapshot={"baseline": baseline, "forecast": version},
            observed_source=_latest_source(versions, baseline),
        )
        return _safe_view(result)
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/fy28/revise", status_code=201)
async def revise(request: Request, body: Revise, actor: ActorDep, request_id: RequestId) -> Json:
    baseline = await forecast_routes._access(request, actor)
    _require_write(actor)
    digest = _digest("revise", body)
    try:
        if replay := _replay(actor, request_id, digest):
            return replay
        parent = _parent(actor, body.parent_id)
        _validate_bound_source(parent, baseline)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        result = store.append_draft(
            department=actor.department_id,
            actor=actor.user_id,
            request_id=request_id,
            request_hash=digest,
            parent_id=body.parent_id,
            source=parent["source"],
            inputs=body.inputs,
            calculation=calculate_plan(body.inputs),
            reason=body.reason,
            operation="REVISE",
            observed_source=_latest_source(await _forecasts(actor), baseline),
        )
        return _safe_view(result)
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/fy28/preview")
async def preview(request: Request, body: Preview, actor: ActorDep) -> Json:
    baseline = await forecast_routes._access(request, actor)
    try:
        parent = _parent(actor, body.parent_id)
        _validate_bound_source(parent, baseline)
        _digest("preview", body)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        return {"source": deepcopy(parent["source"]), "calculation": calculate_plan(body.inputs)}
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc
