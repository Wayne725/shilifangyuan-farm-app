"""Protect one open joint-purchase proposal per existing product or bundle."""
from alembic import op
import sqlalchemy as sa


revision = "0042_open_proposal_target"
down_revision = "0041_proposal_submission"
branch_labels = None
depends_on = None

PREDICATE = (
    "target_type IN ('product','bundle') AND target_id IS NOT NULL "
    "AND status IN ('pending_review','voting','conversion_pending')"
)


def upgrade():
    duplicates = op.get_bind().execute(sa.text(
        f"SELECT target_type, target_id, COUNT(*) AS open_count FROM vote_proposals WHERE {PREDICATE} "
        "GROUP BY target_type, target_id HAVING COUNT(*) > 1"
    )).first()
    if duplicates is not None:
        raise RuntimeError("已有同品項的多筆開放共同購買提案；請先由管理員確認並正式結束重複案件，不可自動刪除投票歷史")
    op.create_index("uq_vote_proposals_open_target", "vote_proposals", ["target_type", "target_id"], unique=True,
                    postgresql_where=sa.text(PREDICATE), sqlite_where=sa.text(PREDICATE))


def downgrade():
    op.drop_index("uq_vote_proposals_open_target", table_name="vote_proposals")
