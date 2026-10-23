"""FY27 desktop forecasting, intentionally isolated from annual-budget commands."""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from inspect import isawaitable
from typing import Annotated, Any, Literal, Self, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy.exc import SQLAlchemyError

from apps.api.auth import current_actor
from apps.api.routes.historical_review import fy27_review
from packages.config.settings import get_settings
from packages.domain.identity.models import Actor, Role
from packages.forecast.calculation import calculate_forecast
from packages.forecast.daily import schedule_status
from packages.forecast.daily_diagnostics import mark_stage
from packages.forecast.database_store import PostgreSQLForecastStore
from packages.forecast.refresh_lock import refresh_slot as database_refresh_slot
from packages.forecast.rules import FIXED_RULES_VERSION, build_fixed_assumptions
from packages.forecast.source import extract_forecast_source
from packages.forecast.store import ForecastStoreError, fingerprint
from packages.forecast.trend import TrendDays, TrendVersionNotFound
from packages.forecast.trial import build_trial_assumptions
from packages.source_snapshot.adapter import SourceAdapterError

router = APIRouter()
store = PostgreSQLForecastStore()
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
# The desktop API runs one worker. This prevents duplicate live BI extractions
# in that process; persisted idempotency and database CAS remain authoritative.
_refresh_inflight: set[str] = set()


@contextmanager
def _refresh_slot(department: str) -> Iterator[None]:
    # No await between test and add: entry is atomic on this worker's event loop.
    # Reject rather than queue a second extraction after the first one finishes.
    if department in _refresh_inflight:
        raise HTTPException(
            409,
            {
                "code": "FORECAST_REFRESH_IN_PROGRESS",
                "message": "本部门正在更新预测，请等待本次更新完成后再试；不会重复取数",
                "retryable": True,
                "stage": "forecast_refresh",
            },
            headers={"Retry-After": "3"},
        )
    _refresh_inflight.add(department)
    try:
        yield
    finally:
        _refresh_inflight.discard(department)


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


Season = Literal["暑假", "秋季", "寒假", "春季"]


