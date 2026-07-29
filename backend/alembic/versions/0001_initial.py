"""Create the initial 十里方圓 sandbox schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("external_event_key", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_events")),
        sa.UniqueConstraint(
            "provider",
            "external_event_key",
            name=op.f("uq_external_events_provider"),
        ),
    )
    op.create_index(
        op.f("ix_external_events_processed"),
        "external_events",
        ["processed"],
        unique=False,
    )
    op.create_index(
        op.f("ix_external_events_provider"),
        "external_events",
        ["provider"],
        unique=False,
    )

    op.create_table(
        "group_bundles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("member_price", sa.Integer(), nullable=False),
        sa.Column("nonmember_price", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "member_price >= 0",
            name=op.f("ck_group_bundles_member_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "member_price <= nonmember_price",
            name=op.f(
                "ck_group_bundles_member_price_not_above_nonmember_price"
            ),
        ),
        sa.CheckConstraint(
            "nonmember_price >= 0",
            name=op.f("ck_group_bundles_nonmember_price_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_bundles")),
        sa.UniqueConstraint("name", name=op.f("uq_group_bundles_name")),
    )
    op.create_index(
        op.f("ix_group_bundles_is_active"),
        "group_bundles",
        ["is_active"],
        unique=False,
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "completed",
                "failed",
                name="outbox_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        op.f("ix_outbox_events_aggregate_id"),
        "outbox_events",
        ["aggregate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_available_at"),
        "outbox_events",
        ["available_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_event_type"),
        "outbox_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outbox_events_status"),
        "outbox_events",
        ["status"],
        unique=False,
    )

    op.create_table(
        "products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("member_price", sa.Integer(), nullable=False),
        sa.Column("nonmember_price", sa.Integer(), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "tax_type",
            sa.Enum(
                "taxable",
                "tax_exempt",
                name="tax_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "member_price >= 0",
            name=op.f("ck_products_member_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "nonmember_price >= 0",
            name=op.f("ck_products_nonmember_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "member_price <= nonmember_price",
            name=op.f(
                "ck_products_member_price_not_above_nonmember_price"
            ),
        ),
        sa.CheckConstraint(
            "stock_quantity >= 0",
            name=op.f("ck_products_stock_quantity_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(
        op.f("ix_products_category"),
        "products",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_products_is_active"),
        "products",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_products_slug"),
        "products",
        ["slug"],
        unique=True,
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "user_role",
            sa.Enum(
                "customer",
                "admin",
                name="user_role",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "membership_type",
            sa.Enum(
                "member",
                "nonmember",
                name="membership_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "admin_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_admin_audits_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audits")),
    )
    op.create_index(
        op.f("ix_admin_audits_action"),
        "admin_audits",
        ["action"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_audits_actor_id"),
        "admin_audits",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_audits_aggregate_id"),
        "admin_audits",
        ["aggregate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_audits_created_at"),
        "admin_audits",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "group_bundle_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("bundle_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_group_bundle_items_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["bundle_id"],
            ["group_bundles.id"],
            name=op.f("fk_group_bundle_items_bundle_id_group_bundles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_group_bundle_items_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_bundle_items")),
        sa.UniqueConstraint(
            "bundle_id",
            "product_id",
            name=op.f("uq_group_bundle_items_bundle_id"),
        ),
    )
    op.create_index(
        op.f("ix_group_bundle_items_bundle_id"),
        "group_bundle_items",
        ["bundle_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_group_bundle_items_product_id"),
        "group_bundle_items",
        ["product_id"],
        unique=False,
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        op.f("ix_notifications_created_at"),
        "notifications",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_event_type"),
        "notifications",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_read_at"),
        "notifications",
        ["read_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_user_id"),
        "notifications",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "vote_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proposer_id", sa.String(length=36), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "product",
                "bundle",
                name="proposal_target_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("target_name_snapshot", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_review",
                "voting",
                "ended_unmet",
                "conversion_pending",
                "converted",
                "rejected",
                "expired_unhandled",
                name="proposal_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "conversion_deadline",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("frozen_vote_count", sa.Integer(), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by_id", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "threshold > 0",
            name=op.f("ck_vote_proposals_threshold_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["proposer_id"],
            ["users.id"],
            name=op.f("fk_vote_proposals_proposer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f("fk_vote_proposals_reviewed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vote_proposals")),
    )
    op.create_index(
        op.f("ix_vote_proposals_conversion_deadline"),
        "vote_proposals",
        ["conversion_deadline"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vote_proposals_deadline"),
        "vote_proposals",
        ["deadline"],
        unique=False,
    )
    op.create_index(
        "ix_vote_proposals_open_target",
        "vote_proposals",
        ["target_type", "target_id", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vote_proposals_proposer_id"),
        "vote_proposals",
        ["proposer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vote_proposals_status"),
        "vote_proposals",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vote_proposals_target_id"),
        "vote_proposals",
        ["target_id"],
        unique=False,
    )

    op.create_table(
        "group_campaigns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_proposal_id", sa.String(length=36), nullable=True),
        sa.Column(
            "target_type",
            sa.Enum(
                "product",
                "bundle",
                name="campaign_target_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("member_price", sa.Integer(), nullable=False),
        sa.Column("nonmember_price", sa.Integer(), nullable=False),
        sa.Column("min_paid_quantity", sa.Integer(), nullable=False),
        sa.Column("supply_cap", sa.Integer(), nullable=False),
        sa.Column("per_user_cap", sa.Integer(), nullable=False),
        sa.Column("paid_quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "estimated_pickup_start",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "estimated_pickup_end",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("final_pickup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "decision_status",
            sa.Enum(
                "recruiting",
                "pending_confirmation",
                "confirmed",
                "rejected",
                "failed_unmet",
                "expired_unconfirmed",
                "cancelled",
                name="group_decision_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "intake_status",
            sa.Enum(
                "open",
                "paused",
                "settling",
                "full",
                "closed",
                name="group_intake_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("core_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confirmation_deadline",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("threshold_version", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "member_price >= 0",
            name=op.f("ck_group_campaigns_member_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "member_price <= nonmember_price",
            name=op.f(
                "ck_group_campaigns_member_price_not_above_nonmember_price"
            ),
        ),
        sa.CheckConstraint(
            "min_paid_quantity <= supply_cap",
            name=op.f("ck_group_campaigns_minimum_within_supply_cap"),
        ),
        sa.CheckConstraint(
            "min_paid_quantity > 0",
            name=op.f("ck_group_campaigns_min_paid_quantity_positive"),
        ),
        sa.CheckConstraint(
            "nonmember_price >= 0",
            name=op.f("ck_group_campaigns_nonmember_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "per_user_cap > 0",
            name=op.f("ck_group_campaigns_per_user_cap_positive"),
        ),
        sa.CheckConstraint(
            "supply_cap > 0",
            name=op.f("ck_group_campaigns_supply_cap_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_group_campaigns_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_proposal_id"],
            ["vote_proposals.id"],
            name=op.f(
                "fk_group_campaigns_source_proposal_id_vote_proposals"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_campaigns")),
        sa.UniqueConstraint(
            "source_proposal_id",
            name=op.f("uq_group_campaigns_source_proposal_id"),
        ),
    )
    op.create_index(
        op.f("ix_group_campaigns_confirmation_deadline"),
        "group_campaigns",
        ["confirmation_deadline"],
        unique=False,
    )
    op.create_index(
        op.f("ix_group_campaigns_deadline"),
        "group_campaigns",
        ["deadline"],
        unique=False,
    )
    op.create_index(
        op.f("ix_group_campaigns_decision_status"),
        "group_campaigns",
        ["decision_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_group_campaigns_intake_status"),
        "group_campaigns",
        ["intake_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_group_campaigns_target_id"),
        "group_campaigns",
        ["target_id"],
        unique=False,
    )

    op.create_table(
        "votes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("estimated_quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "estimated_quantity > 0",
            name=op.f("ck_votes_estimated_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["vote_proposals.id"],
            name=op.f("fk_votes_proposal_id_vote_proposals"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_votes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_votes")),
        sa.UniqueConstraint(
            "proposal_id",
            "user_id",
            name=op.f("uq_votes_proposal_id"),
        ),
    )
    op.create_index(
        op.f("ix_votes_proposal_id"),
        "votes",
        ["proposal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_votes_user_id"),
        "votes",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_number", sa.String(length=32), nullable=False),
        sa.Column(
            "order_kind",
            sa.Enum(
                "regular",
                "group",
                name="order_kind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("group_campaign_id", sa.String(length=36), nullable=True),
        sa.Column(
            "membership_type_snapshot",
            sa.Enum(
                "member",
                "nonmember",
                name="order_membership_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("amount_total", sa.Integer(), nullable=False),
        sa.Column("contact_email", sa.String(length=320), nullable=False),
        sa.Column(
            "invoice_carrier_type",
            sa.Enum(
                "ecpay",
                "mobile_barcode",
                name="invoice_carrier_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("invoice_carrier_value", sa.String(length=64), nullable=True),
        sa.Column(
            "fulfillment_status",
            sa.Enum(
                "pending_confirmation",
                "preparing",
                "ready_for_pickup",
                "picked_up",
                "cancelled",
                name="fulfillment_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "payment_status",
            sa.Enum(
                "pending",
                "paid",
                "late_paid_refund_required",
                "refund_pending",
                "refunded",
                "failed",
                "expired",
                name="payment_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "invoice_status",
            sa.Enum(
                "not_eligible",
                "pending",
                "issued",
                "failed",
                name="invoice_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_total >= 0",
            name=op.f("ck_orders_amount_total_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["group_campaign_id"],
            ["group_campaigns.id"],
            name=op.f("fk_orders_group_campaign_id_group_campaigns"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_orders_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(
        op.f("ix_orders_created_at"),
        "orders",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_fulfillment_status"),
        "orders",
        ["fulfillment_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_group_campaign_id"),
        "orders",
        ["group_campaign_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_invoice_status"),
        "orders",
        ["invoice_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_order_kind"),
        "orders",
        ["order_kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_order_number"),
        "orders",
        ["order_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_orders_payment_status"),
        "orders",
        ["payment_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_user_id"),
        "orders",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("relate_number", sa.String(length=30), nullable=False),
        sa.Column("invoice_number", sa.String(length=24), nullable=True),
        sa.Column("invoice_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("random_number", sa.String(length=8), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "not_eligible",
                "pending",
                "issued",
                "failed",
                name="invoice_record_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("provider_response", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_invoices_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint(
            "invoice_number",
            name=op.f("uq_invoices_invoice_number"),
        ),
    )
    op.create_index(
        op.f("ix_invoices_order_id"),
        "invoices",
        ["order_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_invoices_relate_number"),
        "invoices",
        ["relate_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_invoices_status"),
        "invoices",
        ["status"],
        unique=False,
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("source_product_id", sa.String(length=36), nullable=True),
        sa.Column("source_bundle_id", sa.String(length=36), nullable=True),
        sa.Column("product_name", sa.String(length=120), nullable=False),
        sa.Column("unit_label", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("subtotal", sa.Integer(), nullable=False),
        sa.Column(
            "tax_type",
            sa.Enum(
                "taxable",
                "tax_exempt",
                name="order_tax_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_order_items_quantity_positive"),
        ),
        sa.CheckConstraint(
            "subtotal >= 0",
            name=op.f("ck_order_items_subtotal_nonnegative"),
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name=op.f("ck_order_items_unit_price_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_bundle_id"],
            ["group_bundles.id"],
            name=op.f("fk_order_items_source_bundle_id_group_bundles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_product_id"],
            ["products.id"],
            name=op.f("fk_order_items_source_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index(
        op.f("ix_order_items_order_id"),
        "order_items",
        ["order_id"],
        unique=False,
    )

    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("merchant_trade_no", sa.String(length=20), nullable=False),
        sa.Column("provider_trade_no", sa.String(length=64), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "paid",
                "late_paid_refund_required",
                "refund_pending",
                "refunded",
                "failed",
                "expired",
                name="payment_attempt_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("checkout_payload", sa.JSON(), nullable=False),
        sa.Column("provider_response", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount >= 0",
            name=op.f("ck_payment_attempts_amount_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_payment_attempts_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
    )
    op.create_index(
        op.f("ix_payment_attempts_expires_at"),
        "payment_attempts",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_attempts_merchant_trade_no"),
        "payment_attempts",
        ["merchant_trade_no"],
        unique=True,
    )
    op.create_index(
        op.f("ix_payment_attempts_order_id"),
        "payment_attempts",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_attempts_provider_trade_no"),
        "payment_attempts",
        ["provider_trade_no"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_attempts_status"),
        "payment_attempts",
        ["status"],
        unique=False,
    )

    op.create_table(
        "refunds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "completed",
                "failed",
                name="refund_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by_id", sa.String(length=36), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount >= 0",
            name=op.f("ck_refunds_amount_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_refunds_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_refunds_requested_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
    )
    op.create_index(
        op.f("ix_refunds_order_id"),
        "refunds",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refunds_status"),
        "refunds",
        ["status"],
        unique=False,
    )

    op.create_table(
        "inventory_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("group_campaign_id", sa.String(length=36), nullable=True),
        sa.Column("source_product_id", sa.String(length=36), nullable=True),
        sa.Column("payment_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "consumed",
                "released",
                "expired",
                name="reservation_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck_inventory_reservations_quantity_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["group_campaign_id"],
            ["group_campaigns.id"],
            name=op.f(
                "fk_inventory_reservations_group_campaign_id_group_campaigns"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_inventory_reservations_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"],
            ["payment_attempts.id"],
            name=op.f(
                "fk_inventory_reservations_payment_attempt_id_payment_attempts"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_product_id"],
            ["products.id"],
            name=op.f(
                "fk_inventory_reservations_source_product_id_products"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_inventory_reservations"),
        ),
    )
    op.create_index(
        op.f("ix_inventory_reservations_expires_at"),
        "inventory_reservations",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_reservations_group_campaign_id"),
        "inventory_reservations",
        ["group_campaign_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_reservations_order_id"),
        "inventory_reservations",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_reservations_payment_attempt_id"),
        "inventory_reservations",
        ["payment_attempt_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_reservations_source_product_id"),
        "inventory_reservations",
        ["source_product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_reservations_status"),
        "inventory_reservations",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_inventory_reservations_status"),
        table_name="inventory_reservations",
    )
    op.drop_index(
        op.f("ix_inventory_reservations_source_product_id"),
        table_name="inventory_reservations",
    )
    op.drop_index(
        op.f("ix_inventory_reservations_payment_attempt_id"),
        table_name="inventory_reservations",
    )
    op.drop_index(
        op.f("ix_inventory_reservations_order_id"),
        table_name="inventory_reservations",
    )
    op.drop_index(
        op.f("ix_inventory_reservations_group_campaign_id"),
        table_name="inventory_reservations",
    )
    op.drop_index(
        op.f("ix_inventory_reservations_expires_at"),
        table_name="inventory_reservations",
    )
    op.drop_table("inventory_reservations")

    op.drop_index(op.f("ix_refunds_status"), table_name="refunds")
    op.drop_index(op.f("ix_refunds_order_id"), table_name="refunds")
    op.drop_table("refunds")

    op.drop_index(
        op.f("ix_payment_attempts_status"),
        table_name="payment_attempts",
    )
    op.drop_index(
        op.f("ix_payment_attempts_provider_trade_no"),
        table_name="payment_attempts",
    )
    op.drop_index(
        op.f("ix_payment_attempts_order_id"),
        table_name="payment_attempts",
    )
    op.drop_index(
        op.f("ix_payment_attempts_merchant_trade_no"),
        table_name="payment_attempts",
    )
    op.drop_index(
        op.f("ix_payment_attempts_expires_at"),
        table_name="payment_attempts",
    )
    op.drop_table("payment_attempts")

    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")

    op.drop_index(op.f("ix_invoices_status"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_relate_number"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_order_id"), table_name="invoices")
    op.drop_table("invoices")

    op.drop_index(op.f("ix_orders_user_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_payment_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_order_number"), table_name="orders")
    op.drop_index(op.f("ix_orders_order_kind"), table_name="orders")
    op.drop_index(op.f("ix_orders_invoice_status"), table_name="orders")
    op.drop_index(
        op.f("ix_orders_group_campaign_id"),
        table_name="orders",
    )
    op.drop_index(
        op.f("ix_orders_fulfillment_status"),
        table_name="orders",
    )
    op.drop_index(op.f("ix_orders_created_at"), table_name="orders")
    op.drop_table("orders")

    op.drop_index(op.f("ix_votes_user_id"), table_name="votes")
    op.drop_index(op.f("ix_votes_proposal_id"), table_name="votes")
    op.drop_table("votes")

    op.drop_index(
        op.f("ix_group_campaigns_target_id"),
        table_name="group_campaigns",
    )
    op.drop_index(
        op.f("ix_group_campaigns_intake_status"),
        table_name="group_campaigns",
    )
    op.drop_index(
        op.f("ix_group_campaigns_decision_status"),
        table_name="group_campaigns",
    )
    op.drop_index(
        op.f("ix_group_campaigns_deadline"),
        table_name="group_campaigns",
    )
    op.drop_index(
        op.f("ix_group_campaigns_confirmation_deadline"),
        table_name="group_campaigns",
    )
    op.drop_table("group_campaigns")

    op.drop_index(
        op.f("ix_vote_proposals_target_id"),
        table_name="vote_proposals",
    )
    op.drop_index(
        op.f("ix_vote_proposals_status"),
        table_name="vote_proposals",
    )
    op.drop_index(
        op.f("ix_vote_proposals_proposer_id"),
        table_name="vote_proposals",
    )
    op.drop_index(
        "ix_vote_proposals_open_target",
        table_name="vote_proposals",
    )
    op.drop_index(
        op.f("ix_vote_proposals_deadline"),
        table_name="vote_proposals",
    )
    op.drop_index(
        op.f("ix_vote_proposals_conversion_deadline"),
        table_name="vote_proposals",
    )
    op.drop_table("vote_proposals")

    op.drop_index(
        op.f("ix_notifications_user_id"),
        table_name="notifications",
    )
    op.drop_index(
        op.f("ix_notifications_read_at"),
        table_name="notifications",
    )
    op.drop_index(
        op.f("ix_notifications_event_type"),
        table_name="notifications",
    )
    op.drop_index(
        op.f("ix_notifications_created_at"),
        table_name="notifications",
    )
    op.drop_table("notifications")

    op.drop_index(
        op.f("ix_group_bundle_items_product_id"),
        table_name="group_bundle_items",
    )
    op.drop_index(
        op.f("ix_group_bundle_items_bundle_id"),
        table_name="group_bundle_items",
    )
    op.drop_table("group_bundle_items")

    op.drop_index(
        op.f("ix_admin_audits_created_at"),
        table_name="admin_audits",
    )
    op.drop_index(
        op.f("ix_admin_audits_aggregate_id"),
        table_name="admin_audits",
    )
    op.drop_index(
        op.f("ix_admin_audits_actor_id"),
        table_name="admin_audits",
    )
    op.drop_index(
        op.f("ix_admin_audits_action"),
        table_name="admin_audits",
    )
    op.drop_table("admin_audits")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    op.drop_index(op.f("ix_products_slug"), table_name="products")
    op.drop_index(op.f("ix_products_is_active"), table_name="products")
    op.drop_index(op.f("ix_products_category"), table_name="products")
    op.drop_table("products")

    op.drop_index(
        op.f("ix_outbox_events_status"),
        table_name="outbox_events",
    )
    op.drop_index(
        op.f("ix_outbox_events_event_type"),
        table_name="outbox_events",
    )
    op.drop_index(
        op.f("ix_outbox_events_available_at"),
        table_name="outbox_events",
    )
    op.drop_index(
        op.f("ix_outbox_events_aggregate_id"),
        table_name="outbox_events",
    )
    op.drop_table("outbox_events")

    op.drop_index(
        op.f("ix_group_bundles_is_active"),
        table_name="group_bundles",
    )
    op.drop_table("group_bundles")

    op.drop_index(
        op.f("ix_external_events_provider"),
        table_name="external_events",
    )
    op.drop_index(
        op.f("ix_external_events_processed"),
        table_name="external_events",
    )
    op.drop_table("external_events")
