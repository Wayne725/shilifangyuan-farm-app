"""Uploaded meal photos and custom group purchase requests."""
from alembic import op
import sqlalchemy as sa

revision = "0031_meal_photos_custom_requests"
down_revision = "0030_feie_print_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("meal_photo_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meal_id", sa.String(36), sa.ForeignKey("meals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("option_id", sa.String(36), sa.ForeignKey("meal_options.id", ondelete="CASCADE"), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(content) BETWEEN 1 AND 1048576", name="photo_size_valid"),
    )
    op.create_index("ix_meal_photo_assets_meal_id", "meal_photo_assets", ["meal_id"])
    with op.batch_alter_table("vote_proposals") as batch:
        batch.alter_column("target_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("request_details", sa.Text(), nullable=True))


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM meal_photo_assets")) or connection.scalar(sa.text("SELECT COUNT(*) FROM vote_proposals WHERE target_type = 'custom'")):
        raise RuntimeError("已有上傳照片或其他需求，請保留資料並向前修復")
    with op.batch_alter_table("vote_proposals") as batch:
        batch.drop_column("request_details")
        batch.alter_column("target_id", existing_type=sa.String(36), nullable=False)
    op.drop_table("meal_photo_assets")