class TrialFinancial(StrictBody):
    price: Decimal = Field(ge=0, le=1000000000, allow_inf_nan=False)
    discount: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    recognition: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    renewal: Decimal | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    share: Decimal | None = Field(default=None, ge=0, lt=1, allow_inf_nan=False)
    method: str = Field(min_length=1, max_length=100)

    @field_validator("price", "discount", "recognition", "renewal", "share", mode="before")
    @classmethod
    def numeric_not_boolean(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("预测参数须为数值，不能使用布尔值")
        return value

    @field_validator("method")
    @classmethod
    def method_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("必须指定参数使用方式")
        return value.strip()


class ForecastScope(StrictBody):
    dims: Dimensions
    kind: Literal["UNBUDGETED_BUSINESS", "FINANCIAL_ONLY"] = "UNBUDGETED_BUSINESS"
    reason: str = Field(min_length=1, max_length=1000)
    financial: dict[Season, TrialFinancial] = Field(default_factory=dict)
    additional: dict[Season, StrictInt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def nonnegative_additional(self) -> Self:
        if any(value < 0 or value > 10000000 for value in self.additional.values()):
            raise ValueError("明细后续新增人次须为非负整数")
        return self


class Assumptions(StrictBody):
    states: dict[str, SeasonState] = Field(default_factory=lambda: dict(DEFAULT_STATES))
    minimum_sample: StrictInt = Field(default=30, ge=1, le=1000000)
    mappings: list[Mapping] = Field(default_factory=list, max_length=2000)
    adjustments: list[Adjustment] = Field(default_factory=list, max_length=2000)
    trial_mode: StrictBool = False
    fixed_rules_version: Literal["fy27-fixed-rules-v1"] | None = None
    forecast_scopes: list[ForecastScope] = Field(default_factory=list, max_length=2000)

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
        keys = [tuple(scope.dims.model_dump().values()) for scope in self.forecast_scopes]
        if len(set(keys)) != len(keys):
            raise ValueError("同一完整业务维度只能有一个独立预测范围")
        if self.fixed_rules_version and self.trial_mode:
            raise ValueError("固定预测规则不能同时声明为试用模式")
        if self.forecast_scopes and not (self.trial_mode or self.fixed_rules_version):
            raise ValueError("独立预测范围当前仅用于试用试算")
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


class PrepareTrial(StrictBody):
    parent_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)


class AdoptFixedRules(PrepareTrial):
    pass


def _safe_view(view: Json) -> Json:
    # The internal snapshot and field provenance stay in the immutable ledger.
    if view["version"]:
        view = {
            **view,
            "version": {key: value for key, value in view["version"].items() if key not in {"snapshot"}},
        }
    return view


async def _store_call(method: str, *args: Any, **kwargs: Any) -> Any:
    """Production is database-only; support synchronous isolated test stores."""
    try:
        result = getattr(store, method)(*args, **kwargs)
        return await result if isawaitable(result) else result
    except (SQLAlchemyError, OSError) as exc:
        # Never serialize SQL parameters: a failed write can contain a snapshot.
        raise HTTPException(
            503,
            {
                "code": "FORECAST_STORAGE_UNAVAILABLE",
                "message": "预测存储暂时无法连接，请稍后重试；已保存版本不会被覆盖",
                "retryable": True,
                "stage": "forecast_storage",
            },
        ) from exc


async def _current(actor: Actor) -> Json:
    try:
        return cast(Json, await _store_call("current", actor.department_id))
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


def _guard_fixed_rules(current: Json | None, assumptions: Json) -> None:
    prior = current["assumptions"] if current else {}
    if prior.get("fixed_rules_version") == FIXED_RULES_VERSION and (
        assumptions.get("fixed_rules_version") != FIXED_RULES_VERSION or assumptions.get("trial_mode")
    ):
        raise HTTPException(409, "固定预测规则不能回退为试用或无规则模式")


def _guard_change(current: Json | None, assumptions: Json, actor: Actor) -> None:
    prior = current["assumptions"] if current else Assumptions().model_dump()
    _guard_fixed_rules(current, assumptions)
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
    dimension_names = ("track", "product", "grade", "subject", "campus")
    prior_scopes = {
        tuple(scope["dims"][name] for name in dimension_names): scope
        for scope in prior.get("forecast_scopes", [])
    }
    for scope in assumptions.get("forecast_scopes", []):
        previous = prior_scopes.get(tuple(scope["dims"][name] for name in dimension_names), {})
        for season, value in scope["additional"].items():
            if assumptions["states"][season] not in {"OPEN", "ENDED"}:
                if previous.get("additional", {}).get(season) != value:
                    raise HTTPException(409, "独立业务后续新增只能调整开放招生季度")
        for season, values in scope["financial"].items():
            if assumptions["states"][season] == "FINALIZED":
                if previous.get("financial", {}).get(season) != values:
                    raise HTTPException(409, "已定版季度使用冻结实际，不能修改试用参数")


async def _replay(actor: Actor, request_id: str, digest: str) -> Json | None:
    try:
        result = await _store_call("request_result", actor.department_id, request_id, digest)
        return _safe_view(result) if result else None
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


async def _access(request: Request, actor: Actor) -> Json:
    # Reuse the historical read guard: explicit local-only + department + role.
    return await fy27_review(request, actor)


@router.get("/forecasts/fy27/current")
async def current(request: Request, actor: ActorDep) -> Json:
    baseline = await _access(request, actor)
    result = await _current(actor)
    if result["version"] and result["version"]["baseline_hash"] != baseline["sha256"]:
        raise HTTPException(409, "FY27 定版档案与预测基准不一致")
    return _safe_view({**result, "baseline_hash": baseline["sha256"], "default_states": DEFAULT_STATES})


async def _saved_version(request: Request, actor: Actor, version_id: str) -> Json:
    baseline = await _access(request, actor)
    try:
        view = await _store_call("version", actor.department_id, version_id)
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    if view is None:
        raise HTTPException(404, "本部门不存在这次预测")
    if view["version"]["baseline_hash"] != baseline["sha256"]:
        raise HTTPException(409, "FY27 定版档案与预测基准不一致")
    return cast(Json, view)


@router.get("/forecasts/fy27/versions/{version_id}")
async def saved_version(request: Request, version_id: str, actor: ActorDep) -> Json:
    """Review an exact frozen version without querying BI or recalculating."""
    return _safe_view(await _saved_version(request, actor, version_id))


@router.get("/forecasts/fy27/versions/{version_id}/snapshot")
async def saved_snapshot(request: Request, version_id: str, actor: ActorDep) -> Json:
    """Authenticated, local-only archive export: inputs, result and confirmation."""
    version = (await _saved_version(request, actor, version_id))["version"]
    if fingerprint(version["snapshot"]) != version["snapshot_hash"]:
        raise HTTPException(409, "预测来源快照完整性校验失败")
    return {
        "version": {
            key: version[key]
            for key in (
                "id",
                "sequence",
                "as_of",
                "created_at",
                "created_by",
                "status",
                "reason",
                "parent_id",
                "baseline_kind",
            )
        },
        "baseline_hash": version["baseline_hash"],
        "snapshot_hash": version["snapshot_hash"],
        "calculation_hash": fingerprint(version["calculation"]),
        "snapshot": version["snapshot"],
        "assumptions": version["assumptions"],
        "calculation": version["calculation"],
        "confirmation": version["confirmation"],
    }


@router.get("/forecasts/fy27/trend")
async def revenue_trend(
    request: Request, actor: ActorDep, days: TrendDays = "30", through_version: str | None = None
) -> Json:
    """Project existing immutable snapshots; never fetch BI or run calculations."""
    baseline = await _access(request, actor)
    try:
        return cast(
            Json,
            await _store_call(
                "trend",
                actor.department_id,
                baseline_hash=baseline["sha256"],
                days=days,
                through_version=through_version,
            ),
        )
    except TrendVersionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/forecasts/fy27/refresh")
async def refresh(request: Request, body: Refresh, actor: ActorDep, request_id: RequestId) -> Json:
    baseline = await _access(request, actor)
    digest = fingerprint({"command": "refresh", "body": body.model_dump()})
    if replay := await _replay(actor, request_id, digest):
        return replay
    if not actor.has_role(Role.DATA_MAINTAINER):
        raise HTTPException(403, "只有数据维护人员可以刷新实际快照")
    with _refresh_slot(actor.department_id):
        # Another command may have committed while the initial replay lookup
        # was awaiting I/O. Recheck after taking the slot, before any live query.
        if replay := await _replay(actor, request_id, digest):
            return replay
        if isinstance(store, PostgreSQLForecastStore):
            try:
                async with database_refresh_slot(actor.department_id):
                    if replay := await _replay(actor, request_id, digest):
                        return replay
                    return await _refresh_from_source(baseline, body, actor, request_id, digest)
            except SourceAdapterError as exc:
                raise HTTPException(
                    409,
                    {"code": exc.code, "message": exc.safe_message, "retryable": exc.retryable},
                    headers={"Retry-After": "3"},
                ) from exc
            except SQLAlchemyError as exc:
                raise HTTPException(
                    503,
                    {
                        "code": "FORECAST_STORAGE_UNAVAILABLE",
                        "message": "预测存储暂时无法连接；已保存版本不会被覆盖",
                        "retryable": True,
                    },
                ) from exc
        return await _refresh_from_source(baseline, body, actor, request_id, digest)


async def _refresh_from_source(
    baseline: Json, body: Refresh, actor: Actor, request_id: str, digest: str, *, automatic: bool = False
) -> Json:
    mark_stage("parent_read")
    view = await _current(actor)
    parent = view["version"]
    # The background job may have waited while a maintainer revised the parent.
    # Inherit states from THIS read, never replay a stale job's state decisions.
    states = parent["assumptions"]["states"] if automatic and parent else body.states
    mark_stage("validate_assumptions")
    assumptions = Assumptions.model_validate(
        {**(parent["assumptions"] if parent else {}), "states": states}
    ).model_dump()
    assumptions = _retire_finalized_adjustments(parent, assumptions)
    _guard_change(parent, assumptions, actor)
    try:
        mark_stage("source_extract")
        snapshot = await extract_forecast_source(get_settings())
        snapshot.setdefault("provenance", {})["verified_by"] = actor.user_id
        snapshot["provenance"]["verification_mode"] = (
            "AUTOMATED_SOURCE_VALIDATION_NO_HUMAN_CONFIRMATION"
            if automatic
            else "SIGNED_MAINTAINER_APPROVED_FORMULA_CHECK"
        )
        snapshot["provenance"]["refresh_mode"] = "AUTOMATIC" if automatic else "MANUAL"
        mark_stage("fixed_assumptions")
        assumptions = Assumptions.model_validate(
            build_fixed_assumptions(baseline, snapshot, assumptions)
        ).model_dump(mode="json")
        _guard_change(parent, assumptions, actor)
        mark_stage("calculation")
        calculation = calculate_forecast(baseline, snapshot, assumptions)
        if automatic and calculation.get("ready") is not True:
            raise SourceAdapterError(
                "FORECAST_DAILY_DATA_INCOMPLETE",
                "本次来源数据检查未通过，自动更新未保存新版本；请人工查看数据问题，上次结果已保留",
            )
        mark_stage("append_version")
        result = await _store_call(
            "append_version",
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
        retained = (await _current(actor))["version"]
        raise HTTPException(
            409,
            {
                "code": exc.code,
                "message": exc.safe_message,
                "retryable": getattr(exc, "retryable", False),
                "diagnostic_id": getattr(exc, "diagnostic_id", None),
                "stage": getattr(exc, "diagnostics", {}).get("stage"),
                "retained_version": {
                    key: retained[key] for key in ("id", "sequence", "as_of", "created_at", "status")
                }
                if retained
                else None,
            },
        ) from exc
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _safe_view(result)


@router.post("/forecasts/fy27/revise")
async def revise(request: Request, body: Revise, actor: ActorDep, request_id: RequestId) -> Json:
    baseline = await _access(request, actor)
    digest = fingerprint({"command": "revise", "body": body.model_dump()})
    if replay := await _replay(actor, request_id, digest):
        return replay
    parent = (await _current(actor))["version"]
    if not parent or parent["id"] != body.parent_id:
        raise HTTPException(409, "预测版本已变化，请重新读取后操作")
    assumptions = body.assumptions.model_dump(mode="json")
    _guard_fixed_rules(parent, assumptions)
    assumptions = _retire_finalized_adjustments(parent, assumptions)
    assumptions = Assumptions.model_validate(
        build_fixed_assumptions(baseline, parent["snapshot"], assumptions)
    ).model_dump(mode="json")
    _guard_change(parent, assumptions, actor)
    try:
        result = await _store_call(
            "append_version",
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


@router.post("/forecasts/fy27/prepare-trial")
async def prepare_trial(request: Request, body: PrepareTrial, actor: ActorDep, request_id: RequestId) -> Json:
    baseline = await _access(request, actor)
    if not actor.has_role(Role.DATA_MAINTAINER):
        raise HTTPException(403, "只有数据维护人员可以准备试用预测")
    digest = fingerprint({"command": "prepare-trial", "body": body.model_dump()})
    if replay := await _replay(actor, request_id, digest):
        return replay
    parent = (await _current(actor))["version"]
    if not parent or parent["id"] != body.parent_id:
        raise HTTPException(409, "预测版本已变化，请重新读取后操作")
    if parent["baseline_hash"] != baseline["sha256"]:
        raise HTTPException(409, "FY27 定版档案与预测基准不一致")
    if parent["assumptions"].get("fixed_rules_version"):
        raise HTTPException(409, "固定预测规则不能回退为试用模式")
    assumptions = Assumptions.model_validate(
        build_trial_assumptions(baseline, parent["snapshot"], parent["assumptions"])
    ).model_dump(mode="json")
    _guard_change(parent, assumptions, actor)
    try:
        result = await _store_call(
            "append_version",
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


@router.post("/forecasts/fy27/adopt-fixed-rules")
async def adopt_fixed_rules(
    request: Request, body: AdoptFixedRules, actor: ActorDep, request_id: RequestId
) -> Json:
    """Version the adopted policy on the same frozen snapshot, not the BI source."""
    baseline = await _access(request, actor)
    if not actor.has_role(Role.DATA_MAINTAINER):
        raise HTTPException(403, "只有数据维护人员可以启用固定预测规则")
    digest = fingerprint({"command": "adopt-fixed-rules", "body": body.model_dump()})
    if replay := await _replay(actor, request_id, digest):
        return replay
    parent = (await _current(actor))["version"]
    if not parent or parent["id"] != body.parent_id:
        raise HTTPException(409, "预测版本已变化，请重新读取后操作")
    if parent["baseline_hash"] != baseline["sha256"]:
        raise HTTPException(409, "FY27 定版档案与预测基准不一致")
    assumptions = Assumptions.model_validate(
        build_fixed_assumptions(baseline, parent["snapshot"], parent["assumptions"])
    ).model_dump(mode="json")
    _guard_change(parent, assumptions, actor)
    try:
        result = await _store_call(
            "append_version",
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
    if replay := await _replay(actor, request_id, digest):
        return replay
    version = (await _current(actor))["version"]
    if not version or version["id"] != version_id:
        raise HTTPException(409, "只能确认当前预测版本")
    if version["baseline_hash"] != baseline["sha256"]:
        raise HTTPException(409, "FY27 定版档案与预测基准不一致，不能确认预测")
    try:
        return _safe_view(
            await _store_call(
                "confirm",
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


@router.get("/forecasts/fy27/schedule")
async def daily_schedule(request: Request, actor: ActorDep) -> Json:
    await _access(request, actor)
    try:
        return await schedule_status(get_settings(), actor.department_id)
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(
            503,
            {
                "code": "FORECAST_SCHEDULE_UNAVAILABLE",
                "message": "自动更新状态暂时无法读取；已保存预测不受影响",
            },
        ) from exc
