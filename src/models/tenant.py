from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import TenantScopedBase


class Tenant(TenantScopedBase):
    __tablename__ = "tenants"

    clerk_org_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    subscription_tier: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
