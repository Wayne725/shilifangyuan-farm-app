"""Add cooperative finance, education, governance, points and wishes.

Revision ID: 0004_cooperative_core
Revises: 0003_shipment_selection_token
Create Date: 2026-08-03
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.database import Base
from app import models  # noqa: F401


revision = "0004_cooperative_core"
down_revision = "0003_shipment_selection_token"
branch_labels = None
depends_on = None


NEW_TABLES = [
    "system_settings",
    "fiscal_years",
    "surplus_ledgers",
    "surplus_distributions",
    "education_lectures",
    "education_questions",
    "education_attempts",
    "point_accounts",
    "point_transactions",
    "meetings",
    "meeting_attendances",
    "proposal_options",
    "meeting_resolutions",
    "wishes",
    "wish_supports",
    "badge_definitions",
    "member_badges",
]


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in NEW_TABLES:
        Base.metadata.tables[table_name].create(bind, checkfirst=True)

    with op.batch_alter_table("member_proposals") as batch:
        batch.add_column(
            sa.Column(
                "proposal_type",
                sa.String(length=15),
                nullable=False,
                server_default="resolution",
            )
        )
        batch.create_index(
            "ix_member_proposals_proposal_type", ["proposal_type"]
        )

    with op.batch_alter_table("member_proposal_votes") as batch:
        batch.alter_column("choice", existing_type=sa.String(length=7), nullable=True)
        batch.add_column(sa.Column("option_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_member_proposal_votes_option_id", ["option_id"])
        batch.create_foreign_key(
            "fk_member_proposal_votes_option_id_proposal_options",
            "proposal_options",
            ["option_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("member_proposal_votes") as batch:
        batch.drop_constraint(
            "fk_member_proposal_votes_option_id_proposal_options",
            type_="foreignkey",
        )
        batch.drop_index("ix_member_proposal_votes_option_id")
        batch.drop_column("option_id")
        batch.alter_column("choice", existing_type=sa.String(length=7), nullable=False)

    with op.batch_alter_table("member_proposals") as batch:
        batch.drop_index("ix_member_proposals_proposal_type")
        batch.drop_column("proposal_type")

    bind = op.get_bind()
    for table_name in reversed(NEW_TABLES):
        Base.metadata.tables[table_name].drop(bind, checkfirst=True)
