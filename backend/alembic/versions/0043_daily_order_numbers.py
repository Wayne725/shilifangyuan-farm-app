"""Allocate short order numbers atomically by Taiwan date and sales prefix."""
from alembic import op
import sqlalchemy as sa

revision = "0043_daily_order_numbers"
down_revision = "0042_order_reference_printing"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_number_counters",
        sa.Column("prefix", sa.String(1), primary_key=True),
        sa.Column("business_date", sa.Date(), primary_key=True),
        sa.Column("last_value", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("prefix IN ('M', 'O')", name="valid_prefix"),
        sa.CheckConstraint("last_value > 0", name="positive_value"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM order_number_counters")):
        raise RuntimeError("已有每日訂單編號，請保留流水紀錄並向前修復")
    op.drop_table("order_number_counters")
