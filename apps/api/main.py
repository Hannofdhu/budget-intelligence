from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from apps.api.errors import domain_error_handler
from apps.api.middleware import RequestContextMiddleware
from apps.api.routes.health import router as health_router
from apps.api.routes.local_trial import router as local_trial_router
from apps.api.routes.versioning import router as versioning_router
from apps.api.routes.workspace import router as workspace_router
from packages.config.settings import get_settings
from packages.domain.versioning.errors import DomainError
from packages.observability.logging import configure_logging
from packages.persistence.database import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="预算智解 API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(versioning_router, prefix="/api/v1")
    app.include_router(workspace_router, prefix="/api/v1")
    app.include_router(local_trial_router, prefix="/api/v1")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("apps.api.main:app", host=settings.api_host, port=settings.api_port, reload=False)
