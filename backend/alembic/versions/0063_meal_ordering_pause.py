"""Allow administrators to pause new orders for an individual meal event."""

from alembic import op
import sqlalchemy as sa


revision = "0063_meal_ordering_pause"
down_revision = "0062_meal_business_only"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("meal_events") as batch:
        batch.add_column(sa.Column("ordering_paused_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("ordering_pause_version", sa.Integer(), nullable=False, server_default="0"))
        batch.create_check_constraint(
            "meal_ordering_pause_version",
            "ordering_pause_version >= 0 AND (ordering_paused_at IS NULL OR ordering_pause_version > 0)",
        )


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE meal_events IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE meal_events SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM meal_events WHERE ordering_pause_version > 0 OR ordering_paused_at IS NOT NULL)"
    )):
        raise RuntimeError("已有暫停接單紀錄，請保留接單狀態並向前修復")
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_constraint("meal_ordering_pause_version", type_="check")
        batch.drop_column("ordering_pause_version")
        batch.drop_column("ordering_paused_at")
