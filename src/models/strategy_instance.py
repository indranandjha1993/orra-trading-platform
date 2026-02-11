from __future__ import annotations

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import TenantScopedBase


class StrategyInstance(TenantScopedBase):
    __tablename__ = "strategy_instances"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(80), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
