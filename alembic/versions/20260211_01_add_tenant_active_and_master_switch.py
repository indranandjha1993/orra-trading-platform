"""add tenant activation and trading profile master switch

Revision ID: 20260211_01
Revises: 
Create Date: 2026-02-11 13:05:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260211_01"
down_revision = "20260211_00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "trading_profiles",
        sa.Column(
            "master_switch_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_tenants_is_active", "tenants", ["is_active"], unique=False)

    op.alter_column("tenants", "is_active", server_default=None)
    op.alter_column("trading_profiles", "master_switch_enabled", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_tenants_is_active", table_name="tenants")
    op.drop_column("trading_profiles", "master_switch_enabled")
    op.drop_column("tenants", "is_active")
