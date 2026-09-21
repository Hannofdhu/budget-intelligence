from fastapi import APIRouter
from fastapi.responses import JSONResponse

from packages.contracts.health import HealthResponse
from packages.persistence.database import database_ping

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse | JSONResponse:
    try:
        await database_ping()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return HealthResponse(status="ok")
