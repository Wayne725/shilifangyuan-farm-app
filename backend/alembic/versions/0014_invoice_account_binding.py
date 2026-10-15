"""Bind new invoice intents to their originating environment and account."""

from alembic import op
import sqlalchemy as sa


revision = "0014_invoice_account_binding"
down_revision = "0013_payment_poll_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical origin cannot be inferred from today's deployment settings.
    op.add_column("orders", sa.Column("invoice_provider_context", sa.JSON(), nullable=True))
    op.add_column("invoices", sa.Column("provider_context", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "provider_context")
    op.drop_column("orders", "invoice_provider_context")
