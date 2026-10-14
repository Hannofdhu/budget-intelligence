"""Desktop-only planning drafts; no annual-budget writes or publication route."""

import re
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
    "operation",
    "restored_from_id",
}


class Prepare(StrictBody):
    forecast_id: str = Field(min_length=1, max_length=128)
    parent_id: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


class PreparePreview(StrictBody):
    forecast_id: str = Field(min_length=1, max_length=128)
    parent_id: str | None = Field(default=None, min_length=1, max_length=128)


class Revise(StrictBody):
    parent_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)
    inputs: Json


class Preview(StrictBody):
    parent_id: str = Field(min_length=1, max_length=128)
    inputs: Json


class Restore(StrictBody):
    parent_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)


class Balance(Preview):
    options: Json = Field(default_factory=dict)


def _year(value: str) -> int:
    match = re.fullmatch(r"(?:fy)?(\d{2}|\d{4})", value.lower())
    if match is None:
        raise HTTPException(422, "财年格式无效，请使用 FY28 或 2028")
    year = int(match[1])
    year = year + 2000 if year < 100 else year
    if not 2028 <= year <= 2100:
        raise HTTPException(422, "预算财年须为 2028 至 2100")
    return year


def _store_for_year(fiscal_year: str) -> NextYearStore:
    year = _year(fiscal_year)
    # FY28's existing immutable ledger stays exactly where it is. Other years
    # receive isolated chains; never move, rewrite or mingle saved versions.
    return store if year == 2028 else NextYearStore(store.root / f"fy{year}")


def _check_year(draft: Json, fiscal_year: str) -> None:
    if draft["inputs"].get("fiscal_year") != _year(fiscal_year):
        raise HTTPException(409, "预算版本与选定财年不一致")


def _safe_draft(draft: Json | None) -> Json | None:
    if draft is None:
        return None
    return {key: draft.get(key) for key in DRAFT_FIELDS}


