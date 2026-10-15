"""Add payment provider snapshots and refund provider audit fields.

Revision ID: 0012_raygate_payments
Revises: 0011_invoice_provider_snapshot
Create Date: 2026-09-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_raygate_payments"
down_revision = "0011_invoice_provider_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("payment_attempts") as batch_op:
        batch_op.alter_column(
            "merchant_trade_no",
            existing_type=sa.String(length=20),
            type_=sa.String(length=50),
            existing_nullable=False,
        )
        batch_op.add_column(
            sa.Column(
                "provider",
                sa.String(length=20),
                nullable=False,
                server_default="ecpay",
            )
        )
        batch_op.create_index(
            op.f("ix_payment_attempts_provider"),
            ["provider"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_payment_attempts_provider_trade_no",
            ["provider", "provider_trade_no"],
        )
    with op.batch_alter_table("payment_attempts") as batch_op:
        batch_op.alter_column("provider", server_default=None)

    with op.batch_alter_table("refunds") as batch_op:
        batch_op.add_column(
            sa.Column("payment_attempt_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("provider", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("provider_refund_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "provider_response",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.create_foreign_key(
            op.f("fk_refunds_payment_attempt_id_payment_attempts"),
            "payment_attempts",
            ["payment_attempt_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            op.f("ix_refunds_payment_attempt_id"),
            ["payment_attempt_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_refunds_payment_attempt_id",
            ["payment_attempt_id"],
        )
        batch_op.create_index(
            op.f("ix_refunds_provider"),
            ["provider"],
            unique=False,
        )
        batch_op.create_index(
            op.f("ix_refunds_provider_refund_id"),
            ["provider_refund_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_refunds_provider_refund_id",
            ["provider", "provider_refund_id"],
        )
    with op.batch_alter_table("refunds") as batch_op:
        batch_op.alter_column("provider_response", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("refunds") as batch_op:
        batch_op.drop_constraint(
            "uq_refunds_payment_attempt_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_refunds_provider_refund_id",
            type_="unique",
        )
        batch_op.drop_index(op.f("ix_refunds_provider_refund_id"))
        batch_op.drop_index(op.f("ix_refunds_provider"))
        batch_op.drop_index(op.f("ix_refunds_payment_attempt_id"))
        batch_op.drop_constraint(
            op.f("fk_refunds_payment_attempt_id_payment_attempts"),
            type_="foreignkey",
        )
        batch_op.drop_column("provider_response")
        batch_op.drop_column("provider_refund_id")
        batch_op.drop_column("provider")
        batch_op.drop_column("payment_attempt_id")

    with op.batch_alter_table("payment_attempts") as batch_op:
        batch_op.drop_constraint(
            "uq_payment_attempts_provider_trade_no",
            type_="unique",
        )
        batch_op.drop_index(op.f("ix_payment_attempts_provider"))
        batch_op.drop_column("provider")
        batch_op.alter_column(
            "merchant_trade_no",
            existing_type=sa.String(length=50),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
