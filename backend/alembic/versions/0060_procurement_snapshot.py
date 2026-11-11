"""Preserve the supplier and product codes selected when a direct item is ordered."""
from alembic import op
import sqlalchemy as sa

revision = "0060_procurement_snapshot"
down_revision = "0059_recovery_quarantine"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("order_items", sa.Column("procurement_snapshot", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE order_items IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE order_items SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM order_items WHERE procurement_snapshot IS NOT NULL)")):
        raise RuntimeError("已有商品供應者快照，請保留叫貨紀錄並向前修復")
    op.drop_column("order_items", "procurement_snapshot")
