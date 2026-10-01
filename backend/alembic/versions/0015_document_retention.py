"""Track new document deadlines without deleting or backfilling legacy data."""

from alembic import op
import sqlalchemy as sa

revision = "0015_document_retention"
down_revision = "0014_invoice_account_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("membership_documents", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("membership_documents", sa.Column("deletion_attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("membership_documents", sa.Column("deletion_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("membership_documents", sa.Column("deletion_error", sa.String(80), nullable=True))
    op.create_index("ix_membership_documents_expires_at", "membership_documents", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_membership_documents_expires_at", table_name="membership_documents")
    for name in ("deletion_error", "deletion_retry_at", "deletion_attempts", "expires_at"):
        op.drop_column("membership_documents", name)
