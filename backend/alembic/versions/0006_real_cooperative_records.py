"""Add real cooperative member, supplier, SKU, and pickup records.

Revision ID: 0006_real_cooperative_records
Revises: 0005_trainee_membership
Create Date: 2026-08-16
"""
from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0006_real_cooperative_records"
down_revision = "0005_trainee_membership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in (
        "identity_number_encrypted",
        "gender_encrypted",
        "place_of_origin_encrypted",
        "occupation_encrypted",
        "registered_address_encrypted",
        "correspondence_address_encrypted",
        "landline_phone_encrypted",
        "line_id_encrypted",
    ):
        op.add_column(
            "member_profiles",
            sa.Column(name, sa.Text(), nullable=True),
        )

    op.add_column(
        "memberships",
        sa.Column("share_certificate_number", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "memberships",
        sa.Column(
            "share_capital_amount",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "memberships",
        sa.Column("share_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "memberships",
        sa.Column("share_subscribed_on", sa.Date(), nullable=True),
    )
    op.add_column(
        "memberships",
        sa.Column("share_paid_on", sa.Date(), nullable=True),
    )
    op.create_index(
        op.f("ix_memberships_share_certificate_number"),
        "memberships",
        ["share_certificate_number"],
        unique=True,
    )

    op.create_table(
        "pickup_locations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pickup_locations")),
    )
    op.create_index(
        op.f("ix_pickup_locations_code"),
        "pickup_locations",
        ["code"],
        unique=True,
    )
    op.create_index(
        op.f("ix_pickup_locations_name"),
        "pickup_locations",
        ["name"],
        unique=True,
    )
    op.create_index(
        op.f("ix_pickup_locations_is_active"),
        "pickup_locations",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("supplier_number", sa.String(length=40), nullable=True),
        sa.Column("business_name", sa.String(length=160), nullable=False),
        sa.Column("tax_id", sa.String(length=20), nullable=True),
        sa.Column("responsible_person_encrypted", sa.Text(), nullable=False),
        sa.Column("contact_person_encrypted", sa.Text(), nullable=False),
        sa.Column("phone_encrypted", sa.Text(), nullable=False),
        sa.Column("email_encrypted", sa.Text(), nullable=False),
        sa.Column("line_id_encrypted", sa.Text(), nullable=True),
        sa.Column("settlement_terms", sa.Text(), nullable=False),
        sa.Column("bank_account_encrypted", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=32), nullable=False),
        sa.Column("accredited_on", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
    )
    op.create_index(
        op.f("ix_suppliers_supplier_number"),
        "suppliers",
        ["supplier_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_suppliers_business_name"),
        "suppliers",
        ["business_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_suppliers_tax_id"),
        "suppliers",
        ["tax_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_suppliers_is_active"),
        "suppliers",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "supplier_accreditations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("reviewed_on", sa.Date(), nullable=False),
        sa.Column("reviewer_id", sa.String(length=36), nullable=False),
        sa.Column("process_notes", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                name="supplier_accreditation_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("result_notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_supplier_accreditations_reviewer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_supplier_accreditations_supplier_id_suppliers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_accreditations")),
    )
    op.create_index(
        op.f("ix_supplier_accreditations_supplier_id"),
        "supplier_accreditations",
        ["supplier_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_accreditations_reviewer_id"),
        "supplier_accreditations",
        ["reviewer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_accreditations_reviewed_on"),
        "supplier_accreditations",
        ["reviewed_on"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_accreditations_status"),
        "supplier_accreditations",
        ["status"],
        unique=False,
    )

    op.create_table(
        "supplier_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("supplier_id", sa.String(length=36), nullable=False),
        sa.Column("accreditation_id", sa.String(length=36), nullable=True),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes <= 8388608",
            name=op.f("ck_supplier_documents_size_at_most_8mb"),
        ),
        sa.CheckConstraint(
            "size_bytes > 0",
            name=op.f("ck_supplier_documents_size_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["accreditation_id"],
            ["supplier_accreditations.id"],
            name=op.f(
                "fk_supplier_documents_accreditation_id_supplier_accreditations"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["suppliers.id"],
            name=op.f("fk_supplier_documents_supplier_id_suppliers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_documents")),
        sa.UniqueConstraint("object_key", name=op.f("uq_supplier_documents_object_key")),
    )
    op.create_index(
        op.f("ix_supplier_documents_supplier_id"),
        "supplier_documents",
        ["supplier_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_documents_accreditation_id"),
        "supplier_documents",
        ["accreditation_id"],
        unique=False,
    )

    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(
            sa.Column("product_number", sa.String(length=40), nullable=True),
        )
        batch_op.add_column(
            sa.Column("sku", sa.String(length=80), nullable=True),
        )
        batch_op.add_column(
            sa.Column("supplier_id", sa.String(length=36), nullable=True),
        )
        batch_op.create_index(
            op.f("ix_products_product_number"),
            ["product_number"],
            unique=True,
        )
        batch_op.create_index(op.f("ix_products_sku"), ["sku"], unique=True)
        batch_op.create_index(
            op.f("ix_products_supplier_id"),
            ["supplier_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            op.f("fk_products_supplier_id_suppliers"),
            "suppliers",
            ["supplier_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.add_column(
        "orders",
        sa.Column("tax_amount", sa.Integer(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("order_fulfillments") as batch_op:
        batch_op.add_column(
            sa.Column("pickup_location_id", sa.String(length=36), nullable=True),
        )
        batch_op.create_index(
            op.f("ix_order_fulfillments_pickup_location_id"),
            ["pickup_location_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            op.f("fk_order_fulfillments_pickup_location_id_pickup_locations"),
            "pickup_locations",
            ["pickup_location_id"],
            ["id"],
            ondelete="SET NULL",
        )

    pickup_locations = sa.table(
        "pickup_locations",
        sa.column("id", sa.String(length=36)),
        sa.column("code", sa.String(length=40)),
        sa.column("name", sa.String(length=160)),
        sa.column("address", sa.String(length=500)),
        sa.column("instructions", sa.Text()),
        sa.column("sort_order", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    current = datetime.now(timezone.utc)
    op.bulk_insert(
        pickup_locations,
        [
            {
                "id": "pickup-coop-store",
                "code": "coop-store",
                "name": "合作社門市（水木書苑內左側）",
                "address": "",
                "instructions": "",
                "sort_order": 10,
                "is_active": True,
                "created_at": current,
                "updated_at": current,
            },
            {
                "id": "pickup-tsmc-building",
                "code": "tsmc-building",
                "name": "台積館",
                "address": "",
                "instructions": "",
                "sort_order": 20,
                "is_active": True,
                "created_at": current,
                "updated_at": current,
            },
            {
                "id": "pickup-education-building",
                "code": "education-building",
                "name": "教育學院大樓",
                "address": "",
                "instructions": "",
                "sort_order": 30,
                "is_active": True,
                "created_at": current,
                "updated_at": current,
            },
            {
                "id": "pickup-humanities-building",
                "code": "humanities-building",
                "name": "人社院",
                "address": "",
                "instructions": "",
                "sort_order": 40,
                "is_active": True,
                "created_at": current,
                "updated_at": current,
            },
            {
                "id": "pickup-incubation-center",
                "code": "incubation-center",
                "name": "創新育成大樓",
                "address": "",
                "instructions": "",
                "sort_order": 50,
                "is_active": True,
                "created_at": current,
                "updated_at": current,
            },
        ],
    )


def downgrade() -> None:
    with op.batch_alter_table("order_fulfillments") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_order_fulfillments_pickup_location_id_pickup_locations"),
            type_="foreignkey",
        )
        batch_op.drop_index(
            op.f("ix_order_fulfillments_pickup_location_id"),
        )
        batch_op.drop_column("pickup_location_id")
    op.drop_column("orders", "tax_amount")

    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_products_supplier_id_suppliers"),
            type_="foreignkey",
        )
        batch_op.drop_index(op.f("ix_products_supplier_id"))
        batch_op.drop_index(op.f("ix_products_sku"))
        batch_op.drop_index(op.f("ix_products_product_number"))
        batch_op.drop_column("supplier_id")
        batch_op.drop_column("sku")
        batch_op.drop_column("product_number")

    op.drop_index(
        op.f("ix_supplier_documents_accreditation_id"),
        table_name="supplier_documents",
    )
    op.drop_index(
        op.f("ix_supplier_documents_supplier_id"),
        table_name="supplier_documents",
    )
    op.drop_table("supplier_documents")
    op.drop_index(
        op.f("ix_supplier_accreditations_status"),
        table_name="supplier_accreditations",
    )
    op.drop_index(
        op.f("ix_supplier_accreditations_reviewed_on"),
        table_name="supplier_accreditations",
    )
    op.drop_index(
        op.f("ix_supplier_accreditations_reviewer_id"),
        table_name="supplier_accreditations",
    )
    op.drop_index(
        op.f("ix_supplier_accreditations_supplier_id"),
        table_name="supplier_accreditations",
    )
    op.drop_table("supplier_accreditations")
    op.drop_index(op.f("ix_suppliers_is_active"), table_name="suppliers")
    op.drop_index(op.f("ix_suppliers_tax_id"), table_name="suppliers")
    op.drop_index(op.f("ix_suppliers_business_name"), table_name="suppliers")
    op.drop_index(op.f("ix_suppliers_supplier_number"), table_name="suppliers")
    op.drop_table("suppliers")

    op.drop_index(
        op.f("ix_pickup_locations_is_active"), table_name="pickup_locations"
    )
    op.drop_index(op.f("ix_pickup_locations_name"), table_name="pickup_locations")
    op.drop_index(op.f("ix_pickup_locations_code"), table_name="pickup_locations")
    op.drop_table("pickup_locations")

    op.drop_index(
        op.f("ix_memberships_share_certificate_number"),
        table_name="memberships",
    )
    op.drop_column("memberships", "share_paid_on")
    op.drop_column("memberships", "share_subscribed_on")
    op.drop_column("memberships", "share_count")
    op.drop_column("memberships", "share_capital_amount")
    op.drop_column("memberships", "share_certificate_number")

    for name in reversed(
        (
            "identity_number_encrypted",
            "gender_encrypted",
            "place_of_origin_encrypted",
            "occupation_encrypted",
            "registered_address_encrypted",
            "correspondence_address_encrypted",
            "landline_phone_encrypted",
            "line_id_encrypted",
        )
    ):
        op.drop_column("member_profiles", name)
