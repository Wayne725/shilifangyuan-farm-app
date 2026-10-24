"""Preserve selected meal pickup details and utensil preference."""
from alembic import op
import sqlalchemy as sa

revision = "0034_meal_pickup_utensils"
down_revision = "0033_saved_mobile_barcode"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("order_fulfillments", sa.Column("pickup_address", sa.String(500), nullable=False, server_default=""))
    op.add_column("order_fulfillments", sa.Column("pickup_instructions", sa.Text(), nullable=False, server_default=""))
    op.add_column("order_fulfillments", sa.Column("needs_utensils", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("order_fulfillments") as batch:
        batch.drop_column("needs_utensils")
        batch.drop_column("pickup_instructions")
        batch.drop_column("pickup_address")
