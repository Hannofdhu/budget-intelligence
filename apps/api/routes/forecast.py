"""FY27 desktop forecasting, intentionally isolated from annual-budget commands."""

from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from apps.api.auth import current_actor
from apps.api.routes.historical_review import fy27_review
from packages.config.settings import get_settings
from packages.domain.identity.models import Actor, Role
from packages.forecast.calculation import calculate_forecast
from packages.forecast.source import extract_forecast_source
from packages.forecast.store import ForecastStore, ForecastStoreError, fingerprint
from packages.source_snapshot.adapter import SourceAdapterError

router = APIRouter()
store = ForecastStore()
SEASONS = ("暑假", "秋季", "寒假", "春季")
SeasonState = Literal["FINALIZED", "OPEN", "ENDED", "FUTURE"]
DEFAULT_STATES: dict[str, SeasonState] = {
    "暑假": "FINALIZED",
    "秋季": "OPEN",
    "寒假": "FUTURE",
    "春季": "FUTURE",
}
Json = dict[str, Any]
ActorDep = Annotated[Actor, Depends(current_actor)]
RequestId = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def valid_reason(self) -> Self:
        if hasattr(self, "reason") and not self.reason.strip():
            raise ValueError("必须填写操作原因")
        return self


class Dimensions(StrictBody):
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    grade: str = Field(min_length=1, max_length=100)
    subject: str = Field(min_length=1, max_length=100)
    campus: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def nonblank(self) -> Self:
        if any(not value.strip() for value in self.model_dump().values()):
            raise ValueError("映射维度必须填写具体值")
        return self


class Mapping(StrictBody):
    source: Dimensions
    target: Dimensions
    reason: str = Field(min_length=1, max_length=1000)


class Adjustment(StrictBody):
    season: Literal["暑假", "秋季", "寒假", "春季"]
    campus: str = Field(min_length=1, max_length=200)
    kind: Literal["additional", "new_withdrawal", "old_withdrawal"]
    value: StrictInt = Field(ge=0, le=10000000)
    reason: str = Field(min_length=1, max_length=1000)


class Assumptions(StrictBody):
    states: dict[str, SeasonState] = Field(default_factory=lambda: dict(DEFAULT_STATES))
    minimum_sample: StrictInt = Field(default=30, ge=1, le=1000000)
    mappings: list[Mapping] = Field(default_factory=list, max_length=2000)
    adjustments: list[Adjustment] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def full_states(self) -> Self:
        if set(self.states) != set(SEASONS):
            raise ValueError("必须指定四个季度的状态")
        states = [self.states[season] for season in SEASONS]
        # Financially finalized periods must be a prefix, not a hole in the year.
        seen_nonfinal = False
        for state in states:
            if state == "FINALIZED" and seen_nonfinal:
                raise ValueError("已定版季度必须从暑假开始连续指定")
            seen_nonfinal = seen_nonfinal or state != "FINALIZED"
        return self


class Refresh(StrictBody):
    states: dict[str, SeasonState] = Field(default_factory=lambda: dict(DEFAULT_STATES))
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def valid_states(self) -> Self:
        Assumptions(states=self.states)
        return self


class Revise(StrictBody):
    parent_id: str
    reason: str = Field(min_length=1, max_length=1000)
    assumptions: Assumptions


class Confirmation(StrictBody):
    reason: str = Field(min_length=1, max_length=1000)


def _safe_view(view: Json) -> Json:
    # The internal snapshot and field provenance stay in the immutable ledger.
    if view["version"]:
        view = {
            **view,
            "version": {key: value for key, value in view["version"].items() if key not in {"snapshot"}},
        }
    return view


def _current(actor: Actor) -> Json:
    try:
        return store.current(actor.department_id)
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


def _retire_finalized_adjustments(current: Json | None, assumptions: Json) -> Json:
    """End inherited predictions when a quarter switches to frozen actuals.

    The parent's immutable version remains the audit record. Only unchanged
    inherited values may retire automatically; a new or changed adjustment to
    a finalized period must still fail the ordinary permission/state guard.
    """
    if current is None:
        return assumptions
    prior = current["assumptions"]
    newly_finalized = {
        season
        for season in SEASONS
        if prior["states"][season] in {"OPEN", "ENDED"} and assumptions["states"][season] == "FINALIZED"
    }
    return {
        **assumptions,
        "adjustments": [
            adjustment
            for adjustment in assumptions["adjustments"]
            if adjustment["season"] not in newly_finalized or adjustment not in prior["adjustments"]
        ],
    }


def _guard_change(current: Json | None, assumptions: Json, actor: Actor) -> None:
    prior = current["assumptions"] if current else Assumptions().model_dump()
    for season in SEASONS:
        if prior["states"][season] == "FINALIZED" and assumptions["states"][season] != "FINALIZED":
            raise HTTPException(409, "已定版季度不能回退；实际修正请刷新并生成新预测")
    needs_maintainer = any(assumptions[key] != prior[key] for key in ("states", "minimum_sample", "mappings"))
    if needs_maintainer and not actor.has_role(Role.DATA_MAINTAINER):
        raise HTTPException(403, "只有数据维护人员可以修改季度状态、历史样本门槛和映射")
    for adjustment in assumptions["adjustments"]:
        if assumptions["states"][adjustment["season"]] not in {"OPEN", "ENDED"}:
            raise HTTPException(409, "只能调整开放招生或招生结束待定版季度的预测人数")
        if not adjustment["reason"].strip() or not adjustment["campus"].strip():
            raise HTTPException(422, "人数调整必须填写具体校区和原因")
    if any(not mapping["reason"].strip() for mapping in assumptions["mappings"]):
        raise HTTPException(422, "映射必须填写原因")
    adjustments = [(a["season"], a["campus"], a["kind"]) for a in assumptions["adjustments"]]
    if len(set(adjustments)) != len(adjustments):
        raise HTTPException(422, "同一季度、校区和类型只能填写一个调整值")


