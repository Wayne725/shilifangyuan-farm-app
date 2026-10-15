"""Keep new meal booking deadlines separate from legacy orders."""

from alembic import op
import sqlalchemy as sa


revision = "0020_meal_booking_deadline"
down_revision = "0019_meal_cancellation_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meal_events", sa.Column("pickup_slot_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("order_fulfillments", sa.Column("meal_ordering_ends_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM order_fulfillments WHERE meal_ordering_ends_at IS NOT NULL")).scalar_one():
        raise RuntimeError("已有新制便當訂單，不可移除預訂截止快照；請先確認歷史訂單保留方式")
    if connection.execute(sa.text("SELECT COUNT(*) FROM meal_events WHERE pickup_slot_ends_at IS NOT NULL")).scalar_one():
        raise RuntimeError("已有新制便當場次，不可移除最後取餐時段；請先確認場次保留方式")
    op.drop_column("order_fulfillments", "meal_ordering_ends_at")
    op.drop_column("meal_events", "pickup_slot_ends_at")
