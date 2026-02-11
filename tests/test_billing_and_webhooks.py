from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes.webhooks import _extract_org_id, _extract_tier, _verify_and_parse_event
from src.core.auth import AuthContext
from src.core.billing import (
    ClerkBillingClient,
    require_pro_tier,
    resolve_tenant_subscription_tier,
    tier_to_entitlements,
)


def test_tier_to_entitlements_basic_and_pro() -> None:
    basic = tier_to_entitlements("basic")
    pro = tier_to_entitlements("pro")

    assert basic.tier == "basic"
    assert basic.max_strategies == 1
    assert basic.daily_trade_limit == 5
    assert basic.priority_execution is False

    assert pro.tier == "pro"
    assert pro.max_strategies is None
    assert pro.daily_trade_limit is None
    assert pro.priority_execution is True


@pytest.mark.asyncio
async def test_require_pro_tier_blocks_non_pro() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_pro_tier(tier_to_entitlements("basic"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_pro_tier_allows_pro() -> None:
    ent = tier_to_entitlements("pro")
    result = await require_pro_tier(ent)
    assert result is ent


def test_extract_org_and_tier_helpers() -> None:
    payload = {
        "data": {
            "object": {
                "metadata": {
                    "clerk_org_id": "org_abc",
                    "subscription_tier": "Pro",
                }
            }
        }
    }
    assert _extract_org_id(payload) == "org_abc"
    assert _extract_tier(payload) == "pro"


@pytest.mark.asyncio
async def test_verify_and_parse_event_requires_signature_when_secret_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import webhooks

    monkeypatch.setattr(webhooks.settings, "stripe_webhook_secret", "whsec_test")
    with pytest.raises(HTTPException) as exc:
        _verify_and_parse_event(b"{}", stripe_signature=None)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_and_parse_event_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import webhooks

    monkeypatch.setattr(webhooks.settings, "stripe_webhook_secret", "")
    payload = {"type": "invoice.paid"}
    parsed = _verify_and_parse_event(json.dumps(payload).encode(), stripe_signature=None)
    assert parsed == payload

    with pytest.raises(HTTPException) as exc:
        _verify_and_parse_event(b"{bad-json", stripe_signature=None)
    assert exc.value.status_code == 400


class _FakeSession:
    def __init__(self, tenant: object | None) -> None:
        self._tenant = tenant
        self.committed = False

    async def scalar(self, stmt):  # noqa: ANN001
        return self._tenant

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_resolve_tenant_subscription_tier_fallback_when_clerk_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = SimpleNamespace(id=uuid4(), subscription_tier="basic", is_active=True)
    session = _FakeSession(tenant)
    context = AuthContext(
        tenant_id=tenant.id,
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )

    monkeypatch.setattr(ClerkBillingClient, "fetch_org_subscription_tier", lambda self, org: (_ for _ in ()).throw(RuntimeError("down")))

    tier = await resolve_tenant_subscription_tier(context, session)
    assert tier == "basic"


@pytest.mark.asyncio
async def test_resolve_tenant_subscription_tier_blocks_inactive_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = SimpleNamespace(id=uuid4(), subscription_tier="basic", is_active=False)
    session = _FakeSession(tenant)
    context = AuthContext(
        tenant_id=tenant.id,
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )

    monkeypatch.setattr(ClerkBillingClient, "fetch_org_subscription_tier", lambda self, org: (_ for _ in ()).throw(RuntimeError("down")))

    with pytest.raises(HTTPException) as exc:
        await resolve_tenant_subscription_tier(context, session)
    assert exc.value.status_code == 403
