"""Remember each account's preferred mobile barcode."""

from alembic import op
import sqlalchemy as sa

revision = "0033_saved_mobile_barcode"
down_revision = "0032_product_member_photos"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("saved_mobile_barcode", sa.String(8), nullable=True))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("saved_mobile_barcode")
