"""Support Sunday catering and fixed schedule booking rules."""

from alembic import op
import sqlalchemy as sa


revision = "0070_meal_schedule_weekend"
down_revision = "0069_business_meal_cash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    for name in ("sunday_business_only", "fixed_booking_window"):
        op.add_column("meal_schedule_templates", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    for name in ("fixed_booking_window", "sunday_business_only"):
        op.drop_column("meal_schedule_templates", name)
