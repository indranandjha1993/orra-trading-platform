from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from starlette.responses import Response

from src.core.context import reset_current_tenant_id, set_current_tenant_id


async def tenant_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    token = set_current_tenant_id(None)
    try:
        return await call_next(request)
    finally:
        reset_current_tenant_id(token)
