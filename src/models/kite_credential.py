from __future__ import annotations

from uuid import UUID

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import TenantScopedBase


class KiteCredential(TenantScopedBase):
    __tablename__ = "kite_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_kite_credentials_tenant_user"),
    )

    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    api_key_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_secret_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    totp_secret_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
