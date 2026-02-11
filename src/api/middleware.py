from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request
from starlette import status
from starlette.responses import JSONResponse, Response

from src.core.context import reset_current_tenant_id, set_current_tenant_id


async def tenant_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    tenant_header = request.headers.get("X-Tenant-Id")
    tenant_id: UUID | None = None
    if tenant_header:
        try:
            tenant_id = UUID(tenant_header)
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid X-Tenant-Id header"},
            )

    token = set_current_tenant_id(tenant_id)
    try:
        return await call_next(request)
    finally:
        reset_current_tenant_id(token)
