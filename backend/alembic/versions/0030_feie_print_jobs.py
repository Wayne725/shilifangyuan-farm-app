"""Durable Feie receipt jobs and meal submission idempotency."""
from alembic import op
import sqlalchemy as sa

revision = "0030_feie_print_jobs"
down_revision = "0029_onsite_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("client_request_id", sa.String(36), nullable=True))
    op.add_column("orders", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    op.add_column("orders", sa.Column("kitchen_note", sa.String(500), nullable=True))
    with op.batch_alter_table("orders") as batch:
        batch.create_unique_constraint("uq_orders_user_client_request", ["user_id", "client_request_id"])
    op.create_table("print_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_key", sa.String(120), nullable=False, unique=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reprint_of_id", sa.String(36), sa.ForeignKey("print_jobs.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("printer_fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False), sa.Column("content", sa.Text(), nullable=True),
        sa.Column("vendor_order_id", sa.String(120), nullable=True), sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(240), nullable=True), sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True), sa.Column("lease_token", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True), sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','sending','submitted','printed','failed','unknown','simulated')", name="valid_status"),
        sa.CheckConstraint("mode IN ('dry_run','live')", name="valid_mode"),
        sa.CheckConstraint("kind IN ('order','cancellation')", name="valid_kind"),
    )
    op.create_index("ix_print_jobs_order_id", "print_jobs", ["order_id"])
    op.create_index("ix_print_jobs_due", "print_jobs", ["status", "next_attempt_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM print_jobs")):
        raise RuntimeError("已有出單紀錄，請保留紀錄並向前修復")
    op.drop_table("print_jobs")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("uq_orders_user_client_request", type_="unique")
        batch.drop_column("kitchen_note")
        batch.drop_column("request_fingerprint")
        batch.drop_column("client_request_id")
