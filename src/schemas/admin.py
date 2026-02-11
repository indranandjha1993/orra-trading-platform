from __future__ import annotations

from pydantic import BaseModel


class TenantConnectionStatus(BaseModel):
    tenant_id: str
    clerk_org_id: str
    subscription_tier: str
    connected: bool
    token_ttl_seconds: int | None = None


class SystemHealthResponse(BaseModel):
    status: str
    database_ok: bool
    redis_ok: bool
    total_tenants: int
    connected_tenants: int
