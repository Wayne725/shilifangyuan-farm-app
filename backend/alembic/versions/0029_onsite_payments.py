"""Explicit cash payment methods and opt-in meal events."""

from alembic import op
import sqlalchemy as sa

revision = "0029_onsite_payments"
down_revision = "0028_financial_statements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("payment_method", sa.String(16), nullable=False, server_default="online"))
    op.create_index("ix_orders_payment_method", "orders", ["payment_method"])
    op.add_column("meal_events", sa.Column("allow_cash_payment", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    onsite_orders = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM orders WHERE payment_method <> 'online'" )).scalar()
    if onsite_orders:
        raise RuntimeError("已有現場付款紀錄，不可移除付款方式；請保留此版本並向前修復")
    op.drop_column("meal_events", "allow_cash_payment")
    op.drop_index("ix_orders_payment_method", table_name="orders")
    op.drop_column("orders", "payment_method")
