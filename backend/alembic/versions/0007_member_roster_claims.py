"""Add claimable member roster entries.

Revision ID: 0007_member_roster_claims
Revises: 0006_real_cooperative_records
Create Date: 2026-08-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_member_roster_claims"
down_revision = "0006_real_cooperative_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_roster_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("member_number", sa.String(length=32), nullable=False),
        sa.Column("legal_name_encrypted", sa.Text(), nullable=False),
        sa.Column("email_encrypted", sa.Text(), nullable=False),
        sa.Column("phone_encrypted", sa.Text(), nullable=False),
        sa.Column("encryption_key_version", sa.String(length=32), nullable=False),
        sa.Column(
            "share_certificate_number",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "share_capital_amount",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "share_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("share_subscribed_on", sa.Date(), nullable=True),
        sa.Column("share_paid_on", sa.Date(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("claimed_user_id", sa.String(length=36), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "share_capital_amount >= 0",
            name=op.f("ck_member_roster_entries_share_capital_nonnegative"),
        ),
        sa.CheckConstraint(
            "share_count >= 0",
            name=op.f("ck_member_roster_entries_share_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["claimed_user_id"],
            ["users.id"],
            name=op.f("fk_member_roster_entries_claimed_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_roster_entries")),
    )
    op.create_index(
        op.f("ix_member_roster_entries_member_number"),
        "member_roster_entries",
        ["member_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_member_roster_entries_share_certificate_number"),
        "member_roster_entries",
        ["share_certificate_number"],
        unique=True,
    )
    op.create_index(
        op.f("ix_member_roster_entries_is_active"),
        "member_roster_entries",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_member_roster_entries_claimed_user_id"),
        "member_roster_entries",
        ["claimed_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_member_roster_entries_claimed_user_id"),
        table_name="member_roster_entries",
    )
    op.drop_index(
        op.f("ix_member_roster_entries_is_active"),
        table_name="member_roster_entries",
    )
    op.drop_index(
        op.f("ix_member_roster_entries_share_certificate_number"),
        table_name="member_roster_entries",
    )
    op.drop_index(
        op.f("ix_member_roster_entries_member_number"),
        table_name="member_roster_entries",
    )
    op.drop_table("member_roster_entries")
