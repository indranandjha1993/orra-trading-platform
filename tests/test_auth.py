from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.auth import AuthContext, _is_super_admin, require_super_admin, subject_to_user_id


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
