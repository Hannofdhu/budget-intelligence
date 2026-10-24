"""An authenticated read-only join of existing saved module outputs."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from apps.api.auth import current_actor
from apps.api.routes import forecast as forecast_routes
from apps.api.routes import next_year as next_year_routes
from packages.cockpit.projection import CockpitProjectionError, project_cockpit
from packages.domain.identity.models import Actor
from packages.forecast.store import ForecastStoreError
from packages.next_year.store import NextYearStoreError

router = APIRouter()
VersionId = Annotated[str | None, Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


@router.get("/budget-cockpit")
async def cockpit(
    request: Request,
    actor: Annotated[Actor, Depends(current_actor)],
    forecast_version: VersionId = None,
    next_year_version: VersionId = None,
) -> dict[str, Any]:
    baseline = await forecast_routes._access(request, actor)
    try:

        async def read_forecast() -> dict[str, Any] | None:
            if forecast_version == "none":
                return None
            return (
                await forecast_routes._store_call("version", actor.department_id, forecast_version)
                if forecast_version
                else await forecast_routes._current(actor)
            )

        async def read_budget() -> dict[str, Any] | None:
            if next_year_version == "none":
                return None
            store = next_year_routes._store_for_year(str(baseline["fiscal_year"] + 1))
            current = await run_in_threadpool(store.current, actor.department_id)
            if not next_year_version or (current.get("draft") or {}).get("id") == next_year_version:
                return current
            return await run_in_threadpool(store.version, actor.department_id, next_year_version)

        forecast_view, budget_view = await asyncio.gather(read_forecast(), read_budget())
        if forecast_version and forecast_version != "none" and forecast_view is None:
            raise HTTPException(404, "指定的预测快照不存在，未替换为最新版本")
        forecast = forecast_view.get("version") if forecast_view else None
        if next_year_version and next_year_version != "none" and budget_view is None:
            raise HTTPException(404, "指定的预算草稿不存在，未替换为最新版本")
        draft = budget_view.get("draft") if budget_view else None
        if draft is not None:
            next_year_routes._validate_bound_source(draft, baseline)
        return project_cockpit(baseline, forecast, draft)
    except (ForecastStoreError, NextYearStoreError, CockpitProjectionError) as exc:
        raise HTTPException(409, str(exc)) from exc
