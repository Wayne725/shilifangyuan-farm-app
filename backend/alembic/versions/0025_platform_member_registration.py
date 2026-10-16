"""Persist platform controls and encrypted existing-member LINE intake."""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone

revision = "0025_platform_member_intake"
down_revision = "0024_line_data_consent"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("line_oauth_states") as batch:
        batch.add_column(sa.Column("member_claim_encrypted", sa.Text(), nullable=True))
    settings = sa.table("system_settings", sa.column("key", sa.String()), sa.column("value", sa.JSON()), sa.column("updated_at", sa.DateTime(timezone=True)))
    op.bulk_insert(settings, [{"key": "storefront_operations", "value": {"orders_open": True, "closed_message": "目前暫停接受新訂單"}, "updated_at": datetime.now(timezone.utc)}])


def downgrade():
    op.execute(sa.text("DELETE FROM system_settings WHERE key = 'storefront_operations'"))
    with op.batch_alter_table("line_oauth_states") as batch:
        batch.drop_column("member_claim_encrypted")
