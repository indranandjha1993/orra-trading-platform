from __future__ import annotations

from contextvars import ContextVar
from typing import Final
from uuid import UUID

_CURRENT_TENANT_ID: Final[ContextVar[UUID | None]] = ContextVar(
    "current_tenant_id",
    default=None,
)


def set_current_tenant_id(tenant_id: UUID | None) -> object:
    return _CURRENT_TENANT_ID.set(tenant_id)


def get_current_tenant_id() -> UUID | None:
    return _CURRENT_TENANT_ID.get()


def reset_current_tenant_id(token: object) -> None:
    _CURRENT_TENANT_ID.reset(token)
