"""Persist exact provider invoice requests and decimal line values.

Revision ID: 0011_invoice_provider_snapshot
Revises: 0010_fanyu_cloud_invoices
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_invoice_provider_snapshot"
down_revision = "0010_fanyu_cloud_invoices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "provider_request",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.alter_column("provider_request", server_default=None)

    for table_name in ("invoice_items", "invoice_allowance_items"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "unit_price",
                existing_type=sa.Integer(),
                type_=sa.Numeric(18, 6),
                existing_nullable=False,
                postgresql_using="unit_price::numeric(18, 6)",
            )
            batch_op.alter_column(
                "amount",
                existing_type=sa.Integer(),
                type_=sa.Numeric(18, 6),
                existing_nullable=False,
                postgresql_using="amount::numeric(18, 6)",
            )


def downgrade() -> None:
    for table_name in ("invoice_allowance_items", "invoice_items"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "unit_price",
                existing_type=sa.Numeric(18, 6),
                type_=sa.Integer(),
                existing_nullable=False,
                postgresql_using="unit_price::integer",
            )
            batch_op.alter_column(
                "amount",
                existing_type=sa.Numeric(18, 6),
                type_=sa.Integer(),
                existing_nullable=False,
                postgresql_using="amount::integer",
            )

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_column("provider_request")
