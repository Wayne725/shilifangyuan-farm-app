"""Freeze cooperative member tax eligibility at checkout."""

from alembic import op
import sqlalchemy as sa


revision = "0018_member_invoice_tax"
down_revision = "0017_meal_order_pickup_overlap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "member_tax_exempt_snapshot",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    member_orders = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM orders WHERE member_tax_exempt_snapshot = true")
    ).scalar_one()
    if member_orders:
        raise RuntimeError("已有社員免稅訂單，不可移除成交稅別快照；請先確認資料處理方式")
    op.drop_column("orders", "member_tax_exempt_snapshot")
