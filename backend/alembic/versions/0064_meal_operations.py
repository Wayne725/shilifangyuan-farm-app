"""Store reusable meal contact details and Saturday schedule preferences."""

from alembic import op
import sqlalchemy as sa


revision = "0064_meal_operations"
down_revision = "0063_meal_ordering_pause"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("users", sa.Column("meal_contact_phone_encrypted", sa.Text()))
    op.add_column("meal_schedule_templates", sa.Column(
        "saturday_business_only", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade():
    dialect = op.get_context().dialect.name
    if dialect == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
        op.execute("LOCK TABLE users, meal_schedule_templates IN ACCESS EXCLUSIVE MODE")
    elif dialect == "sqlite":
        op.execute("UPDATE users SET id = id WHERE 0")
    if op.get_bind().scalar(sa.text("""
        SELECT EXISTS (SELECT 1 FROM users WHERE meal_contact_phone_encrypted IS NOT NULL)
            OR EXISTS (SELECT 1 FROM meal_schedule_templates WHERE saturday_business_only)
    """)):
        raise RuntimeError("已儲存便當聯絡電話或週六排程，請保留資料並向前修復")
    with op.batch_alter_table("meal_schedule_templates") as batch:
        batch.drop_column("saturday_business_only")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("meal_contact_phone_encrypted")
