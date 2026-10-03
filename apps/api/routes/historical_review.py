"""Authenticated, local-trial-only historical read model. No mutation endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from apps.api.auth import current_actor
from apps.api.routes.local_trial import trial_session
from packages.domain.identity.models import Actor, Role
from packages.source_snapshot.fy27_review import read_review

router = APIRouter()


@router.get("/historical-budgets/fy27")
async def fy27_review(request: Request, actor: Annotated[Actor, Depends(current_actor)]) -> dict:
    session = await trial_session(request)
    address = request.headers.get("X-Budget-Client-Address", "")
    if address not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
        raise HTTPException(403, "FY27 真实预算仅在本机查看，尚未向局域网开放")
    if actor.department_id != session["departmentId"] or not actor.roles.intersection(
        {Role.DATA_MAINTAINER, Role.DEPARTMENT_BUDGET_OWNER}
    ):
        raise HTTPException(403, "无权查看本部门历史预算")
    try:
        return read_review()
    except FileNotFoundError as exc:
        raise HTTPException(404, "FY27 原表或只读提取缺失，请在本机准备历史预算文件") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
