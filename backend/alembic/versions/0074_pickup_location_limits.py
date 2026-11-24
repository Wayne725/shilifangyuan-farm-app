"""Let admins limit pickup locations per group buy or meal, and stop capping meal preorders."""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0074_pickup_location_limits"
down_revision = "0073_group_pickup_slots"
branch_labels = None
depends_on = None

UNLIMITED_MEAL_CAPACITY = 100_000

templates = sa.table("meal_schedule_templates", sa.column("id", sa.String), sa.column("offerings", sa.JSON))
events = sa.table(
    "meal_events", sa.column("id", sa.String), sa.column("status", sa.String),
    sa.column("pickup_ends_at", sa.DateTime(timezone=True)),
)
offerings_table = sa.table(
    "meal_event_offerings", sa.column("meal_event_id", sa.String), sa.column("capacity", sa.Integer),
)


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    for table in ("group_campaigns", "meal_events", "meal_schedule_templates"):
        op.add_column(table, sa.Column("pickup_location_ids", sa.JSON(), nullable=False, server_default="[]"))

    connection = op.get_bind()
    for template_id, offerings in connection.execute(sa.select(templates.c.id, templates.c.offerings)).all():
        raised = [{**item, "capacity": UNLIMITED_MEAL_CAPACITY} for item in offerings or []]
        if raised != offerings:
            connection.execute(templates.update().where(templates.c.id == template_id).values(offerings=raised))
    upcoming = sa.select(events.c.id).where(
        events.c.status.in_(["draft", "published"]), events.c.pickup_ends_at > datetime.now(timezone.utc),
    )
    connection.execute(
        offerings_table.update()
        .where(offerings_table.c.capacity < UNLIMITED_MEAL_CAPACITY, offerings_table.c.meal_event_id.in_(upcoming))
        .values(capacity=UNLIMITED_MEAL_CAPACITY)
    )


def downgrade() -> None:
    # Raised meal capacities stay valid under the previous schema, so they are kept.
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    for table in ("meal_schedule_templates", "meal_events", "group_campaigns"):
        op.drop_column(table, "pickup_location_ids")