def _replay(actor: Actor, request_id: str, digest: str) -> Json | None:
    try:
        result = store.request_result(actor.department_id, request_id, digest)
        return _safe_view(result) if result else None
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


async def _access(request: Request, actor: Actor) -> Json:
    # Reuse the historical read guard: explicit local-only + department + role.
    return await fy27_review(request, actor)


@router.get("/forecasts/fy27/current")
async def current(request: Request, actor: ActorDep) -> Json:
    baseline = await _access(request, actor)
    result = _current(actor)
    if result["version"] and result["version"]["baseline_hash"] != baseline["sha256"]:
        raise HTTPException(409, "FY27 定版档案与预测基准不一致")
    return _safe_view({**result, "baseline_hash": baseline["sha256"], "default_states": DEFAULT_STATES})


@router.post("/forecasts/fy27/refresh")
async def refresh(request: Request, body: Refresh, actor: ActorDep, request_id: RequestId) -> Json:
    baseline = await _access(request, actor)
    digest = fingerprint({"command": "refresh", "body": body.model_dump()})
    if replay := _replay(actor, request_id, digest):
        return replay
    view = _current(actor)
    parent = view["version"]
    assumptions = Assumptions.model_validate(
        {**(parent["assumptions"] if parent else {}), "states": body.states}
    ).model_dump()
    assumptions = _retire_finalized_adjustments(parent, assumptions)
    _guard_change(parent, assumptions, actor)
    if not actor.has_role(Role.DATA_MAINTAINER):
        raise HTTPException(403, "只有数据维护人员可以刷新实际快照")
    try:
        snapshot = await extract_forecast_source(get_settings())
        snapshot.setdefault("provenance", {})["verified_by"] = actor.user_id
        snapshot["provenance"]["verification_mode"] = "SIGNED_MAINTAINER_APPROVED_FORMULA_CHECK"
        calculation = calculate_forecast(baseline, snapshot, assumptions)
        result = store.append_version(
            department=actor.department_id,
            actor=actor.user_id,
            request_id=request_id,
            request_hash=digest,
            parent_id=parent["id"] if parent else None,
            baseline_hash=baseline["sha256"],
            snapshot=snapshot,
            assumptions=assumptions,
            calculation=calculation,
            reason=body.reason.strip(),
        )
    except SourceAdapterError as exc:
        raise HTTPException(409, {"code": exc.code, "message": exc.safe_message}) from exc
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _safe_view(result)


@router.post("/forecasts/fy27/revise")
async def revise(request: Request, body: Revise, actor: ActorDep, request_id: RequestId) -> Json:
    baseline = await _access(request, actor)
    digest = fingerprint({"command": "revise", "body": body.model_dump()})
    if replay := _replay(actor, request_id, digest):
        return replay
    parent = _current(actor)["version"]
    if not parent or parent["id"] != body.parent_id:
        raise HTTPException(409, "预测版本已变化，请重新读取后操作")
    assumptions = body.assumptions.model_dump()
    assumptions = _retire_finalized_adjustments(parent, assumptions)
    _guard_change(parent, assumptions, actor)
    try:
        result = store.append_version(
            department=actor.department_id,
            actor=actor.user_id,
            request_id=request_id,
            request_hash=digest,
            parent_id=parent["id"],
            baseline_hash=baseline["sha256"],
            snapshot=parent["snapshot"],
            assumptions=assumptions,
            calculation=calculate_forecast(baseline, parent["snapshot"], assumptions),
            reason=body.reason.strip(),
        )
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _safe_view(result)


@router.post("/forecasts/fy27/{version_id}/confirm")
async def confirm(
    request: Request, version_id: str, body: Confirmation, actor: ActorDep, request_id: RequestId
) -> Json:
    baseline = await _access(request, actor)
    if not actor.has_role(Role.DEPARTMENT_BUDGET_OWNER):
        raise HTTPException(403, "只有部门预算负责人可以确认预测")
    digest = fingerprint({"command": "confirm", "version_id": version_id, "body": body.model_dump()})
    if replay := _replay(actor, request_id, digest):
        return replay
    version = _current(actor)["version"]
    if not version or version["id"] != version_id:
        raise HTTPException(409, "只能确认当前预测版本")
    if version["baseline_hash"] != baseline["sha256"]:
        raise HTTPException(409, "FY27 定版档案与预测基准不一致，不能确认预测")
    try:
        return _safe_view(
            store.confirm(
                department=actor.department_id,
                actor=actor.user_id,
                version_id=version_id,
                request_id=request_id,
                request_hash=digest,
                reason=body.reason.strip(),
            )
        )
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
