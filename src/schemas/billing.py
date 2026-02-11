from __future__ import annotations

from pydantic import BaseModel


class EntitlementResponse(BaseModel):
    tier: str
    max_strategies: int | None
    daily_trade_limit: int | None
    priority_execution: bool


class BillingWebhookResponse(BaseModel):
    received: bool
    event_type: str
    tenant_id: str | None = None
    updated: bool
