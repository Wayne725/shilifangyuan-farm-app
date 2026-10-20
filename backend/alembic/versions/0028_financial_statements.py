"""Original financial statements and their reconciliation references."""
from alembic import op
import sqlalchemy as sa

revision = '0028_financial_statements'
down_revision = '0027_meal_promotions'
branch_labels = depends_on = None


def upgrade():
    op.create_table('financial_statement_imports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('checksum', sa.String(64), nullable=False),
        sa.Column('file_content', sa.LargeBinary(), nullable=False),
        sa.Column('rows', sa.JSON(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('skipped_rows', sa.Integer(), nullable=False),
        sa.Column('uploaded_by_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('source', 'checksum', name='uq_statement_source_checksum'))
    op.create_index('ix_financial_statement_imports_created_at', 'financial_statement_imports', ['created_at'])


def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM financial_statement_imports')).scalar():
        raise RuntimeError('已有匯入明細，禁止移除原始檔及對帳紀錄')
    op.drop_index('ix_financial_statement_imports_created_at', table_name='financial_statement_imports')
    op.drop_table('financial_statement_imports')
