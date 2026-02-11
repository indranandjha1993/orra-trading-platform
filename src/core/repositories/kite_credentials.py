from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.repositories.base import TenantRepository
from src.models.kite_credential import KiteCredential


class KiteCredentialRepository(TenantRepository[KiteCredential]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session=session, model=KiteCredential)

    async def get_by_user_id(self, user_id: UUID) -> KiteCredential | None:
        await self._apply_rls()
        result = await self.session.execute(
            self._scoped_select().where(KiteCredential.user_id == user_id)
        )
        return result.scalar_one_or_none()