def _safe_view(view: Json) -> Json:
    draft = view.get("draft")
    return deepcopy(
        {
            "source": view.get("source"),
            "draft": _safe_draft(draft),
            "source_changed": view.get("source_changed", False),
            "history": view.get("history", []),
            **{key: view[key] for key in ("latest_draft_id", "read_only") if key in view},
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


async def _prepare_source(
    actor: Actor, forecast_id: str, baseline: Json, fiscal_year: str
) -> tuple[list[Json], Json, Json, Json]:
    """Use the same frozen-source preparation for preview and explicit creation."""
    versions = await _forecasts(actor)
    version = next((item for item in versions if item["id"] == forecast_id), None)
    if version is None:
        raise HTTPException(404, "指定的 FY27 已确认预测不存在")
    _validate_forecast(version, baseline)
    if _year(fiscal_year) != baseline["fiscal_year"] + 1:
        raise HTTPException(409, "该财年尚无可用的上一财年已确认预测，不会使用其他财年数据代替")
    source = forecast_basis(version)
    inputs = prepare_inputs(baseline, version)
    if inputs["fiscal_year"] != _year(fiscal_year):
        raise HTTPException(409, "预测来源生成的预算财年与所选财年不一致")
    return versions, version, source, inputs


def _latest_source(versions: list[Json], baseline: Json, fiscal_year: str = "fy28") -> Json | None:
    if _year(fiscal_year) != baseline.get("fiscal_year", 2027) + 1:
        return None
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


def _replay(actor: Actor, request_id: str, digest: str, fiscal_year: str = "fy28") -> Json | None:
    result = _store_for_year(fiscal_year).request_result(actor.department_id, request_id, digest)
    return _safe_view(result) if result is not None else None


def _parent(actor: Actor, parent_id: str, fiscal_year: str = "fy28") -> Json:
    current = _store_for_year(fiscal_year).current(actor.department_id)["draft"]
    if current is None:
        raise HTTPException(404, "请先准备该财年的预算草稿")
    if current["id"] != parent_id:
        raise HTTPException(409, "预算准备草稿已变化，请重新读取后操作")
    _check_year(current, fiscal_year)
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
    if draft["inputs"].get("fiscal_year") != saved_baseline.get("fiscal_year", 2027) + 1:
        raise HTTPException(409, "预算草稿财年与冻结预测来源财年不一致")
    try:
        validate_plan_inputs(draft["inputs"], draft["prepared_inputs"])
    except NextYearError as exc:
        raise HTTPException(409, "预算准备草稿输入与初次冻结来源不一致") from exc


@router.get("/next-year-budgets/catalog")
async def catalog(request: Request, actor: ActorDep) -> Json:
    baseline = await forecast_routes._access(request, actor)
    try:
        versions = await _forecasts(actor)
        sources = []
        for version in reversed(versions):
            if version.get("status") == "CONFIRMED":
                _validate_forecast(version, baseline)
                sources.append({**forecast_basis(version), "fiscal_year": baseline["fiscal_year"]})
        years = {2028, baseline["fiscal_year"] + 1}
        if store.root.exists():
            years.update(
                int(path.name[2:])
                for path in store.root.iterdir()
                if path.is_dir() and re.fullmatch(r"fy20\d{2}|fy2100", path.name)
            )
        contexts = []
        for year in sorted(years):
            draft = _store_for_year(str(year)).current(actor.department_id)["draft"]
            if draft is None and year not in {2028, baseline["fiscal_year"] + 1}:
                continue
            contexts.append(
                {
                    "fiscal_year": year,
                    "has_draft": draft is not None,
                    "latest_sequence": draft["sequence"] if draft else None,
                    "latest_draft_id": draft["id"] if draft else None,
                    "source_fiscal_year": (
                        draft["source_snapshot"]["baseline"].get("fiscal_year", 2027)
                        if draft
                        else baseline["fiscal_year"]
                        if sources
                        else None
                    ),
                    "can_prepare": bool(sources) and year == baseline["fiscal_year"] + 1,
                }
            )
        return {
            "source_fiscal_year": baseline["fiscal_year"],
            "default_fiscal_year": baseline["fiscal_year"] + 1,
            "sources": sources,
            "fiscal_years": contexts,
        }
    except (ForecastStoreError, NextYearError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/next-year-budgets/{fiscal_year}/current")
async def current(request: Request, actor: ActorDep, fiscal_year: str = "fy28") -> Json:
    baseline = await forecast_routes._access(request, actor)
    try:
        view = _store_for_year(fiscal_year).current(actor.department_id)
        source = _latest_source(await _forecasts(actor), baseline, fiscal_year)
        draft = view["draft"]
        if draft:
            _check_year(draft, fiscal_year)
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


@router.post("/next-year-budgets/{fiscal_year}/prepare", status_code=201)
async def prepare(
    request: Request, body: Prepare, actor: ActorDep, request_id: RequestId, fiscal_year: str = "fy28"
) -> Json:
    baseline = await forecast_routes._access(request, actor)
    _require_write(actor)
    digest = _digest("prepare", body)
    try:
        selected_store = _store_for_year(fiscal_year)
        if replay := _replay(actor, request_id, digest, fiscal_year):
            return replay
        versions, version, source, inputs = await _prepare_source(
            actor, body.forecast_id, baseline, fiscal_year
        )
        result = selected_store.append_draft(
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
            observed_source=_latest_source(versions, baseline, fiscal_year),
        )
        return _safe_view(result)
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/prepare-preview")
async def prepare_preview(
    request: Request, body: PreparePreview, actor: ActorDep, fiscal_year: str = "fy28"
) -> Json:
    """Calculate a replacement basis without creating or changing a draft."""
    baseline = await forecast_routes._access(request, actor)
    _require_write(actor)
    try:
        selected_store = _store_for_year(fiscal_year)
        parent = selected_store.current(actor.department_id)["draft"]
        if (parent["id"] if parent else None) != body.parent_id:
            raise HTTPException(409, "预算准备草稿已变化，请重新读取后操作")
        if parent:
            _check_year(parent, fiscal_year)
            _validate_bound_source(parent, baseline)
        _, _, source, inputs = await _prepare_source(actor, body.forecast_id, baseline, fiscal_year)
        return {
            "read_only": True,
            "fiscal_year": _year(fiscal_year),
            "parent_id": body.parent_id,
            "source_id": source["id"],
            "source": source,
            "inputs": inputs,
            "calculation": calculate_plan(inputs),
        }
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/revise", status_code=201)
async def revise(
    request: Request, body: Revise, actor: ActorDep, request_id: RequestId, fiscal_year: str = "fy28"
) -> Json:
    baseline = await forecast_routes._access(request, actor)
    _require_write(actor)
    digest = _digest("revise", body)
    try:
        if replay := _replay(actor, request_id, digest, fiscal_year):
            return replay
        parent = _parent(actor, body.parent_id, fiscal_year)
        _validate_bound_source(parent, baseline)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        result = _store_for_year(fiscal_year).append_draft(
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
            observed_source=_latest_source(await _forecasts(actor), baseline, fiscal_year),
        )
        return _safe_view(result)
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/preview")
async def preview(request: Request, body: Preview, actor: ActorDep, fiscal_year: str = "fy28") -> Json:
    baseline = await forecast_routes._access(request, actor)
    try:
        parent = _parent(actor, body.parent_id, fiscal_year)
        _validate_bound_source(parent, baseline)
        _digest("preview", body)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        return {"source": deepcopy(parent["source"]), "calculation": calculate_plan(body.inputs)}
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


def _selected_version(actor: Actor, version_id: str, fiscal_year: str) -> Json:
    view = _store_for_year(fiscal_year).version(actor.department_id, version_id)
    if view is None:
        raise HTTPException(404, "指定的预算版本不存在")
    _check_year(view["draft"], fiscal_year)
    return view


def _input_changes(before: Json, after: Json) -> Json:
    """Explain edits using stable row IDs, without exposing frozen source data."""
    scalars = []
    for key in ("target_revenue_yuan", "track_targets", "entry_total", "tax_factor"):
        if before.get(key) != after.get(key):
            scalars.append(
                {"field": key, "before": deepcopy(before.get(key)), "after": deepcopy(after.get(key))}
            )
    tables = []
    total = len(scalars)
    for name in ("combinations", "rates", "finance", "plans", "class_sizes"):

        def identity(row: Json, table: str = name) -> str:
            if table == "plans":
                return str((row.get("combination_id"), row.get("season")))
            if table == "class_sizes":
                return str((row.get("track"), row.get("product")))
            return str(row.get("id"))

        original = {identity(row): row for row in before.get(name, [])}
        proposed = {identity(row): row for row in after.get(name, [])}
        changes = []
        for row_id in sorted(original.keys() | proposed.keys()):
            old, new = original.get(row_id), proposed.get(row_id)
            if old == new:
                continue
            fields = [
                {"field": key, "before": (old or {}).get(key), "after": (new or {}).get(key)}
                for key in sorted((old or {}).keys() | (new or {}).keys())
                if (old or {}).get(key) != (new or {}).get(key)
            ]
            changes.append(
                {
                    "id": row_id,
                    "kind": "ADDED" if old is None else "REMOVED" if new is None else "CHANGED",
                    "fields": fields,
                    "before": deepcopy(old),
                    "after": deepcopy(new),
                }
            )
        if changes:
            tables.append({"table": name, "count": len(changes), "rows": changes})
            total += len(changes)
    return {"count": total, "fields": scalars, "tables": tables}


@router.get("/next-year-budgets/{fiscal_year}/versions")
async def versions(request: Request, actor: ActorDep, fiscal_year: str = "fy28") -> Json:
    view = await current(request, actor, fiscal_year)
    return {"history": view["history"], "latest_draft_id": view["draft"]["id"] if view["draft"] else None}


@router.get("/next-year-budgets/{fiscal_year}/versions/{version_id}")
async def version(request: Request, version_id: str, actor: ActorDep, fiscal_year: str = "fy28") -> Json:
    baseline = await forecast_routes._access(request, actor)
    try:
        view = _selected_version(actor, version_id, fiscal_year)
        _validate_bound_source(view["draft"], baseline)
        source = _latest_source(await _forecasts(actor), baseline, fiscal_year)
        bound = view["draft"]["source"]
        changed = source is None or any(
            bound.get(key) != source.get(key) for key in ("id", "calculation_hash")
        )
        return _safe_view({**view, "source": source, "source_changed": changed})
    except (ForecastStoreError, NextYearError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/next-year-budgets/{fiscal_year}/compare")
async def compare(
    request: Request,
    before_id: str,
    after_id: str,
    actor: ActorDep,
    fiscal_year: str = "fy28",
) -> Json:
    from packages.next_year.planning import compare_calculations

    baseline = await forecast_routes._access(request, actor)
    try:
        before = _selected_version(actor, before_id, fiscal_year)["draft"]
        after = _selected_version(actor, after_id, fiscal_year)["draft"]
        for draft in (before, after):
            _validate_bound_source(draft, baseline)
        return {
            "before": _safe_draft(before),
            "after": _safe_draft(after),
            "impact": compare_calculations(before["calculation"], after["calculation"]),
            "input_changes": _input_changes(before["inputs"], after["inputs"]),
        }
    except (ForecastStoreError, NextYearError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/restore", status_code=201)
async def restore(
    request: Request, body: Restore, actor: ActorDep, request_id: RequestId, fiscal_year: str = "fy28"
) -> Json:
    baseline = await forecast_routes._access(request, actor)
    _require_write(actor)
    digest = _digest("restore", body)
    try:
        if replay := _replay(actor, request_id, digest, fiscal_year):
            return replay
        _parent(actor, body.parent_id, fiscal_year)
        selected = _selected_version(actor, body.version_id, fiscal_year)["draft"]
        _validate_bound_source(selected, baseline)
        result = _store_for_year(fiscal_year).append_draft(
            department=actor.department_id,
            actor=actor.user_id,
            request_id=request_id,
            request_hash=digest,
            parent_id=body.parent_id,
            source=selected["source"],
            inputs=selected["inputs"],
            calculation=selected["calculation"],
            reason=body.reason,
            operation="RESTORE",
            restored_from_id=body.version_id,
            observed_source=_latest_source(await _forecasts(actor), baseline, fiscal_year),
        )
        return _safe_view(result)
    except (ForecastStoreError, NextYearError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/impact")
async def impact(request: Request, body: Preview, actor: ActorDep, fiscal_year: str = "fy28") -> Json:
    from packages.next_year.planning import compare_calculations

    baseline = await forecast_routes._access(request, actor)
    try:
        parent = _parent(actor, body.parent_id, fiscal_year)
        _validate_bound_source(parent, baseline)
        _digest("impact", body)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        calculation = calculate_plan(body.inputs)
        return {
            "source": deepcopy(parent["source"]),
            "calculation": calculation,
            "impact": compare_calculations(parent["calculation"], calculation),
            "input_changes": _input_changes(parent["inputs"], body.inputs),
        }
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/balance")
async def balance(request: Request, body: Balance, actor: ActorDep, fiscal_year: str = "fy28") -> Json:
    from packages.next_year.planning import balance_plan

    baseline = await forecast_routes._access(request, actor)
    try:
        parent = _parent(actor, body.parent_id, fiscal_year)
        _validate_bound_source(parent, baseline)
        _digest("balance", body)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        result = balance_plan(body.inputs, body.options)
        proposed = result.get("proposed_inputs")
        if proposed is not None:
            validate_plan_inputs(proposed, parent["prepared_inputs"])
        return {
            **result,
            "source": deepcopy(parent["source"]),
            "input_changes": _input_changes(body.inputs, proposed if proposed is not None else body.inputs),
        }
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/class-readiness")
async def class_readiness(
    request: Request, body: Preview, actor: ActorDep, fiscal_year: str = "fy28"
) -> Json:
    from packages.next_year.readiness import class_readiness as diagnose_classes

    baseline = await forecast_routes._access(request, actor)
    try:
        parent = _parent(actor, body.parent_id, fiscal_year)
        _validate_bound_source(parent, baseline)
        _digest("class-readiness", body)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        return {"source": deepcopy(parent["source"]), **diagnose_classes(body.inputs)}
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/next-year-budgets/{fiscal_year}/class-repair")
async def class_repair(request: Request, body: Preview, actor: ActorDep, fiscal_year: str = "fy28") -> Json:
    from packages.next_year.class_repair import repair_class_plan

    baseline = await forecast_routes._access(request, actor)
    try:
        parent = _parent(actor, body.parent_id, fiscal_year)
        _validate_bound_source(parent, baseline)
        _digest("class-repair", body)
        validate_plan_inputs(body.inputs, parent["prepared_inputs"])
        result = repair_class_plan(body.inputs)
        proposed = result.get("proposed_inputs")
        if proposed is not None:
            # A generated proposal receives the same frozen-source validation
            # as a user-edited draft. Generation is not an apply/save action.
            validate_plan_inputs(proposed, parent["prepared_inputs"])
        return {
            **result,
            "source": deepcopy(parent["source"]),
            "input_changes": _input_changes(body.inputs, proposed if proposed is not None else body.inputs),
        }
    except ForecastStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    except NextYearError as exc:
        raise HTTPException(422, str(exc)) from exc
