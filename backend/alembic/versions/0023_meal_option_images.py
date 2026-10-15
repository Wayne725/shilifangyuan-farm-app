"""Add optional photos to meal choices."""

from alembic import op
import sqlalchemy as sa

revision = "0023_meal_option_images"
down_revision = "0022_line_customer_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meal_options", sa.Column("image_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("meal_options", "image_url")
