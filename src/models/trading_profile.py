from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import TenantScopedBase


class TradingProfile(TenantScopedBase):
    __tablename__ = "trading_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_trading_profiles_tenant_user"),
    )

    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    max_daily_loss: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    max_orders: Mapped[int] = mapped_column(nullable=False)
    master_switch_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
