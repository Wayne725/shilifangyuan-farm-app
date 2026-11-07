"""Recover governance proposal creation without duplicating drafts."""
from alembic import op
import sqlalchemy as sa


revision = "0041_proposal_submission"
down_revision = "0040_membership_admission"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("member_proposals", sa.Column("client_request_id", sa.String(36), nullable=True))
    op.add_column("member_proposals", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.create_index("uq_member_proposal_creator_request", "member_proposals", ["created_by_id", "client_request_id"], unique=True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM member_proposals WHERE client_request_id IS NOT NULL")):
        raise RuntimeError("已有提案送單識別，降版會失去防重複保護；請先完成資料保全")
    op.drop_index("uq_member_proposal_creator_request", table_name="member_proposals")
    op.drop_column("member_proposals", "request_fingerprint")
    op.drop_column("member_proposals", "client_request_id")
