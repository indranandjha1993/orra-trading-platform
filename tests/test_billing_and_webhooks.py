from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routes.webhooks import (
    _extract_org_id,
    _extract_tier,
    _set_tenant_redis_state,
    _verify_and_parse_event,
)
from src.core.auth import AuthContext
from src.core.billing import (
    ClerkBillingClient,
    get_current_entitlements,
    enforce_daily_trade_limit,
    enforce_strategy_limit,
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


@pytest.mark.asyncio
async def test_resolve_tenant_subscription_tier_updates_from_clerk(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = SimpleNamespace(id=uuid4(), subscription_tier="basic", is_active=True)
    session = _FakeSession(tenant)
    context = AuthContext(
        tenant_id=tenant.id,
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )

    monkeypatch.setattr(ClerkBillingClient, "fetch_org_subscription_tier", lambda self, org: "pro")
    tier = await resolve_tenant_subscription_tier(context, session)

    assert tier == "pro"
    assert tenant.subscription_tier == "pro"
    assert session.committed is True


@pytest.mark.asyncio
async def test_enforce_strategy_limit_blocks_on_basic_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    context = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=1))
    monkeypatch.setattr(
        billing,
        "get_current_entitlements",
        AsyncMock(return_value=tier_to_entitlements("basic")),
    )

    with pytest.raises(HTTPException) as exc:
        await enforce_strategy_limit(context, session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_enforce_daily_trade_limit_blocks_when_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    class _FakeRedis:
        async def incr(self, _key):  # noqa: ANN001
            return 6

        async def expire(self, _key, _ttl):  # noqa: ANN001
            return True

        async def aclose(self):
            return None

    context = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )
    monkeypatch.setattr(
        billing,
        "get_current_entitlements",
        AsyncMock(return_value=tier_to_entitlements("basic")),
    )
    monkeypatch.setattr(billing.redis, "from_url", lambda *args, **kwargs: _FakeRedis())

    with pytest.raises(HTTPException) as exc:
        await enforce_daily_trade_limit(context, session=SimpleNamespace())
    assert exc.value.status_code == 403


def test_clerk_billing_client_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    monkeypatch.setattr(billing.settings, "clerk_secret_key", "")
    with pytest.raises(HTTPException) as exc:
        ClerkBillingClient().fetch_org_subscription_tier("org_1")
    assert exc.value.status_code == 500


def test_clerk_billing_client_fetches_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"public_metadata": {"subscription_tier": "pro"}}

    called = {}

    def _get(url: str, headers: dict, timeout: int):  # noqa: ANN001
        called["url"] = url
        called["headers"] = headers
        called["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(billing.settings, "clerk_secret_key", "sk_test")
    monkeypatch.setattr(billing.requests, "get", _get)

    tier = ClerkBillingClient().fetch_org_subscription_tier("org_123")
    assert tier == "pro"
    assert called["headers"]["Authorization"] == "Bearer sk_test"


@pytest.mark.asyncio
async def test_enforce_daily_trade_limit_sets_expiry_on_first_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    class _FakeRedis:
        def __init__(self) -> None:
            self.expired = False

        async def incr(self, _key):  # noqa: ANN001
            return 1

        async def expire(self, _key, _ttl):  # noqa: ANN001
            self.expired = True
            return True

        async def aclose(self):
            return None

    context = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        billing,
        "get_current_entitlements",
        AsyncMock(return_value=tier_to_entitlements("basic")),
    )
    monkeypatch.setattr(billing.redis, "from_url", lambda *args, **kwargs: fake_redis)

    entitlements = await enforce_daily_trade_limit(context, session=SimpleNamespace())
    assert entitlements.tier == "basic"
    assert fake_redis.expired is True


@pytest.mark.asyncio
async def test_resolve_tenant_subscription_tier_missing_tenant_after_clerk_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession(None)
    context = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )
    monkeypatch.setattr(ClerkBillingClient, "fetch_org_subscription_tier", lambda self, org: "pro")

    with pytest.raises(HTTPException) as exc:
        await resolve_tenant_subscription_tier(context, session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_current_entitlements_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    context = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )
    monkeypatch.setattr(billing, "resolve_tenant_subscription_tier", AsyncMock(return_value="basic"))
    ent = await get_current_entitlements(context, session=SimpleNamespace())
    assert ent.tier == "basic"


@pytest.mark.asyncio
async def test_enforce_strategy_limit_allows_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    context = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )
    monkeypatch.setattr(
        billing,
        "get_current_entitlements",
        AsyncMock(return_value=tier_to_entitlements("pro")),
    )

    ent = await enforce_strategy_limit(context, session=SimpleNamespace())
    assert ent.tier == "pro"


@pytest.mark.asyncio
async def test_enforce_daily_trade_limit_allows_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import billing

    context = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_1",
        org_id="org_1",
        claims={},
    )
    monkeypatch.setattr(
        billing,
        "get_current_entitlements",
        AsyncMock(return_value=tier_to_entitlements("pro")),
    )

    ent = await enforce_daily_trade_limit(context, session=SimpleNamespace())
    assert ent.tier == "pro"


def test_verify_and_parse_event_with_stripe_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import webhooks

    class _Event:
        def to_dict_recursive(self) -> dict:
            return {"type": "invoice.paid"}

    monkeypatch.setattr(webhooks.settings, "stripe_webhook_secret", "whsec_test")
    monkeypatch.setattr(webhooks.stripe.Webhook, "construct_event", lambda **kwargs: _Event())

    parsed = _verify_and_parse_event(b"{}", stripe_signature="sig_header")
    assert parsed["type"] == "invoice.paid"


def test_verify_and_parse_event_with_invalid_stripe_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import webhooks

    def _boom(**kwargs):  # noqa: ANN003
        raise ValueError("bad sig")

    monkeypatch.setattr(webhooks.settings, "stripe_webhook_secret", "whsec_test")
    monkeypatch.setattr(webhooks.stripe.Webhook, "construct_event", _boom)

    with pytest.raises(HTTPException) as exc:
        _verify_and_parse_event(b"{}", stripe_signature="sig_header")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_set_tenant_redis_state_active_and_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import webhooks

    class _FakeRedis:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def set(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls.append(("set", args, kwargs))
            return True

        async def publish(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls.append(("publish", args, kwargs))
            return 1

        async def delete(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls.append(("delete", args, kwargs))
            return 1

        async def aclose(self):
            self.calls.append(("aclose", (), {}))
            return None

    fake = _FakeRedis()
    monkeypatch.setattr(webhooks.redis, "from_url", lambda *args, **kwargs: fake)

    await _set_tenant_redis_state("tenant_1", active=True)
    await _set_tenant_redis_state("tenant_1", active=False)

    assert any(call[0] == "publish" for call in fake.calls)
    assert any(call[0] == "delete" for call in fake.calls)
