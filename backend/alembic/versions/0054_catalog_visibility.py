"""Keep public catalog visibility separate from fulfillment and finance."""
from alembic import op
import sqlalchemy as sa

revision = '0054_catalog_visibility'
down_revision = '0053_email_registration_consent'
branch_labels = None
depends_on = None


def upgrade():
    for table in ['activities', 'group_campaigns']:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column('is_public', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    for table in ['activities', 'group_campaigns']:
        if op.get_bind().execute(sa.text(f'SELECT count(*) FROM {table} WHERE is_public = false')).scalar():
            raise RuntimeError('已有停止公開的活動或共同購買，不可降版重新公開')
    for table in ['group_campaigns', 'activities']:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('is_public')
