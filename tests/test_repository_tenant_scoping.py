from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.core.context import reset_current_tenant_id, set_current_tenant_id
from src.core.repositories.base import TenantContextMissingError, TenantRepository
from src.models.tenant import Tenant


def test_tenant_id_missing_raises() -> None:
    session = Mock()
    repo = TenantRepository(session=session, model=Tenant)

    with pytest.raises(TenantContextMissingError):
        _ = repo.tenant_id


def test_scoped_select_contains_tenant_filter() -> None:
    tenant_id = uuid4()
    token = set_current_tenant_id(tenant_id)
    try:
        repo = TenantRepository(session=Mock(), model=Tenant)
        stmt = repo._scoped_select()
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

        assert "WHERE" in sql
        assert "tenants.tenant_id" in sql
        assert str(tenant_id) in sql
    finally:
        reset_current_tenant_id(token)


@pytest.mark.asyncio
async def test_create_injects_tenant_id() -> None:
    tenant_id = uuid4()
    token = set_current_tenant_id(tenant_id)
    try:
        session = Mock()
        session.add = Mock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()

        repo = TenantRepository(session=session, model=Tenant)
        repo._apply_rls = AsyncMock()

        created = await repo.create(clerk_org_id="org_test", subscription_tier="basic")

        assert created.tenant_id == tenant_id
        session.add.assert_called_once_with(created)
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(created)
    finally:
        reset_current_tenant_id(token)
