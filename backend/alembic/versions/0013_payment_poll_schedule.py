"""Persist fair payment polling without changing provider payloads."""

from alembic import op
import sqlalchemy as sa


revision = "0013_payment_poll_schedule"
down_revision = "0012_raygate_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_attempts",
        sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_payment_attempts_next_reconcile_at", "payment_attempts", ["next_reconcile_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_attempts_next_reconcile_at", table_name="payment_attempts")
    op.drop_column("payment_attempts", "next_reconcile_at")
