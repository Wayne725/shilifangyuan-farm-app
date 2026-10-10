"""Separate buyer cancellation requests from refunds and fulfillment."""

from alembic import op
import sqlalchemy as sa

revision = "0019_meal_cancellation_review"
down_revision = "0018_member_invoice_tax"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_fulfillments", sa.Column("meal_capacity_retained", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "meal_cancellation_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewed_at IS NULL AND reviewed_by_id IS NULL AND review_reason IS NULL) "
            "OR (status <> 'pending' AND reviewed_at IS NOT NULL AND reviewed_by_id IS NOT NULL AND review_reason IS NOT NULL)",
            name="review_consistent",
        ),
    )
    op.create_index("ix_meal_cancellation_requests_order_id", "meal_cancellation_requests", ["order_id"])
    op.create_index("ix_meal_cancellation_requests_requested_by_id", "meal_cancellation_requests", ["requested_by_id"])
    op.create_index("ix_meal_cancellation_status_requested", "meal_cancellation_requests", ["status", "requested_at"])
    op.create_index(
        "uq_meal_cancellation_pending_order", "meal_cancellation_requests", ["order_id"], unique=True,
        sqlite_where=sa.text("status = 'pending'"), postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM meal_cancellation_requests")).scalar_one():
        raise RuntimeError("已有退單審核紀錄，不可移除；請先確認稽核資料保留方式")
    op.drop_table("meal_cancellation_requests")
    op.drop_column("order_fulfillments", "meal_capacity_retained")
