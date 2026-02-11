from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import redis.asyncio as redis
import requests
from fastapi import Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import AuthContext, require_auth_context
from src.core.config import settings
from src.core.db import get_db_session
from src.models.strategy_instance import StrategyInstance
from src.models.tenant import Tenant

SubscriptionTier = Literal["basic", "pro"]


@dataclass(slots=True)
class TierEntitlements:
    tier: SubscriptionTier
    max_strategies: int | None
    daily_trade_limit: int | None
    priority_execution: bool


def tier_to_entitlements(tier: str) -> TierEntitlements:
    normalized = (tier or "basic").strip().lower()
    if normalized == "pro":
        return TierEntitlements(
            tier="pro",
            max_strategies=None,
            daily_trade_limit=None,
            priority_execution=True,
        )

    return TierEntitlements(
        tier="basic",
        max_strategies=settings.basic_strategy_limit,
        daily_trade_limit=settings.basic_daily_trade_limit,
        priority_execution=False,
    )


class ClerkBillingClient:
    def __init__(self) -> None:
        self.base_url = settings.clerk_api_base_url.rstrip("/")

    def fetch_org_subscription_tier(self, clerk_org_id: str) -> str:
        if not settings.clerk_secret_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="CLERK_SECRET_KEY is not configured",
            )

        response = requests.get(
            f"{self.base_url}/organizations/{clerk_org_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            timeout=8,
        )
        response.raise_for_status()
        body = response.json()

        public_meta = body.get("public_metadata") or {}
        private_meta = body.get("private_metadata") or {}

        return (
            public_meta.get("subscription_tier")
            or private_meta.get("subscription_tier")
            or "basic"
        )


async def resolve_tenant_subscription_tier(
    context: AuthContext,
    session: AsyncSession,
) -> str:
    client = ClerkBillingClient()
    try:
        clerk_tier = await asyncio.to_thread(client.fetch_org_subscription_tier, context.org_id)
    except Exception:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == context.tenant_id))
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant not found",
            )
        if not tenant.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant subscription is inactive",
            )
        return tenant.subscription_tier

    tenant = await session.scalar(select(Tenant).where(Tenant.id == context.tenant_id))
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant not found",
        )
    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant subscription is inactive",
        )

    if tenant.subscription_tier != clerk_tier:
        tenant.subscription_tier = clerk_tier
        await session.commit()

    return clerk_tier


async def get_current_entitlements(
    context: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> TierEntitlements:
    tier = await resolve_tenant_subscription_tier(context, session)
    return tier_to_entitlements(tier)


async def enforce_strategy_limit(
    context: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> TierEntitlements:
    entitlements = await get_current_entitlements(context, session)
    if entitlements.max_strategies is None:
        return entitlements

    active_count = int(
        await session.scalar(
            select(func.count(StrategyInstance.id)).where(
                StrategyInstance.tenant_id == context.tenant_id,
                StrategyInstance.is_active.is_(True),
            )
        )
        or 0
    )
    if active_count >= entitlements.max_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Basic tier allows up to {entitlements.max_strategies} active strategy. "
                "Upgrade to Pro for unlimited strategies."
            ),
        )

    return entitlements


async def enforce_daily_trade_limit(
    context: AuthContext = Depends(require_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> TierEntitlements:
    entitlements = await get_current_entitlements(context, session)
    if entitlements.daily_trade_limit is None:
        return entitlements

    day_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    redis_key = f"trades:count:{context.tenant_id}:{day_key}"
    try:
        count = await redis_client.incr(redis_key)
        if count == 1:
            await redis_client.expire(redis_key, 24 * 60 * 60)
    finally:
        await redis_client.aclose()

    if count > entitlements.daily_trade_limit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Basic tier allows {entitlements.daily_trade_limit} trades/day. "
                "Upgrade to Pro for unlimited trades and priority execution."
            ),
        )

    return entitlements


async def require_pro_tier(
    entitlements: TierEntitlements = Depends(get_current_entitlements),
) -> TierEntitlements:
    if entitlements.tier != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pro tier required for this operation",
        )
    return entitlements
