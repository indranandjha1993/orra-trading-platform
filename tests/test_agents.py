from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.agents.auth_service import AuthAgent, TenantAuthRecord
from src.agents.health import AgentHealth
from src.agents.notification_service import (
    NotificationAgent,
    NotificationDispatcher,
    NotificationTarget,
)
from src.agents.ticker_service import TenantTickerConfig, TenantTickerWorker, TickerAgent


def test_agent_health_lifecycle_payload() -> None:
    health = AgentHealth(name="agent-x")
    health.mark_run()
    health.mark_success()
    health.mark_error(ValueError("boom"))

    payload = health.payload()
    assert payload["name"] == "agent-x"
    assert payload["healthy"] is False
    assert payload["ready"] is True
    assert payload["last_error"] == "boom"
    assert payload["last_run_at"] is not None
    assert payload["last_success_at"] is not None


@pytest.mark.asyncio
async def test_notification_dispatcher_errors_without_webhook() -> None:
    dispatcher = NotificationDispatcher()
    with pytest.raises(ValueError):
        await dispatcher.dispatch(channel="telegram", payload={"x": 1}, urgent=False)


@pytest.mark.asyncio
async def test_notification_dispatcher_posts_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agents import notification_service

    dispatcher = NotificationDispatcher()
    monkeypatch.setattr(notification_service.settings, "n8n_email_webhook_url", "https://example.test")
    dispatcher.channel_webhooks["email"] = "https://example.test"

    called: dict[str, object] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

    def _post(url: str, json: dict, timeout: int):  # noqa: A002
        called["url"] = url
        called["json"] = json
        called["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(notification_service.requests, "post", _post)
    await dispatcher.dispatch(channel="email", payload={"event": "ok"}, urgent=False)

    assert called["url"] == "https://example.test"
    assert called["json"] == {"event": "ok"}


@pytest.mark.asyncio
async def test_notification_agent_process_event_success_and_failure() -> None:
    agent = NotificationAgent()

    class _Redis:
        def __init__(self) -> None:
            self.acks: list[tuple[str, str, str]] = []
            self.failed: list[dict[str, str]] = []

        async def xack(self, stream: str, group: str, msg_id: str) -> None:
            self.acks.append((stream, group, msg_id))

        async def xadd(self, stream: str, payload: dict[str, str]) -> None:
            self.failed.append(payload)

        async def aclose(self) -> None:
            return None

    fake_redis = _Redis()
    agent._redis = fake_redis  # type: ignore[assignment]

    async def _ok(_fields: dict[str, str]) -> None:
        return None

    async def _boom(_fields: dict[str, str]) -> None:
        raise ValueError("bad")

    agent._handle_execution_result = _ok  # type: ignore[method-assign]
    await agent._process_event("execution_results", "1-0", {"tenant_id": str(uuid4()), "user_id": str(uuid4())})
    assert agent.health.metrics["events_processed"] == 1
    assert len(fake_redis.acks) == 1

    agent._handle_execution_result = _boom  # type: ignore[method-assign]
    await agent._process_event("execution_results", "2-0", {"tenant_id": str(uuid4()), "user_id": str(uuid4())})
    assert agent.health.metrics["events_failed"] == 1
    assert len(fake_redis.failed) == 1


@pytest.mark.asyncio
async def test_notification_agent_handle_execution_result_and_auth_error() -> None:
    agent = NotificationAgent()

    sent: list[tuple[str, dict[str, object], bool]] = []

    async def _dispatch(*, channel: str, payload: dict[str, object], urgent: bool = False) -> None:
        sent.append((channel, payload, urgent))

    async def _resolve(_tenant_id, _user_id, prefer_urgent: bool = False):  # noqa: ANN001
        _ = prefer_urgent
        return NotificationTarget(channel="email", destination="org_1")

    agent._dispatcher = SimpleNamespace(dispatch=_dispatch)  # type: ignore[assignment]
    agent._resolve_target = _resolve  # type: ignore[method-assign]

    await agent._handle_execution_result(
        {"tenant_id": str(uuid4()), "user_id": str(uuid4()), "status": "success"}
    )
    await agent._handle_auth_error({"tenant_id": str(uuid4()), "user_id": str(uuid4())})

    assert sent[0][2] is False
    assert sent[1][2] is True

    with pytest.raises(ValueError):
        await agent._handle_execution_result({"tenant_id": str(uuid4())})

    with pytest.raises(ValueError):
        await agent._handle_auth_error({"user_id": str(uuid4())})


@pytest.mark.asyncio
async def test_auth_agent_emit_failure_and_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agents import auth_service

    agent = AuthAgent()

    class _Redis:
        def __init__(self) -> None:
            self.events: list[dict[str, str]] = []
            self.set_calls: list[tuple[str, str, int]] = []
            self.publish_calls: list[tuple[str, str]] = []

        async def xadd(self, _stream: str, payload: dict[str, str]) -> None:
            self.events.append(payload)

        async def set(self, key: str, val: str, ex: int) -> None:
            self.set_calls.append((key, val, ex))

        async def publish(self, channel: str, payload: str) -> None:
            self.publish_calls.append((channel, payload))

    record = TenantAuthRecord(
        tenant_id=uuid4(),
        user_id=uuid4(),
        api_key_encrypted="enc:key",
        api_secret_encrypted="enc:sec",
        totp_secret_encrypted="enc:totp",
    )

    redis_client = _Redis()
    await agent._emit_auth_failure_event(redis_client, record, ValueError("2fa failed"))
    assert redis_client.events[0]["event_type"] == "auth_2fa_failed"

    class _Cipher:
        def decrypt(self, value: str) -> str:
            return value.replace("enc:", "")

    class _Kite:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def generate_session(self, request_token: str, api_secret: str) -> dict:
            _ = request_token, api_secret
            return {"access_token": "at"}

    async def _login(**kwargs):  # noqa: ANN003
        _ = kwargs
        return "rt"

    monkeypatch.setattr(auth_service, "get_security_cipher", lambda: _Cipher())
    monkeypatch.setattr(auth_service, "KiteConnect", _Kite)
    agent._perform_zerodha_login = _login  # type: ignore[method-assign]

    await agent._refresh_single_tenant_token(SimpleNamespace(), redis_client, record)
    assert redis_client.set_calls
    assert redis_client.publish_calls


@pytest.mark.asyncio
async def test_auth_agent_perform_login_requires_mapped_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agents import auth_service

    agent = AuthAgent()
    monkeypatch.setattr(auth_service.settings, "zerodha_user_id_map_json", "{}")
    monkeypatch.setattr(auth_service.settings, "zerodha_password_map_json", "{}")

    with pytest.raises(ValueError):
        await agent._perform_zerodha_login(SimpleNamespace(), uuid4(), "key", "totp")


@pytest.mark.asyncio
async def test_ticker_worker_callbacks_publish_and_errors() -> None:
    class _Redis:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []

        def get(self, key: str):
            if key.startswith("tenant:active:"):
                return "1"
            return None

        def publish(self, channel: str, payload: str) -> None:
            self.published.append((channel, payload))

    class _Kws:
        MODE_FULL = "full"

        def __init__(self) -> None:
            self.on_connect = None
            self.on_ticks = None
            self.on_close = None
            self.on_error = None
            self.subscribed = []

        def subscribe(self, tokens: list[int]) -> None:
            self.subscribed = tokens

        def set_mode(self, mode: str, tokens: list[int]) -> None:
            _ = mode, tokens

    health = AgentHealth(name="ticker")
    worker = TenantTickerWorker(
        tenant_id=uuid4(),
        api_key="k",
        redis_client=_Redis(),
        instrument_tokens=[111],
        health=health,
    )
    kws = _Kws()
    worker._configure_callbacks(kws)  # noqa: SLF001

    kws.on_connect(kws, {})
    kws.on_ticks(kws, [{"instrument_token": 111, "last_price": 123}])
    kws.on_close(kws, 1000, "bye")
    kws.on_error(kws, 500, "oops")

    await asyncio.sleep(0)
    assert health.metrics["ticks_published"] == 1
    assert health.last_error == "500:oops"


@pytest.mark.asyncio
async def test_ticker_agent_run_without_instruments_marks_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agents import ticker_service

    agent = TickerAgent()
    monkeypatch.setattr(ticker_service.settings, "ticker_instrument_tokens_csv", "")

    await agent.run()
    assert agent.health.healthy is False
    assert "No ticker instruments configured" in (agent.health.last_error or "")


@pytest.mark.asyncio
async def test_ticker_agent_run_workers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agents import ticker_service

    agent = TickerAgent()
    tenant_id = uuid4()

    async def _fetch() -> list[TenantTickerConfig]:
        return [TenantTickerConfig(tenant_id=tenant_id, api_key_encrypted="enc:key")]

    class _Cipher:
        def decrypt(self, value: str) -> str:
            return value.replace("enc:", "")

    class _Worker:
        def __init__(self, **kwargs):  # noqa: ANN003
            self.kwargs = kwargs

        async def run(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    created: list[_Worker] = []

    def _worker_factory(**kwargs):  # noqa: ANN003
        worker = _Worker(**kwargs)
        created.append(worker)
        return worker

    monkeypatch.setattr(ticker_service, "get_security_cipher", lambda: _Cipher())
    monkeypatch.setattr(ticker_service.settings, "ticker_instrument_tokens_csv", "111,222")
    monkeypatch.setattr(ticker_service, "TenantTickerWorker", _worker_factory)
    monkeypatch.setattr(agent, "_fetch_tenant_configs", _fetch)

    await agent._run_workers_once()
    assert agent.health.metrics["tenants_seen"] == 1
    assert len(created) == 1
