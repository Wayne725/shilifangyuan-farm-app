"""Give shipments a lookupable, expiring logistics selection token.

The ECPay store-selection page has to be opened by a top-level browser
navigation, which cannot carry an Authorization header. The token lets an
unauthenticated GET resolve exactly one shipment, mirroring how the payment
checkout page works.

Revision ID: 0003_shipment_selection_token
Revises: 0002_v2_social_commerce
Create Date: 2026-08-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_shipment_selection_token"
down_revision = "0002_v2_social_commerce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shipments",
        sa.Column("selection_token_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "shipments",
        sa.Column(
            "selection_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_shipments_selection_token_hash",
        "shipments",
        ["selection_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_shipments_selection_token_hash", table_name="shipments")
    op.drop_column("shipments", "selection_token_expires_at")
    op.drop_column("shipments", "selection_token_hash")
