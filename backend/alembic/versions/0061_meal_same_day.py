"""Keep same-day meal ordering off until an administrator configures a scene."""
from alembic import op
import sqlalchemy as sa

revision = "0061_meal_same_day"
down_revision = "0060_procurement_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("meal_events") as batch:
        batch.add_column(sa.Column("same_day_preparation_minutes", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "meal_same_day_configuration",
            "same_day_preparation_minutes IS NULL OR (same_day_preparation_minutes BETWEEN 1 AND 1439 "
            "AND booking_policy IN ('workday', 'special') AND NOT allow_cash_payment)",
        )


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE meal_events IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE meal_events SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM meal_events WHERE same_day_preparation_minutes IS NOT NULL)"
    )):
        raise RuntimeError("已有當日訂餐設定，請保留接單規則並向前修復")
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_constraint("meal_same_day_configuration", type_="check")
        batch.drop_column("same_day_preparation_minutes")
