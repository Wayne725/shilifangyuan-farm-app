"""Separate daily catalog availability from shared product activation."""
from alembic import op
import sqlalchemy as sa


revision = "0039_product_daily_sales"
down_revision = "0038_activity_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("daily_sales_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM products WHERE daily_sales_enabled = false")):
        raise RuntimeError("已有未上架日常選購的商品，降版會改變販售範圍；請先確認商品上架設定")
    with op.batch_alter_table("products") as batch:
        batch.drop_column("daily_sales_enabled")
