"""Allow completed or cancelled meal events to be archived."""

from alembic import op
import sqlalchemy as sa


revision = "0067_meal_event_archive"
down_revision = "0066_meal_cancellation_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("meal_events", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE meal_events IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE meal_events SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM meal_events WHERE archived_at IS NOT NULL)")):
        raise RuntimeError("已有場次封存紀錄，請保留封存狀態並向前修復")
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_column("archived_at")
