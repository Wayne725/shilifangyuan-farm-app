"""Preserve historical invoice ownership and add an explicit delayed issue intent."""
from alembic import op
import sqlalchemy as sa

revision = "0044_invoice_timing"
down_revision = "0043_daily_order_numbers"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("invoice_handling", sa.String(20), nullable=True))
    op.add_column("orders", sa.Column("invoice_issue_after", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE orders SET invoice_handling = CASE WHEN payment_method = 'cash_on_pickup' THEN 'external' ELSE 'on_payment' END")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM orders WHERE invoice_handling = 'pickup_day' OR (payment_method = 'cash_on_pickup' AND invoice_handling = 'on_payment')")):
        raise RuntimeError("訂單已有新開票安排，請保留開票意圖並向前修復")
    op.drop_column("orders", "invoice_issue_after")
    op.drop_column("orders", "invoice_handling")
