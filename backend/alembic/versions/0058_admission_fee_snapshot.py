"""Pin the fee and share unit amounts at creation of each admission round."""
from alembic import op
import sqlalchemy as sa

revision = "0058_admission_fee_snapshot"
down_revision = "0057_browser_refresh_recovery"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    table = "membership_admission_rounds"
    op.add_column(table, sa.Column("admission_fee_amount", sa.Integer(), nullable=False, server_default="500"))
    op.add_column(table, sa.Column("admission_fee_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(table, sa.Column("share_unit_amount", sa.Integer(), nullable=False, server_default="100"))


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE system_settings, membership_admission_rounds IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE membership_admission_rounds SET id = id WHERE 0")
    changed = op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM system_settings WHERE key = 'membership_admission_fee') "
        "OR EXISTS (SELECT 1 FROM membership_admission_rounds "
        "WHERE admission_fee_version <> 0 OR admission_fee_amount <> 500 OR share_unit_amount <> 100)"
    ))
    if changed:
        raise RuntimeError("已有新版入社費設定或申請金額快照，請保留歷史資料並向前修復")
    for column in ("share_unit_amount", "admission_fee_version", "admission_fee_amount"):
        op.drop_column("membership_admission_rounds", column)
