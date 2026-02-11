from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from src.core.context import get_current_tenant_id
from src.core.db import apply_rls_tenant_context
from src.models.base import TenantScopedBase

ModelT = TypeVar("ModelT", bound=TenantScopedBase)


class TenantContextMissingError(RuntimeError):
    pass


class TenantRepository(Generic[ModelT]):
    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self.session = session
        self.model = model

    @property
    def tenant_id(self) -> UUID:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise TenantContextMissingError("Tenant context is missing from the current request")
        return tenant_id

    async def _apply_rls(self) -> None:
        await apply_rls_tenant_context(self.session, self.tenant_id)

    def _scoped_select(self) -> Select[tuple[ModelT]]:
        return select(self.model).where(self.model.tenant_id == self.tenant_id)

    async def create(self, **values: object) -> ModelT:
        await self._apply_rls()
        payload = dict(values)
        payload.setdefault("tenant_id", self.tenant_id)
        instance = self.model(**payload)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def get(self, entity_id: UUID) -> ModelT | None:
        await self._apply_rls()
        result = await self.session.execute(
            self._scoped_select().where(self.model.id == entity_id)
        )
        return result.scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        await self._apply_rls()
        result = await self.session.execute(
            self._scoped_select().limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def update(self, entity_id: UUID, **values: object) -> ModelT | None:
        instance = await self.get(entity_id)
        if instance is None:
            return None

        for field, value in values.items():
            if field in {"id", "tenant_id"}:
                continue
            setattr(instance, field, value)

        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def delete(self, entity_id: UUID) -> bool:
        await self._apply_rls()
        result = await self.session.execute(
            delete(self.model)
            .where(self.model.id == entity_id)
            .where(self.model.tenant_id == self.tenant_id)
        )
        return (result.rowcount or 0) > 0
