"""Add v2 membership, community, meal, and logistics domains.

Revision ID: 0002_v2_social_commerce
Revises: 0001_initial
Create Date: 2026-07-30
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0002_v2_social_commerce"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def enum_type(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def new_id() -> str:
    return str(uuid.uuid4())


def create_index(table: str, column: str, *, unique: bool = False) -> None:
    op.create_index(op.f(f"ix_{table}_{column}"), table, [column], unique=unique)


def drop_index(table: str, column: str) -> None:
    op.drop_index(op.f(f"ix_{table}_{column}"), table_name=table)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE users SET email_verified_at = created_at "
            "WHERE email_verified_at IS NULL"
        )
    )

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_verification_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_email_verification_tokens")
        ),
    )
    create_index("email_verification_tokens", "user_id")
    create_index("email_verification_tokens", "token_hash", unique=True)
    create_index("email_verification_tokens", "expires_at")

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_reset_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
    )
    create_index("password_reset_tokens", "user_id")
    create_index("password_reset_tokens", "token_hash", unique=True)
    create_index("password_reset_tokens", "expires_at")

    op.create_table(
        "member_profiles",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("legal_name_encrypted", sa.Text(), nullable=False),
        sa.Column("phone_encrypted", sa.Text(), nullable=False),
        sa.Column("birth_date_encrypted", sa.Text(), nullable=False),
        sa.Column("address_encrypted", sa.Text(), nullable=False),
        sa.Column("emergency_contact_encrypted", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(32), nullable=False),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_member_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_profiles")),
    )
    create_index("member_profiles", "user_id", unique=True)

    op.create_table(
        "membership_applications",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column(
            "status",
            enum_type(
                "membership_application_status",
                "draft",
                "submitted",
                "needs_supplement",
                "approved",
                "rejected",
                "withdrawn",
            ),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f(
                "fk_membership_applications_reviewed_by_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_membership_applications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_membership_applications")
        ),
    )
    create_index("membership_applications", "user_id", unique=True)
    create_index("membership_applications", "status")
    create_index("membership_applications", "reviewed_by_id")

    op.create_table(
        "memberships",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("application_id", sa.String(36), nullable=True),
        sa.Column("member_number", sa.String(32), nullable=True),
        sa.Column(
            "status",
            enum_type(
                "membership_status",
                "pending_payment",
                "active",
                "suspended",
                "resigned",
                "terminated",
            ),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["membership_applications.id"],
            name=op.f("fk_memberships_application_id_membership_applications"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint(
            "application_id", name=op.f("uq_memberships_application_id")
        ),
    )
    create_index("memberships", "user_id", unique=True)
    create_index("memberships", "member_number", unique=True)
    create_index("memberships", "status")

    op.create_table(
        "membership_documents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("application_id", sa.String(36), nullable=False),
        sa.Column(
            "document_type",
            enum_type(
                "membership_document_type",
                "id_front",
                "id_back",
                "secondary",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            enum_type(
                "membership_document_status",
                "pending_upload",
                "confirmed",
                "deleted",
            ),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes > 0",
            name=op.f("ck_membership_documents_size_positive"),
        ),
        sa.CheckConstraint(
            "size_bytes <= 8388608",
            name=op.f("ck_membership_documents_size_at_most_8mb"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["membership_applications.id"],
            name=op.f(
                "fk_membership_documents_application_id_membership_applications"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership_documents")),
        sa.UniqueConstraint(
            "application_id",
            "document_type",
            name=op.f("uq_membership_documents_application_id"),
        ),
        sa.UniqueConstraint(
            "object_key", name=op.f("uq_membership_documents_object_key")
        ),
    )
    create_index("membership_documents", "application_id")
    create_index("membership_documents", "status")

    op.create_table(
        "membership_fee_schedules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "charge_kind",
            enum_type(
                "membership_fee_kind", "admission_fee", "share_capital"
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount >= 0",
            name=op.f("ck_membership_fee_schedules_amount_nonnegative"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f(
                "ck_membership_fee_schedules_effective_range_valid"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_membership_fee_schedules")
        ),
        sa.UniqueConstraint(
            "charge_kind",
            "effective_from",
            name=op.f("uq_membership_fee_schedules_charge_kind"),
        ),
    )
    create_index("membership_fee_schedules", "charge_kind")
    create_index("membership_fee_schedules", "effective_from")
    create_index("membership_fee_schedules", "is_active")

    op.create_table(
        "membership_charges",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("application_id", sa.String(36), nullable=False),
        sa.Column("membership_id", sa.String(36), nullable=False),
        sa.Column("fee_schedule_id", sa.String(36), nullable=False),
        sa.Column(
            "charge_kind",
            enum_type(
                "membership_charge_kind", "admission_fee", "share_capital"
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            enum_type(
                "membership_charge_status",
                "pending",
                "paid",
                "refund_pending",
                "refunded",
                "waived",
            ),
            nullable=False,
        ),
        sa.Column("receipt_number", sa.String(40), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount >= 0",
            name=op.f("ck_membership_charges_amount_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["membership_applications.id"],
            name=op.f(
                "fk_membership_charges_application_id_membership_applications"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fee_schedule_id"],
            ["membership_fee_schedules.id"],
            name=op.f(
                "fk_membership_charges_fee_schedule_id_membership_fee_schedules"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name=op.f("fk_membership_charges_membership_id_memberships"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_membership_charges_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_membership_charges")),
        sa.UniqueConstraint(
            "application_id",
            "charge_kind",
            name=op.f("uq_membership_charges_application_id"),
        ),
        sa.UniqueConstraint(
            "receipt_number",
            name=op.f("uq_membership_charges_receipt_number"),
        ),
    )
    for column in (
        "user_id",
        "application_id",
        "membership_id",
        "fee_schedule_id",
        "charge_kind",
        "status",
    ):
        create_index("membership_charges", column)

    op.create_table(
        "member_directory_entries",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("nickname", sa.String(80), nullable=False),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("expertise", sa.String(240), nullable=False),
        sa.Column("bio", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_member_directory_entries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_member_directory_entries")
        ),
    )
    create_index("member_directory_entries", "user_id", unique=True)
    create_index("member_directory_entries", "is_public")

    op.create_table(
        "activities",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("created_by_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(500), nullable=True),
        sa.Column("location", sa.String(240), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "registration_deadline",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("waitlist_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            enum_type(
                "activity_status",
                "draft",
                "pending_review",
                "published",
                "rejected",
                "cancelled",
                "completed",
            ),
            nullable=False,
        ),
        sa.Column("reviewed_by_id", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capacity > 0", name=op.f("ck_activities_capacity_positive")
        ),
        sa.CheckConstraint(
            "ends_at > starts_at",
            name=op.f("ck_activities_time_range_valid"),
        ),
        sa.CheckConstraint(
            "registration_deadline <= starts_at",
            name=op.f("ck_activities_registration_before_start"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_activities_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f("fk_activities_reviewed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activities")),
    )
    for column in (
        "created_by_id",
        "starts_at",
        "registration_deadline",
        "status",
        "reviewed_by_id",
    ):
        create_index("activities", column)

    op.create_table(
        "activity_registrations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("activity_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column(
            "status",
            enum_type(
                "activity_registration_status",
                "registered",
                "waitlisted",
                "cancelled",
                "attended",
                "no_show",
            ),
            nullable=False,
        ),
        sa.Column("queue_position", sa.Integer(), nullable=False),
        sa.Column(
            "registered_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "queue_position > 0",
            name=op.f(
                "ck_activity_registrations_queue_position_positive"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activities.id"],
            name=op.f(
                "fk_activity_registrations_activity_id_activities"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_activity_registrations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_activity_registrations")
        ),
        sa.UniqueConstraint(
            "activity_id",
            "user_id",
            name=op.f("uq_activity_registrations_activity_id"),
        ),
    )
    for column in ("activity_id", "user_id", "status"):
        create_index("activity_registrations", column)

    op.create_table(
        "member_proposals",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("created_by_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            enum_type(
                "member_proposal_status",
                "draft",
                "pending_review",
                "discussion",
                "voting",
                "passed",
                "rejected",
                "withdrawn",
                "closed",
            ),
            nullable=False,
        ),
        sa.Column("minimum_voters", sa.Integer(), nullable=False),
        sa.Column(
            "discussion_ends_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("voting_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", sa.String(36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "minimum_voters > 0",
            name=op.f("ck_member_proposals_minimum_voters_positive"),
        ),
        sa.CheckConstraint(
            "discussion_ends_at IS NULL OR voting_ends_at IS NULL "
            "OR voting_ends_at > discussion_ends_at",
            name=op.f("ck_member_proposals_proposal_timeline_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_member_proposals_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f("fk_member_proposals_reviewed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_proposals")),
    )
    for column in (
        "created_by_id",
        "status",
        "discussion_ends_at",
        "voting_ends_at",
        "reviewed_by_id",
    ):
        create_index("member_proposals", column)

    op.create_table(
        "member_proposal_comments",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["member_proposals.id"],
            name=op.f(
                "fk_member_proposal_comments_proposal_id_member_proposals"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_member_proposal_comments_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_member_proposal_comments")
        ),
    )
    for column in ("proposal_id", "user_id", "created_at"):
        create_index("member_proposal_comments", column)

    op.create_table(
        "member_proposal_votes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column(
            "choice",
            enum_type("member_vote_choice", "yes", "no", "abstain"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["member_proposals.id"],
            name=op.f(
                "fk_member_proposal_votes_proposal_id_member_proposals"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_member_proposal_votes_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_member_proposal_votes")
        ),
        sa.UniqueConstraint(
            "proposal_id",
            "user_id",
            name=op.f("uq_member_proposal_votes_proposal_id"),
        ),
    )
    for column in ("proposal_id", "user_id", "choice"):
        create_index("member_proposal_votes", column)

    op.create_table(
        "meals",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("image_url", sa.String(500), nullable=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column(
            "tax_type",
            enum_type("meal_tax_type", "taxable", "tax_exempt"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "price >= 0", name=op.f("ck_meals_price_nonnegative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meals")),
    )
    create_index("meals", "slug", unique=True)
    create_index("meals", "is_active")

    op.create_table(
        "meal_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("location", sa.String(240), nullable=False),
        sa.Column(
            "ordering_starts_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "ordering_ends_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "pickup_starts_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("pickup_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            enum_type(
                "meal_event_status",
                "draft",
                "published",
                "ordering_closed",
                "pickup_open",
                "cancelled",
                "completed",
            ),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.String(36), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordering_ends_at > ordering_starts_at",
            name=op.f("ck_meal_events_ordering_range_valid"),
        ),
        sa.CheckConstraint(
            "pickup_ends_at > pickup_starts_at",
            name=op.f("ck_meal_events_pickup_range_valid"),
        ),
        sa.CheckConstraint(
            "pickup_starts_at >= ordering_ends_at",
            name=op.f("ck_meal_events_pickup_after_ordering"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_meal_events_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meal_events")),
    )
    for column in (
        "ordering_starts_at",
        "ordering_ends_at",
        "pickup_starts_at",
        "status",
        "created_by_id",
    ):
        create_index("meal_events", column)

    op.create_table(
        "meal_event_offerings",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("meal_event_id", sa.String(36), nullable=False),
        sa.Column("meal_id", sa.String(36), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False),
        sa.Column("paid_quantity", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "price >= 0",
            name=op.f("ck_meal_event_offerings_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "capacity > 0",
            name=op.f("ck_meal_event_offerings_capacity_positive"),
        ),
        sa.CheckConstraint(
            "reserved_quantity >= 0",
            name=op.f("ck_meal_event_offerings_reserved_nonnegative"),
        ),
        sa.CheckConstraint(
            "paid_quantity >= 0",
            name=op.f("ck_meal_event_offerings_paid_nonnegative"),
        ),
        sa.CheckConstraint(
            "reserved_quantity + paid_quantity <= capacity",
            name=op.f(
                "ck_meal_event_offerings_allocation_within_capacity"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["meal_event_id"],
            ["meal_events.id"],
            name=op.f(
                "fk_meal_event_offerings_meal_event_id_meal_events"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["meal_id"],
            ["meals.id"],
            name=op.f("fk_meal_event_offerings_meal_id_meals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_meal_event_offerings")
        ),
        sa.UniqueConstraint(
            "meal_event_id",
            "meal_id",
            name=op.f("uq_meal_event_offerings_meal_event_id"),
        ),
    )
    for column in ("meal_event_id", "meal_id", "is_active"):
        create_index("meal_event_offerings", column)

    op.create_table(
        "shipping_rates",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "channel",
            enum_type(
                "shipping_rate_channel",
                "home_delivery",
                "seven_eleven",
                "family_mart",
                "hilife",
            ),
            nullable=False,
        ),
        sa.Column(
            "temperature",
            enum_type(
                "shipping_rate_temperature", "ambient", "chilled", "frozen"
            ),
            nullable=False,
        ),
        sa.Column("fee", sa.Integer(), nullable=False),
        sa.Column("free_shipping_threshold", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "fee >= 0", name=op.f("ck_shipping_rates_fee_nonnegative")
        ),
        sa.CheckConstraint(
            "free_shipping_threshold >= 0",
            name=op.f("ck_shipping_rates_threshold_nonnegative"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_shipping_rates_effective_range_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipping_rates")),
        sa.UniqueConstraint(
            "channel",
            "temperature",
            "effective_from",
            name=op.f("uq_shipping_rates_channel"),
        ),
    )
    for column in (
        "channel",
        "temperature",
        "effective_from",
        "is_active",
    ):
        create_index("shipping_rates", column)

    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(
            sa.Column(
                "can_ship",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "shipping_temperature",
                enum_type(
                    "product_shipping_temperature",
                    "ambient",
                    "chilled",
                    "frozen",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "allowed_shipping_channels",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )

    with op.batch_alter_table("group_campaigns") as batch_op:
        batch_op.add_column(
            sa.Column(
                "can_ship",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "shipping_temperature",
                enum_type(
                    "campaign_shipping_temperature",
                    "ambient",
                    "chilled",
                    "frozen",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "allowed_shipping_channels",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )

    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(
            sa.Column(
                "sales_channel",
                enum_type(
                    "sales_channel", "regular", "group", "meal_preorder"
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "fulfillment_method",
                enum_type(
                    "fulfillment_method",
                    "cooperative_pickup",
                    "event_pickup",
                    "ecpay_logistics",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("meal_event_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f("fk_orders_meal_event_id_meal_events"),
            "meal_events",
            ["meal_event_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.execute(
        sa.text(
            "UPDATE orders SET sales_channel = CASE "
            "WHEN order_kind = 'group' THEN 'group' ELSE 'regular' END, "
            "fulfillment_method = 'cooperative_pickup'"
        )
    )
    with op.batch_alter_table("orders") as batch_op:
        batch_op.alter_column(
            "sales_channel",
            existing_type=enum_type(
                "sales_channel", "regular", "group", "meal_preorder"
            ),
            nullable=False,
        )
        batch_op.alter_column(
            "fulfillment_method",
            existing_type=enum_type(
                "fulfillment_method",
                "cooperative_pickup",
                "event_pickup",
                "ecpay_logistics",
            ),
            nullable=False,
        )
    create_index("orders", "sales_channel")
    create_index("orders", "fulfillment_method")
    create_index("orders", "meal_event_id")

    with op.batch_alter_table("order_items") as batch_op:
        batch_op.add_column(
            sa.Column("source_meal_offering_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_order_items_source_meal_offering_id_meal_event_offerings"
            ),
            "meal_event_offerings",
            ["source_meal_offering_id"],
            ["id"],
            ondelete="SET NULL",
        )
    create_index("order_items", "source_meal_offering_id")

    with op.batch_alter_table("inventory_reservations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_meal_offering_id",
                sa.String(36),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_inventory_reservations_"
                "source_meal_offering_id_meal_event_offerings"
            ),
            "meal_event_offerings",
            ["source_meal_offering_id"],
            ["id"],
            ondelete="SET NULL",
        )
    create_index("inventory_reservations", "source_meal_offering_id")

    op.create_table(
        "order_fulfillments",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column(
            "method",
            enum_type(
                "order_fulfillment_method",
                "cooperative_pickup",
                "event_pickup",
                "ecpay_logistics",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            enum_type(
                "order_fulfillment_state",
                "pending_confirmation",
                "preparing",
                "ready_for_pickup",
                "picked_up",
                "awaiting_shipment",
                "shipped",
                "delivered",
                "no_show",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("pickup_location", sa.String(240), nullable=True),
        sa.Column("pickup_starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pickup_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pickup_code", sa.String(6), nullable=True),
        sa.Column("pickup_qr_token_hash", sa.String(64), nullable=True),
        sa.Column("recipient_name_encrypted", sa.Text(), nullable=True),
        sa.Column("recipient_phone_encrypted", sa.Text(), nullable=True),
        sa.Column("shipping_address_encrypted", sa.Text(), nullable=True),
        sa.Column("encryption_key_version", sa.String(32), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_fulfillments_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_fulfillments")),
        sa.UniqueConstraint(
            "pickup_qr_token_hash",
            name=op.f(
                "uq_order_fulfillments_pickup_qr_token_hash"
            ),
        ),
    )
    create_index("order_fulfillments", "order_id", unique=True)
    create_index("order_fulfillments", "method")
    create_index("order_fulfillments", "status")
    create_index("order_fulfillments", "pickup_code", unique=True)

    op.create_table(
        "shipments",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("order_fulfillment_id", sa.String(36), nullable=False),
        sa.Column(
            "channel",
            enum_type(
                "shipment_channel",
                "home_delivery",
                "seven_eleven",
                "family_mart",
                "hilife",
            ),
            nullable=False,
        ),
        sa.Column(
            "temperature",
            enum_type("shipment_temperature", "ambient", "chilled", "frozen"),
            nullable=False,
        ),
        sa.Column(
            "status",
            enum_type(
                "shipment_status",
                "draft",
                "selection_pending",
                "ready_to_create",
                "created",
                "in_transit",
                "delivered",
                "exception",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("shipping_fee", sa.Integer(), nullable=False),
        sa.Column("ecpay_logistics_id", sa.String(40), nullable=True),
        sa.Column("ecpay_booking_note", sa.String(80), nullable=True),
        sa.Column("tracking_number", sa.String(80), nullable=True),
        sa.Column("provider_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "shipping_fee >= 0",
            name=op.f("ck_shipments_shipping_fee_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_fulfillment_id"],
            ["order_fulfillments.id"],
            name=op.f(
                "fk_shipments_order_fulfillment_id_order_fulfillments"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
    )
    create_index("shipments", "order_fulfillment_id", unique=True)
    create_index("shipments", "channel")
    create_index("shipments", "temperature")
    create_index("shipments", "status")
    create_index("shipments", "ecpay_logistics_id", unique=True)
    create_index("shipments", "tracking_number")

    with op.batch_alter_table("payment_attempts") as batch_op:
        batch_op.alter_column(
            "order_id",
            existing_type=sa.String(36),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("membership_charge_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_payment_attempts_membership_charge_id_membership_charges"
            ),
            "membership_charges",
            ["membership_charge_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_check_constraint(
            op.f(
                "ck_payment_attempts_exactly_one_payment_subject"
            ),
            "(order_id IS NOT NULL AND membership_charge_id IS NULL) OR "
            "(order_id IS NULL AND membership_charge_id IS NOT NULL)",
        )
    create_index("payment_attempts", "membership_charge_id")

    with op.batch_alter_table("refunds") as batch_op:
        batch_op.alter_column(
            "order_id",
            existing_type=sa.String(36),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("membership_charge_id", sa.String(36), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f("fk_refunds_membership_charge_id_membership_charges"),
            "membership_charges",
            ["membership_charge_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_check_constraint(
            op.f("ck_refunds_exactly_one_refund_subject"),
            "(order_id IS NOT NULL AND membership_charge_id IS NULL) OR "
            "(order_id IS NULL AND membership_charge_id IS NOT NULL)",
        )
    create_index("refunds", "membership_charge_id")

    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    users = bind.execute(
        sa.text(
            "SELECT id, created_at FROM users "
            "WHERE membership_type = 'member' ORDER BY created_at, id"
        )
    ).mappings()
    member_rows = []
    for sequence, user in enumerate(users, start=1):
        created_at = user["created_at"] or now
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )
            except ValueError:
                created_at = now
        member_rows.append(
            {
                "id": new_id(),
                "user_id": user["id"],
                "application_id": None,
                "member_number": (
                    f"SLF-{created_at.year}-{sequence:04d}"
                ),
                "status": "active",
                "activated_at": created_at,
                "suspended_at": None,
                "ended_at": None,
                "status_reason": "由 v1 社員資格遷移",
                "created_at": created_at,
                "updated_at": now,
            }
        )
    if member_rows:
        membership_table = sa.table(
            "memberships",
            sa.column("id", sa.String),
            sa.column("user_id", sa.String),
            sa.column("application_id", sa.String),
            sa.column("member_number", sa.String),
            sa.column("status", sa.String),
            sa.column("activated_at", sa.DateTime(timezone=True)),
            sa.column("suspended_at", sa.DateTime(timezone=True)),
            sa.column("ended_at", sa.DateTime(timezone=True)),
            sa.column("status_reason", sa.Text),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(membership_table, member_rows)

    fee_schedule_table = sa.table(
        "membership_fee_schedules",
        sa.column("id", sa.String),
        sa.column("charge_kind", sa.String),
        sa.column("amount", sa.Integer),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        fee_schedule_table,
        [
            {
                "id": new_id(),
                "charge_kind": "admission_fee",
                "amount": 500,
                "effective_from": date(2026, 1, 1),
                "effective_to": None,
                "is_active": True,
                "created_at": now,
            },
            {
                "id": new_id(),
                "charge_kind": "share_capital",
                "amount": 1000,
                "effective_from": date(2026, 1, 1),
                "effective_to": None,
                "is_active": True,
                "created_at": now,
            },
        ],
    )

    shipping_rate_table = sa.table(
        "shipping_rates",
        sa.column("id", sa.String),
        sa.column("channel", sa.String),
        sa.column("temperature", sa.String),
        sa.column("fee", sa.Integer),
        sa.column("free_shipping_threshold", sa.Integer),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        shipping_rate_table,
        [
            {
                "id": new_id(),
                "channel": channel,
                "temperature": "ambient",
                "fee": 160 if channel == "home_delivery" else 70,
                "free_shipping_threshold": 1500,
                "effective_from": date(2026, 1, 1),
                "effective_to": None,
                "is_active": True,
                "created_at": now,
            }
            for channel in (
                "home_delivery",
                "seven_eleven",
                "family_mart",
                "hilife",
            )
        ],
    )

    orders = bind.execute(
        sa.text(
            "SELECT id, fulfillment_method, fulfillment_status, "
            "created_at, updated_at FROM orders"
        )
    ).mappings()
    fulfillment_rows = [
        {
            "id": new_id(),
            "order_id": order["id"],
            "method": order["fulfillment_method"],
            "status": order["fulfillment_status"],
            "pickup_location": None,
            "pickup_starts_at": None,
            "pickup_ends_at": None,
            "pickup_code": None,
            "pickup_qr_token_hash": None,
            "recipient_name_encrypted": None,
            "recipient_phone_encrypted": None,
            "shipping_address_encrypted": None,
            "encryption_key_version": None,
            "fulfilled_at": (
                order["updated_at"]
                if order["fulfillment_status"] == "picked_up"
                else None
            ),
            "created_at": order["created_at"] or now,
            "updated_at": order["updated_at"] or now,
        }
        for order in orders
    ]
    if fulfillment_rows:
        fulfillment_table = sa.table(
            "order_fulfillments",
            sa.column("id", sa.String),
            sa.column("order_id", sa.String),
            sa.column("method", sa.String),
            sa.column("status", sa.String),
            sa.column("pickup_location", sa.String),
            sa.column("pickup_starts_at", sa.DateTime(timezone=True)),
            sa.column("pickup_ends_at", sa.DateTime(timezone=True)),
            sa.column("pickup_code", sa.String),
            sa.column("pickup_qr_token_hash", sa.String),
            sa.column("recipient_name_encrypted", sa.Text),
            sa.column("recipient_phone_encrypted", sa.Text),
            sa.column("shipping_address_encrypted", sa.Text),
            sa.column("encryption_key_version", sa.String),
            sa.column("fulfilled_at", sa.DateTime(timezone=True)),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        op.bulk_insert(fulfillment_table, fulfillment_rows)

    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column(
            "can_ship",
            existing_type=sa.Boolean(),
            server_default=None,
        )
        batch_op.alter_column(
            "allowed_shipping_channels",
            existing_type=sa.JSON(),
            server_default=None,
        )
    with op.batch_alter_table("group_campaigns") as batch_op:
        batch_op.alter_column(
            "can_ship",
            existing_type=sa.Boolean(),
            server_default=None,
        )
        batch_op.alter_column(
            "allowed_shipping_channels",
            existing_type=sa.JSON(),
            server_default=None,
        )


def downgrade() -> None:
    drop_index("refunds", "membership_charge_id")
    op.execute(sa.text("DELETE FROM refunds WHERE order_id IS NULL"))
    with op.batch_alter_table("refunds") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_refunds_exactly_one_refund_subject"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("fk_refunds_membership_charge_id_membership_charges"),
            type_="foreignkey",
        )
        batch_op.drop_column("membership_charge_id")
        batch_op.alter_column(
            "order_id", existing_type=sa.String(36), nullable=False
        )

    drop_index("payment_attempts", "membership_charge_id")
    op.execute(sa.text("DELETE FROM payment_attempts WHERE order_id IS NULL"))
    with op.batch_alter_table("payment_attempts") as batch_op:
        batch_op.drop_constraint(
            op.f(
                "ck_payment_attempts_exactly_one_payment_subject"
            ),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f(
                "fk_payment_attempts_membership_charge_id_membership_charges"
            ),
            type_="foreignkey",
        )
        batch_op.drop_column("membership_charge_id")
        batch_op.alter_column(
            "order_id", existing_type=sa.String(36), nullable=False
        )

    for column in (
        "tracking_number",
        "ecpay_logistics_id",
        "status",
        "temperature",
        "channel",
        "order_fulfillment_id",
    ):
        drop_index("shipments", column)
    op.drop_table("shipments")

    for column in ("pickup_code", "status", "method", "order_id"):
        drop_index("order_fulfillments", column)
    op.drop_table("order_fulfillments")

    drop_index("order_items", "source_meal_offering_id")
    with op.batch_alter_table("order_items") as batch_op:
        batch_op.drop_constraint(
            op.f(
                "fk_order_items_source_meal_offering_id_meal_event_offerings"
            ),
            type_="foreignkey",
        )
        batch_op.drop_column("source_meal_offering_id")

    drop_index("inventory_reservations", "source_meal_offering_id")
    with op.batch_alter_table("inventory_reservations") as batch_op:
        batch_op.drop_constraint(
            op.f(
                "fk_inventory_reservations_"
                "source_meal_offering_id_meal_event_offerings"
            ),
            type_="foreignkey",
        )
        batch_op.drop_column("source_meal_offering_id")

    for column in ("meal_event_id", "fulfillment_method", "sales_channel"):
        drop_index("orders", column)
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_orders_meal_event_id_meal_events"),
            type_="foreignkey",
        )
        batch_op.drop_column("meal_event_id")
        batch_op.drop_column("fulfillment_method")
        batch_op.drop_column("sales_channel")

    with op.batch_alter_table("group_campaigns") as batch_op:
        batch_op.drop_column("allowed_shipping_channels")
        batch_op.drop_column("shipping_temperature")
        batch_op.drop_column("can_ship")
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_column("allowed_shipping_channels")
        batch_op.drop_column("shipping_temperature")
        batch_op.drop_column("can_ship")

    for column in (
        "is_active",
        "effective_from",
        "temperature",
        "channel",
    ):
        drop_index("shipping_rates", column)
    op.drop_table("shipping_rates")

    for column in ("is_active", "meal_id", "meal_event_id"):
        drop_index("meal_event_offerings", column)
    op.drop_table("meal_event_offerings")

    for column in (
        "created_by_id",
        "status",
        "pickup_starts_at",
        "ordering_ends_at",
        "ordering_starts_at",
    ):
        drop_index("meal_events", column)
    op.drop_table("meal_events")
    drop_index("meals", "is_active")
    drop_index("meals", "slug")
    op.drop_table("meals")

    for column in ("choice", "user_id", "proposal_id"):
        drop_index("member_proposal_votes", column)
    op.drop_table("member_proposal_votes")
    for column in ("created_at", "user_id", "proposal_id"):
        drop_index("member_proposal_comments", column)
    op.drop_table("member_proposal_comments")
    for column in (
        "reviewed_by_id",
        "voting_ends_at",
        "discussion_ends_at",
        "status",
        "created_by_id",
    ):
        drop_index("member_proposals", column)
    op.drop_table("member_proposals")

    for column in ("status", "user_id", "activity_id"):
        drop_index("activity_registrations", column)
    op.drop_table("activity_registrations")
    for column in (
        "reviewed_by_id",
        "status",
        "registration_deadline",
        "starts_at",
        "created_by_id",
    ):
        drop_index("activities", column)
    op.drop_table("activities")

    drop_index("member_directory_entries", "is_public")
    drop_index("member_directory_entries", "user_id")
    op.drop_table("member_directory_entries")

    for column in (
        "status",
        "charge_kind",
        "fee_schedule_id",
        "membership_id",
        "application_id",
        "user_id",
    ):
        drop_index("membership_charges", column)
    op.drop_table("membership_charges")

    for column in ("is_active", "effective_from", "charge_kind"):
        drop_index("membership_fee_schedules", column)
    op.drop_table("membership_fee_schedules")

    drop_index("membership_documents", "status")
    drop_index("membership_documents", "application_id")
    op.drop_table("membership_documents")

    drop_index("memberships", "status")
    drop_index("memberships", "member_number")
    drop_index("memberships", "user_id")
    op.drop_table("memberships")

    drop_index("membership_applications", "reviewed_by_id")
    drop_index("membership_applications", "status")
    drop_index("membership_applications", "user_id")
    op.drop_table("membership_applications")

    drop_index("member_profiles", "user_id")
    op.drop_table("member_profiles")

    for column in ("expires_at", "token_hash", "user_id"):
        drop_index("password_reset_tokens", column)
    op.drop_table("password_reset_tokens")
    for column in ("expires_at", "token_hash", "user_id"):
        drop_index("email_verification_tokens", column)
    op.drop_table("email_verification_tokens")

    op.drop_column("users", "email_verified_at")
