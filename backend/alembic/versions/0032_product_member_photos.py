"""Uploaded product and member directory photos."""
from alembic import op
import sqlalchemy as sa

revision = "0032_product_member_photos"
down_revision = "0031_meal_photos_custom_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("profile_photo_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=True, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True, unique=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 1048576", name="profile_photo_size_valid"),
        sa.CheckConstraint("(product_id IS NULL) != (user_id IS NULL)", name="profile_photo_single_owner"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM profile_photo_assets")):
        raise RuntimeError("已有商品或社員照片，請保留資料並向前修復")
    op.drop_table("profile_photo_assets")
