from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import JWTError

from src.core.auth import (
    AuthContext,
    JwksCache,
    _decode_clerk_jwt,
    _get_signing_key,
    _is_super_admin,
    require_auth_context,
    require_super_admin,
    subject_to_user_id,
)


def _ctx(*, subject: str = "user_1", claims: dict | None = None) -> AuthContext:
    return AuthContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        subject=subject,
        org_id="org_123",
        claims=claims or {},
    )


def test_subject_to_user_id_is_deterministic() -> None:
    assert subject_to_user_id("abc") == subject_to_user_id("abc")
    assert subject_to_user_id("abc") != subject_to_user_id("xyz")


def test_is_super_admin_by_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth.settings, "super_admin_subjects_csv", "user_1,user_2")
    assert _is_super_admin(_ctx(subject="user_1")) is True


def test_is_super_admin_by_claim_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth.settings, "super_admin_subjects_csv", "")
    assert _is_super_admin(_ctx(claims={"role": "super_admin"})) is True
    assert _is_super_admin(_ctx(claims={"roles": ["member", "super_admin"]})) is True
    assert _is_super_admin(_ctx(claims={"roles": ["member"]})) is False


@pytest.mark.asyncio
async def test_require_super_admin_allows_super_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth.settings, "super_admin_subjects_csv", "boss")
    ctx = _ctx(subject="boss")
    result = await require_super_admin(ctx)
    assert result is ctx


@pytest.mark.asyncio
async def test_require_super_admin_blocks_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth.settings, "super_admin_subjects_csv", "boss")
    with pytest.raises(HTTPException) as exc:
        await require_super_admin(_ctx(subject="normal"))

    assert exc.value.status_code == 403


def test_get_signing_key_missing_kid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth.jwt, "get_unverified_header", lambda token: {})
    with pytest.raises(HTTPException) as exc:
        _get_signing_key("token")
    assert exc.value.status_code == 401


def test_get_signing_key_no_matching_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth.jwt, "get_unverified_header", lambda token: {"kid": "abc"})
    monkeypatch.setattr(auth.jwks_cache, "get", lambda url: {"keys": [{"kid": "zzz"}]})

    with pytest.raises(HTTPException) as exc:
        _get_signing_key("token")
    assert exc.value.status_code == 401


def test_decode_clerk_jwt_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth, "_get_signing_key", lambda token: {"kid": "abc"})

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise JWTError("bad token")

    monkeypatch.setattr(auth.jwt, "decode", _boom)
    with pytest.raises(HTTPException) as exc:
        _decode_clerk_jwt("token")
    assert exc.value.status_code == 401


def test_jwks_cache_fetches_and_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    calls = {"count": 0}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"keys": [{"kid": "a"}]}

    def _get(url: str, timeout: int):  # noqa: ANN001
        calls["count"] += 1
        return _Resp()

    cache = JwksCache(ttl_seconds=300)
    monkeypatch.setattr(auth.requests, "get", _get)
    monkeypatch.setattr(auth.time, "time", lambda: 1000.0)
    first = cache.get("https://jwks.example")
    second = cache.get("https://jwks.example")
    assert first == second
    assert calls["count"] == 1


def test_get_signing_key_returns_matching_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    monkeypatch.setattr(auth.jwt, "get_unverified_header", lambda token: {"kid": "abc"})
    monkeypatch.setattr(auth.jwks_cache, "get", lambda url: {"keys": [{"kid": "abc", "kty": "RSA"}]})
    key = _get_signing_key("token")
    assert key["kid"] == "abc"


def test_get_signing_key_invalid_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import auth

    def _bad_header(_token: str) -> dict:
        raise JWTError("invalid header")

    monkeypatch.setattr(auth.jwt, "get_unverified_header", _bad_header)
    with pytest.raises(HTTPException) as exc:
        _get_signing_key("token")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_auth_context_sets_request_state(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()

    class _Session:
        async def scalar(self, _stmt):  # noqa: ANN001
            return SimpleNamespace(id=tenant_id)

    from src.core import auth
    from src.core.context import set_current_tenant_id

    monkeypatch.setattr(
        auth,
        "_decode_clerk_jwt",
        lambda _token: {"org_id": "org_1", "sub": "user_1", "role": "member"},
    )

    request = SimpleNamespace(state=SimpleNamespace())
    credentials = SimpleNamespace(credentials="jwt")
    context = await require_auth_context(request, credentials, _Session())

    assert context.tenant_id == tenant_id
    assert context.org_id == "org_1"
    assert request.state.tenant_id == tenant_id
    assert request.state.user_subject == "user_1"
    assert request.state.auth_claims["org_id"] == "org_1"
    set_current_tenant_id(None)


@pytest.mark.asyncio
async def test_require_auth_context_missing_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Session:
        async def scalar(self, _stmt):  # noqa: ANN001
            return None

    from src.core import auth

    request = SimpleNamespace(state=SimpleNamespace())
    credentials = SimpleNamespace(credentials="jwt")

    monkeypatch.setattr(auth, "_decode_clerk_jwt", lambda _token: {"sub": "user_1"})
    with pytest.raises(HTTPException) as exc:
        await require_auth_context(request, credentials, _Session())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_auth_context_tenant_not_provisioned(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Session:
        async def scalar(self, _stmt):  # noqa: ANN001
            return None

    from src.core import auth

    monkeypatch.setattr(
        auth,
        "_decode_clerk_jwt",
        lambda _token: {"org_id": "org_missing", "sub": "user_2"},
    )

    request = SimpleNamespace(state=SimpleNamespace())
    credentials = SimpleNamespace(credentials="jwt")
    with pytest.raises(HTTPException) as exc:
        await require_auth_context(request, credentials, _Session())
    assert exc.value.status_code == 403
