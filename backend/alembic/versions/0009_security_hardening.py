"""Harden account sessions and existing-member claims.

Revision ID: 0009_security_hardening
Revises: 0008_meal_customization_options
Create Date: 2026-08-23
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_security_hardening"
down_revision = "0008_meal_customization_options"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "token_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "pending_member_claim",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("token_version", server_default=None)
        batch_op.alter_column("pending_member_claim", server_default=None)

    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_sessions.id"],
            name=op.f("fk_refresh_sessions_replaced_by_id_refresh_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_sessions")),
    )
    op.create_index(
        op.f("ix_refresh_sessions_user_id"),
        "refresh_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_sessions_expires_at"),
        "refresh_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_refresh_sessions_revoked_at"),
        "refresh_sessions",
        ["revoked_at"],
        unique=False,
    )

    with op.batch_alter_table("member_roster_entries") as batch_op:
        batch_op.add_column(
            sa.Column("pending_claim_user_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "pending_claim_expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            op.f(
                "fk_member_roster_entries_pending_claim_user_id_users"
            ),
            "users",
            ["pending_claim_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        op.f("ix_member_roster_entries_pending_claim_user_id"),
        "member_roster_entries",
        ["pending_claim_user_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_member_roster_entries_pending_claim_expires_at"),
        "member_roster_entries",
        ["pending_claim_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_member_roster_entries_pending_claim_expires_at"),
        table_name="member_roster_entries",
    )
    op.drop_index(
        op.f("ix_member_roster_entries_pending_claim_user_id"),
        table_name="member_roster_entries",
    )
    with op.batch_alter_table("member_roster_entries") as batch_op:
        batch_op.drop_constraint(
            op.f(
                "fk_member_roster_entries_pending_claim_user_id_users"
            ),
            type_="foreignkey",
        )
        batch_op.drop_column("pending_claim_expires_at")
        batch_op.drop_column("pending_claim_user_id")

    op.drop_index(
        op.f("ix_refresh_sessions_revoked_at"),
        table_name="refresh_sessions",
    )
    op.drop_index(
        op.f("ix_refresh_sessions_expires_at"),
        table_name="refresh_sessions",
    )
    op.drop_index(
        op.f("ix_refresh_sessions_user_id"),
        table_name="refresh_sessions",
    )
    op.drop_table("refresh_sessions")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("pending_member_claim")
        batch_op.drop_column("token_version")
