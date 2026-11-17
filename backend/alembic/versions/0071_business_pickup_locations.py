"""Limit selected pickup locations to business meals."""

from alembic import op
import sqlalchemy as sa


revision = "0071_business_pickup_locations"
down_revision = "0070_meal_schedule_weekend"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("pickup_locations", sa.Column("business_meal_only", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("pickup_locations", sa.Column(
        "requires_pickup_note",
        sa.Boolean(),
        sa.CheckConstraint("NOT requires_pickup_note OR business_meal_only", name=op.f("ck_pickup_locations_note_requires_business_meal")),
        nullable=False,
        server_default=sa.false(),
    ))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    # Old checkout has no scope filter. Hide these locations before removing it.
    op.execute("UPDATE pickup_locations SET is_active = false WHERE business_meal_only")
    op.drop_column("pickup_locations", "requires_pickup_note")
    op.drop_column("pickup_locations", "business_meal_only")
