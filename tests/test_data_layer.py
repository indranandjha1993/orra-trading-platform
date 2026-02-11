from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.core.context import reset_current_tenant_id, set_current_tenant_id
from src.core.db import apply_rls_tenant_context, get_db_session
from src.core.repositories import (
    KiteCredentialRepository,
    NotificationPreferenceRepository,
    TenantRepository,
    TradingProfileRepository,
)
from src.models.tenant import Tenant


@pytest.mark.asyncio
async def test_apply_rls_tenant_context_executes_sql() -> None:
    session = Mock()
    session.execute = AsyncMock()

    await apply_rls_tenant_context(session, uuid4())
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_db_session_yields_session(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    class _Ctx:
        async def __aenter__(self):
            return sentinel

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return None

    from src.core import db

    monkeypatch.setattr(db, "AsyncSessionLocal", lambda: _Ctx())

    agen = get_db_session()
    value = await agen.__anext__()
    assert value is sentinel

    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_tenant_repository_get_list_update_delete() -> None:
    tenant_id = uuid4()
    token = set_current_tenant_id(tenant_id)
    try:
        entity = Tenant(
            id=uuid4(),
            tenant_id=tenant_id,
            clerk_org_id="org_a",
            subscription_tier="basic",
            is_active=True,
        )

        execute_values = [
            SimpleNamespace(scalar_one_or_none=lambda: entity),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [entity])),
            SimpleNamespace(scalar_one_or_none=lambda: entity),
            SimpleNamespace(rowcount=1),
        ]

        async def _execute(_stmt):  # noqa: ANN001
            return execute_values.pop(0)

        session = Mock()
        session.execute = AsyncMock(side_effect=_execute)
        session.flush = AsyncMock()
        session.refresh = AsyncMock()

        repo = TenantRepository(session=session, model=Tenant)
        repo._apply_rls = AsyncMock()

        got = await repo.get(entity.id)
        listed = await repo.list(limit=10, offset=0)
        updated = await repo.update(entity.id, subscription_tier="pro", id=uuid4(), tenant_id=uuid4())
        deleted = await repo.delete(entity.id)

        assert got is entity
        assert listed == [entity]
        assert updated is entity
        assert entity.subscription_tier == "pro"
        assert deleted is True
    finally:
        reset_current_tenant_id(token)


def test_repository_exports_and_concrete_repositories_init() -> None:
    session = Mock()

    kite_repo = KiteCredentialRepository(session)
    notif_repo = NotificationPreferenceRepository(session)
    profile_repo = TradingProfileRepository(session)

    assert isinstance(kite_repo, TenantRepository)
    assert isinstance(notif_repo, TenantRepository)
    assert isinstance(profile_repo, TenantRepository)


@pytest.mark.asyncio
async def test_concrete_repositories_get_by_user_id_paths() -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    token = set_current_tenant_id(tenant_id)
    try:
        expected = object()
        session = Mock()
        session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: expected))

        kite = KiteCredentialRepository(session)
        kite._apply_rls = AsyncMock()
        assert await kite.get_by_user_id(user_id) is expected

        notif = NotificationPreferenceRepository(session)
        notif._apply_rls = AsyncMock()
        assert await notif.get_by_user_id(user_id) is expected

        profile = TradingProfileRepository(session)
        profile._apply_rls = AsyncMock()
        assert await profile.get_by_user_id(user_id) is expected
    finally:
        reset_current_tenant_id(token)
