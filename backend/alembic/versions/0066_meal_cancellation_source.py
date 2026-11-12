"""Record whether a cancellation was filed by a buyer or an administrator."""

from alembic import op
import sqlalchemy as sa


revision = "0066_meal_cancellation_source"
down_revision = "0065_admin_editing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("meal_cancellation_requests") as batch:
        batch.add_column(sa.Column("source", sa.String(10), nullable=False, server_default="buyer"))
        batch.create_check_constraint(op.f("ck_meal_cancellation_requests_source_valid"), "source IN ('admin', 'buyer')")
    op.execute(sa.text(
        "UPDATE meal_cancellation_requests SET source = 'admin' "
        "WHERE requested_by_id = reviewed_by_id AND requested_at = reviewed_at"
    ))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    with op.batch_alter_table("meal_cancellation_requests") as batch:
        batch.drop_constraint(op.f("ck_meal_cancellation_requests_source_valid"), type_="check")
        batch.drop_column("source")
