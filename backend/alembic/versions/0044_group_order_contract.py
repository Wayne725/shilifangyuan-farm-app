"""Preserve the campaign core terms confirmed by each new group order."""
from alembic import op
import sqlalchemy as sa


revision = "0044_group_order_contract"
down_revision = "0043_notification_dedupe"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("group_contract_fingerprint", sa.String(64), nullable=True))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM orders WHERE group_contract_fingerprint IS NOT NULL")):
        raise RuntimeError("已有共同購買合約快照，降版會失去付款保護；請先完成資料保全")
    op.drop_column("orders", "group_contract_fingerprint")
