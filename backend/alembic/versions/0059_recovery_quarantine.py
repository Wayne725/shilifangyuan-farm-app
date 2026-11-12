"""Persist recovery cutovers and holds across restarts."""
from alembic import op
import sqlalchemy as sa

revision = "0059_recovery_quarantine"
down_revision = "0058_admission_fee_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recovery_cutovers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("checkpoint_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("armed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "recovery_holds",
        sa.Column("cutover_id", sa.String(36), sa.ForeignKey("recovery_cutovers.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("scope", sa.String(32), primary_key=True),
        sa.Column("reference", sa.String(128), primary_key=True),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("resolution_sha256", sa.String(64)),
        sa.Column("resolution", sa.String(24)),
    )
    op.create_index("ix_recovery_holds_scope_reference_resolved", "recovery_holds", ["scope", "reference", "resolved_at"])


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE recovery_cutovers, recovery_holds IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE recovery_cutovers SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM recovery_cutovers)")):
        raise RuntimeError("已有災難復原隔離紀錄，請保留資料並向前修復")
    op.drop_index("ix_recovery_holds_scope_reference_resolved", table_name="recovery_holds")
    op.drop_table("recovery_holds")
    op.drop_table("recovery_cutovers")
