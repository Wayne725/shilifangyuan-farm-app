"""Add cloud invoice buyer snapshots, items, voids, and allowances.

Revision ID: 0010_fanyu_cloud_invoices
Revises: 0009_security_hardening
Create Date: 2026-08-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_fanyu_cloud_invoices"
down_revision = "0009_security_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column(
                "invoice_buyer_type",
                sa.Enum(
                    "personal",
                    "company",
                    name="invoice_buyer_type",
                    native_enum=False,
                ),
                nullable=False,
                server_default="personal",
            )
        )
        batch_op.add_column(
            sa.Column("invoice_buyer_tax_id", sa.String(length=10), nullable=True)
        )
        batch_op.add_column(
            sa.Column("invoice_buyer_name", sa.String(length=60), nullable=True)
        )
        batch_op.add_column(
            sa.Column("invoice_buyer_email", sa.String(length=320), nullable=True)
        )

    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column("invoice_buyer_type", server_default=None)

    with op.batch_alter_table("invoices") as batch_op:
        batch_op.add_column(
            sa.Column(
                "provider",
                sa.String(length=40),
                nullable=False,
                server_default="ecpay",
            )
        )
        batch_op.add_column(
            sa.Column("payment_attempt_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("provider_status", sa.String(length=40), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "buyer_type",
                sa.Enum(
                    "personal",
                    "company",
                    name="invoice_record_buyer_type",
                    native_enum=False,
                ),
                nullable=False,
                server_default="personal",
            )
        )
        batch_op.add_column(
            sa.Column("buyer_tax_id", sa.String(length=10), nullable=True)
        )
        batch_op.add_column(
            sa.Column("buyer_name", sa.String(length=60), nullable=True)
        )
        batch_op.add_column(
            sa.Column("buyer_email", sa.String(length=320), nullable=True)
        )
        batch_op.add_column(
            sa.Column("carrier_type", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("carrier_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "sales_amount",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "tax_amount",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "total_amount",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("void_reason", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("void_source", sa.String(length=40), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f("fk_invoices_payment_attempt_id_payment_attempts"),
            "payment_attempts",
            ["payment_attempt_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("invoices") as batch_op:
        for column in (
            "provider",
            "buyer_type",
            "sales_amount",
            "tax_amount",
            "total_amount",
        ):
            batch_op.alter_column(column, server_default=None)

    op.create_index(
        op.f("ix_invoices_provider"),
        "invoices",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoices_payment_attempt_id"),
        "invoices",
        ["payment_attempt_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoices_provider_status"),
        "invoices",
        ["provider_status"],
        unique=False,
    )

    op.create_table(
        "invoice_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(length=6), nullable=False),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("tax_type", sa.String(length=1), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_invoice_items_quantity_positive")
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name=op.f("ck_invoice_items_unit_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "amount >= 0", name=op.f("ck_invoice_items_amount_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name=op.f("fk_invoice_items_invoice_id_invoices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_items")),
    )
    op.create_index(
        op.f("ix_invoice_items_invoice_id"),
        "invoice_items",
        ["invoice_id"],
        unique=False,
    )

    op.create_table(
        "invoice_allowances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("sales_return_number", sa.String(length=50), nullable=False),
        sa.Column("allowance_number", sa.String(length=16), nullable=True),
        sa.Column("allowance_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "issued",
                "failed",
                "voided",
                name="invoice_allowance_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("sales_amount", sa.Integer(), nullable=False),
        sa.Column("tax_amount", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("provider_response", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sales_amount >= 0",
            name=op.f("ck_invoice_allowances_sales_amount_nonnegative"),
        ),
        sa.CheckConstraint(
            "tax_amount >= 0",
            name=op.f("ck_invoice_allowances_tax_amount_nonnegative"),
        ),
        sa.CheckConstraint(
            "total_amount >= 0",
            name=op.f("ck_invoice_allowances_total_amount_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name=op.f("fk_invoice_allowances_invoice_id_invoices"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_allowances")),
        sa.UniqueConstraint(
            "allowance_number",
            name=op.f("uq_invoice_allowances_allowance_number"),
        ),
    )
    op.create_index(
        op.f("ix_invoice_allowances_invoice_id"),
        "invoice_allowances",
        ["invoice_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invoice_allowances_sales_return_number"),
        "invoice_allowances",
        ["sales_return_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_invoice_allowances_status"),
        "invoice_allowances",
        ["status"],
        unique=False,
    )

    op.create_table(
        "invoice_allowance_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("allowance_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_item_id", sa.String(length=36), nullable=True),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("tax_amount", sa.Integer(), nullable=False),
        sa.Column("tax_type", sa.String(length=1), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_invoice_allowance_items_quantity_positive"),
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name=op.f("ck_invoice_allowance_items_unit_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name=op.f("ck_invoice_allowance_items_amount_nonnegative"),
        ),
        sa.CheckConstraint(
            "tax_amount >= 0",
            name=op.f("ck_invoice_allowance_items_tax_amount_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["allowance_id"],
            ["invoice_allowances.id"],
            name=op.f(
                "fk_invoice_allowance_items_allowance_id_invoice_allowances"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_item_id"],
            ["invoice_items.id"],
            name=op.f(
                "fk_invoice_allowance_items_invoice_item_id_invoice_items"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_invoice_allowance_items")
        ),
    )
    op.create_index(
        op.f("ix_invoice_allowance_items_allowance_id"),
        "invoice_allowance_items",
        ["allowance_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_invoice_allowance_items_allowance_id"),
        table_name="invoice_allowance_items",
    )
    op.drop_table("invoice_allowance_items")
    op.drop_index(
        op.f("ix_invoice_allowances_status"),
        table_name="invoice_allowances",
    )
    op.drop_index(
        op.f("ix_invoice_allowances_sales_return_number"),
        table_name="invoice_allowances",
    )
    op.drop_index(
        op.f("ix_invoice_allowances_invoice_id"),
        table_name="invoice_allowances",
    )
    op.drop_table("invoice_allowances")
    op.drop_index(
        op.f("ix_invoice_items_invoice_id"), table_name="invoice_items"
    )
    op.drop_table("invoice_items")
    op.drop_index(op.f("ix_invoices_provider_status"), table_name="invoices")
    op.drop_index(
        op.f("ix_invoices_payment_attempt_id"), table_name="invoices"
    )
    op.drop_index(op.f("ix_invoices_provider"), table_name="invoices")
    with op.batch_alter_table("invoices") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_invoices_payment_attempt_id_payment_attempts"),
            type_="foreignkey",
        )
        for column in (
            "void_source",
            "void_reason",
            "voided_at",
            "total_amount",
            "tax_amount",
            "sales_amount",
            "carrier_id",
            "carrier_type",
            "buyer_email",
            "buyer_name",
            "buyer_tax_id",
            "buyer_type",
            "provider_status",
            "payment_attempt_id",
            "provider",
        ):
            batch_op.drop_column(column)
    with op.batch_alter_table("orders") as batch_op:
        for column in (
            "invoice_buyer_email",
            "invoice_buyer_name",
            "invoice_buyer_tax_id",
            "invoice_buyer_type",
        ):
            batch_op.drop_column(column)
