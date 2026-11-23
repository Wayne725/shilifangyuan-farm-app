"""Let group buyers choose a weekly pickup slot."""

from alembic import op
import sqlalchemy as sa


revision = "0073_group_pickup_slots"
down_revision = "0072_monthly_group_preorders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    op.add_column("group_campaigns", sa.Column("pickup_slots", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("order_fulfillments", sa.Column("pickup_slot_label", sa.String(length=80), nullable=True))


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    op.drop_column("order_fulfillments", "pickup_slot_label")
    op.drop_column("group_campaigns", "pickup_slots")
