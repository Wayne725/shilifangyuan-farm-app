"""Persist the policy accepted during Email registration."""
from alembic import op
import sqlalchemy as sa

revision = '0053_email_registration_consent'
down_revision = '0052_business_meal_orders'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('public_policy_versions',
        sa.Column('version', sa.String(40), primary_key=True),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('published_by_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
    )
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('data_consent_version', sa.String(40), nullable=True))
        batch.add_column(sa.Column('data_consented_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    if (op.get_bind().execute(sa.text('SELECT count(*) FROM users WHERE data_consent_version IS NOT NULL')).scalar()
        or op.get_bind().execute(sa.text('SELECT count(*) FROM public_policy_versions')).scalar()):
        raise RuntimeError('已有註冊同意紀錄，請先保存紀錄後再規劃降版')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('data_consented_at')
        batch.drop_column('data_consent_version')
    op.drop_table('public_policy_versions')
