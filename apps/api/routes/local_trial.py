"""Explicitly enabled, loopback-only synthetic desktop trial bootstrap."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.engine import make_url

from packages.config.settings import get_settings

router = APIRouter()


@router.get("/local-trial/session")
async def trial_session(request: Request) -> dict[str, object]:
    settings = get_settings()
    database = make_url(settings.database_url).database or ""
    if (
        not settings.local_trial_enabled
        or settings.environment != "local"
        or settings.auth_mode != "dev_headers"
        or not database.startswith("budget_acceptance_")
        or not settings.local_trial_department_id.startswith("opening-api-")
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1", "testclient"}
    ):
        raise HTTPException(404, "本机合成试用未启用")
    return {
        "mode": "SYNTHETIC_LOCAL_TRIAL",
        "userId": "local-budget-owner",
        "departmentId": settings.local_trial_department_id,
        "roles": ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"],
    }
