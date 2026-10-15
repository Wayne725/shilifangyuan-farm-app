"""Allow meal ordering to continue during the pickup window."""

from alembic import op
import sqlalchemy as sa


revision = "0017_meal_order_pickup_overlap"
down_revision = "0016_meal_daily_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_constraint(op.f("ck_meal_events_pickup_after_ordering"), type_="check")
        batch.create_check_constraint(
            op.f("ck_meal_events_ordering_before_pickup_end"),
            "ordering_ends_at < pickup_ends_at",
        )


def downgrade() -> None:
    overlapping = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM meal_events WHERE pickup_starts_at < ordering_ends_at"
    )).scalar_one()
    if overlapping:
        raise RuntimeError("已有接單與取餐重疊的場次，不可降版恢復舊限制；請先另行確認資料處理方式")
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_constraint(op.f("ck_meal_events_ordering_before_pickup_end"), type_="check")
        batch.create_check_constraint(
            op.f("ck_meal_events_pickup_after_ordering"),
            "pickup_starts_at >= ordering_ends_at",
        )
