"""Add opt-in daily meal schedules and selected pickup time."""

from alembic import op
import sqlalchemy as sa

revision = "0016_meal_daily_schedules"
down_revision = "0015_document_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meal_schedule_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(140), nullable=False),
        sa.Column("location", sa.String(240), nullable=False),
        sa.Column("meal_period", sa.String(12), nullable=False),
        sa.Column("pickup_start_time", sa.String(5), nullable=False),
        sa.Column("pickup_end_time", sa.String(5), nullable=False),
        sa.Column("cutoff_time", sa.String(5), nullable=False),
        sa.Column("cutoff_days_before", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("advance_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_publish", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("offerings", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("advance_days BETWEEN 1 AND 30", name="advance_days_valid"),
        sa.CheckConstraint("cutoff_days_before >= 0 AND cutoff_days_before < advance_days", name="cutoff_days_valid"),
        sa.CheckConstraint("meal_period IN ('lunch', 'dinner')", name="meal_period_valid"),
    )
    op.create_index("ix_meal_schedule_templates_enabled", "meal_schedule_templates", ["enabled"])
    with op.batch_alter_table("meal_events") as batch:
        batch.add_column(sa.Column("schedule_template_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("service_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("meal_period", sa.String(12), nullable=True))
        batch.create_foreign_key("fk_meal_event_schedule", "meal_schedule_templates", ["schedule_template_id"], ["id"], ondelete="RESTRICT")
        batch.create_unique_constraint("uq_meal_event_schedule_date", ["schedule_template_id", "service_date"])
        batch.create_index("ix_meal_events_schedule_template_id", ["schedule_template_id"])
    op.add_column("order_fulfillments", sa.Column("pickup_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("order_fulfillments", "pickup_at")
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_index("ix_meal_events_schedule_template_id")
        batch.drop_constraint("uq_meal_event_schedule_date", type_="unique")
        batch.drop_constraint("fk_meal_event_schedule", type_="foreignkey")
        batch.drop_column("meal_period")
        batch.drop_column("service_date")
        batch.drop_column("schedule_template_id")
    op.drop_index("ix_meal_schedule_templates_enabled", table_name="meal_schedule_templates")
    op.drop_table("meal_schedule_templates")
