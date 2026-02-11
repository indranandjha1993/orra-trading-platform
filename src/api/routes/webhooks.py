from __future__ import annotations

import json

import redis.asyncio as redis
import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.db import get_db_session
from src.models.strategy_instance import StrategyInstance
from src.models.tenant import Tenant
from src.schemas.billing import BillingWebhookResponse

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _extract_org_id(payload: dict) -> str | None:
    data = (payload.get("data") or {}).get("object") or {}
    metadata = data.get("metadata") or {}
    return (
        metadata.get("clerk_org_id")
        or metadata.get("org_id")
        or data.get("clerk_org_id")
        or data.get("org_id")
    )


def _extract_tier(payload: dict) -> str:
    data = (payload.get("data") or {}).get("object") or {}
    metadata = data.get("metadata") or {}
    return (metadata.get("subscription_tier") or metadata.get("tier") or "pro").lower()


async def _set_tenant_redis_state(tenant_id: str, active: bool) -> None:
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await redis_client.set(f"tenant:active:{tenant_id}", "1" if active else "0")
        await redis_client.publish(
            f"billing:tenant_status:{tenant_id}", "active" if active else "inactive"
        )
        if not active:
            await redis_client.delete(f"kite:access_token:{tenant_id}")
            await redis_client.set(f"kite:connection_status:{tenant_id}", "inactive", ex=24 * 60 * 60)
    finally:
        await redis_client.aclose()


def _verify_and_parse_event(raw_body: bytes, stripe_signature: str | None) -> dict:
    if settings.stripe_webhook_secret and not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Stripe signature",
        )

    if stripe_signature and settings.stripe_webhook_secret:
        try:
            event = stripe.Webhook.construct_event(
                payload=raw_body,
                sig_header=stripe_signature,
                secret=settings.stripe_webhook_secret,
            )
            return event.to_dict_recursive()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid Stripe signature: {exc}",
            ) from exc

    try:
        return json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook payload",
        ) from exc


@router.post("/billing", response_model=BillingWebhookResponse)
async def billing_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    clerk_secret: str | None = Header(default=None, alias="X-Clerk-Webhook-Secret"),
) -> BillingWebhookResponse:
    if settings.clerk_webhook_secret and clerk_secret != settings.clerk_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Clerk webhook secret",
        )

    raw_body = await request.body()
    payload = _verify_and_parse_event(raw_body, stripe_signature)
    event_type = payload.get("type", "unknown")

    if event_type not in {"invoice.paid", "subscription.deleted"}:
        return BillingWebhookResponse(received=True, event_type=event_type, tenant_id=None, updated=False)

    org_id = _extract_org_id(payload)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload missing org identifier",
        )

    tenant = await session.scalar(select(Tenant).where(Tenant.clerk_org_id == org_id))
    if tenant is None:
        return BillingWebhookResponse(received=True, event_type=event_type, tenant_id=None, updated=False)

    if event_type == "invoice.paid":
        tenant.subscription_tier = _extract_tier(payload)
        tenant.is_active = True
        await session.commit()
        await _set_tenant_redis_state(str(tenant.id), active=True)
        return BillingWebhookResponse(
            received=True,
            event_type=event_type,
            tenant_id=str(tenant.id),
            updated=True,
        )

    tenant.is_active = False
    await session.execute(
        update(StrategyInstance)
        .where(StrategyInstance.tenant_id == tenant.id)
        .values(is_active=False)
    )
    await session.commit()
    await _set_tenant_redis_state(str(tenant.id), active=False)

    return BillingWebhookResponse(
        received=True,
        event_type=event_type,
        tenant_id=str(tenant.id),
        updated=True,
    )
