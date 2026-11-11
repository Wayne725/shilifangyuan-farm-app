"""Allow selected meal events to accept business bookings only."""

from alembic import op
import sqlalchemy as sa


revision = "0062_meal_business_only"
down_revision = "0061_meal_same_day"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("meal_events") as batch:
        batch.add_column(sa.Column("business_only", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_check_constraint(
            "meal_business_only_configuration",
            "NOT business_only OR (booking_policy IN ('workday', 'special') "
            "AND same_day_preparation_minutes IS NULL AND NOT allow_cash_payment)",
        )


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE meal_events IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE meal_events SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM meal_events WHERE business_only)")):
        raise RuntimeError("已有僅限會餐的場次，請保留接單規則並向前修復")
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_constraint("meal_business_only_configuration", type_="check")
        batch.drop_column("business_only")
