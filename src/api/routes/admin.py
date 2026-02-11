from __future__ import annotations

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import AuthContext, require_super_admin
from src.core.config import settings
from src.core.db import get_db_session
from src.models.kite_credential import KiteCredential
from src.models.tenant import Tenant
from src.schemas.admin import SystemHealthResponse, TenantConnectionStatus

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/tenants/active", response_model=list[TenantConnectionStatus])
async def list_active_tenants(
    _: AuthContext = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> list[TenantConnectionStatus]:
    stmt = (
        select(Tenant)
        .join(KiteCredential, KiteCredential.tenant_id == Tenant.id)
        .group_by(Tenant.id)
        .order_by(Tenant.created_at)
    )
    tenants = (await session.scalars(stmt)).all()

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        statuses: list[TenantConnectionStatus] = []
        for tenant in tenants:
            token_key = f"kite:access_token:{tenant.id}"
            token = await redis_client.get(token_key)
            ttl = await redis_client.ttl(token_key)
            statuses.append(
                TenantConnectionStatus(
                    tenant_id=str(tenant.id),
                    clerk_org_id=tenant.clerk_org_id,
                    subscription_tier=tenant.subscription_tier,
                    connected=bool(token),
                    token_ttl_seconds=ttl if ttl >= 0 else None,
                )
            )
    finally:
        await redis_client.aclose()

    return statuses


@router.get("/system/health", response_model=SystemHealthResponse)
async def system_health(
    _: AuthContext = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> SystemHealthResponse:
    database_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database_ok = False

    total_tenants = int(await session.scalar(select(func.count(Tenant.id))) or 0)

    redis_ok = True
    connected_tenants = 0
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        pong = await redis_client.ping()
        redis_ok = bool(pong)

        tenant_ids = (await session.scalars(select(Tenant.id))).all()
        for tenant_id in tenant_ids:
            key = f"kite:access_token:{tenant_id}"
            if await redis_client.exists(key):
                connected_tenants += 1
    except Exception:
        redis_ok = False
    finally:
        await redis_client.aclose()

    status_value = "ok" if database_ok and redis_ok else "degraded"
    return SystemHealthResponse(
        status=status_value,
        database_ok=database_ok,
        redis_ok=redis_ok,
        total_tenants=total_tenants,
        connected_tenants=connected_tenants,
    )
