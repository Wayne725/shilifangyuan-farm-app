"""Keep existing booking contracts while introducing workday and special events."""
from alembic import op
import sqlalchemy as sa

revision = "0050_meal_workday_policy"
down_revision = "0049_merge_commerce_remediation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("meal_events", sa.Column("booking_policy", sa.String(16), nullable=False, server_default="legacy"))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM meal_events WHERE booking_policy != 'legacy'")).scalar():
        raise RuntimeError("已有工作日或特殊日期場次，請先保留新制接單規則，不可直接降版")
    with op.batch_alter_table("meal_events") as batch:
        batch.drop_column("booking_policy")
