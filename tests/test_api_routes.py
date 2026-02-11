from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.auth import AuthContext, require_auth_context, require_super_admin
from src.core.db import get_db_session
from src.core.security.crypto import EncryptionError


@dataclass
class _FakeScalars:
    data: list

    def all(self) -> list:
        return self.data


class _FakeSession:
    def __init__(self, *, scalar_values: list | None = None, scalars_values: list | None = None) -> None:
        self._scalar_values = list(scalar_values or [])
        self._scalars_values = list(scalars_values or [])
        self.committed = False

    async def scalar(self, stmt):  # noqa: ANN001
        return self._scalar_values.pop(0) if self._scalar_values else None

    async def scalars(self, stmt):  # noqa: ANN001
        value = self._scalars_values.pop(0) if self._scalars_values else []
        return _FakeScalars(value)

    async def execute(self, stmt):  # noqa: ANN001
        return None

    async def commit(self) -> None:
        self.committed = True


@pytest.fixture
def auth_context() -> AuthContext:
    return AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="user_123",
        org_id="org_123",
        claims={"role": "member"},
    )


@pytest.fixture
def client(auth_context: AuthContext):
    async def _auth_override() -> AuthContext:
        return auth_context

    app.dependency_overrides[require_auth_context] = _auth_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_endpoint(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_account_upsert_kite_credentials_create(client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_context: AuthContext) -> None:
    from src.api.routes import account

    class FakeCipher:
        def encrypt(self, value: str) -> str:
            return f"enc:{value}"

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return None

        async def create(self, **values):  # noqa: ANN001
            return SimpleNamespace(updated_at=datetime.now(timezone.utc), **values)

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(account, "get_security_cipher", lambda: FakeCipher())
    monkeypatch.setattr(account, "KiteCredentialRepository", FakeRepo)

    res = client.put(
        "/api/v1/account/kite-credentials",
        json={"api_key": "k", "api_secret": "s", "totp_secret": "12345678"},
    )

    assert res.status_code == 422  # key/secret too short by schema

    res = client.put(
        "/api/v1/account/kite-credentials",
        json={"api_key": "key1", "api_secret": "sec1", "totp_secret": "12345678"},
    )
    assert res.status_code == 200
    assert res.json()["linked"] is True
    assert fake_session.committed is True


def test_account_kite_status_not_linked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import account

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return None

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(account, "KiteCredentialRepository", FakeRepo)

    res = client.get("/api/v1/account/kite-credentials/status")
    assert res.status_code == 200
    assert res.json() == {"linked": False, "updated_at": None}


def test_profiles_upsert_and_master_switch(client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_context: AuthContext) -> None:
    from src.api.routes import profiles

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            self._profile = None

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return self._profile

        async def create(self, **values):  # noqa: ANN001
            self._profile = SimpleNamespace(id=uuid4(), **values)
            return self._profile

        async def update(self, entity_id, **values):  # noqa: ANN001
            if self._profile is None:
                return None
            for k, v in values.items():
                setattr(self._profile, k, v)
            return self._profile

    fake_repo = FakeRepo(None)

    class RepoFactory:
        def __new__(cls, session):  # noqa: ANN001
            return fake_repo

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(profiles, "TradingProfileRepository", RepoFactory)

    res = client.put(
        "/api/v1/profile/trading",
        json={"max_daily_loss": "100.50", "max_orders": 5, "master_switch_enabled": False},
    )
    assert res.status_code == 200
    assert res.json()["max_orders"] == 5

    res = client.patch("/api/v1/profile/trading/master-switch", json={"enabled": True})
    assert res.status_code == 200
    assert res.json()["master_switch_enabled"] is True


def test_admin_routes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import admin

    admin_ctx = AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject="boss",
        org_id="org_admin",
        claims={"role": "super_admin"},
    )

    async def _super_admin_override() -> AuthContext:
        return admin_ctx

    app.dependency_overrides[require_super_admin] = _super_admin_override

    tenant = SimpleNamespace(id=uuid4(), clerk_org_id="org_a", subscription_tier="pro", created_at=datetime.now(timezone.utc))
    fake_session = _FakeSession(
        scalar_values=[1],
        scalars_values=[[tenant], [tenant.id]],
    )

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override

    class FakeRedis:
        async def get(self, key):  # noqa: ANN001
            return "token"

        async def ttl(self, key):  # noqa: ANN001
            return 100

        async def ping(self):
            return True

        async def exists(self, key):  # noqa: ANN001
            return 1

        async def aclose(self):
            return None

    monkeypatch.setattr(admin.redis, "from_url", lambda *a, **k: FakeRedis())

    res = client.get("/api/v1/admin/tenants/active")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["connected"] is True

    res = client.get("/api/v1/admin/system/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["connected_tenants"] == 1


def test_webhook_invoice_paid_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import webhooks

    tenant = SimpleNamespace(id=uuid4(), clerk_org_id="org_123", subscription_tier="basic", is_active=False)
    fake_session = _FakeSession(scalar_values=[tenant])

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override

    called = {"updated": False}

    async def _fake_set_state(tenant_id: str, active: bool) -> None:
        called["updated"] = True
        called["active"] = active

    monkeypatch.setattr(webhooks, "_set_tenant_redis_state", _fake_set_state)
    monkeypatch.setattr(webhooks.settings, "stripe_webhook_secret", "")
    monkeypatch.setattr(webhooks.settings, "clerk_webhook_secret", "")

    payload = {
        "type": "invoice.paid",
        "data": {"object": {"metadata": {"clerk_org_id": "org_123", "subscription_tier": "pro"}}},
    }

    res = client.post("/api/v1/webhooks/billing", json=payload)
    assert res.status_code == 200
    assert res.json()["updated"] is True
    assert called["updated"] is True
    assert called["active"] is True


def test_billing_guard_routes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import billing

    ent = SimpleNamespace(tier="pro", max_strategies=None, daily_trade_limit=None, priority_execution=True)

    async def _ent_override():
        return ent

    async def _ok_guard():
        return ent

    app.dependency_overrides[billing.get_current_entitlements] = _ent_override
    app.dependency_overrides[billing.enforce_strategy_limit] = _ok_guard
    app.dependency_overrides[billing.enforce_daily_trade_limit] = _ok_guard
    app.dependency_overrides[billing.require_pro_tier] = _ok_guard

    assert client.get("/api/v1/billing/entitlements").status_code == 200
    assert client.post("/api/v1/billing/guards/strategy").status_code == 200
    assert client.post("/api/v1/billing/guards/trade").status_code == 200
    assert client.post("/api/v1/billing/guards/priority").status_code == 200


def test_account_upsert_update_failure_returns_500(client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_context: AuthContext) -> None:
    from src.api.routes import account

    class FakeCipher:
        def encrypt(self, value: str) -> str:
            return f"enc:{value}"

    existing = SimpleNamespace(id=uuid4(), user_id=auth_context.user_id)

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            pass

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return existing

        async def update(self, entity_id, **values):  # noqa: ANN001
            return None

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(account, "get_security_cipher", lambda: FakeCipher())
    monkeypatch.setattr(account, "KiteCredentialRepository", FakeRepo)

    res = client.put(
        "/api/v1/account/kite-credentials",
        json={"api_key": "key1", "api_secret": "sec1", "totp_secret": "12345678"},
    )
    assert res.status_code == 500


def test_account_check_connection_branches(client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_context: AuthContext) -> None:
    from src.api.routes import account

    class FakeCipher:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail

        def decrypt(self, value: str) -> str:
            if self.fail:
                raise EncryptionError("bad")
            return value.replace("enc:", "")

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            self._cred = None

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return self._cred

    repo = FakeRepo(None)

    class RepoFactory:
        def __new__(cls, session):  # noqa: ANN001
            return repo

    class FakeRedis:
        async def set(self, key, value, ex=None):  # noqa: ANN001
            return True

        async def aclose(self):
            return None

    class FakeKite:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def generate_session(self, request_token: str, api_secret: str) -> dict:
            if request_token == "boomtoken":
                raise RuntimeError("kite down")
            if request_token == "notokenxx":
                return {"user_id": "u1"}
            return {"access_token": "at", "user_id": "u1"}

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(account, "KiteCredentialRepository", RepoFactory)
    monkeypatch.setattr(account.redis, "from_url", lambda *a, **k: FakeRedis())
    monkeypatch.setattr(account, "KiteConnect", FakeKite)

    res = client.post("/api/v1/account/kite/check-connection", json={"request_token": "tokennnn1"})
    assert res.status_code == 404

    repo._cred = SimpleNamespace(
        api_key_encrypted="enc:key",
        api_secret_encrypted="enc:sec",
        totp_secret_encrypted="enc:12345678",
    )
    monkeypatch.setattr(account, "get_security_cipher", lambda: FakeCipher(fail=True))
    res = client.post("/api/v1/account/kite/check-connection", json={"request_token": "tokennnn1"})
    assert res.status_code == 500

    monkeypatch.setattr(account, "get_security_cipher", lambda: FakeCipher(fail=False))
    res = client.post("/api/v1/account/kite/check-connection", json={"request_token": "boomtoken"})
    assert res.status_code == 200
    assert res.json()["success"] is False

    res = client.post("/api/v1/account/kite/check-connection", json={"request_token": "notokenxx"})
    assert res.status_code == 200
    assert res.json()["success"] is False

    res = client.post("/api/v1/account/kite/check-connection", json={"request_token": "tokennnn1"})
    assert res.status_code == 200
    assert res.json()["success"] is True


def test_profiles_get_and_master_switch_errors(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import profiles

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            self.profile = None

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return self.profile

        async def update(self, entity_id, **values):  # noqa: ANN001
            return None

    repo = FakeRepo(None)

    class RepoFactory:
        def __new__(cls, session):  # noqa: ANN001
            return repo

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(profiles, "TradingProfileRepository", RepoFactory)

    res = client.get("/api/v1/profile/trading")
    assert res.status_code == 404

    repo.profile = SimpleNamespace(
        id=uuid4(),
        max_daily_loss="10.00",
        max_orders=2,
        master_switch_enabled=False,
    )
    res = client.patch("/api/v1/profile/trading/master-switch", json={"enabled": True})
    assert res.status_code == 500


def test_profiles_get_success_and_upsert_update_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import profiles

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            self.profile = SimpleNamespace(
                id=uuid4(),
                max_daily_loss="10.00",
                max_orders=3,
                master_switch_enabled=True,
            )

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return self.profile

        async def update(self, entity_id, **values):  # noqa: ANN001
            return None

    class RepoFactory:
        def __new__(cls, session):  # noqa: ANN001
            return FakeRepo(session)

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(profiles, "TradingProfileRepository", RepoFactory)

    res = client.get("/api/v1/profile/trading")
    assert res.status_code == 200
    assert res.json()["max_orders"] == 3

    res = client.put(
        "/api/v1/profile/trading",
        json={"max_daily_loss": "99.99", "max_orders": 9, "master_switch_enabled": False},
    )
    assert res.status_code == 500


def test_connections_route_branches(client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_context: AuthContext) -> None:
    from src.api.routes import connections

    class FakeCipher:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail

        def decrypt(self, value: str) -> str:
            if self.fail:
                raise EncryptionError("bad")
            return value.replace("enc:", "")

    class FakeRepo:
        def __init__(self, session):  # noqa: ANN001
            self.credential = None

        async def get_by_user_id(self, user_id):  # noqa: ANN001
            return self.credential

    repo = FakeRepo(None)

    class RepoFactory:
        def __new__(cls, session):  # noqa: ANN001
            return repo

    class FakeKite:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def generate_session(self, request_token: str, api_secret: str) -> dict:
            if request_token == "badtoken1":
                raise RuntimeError("bad token")
            if request_token == "notokenx":
                return {"user_id": "u1"}
            return {"access_token": "at", "user_id": "u1"}

        def set_access_token(self, token: str) -> None:
            return None

        def profile(self) -> dict:
            return {"user_name": "Trader"}

    fake_session = _FakeSession()

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(connections, "KiteCredentialRepository", RepoFactory)
    monkeypatch.setattr(connections, "KiteConnect", FakeKite)
    monkeypatch.setattr(connections, "get_security_cipher", lambda: FakeCipher())

    payload = {"user_id": str(auth_context.user_id), "request_token": "goodtokn1"}
    assert client.post("/api/v1/connections/kite/test", json=payload).status_code == 404

    repo.credential = SimpleNamespace(
        tenant_id=uuid4(),
        api_key_encrypted="enc:key",
        api_secret_encrypted="enc:sec",
        totp_secret_encrypted="enc:12345678",
    )
    assert client.post("/api/v1/connections/kite/test", json=payload).status_code == 403

    repo.credential = SimpleNamespace(
        tenant_id=auth_context.tenant_id,
        api_key_encrypted="enc:key",
        api_secret_encrypted="enc:sec",
        totp_secret_encrypted="enc:12345678",
    )
    monkeypatch.setattr(connections, "get_security_cipher", lambda: FakeCipher(fail=True))
    assert client.post("/api/v1/connections/kite/test", json=payload).status_code == 500

    monkeypatch.setattr(connections, "get_security_cipher", lambda: FakeCipher())
    bad_payload = {"user_id": str(auth_context.user_id), "request_token": "badtoken1"}
    assert client.post("/api/v1/connections/kite/test", json=bad_payload).status_code == 400

    missing_token_payload = {"user_id": str(auth_context.user_id), "request_token": "notokenx"}
    assert client.post("/api/v1/connections/kite/test", json=missing_token_payload).status_code == 400

    res = client.post("/api/v1/connections/kite/test", json=payload)
    assert res.status_code == 200
    assert res.json()["connected"] is True


def test_webhook_misc_branches(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import webhooks

    fake_session = _FakeSession(
        scalar_values=[None, SimpleNamespace(id=uuid4(), is_active=True)]
    )

    async def _db_override():
        yield fake_session

    app.dependency_overrides[get_db_session] = _db_override
    monkeypatch.setattr(webhooks.settings, "stripe_webhook_secret", "")
    monkeypatch.setattr(webhooks.settings, "clerk_webhook_secret", "clerk_secret")

    res = client.post("/api/v1/webhooks/billing", headers={"X-Clerk-Webhook-Secret": "bad"}, json={"type": "invoice.paid"})
    assert res.status_code == 401

    monkeypatch.setattr(webhooks.settings, "clerk_webhook_secret", "")
    res = client.post("/api/v1/webhooks/billing", json={"type": "noop.event"})
    assert res.status_code == 200
    assert res.json()["updated"] is False

    res = client.post("/api/v1/webhooks/billing", json={"type": "invoice.paid", "data": {"object": {"metadata": {}}}})
    assert res.status_code == 400

    res = client.post(
        "/api/v1/webhooks/billing",
        json={"type": "invoice.paid", "data": {"object": {"metadata": {"clerk_org_id": "org_none"}}}},
    )
    assert res.status_code == 200
    assert res.json()["updated"] is False

    called = {"inactive": False}

    async def _fake_set_state(tenant_id: str, active: bool) -> None:
        called["inactive"] = not active

    monkeypatch.setattr(webhooks, "_set_tenant_redis_state", _fake_set_state)
    payload = {
        "type": "subscription.deleted",
        "data": {"object": {"metadata": {"clerk_org_id": "org_123"}}},
    }
    res = client.post("/api/v1/webhooks/billing", json=payload)
    assert res.status_code == 200
    assert res.json()["updated"] is True
    assert called["inactive"] is True
