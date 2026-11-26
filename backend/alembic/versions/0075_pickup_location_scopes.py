"""Separate pickup notes from business meals and mark points open to daily items and group buys."""

from alembic import op
import sqlalchemy as sa


revision = "0075_pickup_location_scopes"
down_revision = "0074_pickup_location_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("pickup_locations") as batch:
        batch.drop_constraint(op.f("ck_pickup_locations_note_requires_business_meal"), type_="check")
        batch.add_column(sa.Column("retail_pickup", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute("UPDATE pickup_locations SET retail_pickup = false WHERE business_meal_only OR requires_pickup_note")


def downgrade() -> None:
    postgresql = op.get_context().dialect.name == "postgresql"
    if postgresql:
        op.execute("SET LOCAL lock_timeout = '3s'")
    # The previous checkout shows every non-business point to daily items and group buys.
    # Points kept out of those flows, or needing a note, go back to business meals only.
    op.execute("UPDATE pickup_locations SET business_meal_only = true WHERE NOT retail_pickup OR requires_pickup_note")
    with op.batch_alter_table("pickup_locations") as batch:
        batch.drop_column("retail_pickup")
    # SQLite cannot later drop a column named in a table CHECK, as 0071's downgrade does.
    if postgresql:
        op.create_check_constraint(
            op.f("ck_pickup_locations_note_requires_business_meal"), "pickup_locations",
            "NOT requires_pickup_note OR business_meal_only",
        )
