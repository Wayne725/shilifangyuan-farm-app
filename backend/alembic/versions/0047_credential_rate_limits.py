"""Shared, pseudonymous sliding-window budgets for existing credential gates."""
from alembic import op
import sqlalchemy as sa

revision = "0047_credential_rate_limits"
down_revision = "0046_reconciliation_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("credential_rate_limit_buckets",
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("hits", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_credential_rate_limit_expiry_key", "credential_rate_limit_buckets", ["expires_at", "key_hash"])


def downgrade():
    op.drop_index("ix_credential_rate_limit_expiry_key", table_name="credential_rate_limit_buckets")
    op.drop_table("credential_rate_limit_buckets")
