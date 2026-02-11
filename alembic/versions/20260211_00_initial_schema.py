"""create initial core schema

Revision ID: 20260211_00
Revises: 
Create Date: 2026-02-11 13:35:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260211_00"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("clerk_org_id", sa.String(length=255), nullable=False),
        sa.Column("subscription_tier", sa.String(length=50), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenants_clerk_org_id", "tenants", ["clerk_org_id"], unique=True)
    op.create_index("ix_tenants_tenant_id", "tenants", ["tenant_id"], unique=False)

    op.create_table(
        "kite_credentials",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("api_key_encrypted", sa.String(length=1024), nullable=False),
        sa.Column("api_secret_encrypted", sa.String(length=1024), nullable=False),
        sa.Column("totp_secret_encrypted", sa.String(length=1024), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_kite_credentials_tenant_user"),
    )
    op.create_index("ix_kite_credentials_tenant_id", "kite_credentials", ["tenant_id"], unique=False)
    op.create_index("ix_kite_credentials_user_id", "kite_credentials", ["user_id"], unique=False)

    op.create_table(
        "trading_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("max_daily_loss", sa.Numeric(14, 2), nullable=False),
        sa.Column("max_orders", sa.Integer(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_trading_profiles_tenant_user"),
    )
    op.create_index("ix_trading_profiles_tenant_id", "trading_profiles", ["tenant_id"], unique=False)
    op.create_index("ix_trading_profiles_user_id", "trading_profiles", ["user_id"], unique=False)

    op.create_table(
        "strategy_instances",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("strategy_type", sa.String(length=80), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_instances_tenant_id", "strategy_instances", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_strategy_instances_tenant_id", table_name="strategy_instances")
    op.drop_table("strategy_instances")

    op.drop_index("ix_trading_profiles_user_id", table_name="trading_profiles")
    op.drop_index("ix_trading_profiles_tenant_id", table_name="trading_profiles")
    op.drop_table("trading_profiles")

    op.drop_index("ix_kite_credentials_user_id", table_name="kite_credentials")
    op.drop_index("ix_kite_credentials_tenant_id", table_name="kite_credentials")
    op.drop_table("kite_credentials")

    op.drop_index("ix_tenants_tenant_id", table_name="tenants")
    op.drop_index("ix_tenants_clerk_org_id", table_name="tenants")
    op.drop_table("tenants")
