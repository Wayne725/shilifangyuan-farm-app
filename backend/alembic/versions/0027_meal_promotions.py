"""Meal promotion rules and immutable order discount snapshots."""
from alembic import op
import sqlalchemy as sa
revision = "0027_meal_promotions"
down_revision = "0026_membership_offline_receipts"
branch_labels = depends_on = None

def upgrade():
    from datetime import datetime, timezone
    settings = sa.table("system_settings", sa.column("key", sa.String()), sa.column("value", sa.JSON()), sa.column("updated_at", sa.DateTime(timezone=True)))
    op.bulk_insert(settings, [{"key": "promotion_operations", "value": {}, "updated_at": datetime.now(timezone.utc)}])
    op.create_table("promotions", sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(100), nullable=False), sa.Column("code", sa.String(32), unique=True), sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("minimum_amount", sa.Integer(), nullable=False), sa.Column("discount_kind", sa.String(16), nullable=False), sa.Column("discount_value", sa.Integer(), nullable=False), sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False), sa.Column("max_uses", sa.Integer()), sa.Column("per_user_limit", sa.Integer()), sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("minimum_amount >= 1 AND discount_value > 0", name="valid_amounts"))
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("promotion_id", sa.String(36)))
        batch.add_column(sa.Column("promotion_snapshot", sa.JSON()))
        batch.create_foreign_key("fk_orders_promotion_id", "promotions", ["promotion_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_orders_promotion_id", "orders", ["promotion_id"])

def downgrade():

    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM orders WHERE promotion_id IS NOT NULL")).scalar():
        raise RuntimeError("已有促銷訂單，禁止移除優惠紀錄")
    op.drop_index("ix_orders_promotion_id", table_name="orders")
    op.execute(sa.text("DELETE FROM system_settings WHERE key = 'promotion_operations'"))
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("fk_orders_promotion_id", type_="foreignkey")
        batch.drop_column("promotion_snapshot")
        batch.drop_column("promotion_id")
    op.drop_table("promotions")
