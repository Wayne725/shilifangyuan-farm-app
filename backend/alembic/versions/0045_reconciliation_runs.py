"""Persist reconciliation progress without touching business transactions."""
from alembic import op
import sqlalchemy as sa


revision = "0045_reconciliation_runs"
down_revision = "0044_group_order_contract"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("reconciliation_runs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("progress_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_phase", sa.String(40), nullable=True),
        sa.Column("deferred_reason", sa.String(80), nullable=True),
        sa.Column("phases", sa.JSON(), nullable=False), sa.Column("counts", sa.JSON(), nullable=False),
        sa.CheckConstraint("status IN ('running','succeeded','partial_failure','cancelled')", name=op.f("ck_reconciliation_runs_status_valid")),
        sa.CheckConstraint("source IN ('job','internal','request','periodic')", name=op.f("ck_reconciliation_runs_source_valid")),
        sa.CheckConstraint("(status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL)", name=op.f("ck_reconciliation_runs_completion_consistent")),
        sa.CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name=op.f("ck_reconciliation_runs_completion_after_start")),
    )
    op.create_index("ix_reconciliation_runs_status_id", "reconciliation_runs", ["status", "id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM reconciliation_runs")):
        raise RuntimeError("已有背景結算紀錄，請先保存監測歷史再降版")
    op.drop_index("ix_reconciliation_runs_status_id", table_name="reconciliation_runs")
    op.drop_table("reconciliation_runs")
