from fastapi import Request
from fastapi.responses import JSONResponse

from packages.domain.versioning.errors import DomainError


async def domain_error_handler(request: Request, error: DomainError) -> JSONResponse:
    trace_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=error.status_code,
        content={
            "code": error.code,
            "message": error.message,
            **error.details,
            "trace_id": trace_id,
        },
    )
