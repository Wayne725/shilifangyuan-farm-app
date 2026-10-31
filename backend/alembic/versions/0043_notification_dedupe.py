"""Deduplicate new notification delivery without guessing historical email links."""
import hashlib

from alembic import op
import sqlalchemy as sa


revision = '0043_notification_dedupe'
down_revision = '0042_open_proposal_target'
branch_labels = None
depends_on = None

BATCH_SIZE = 500


def _backfill_notification_hashes():
    connection = op.get_bind()
    table = sa.table('notifications', sa.column('id', sa.String), sa.column('user_id', sa.String),
        sa.column('event_type', sa.String), sa.column('data', sa.JSON),
        sa.column('created_at', sa.DateTime), sa.column('dedupe_hash', sa.String))
    existing = table.alias('canonical')
    update = table.update().where(table.c.id == sa.bindparam('notice_id'), ~sa.exists(
        sa.select(1).select_from(existing).where(existing.c.user_id == sa.bindparam('scope_user'),
            existing.c.event_type == sa.bindparam('scope_event'), existing.c.dedupe_hash == sa.bindparam('token'))
    )).values(dedupe_hash=sa.bindparam('token'))
    rows = connection.execute(sa.select(
        table.c.id, table.c.user_id, table.c.event_type, table.c.data
    ).order_by(table.c.created_at, table.c.id).execution_options(stream_results=True)).yield_per(BATCH_SIZE)
    try:
        for batch in rows.partitions(BATCH_SIZE):
            values = []
            for row in batch:
                key = row.data.get('dedupe_key') if isinstance(row.data, dict) else None
                if isinstance(key, str):
                    values.append({'notice_id': row.id, 'scope_user': row.user_id, 'scope_event': row.event_type,
                        'token': hashlib.sha256(key.encode('utf-8')).hexdigest()})
            if values:
                connection.execute(update, values)
    finally:
        rows.close()


def upgrade():
    op.add_column('notifications', sa.Column('dedupe_hash', sa.String(64), nullable=True))
    op.add_column('notifications', sa.Column('delivery_version', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('uq_notification_user_event_dedupe', 'notifications', ['user_id', 'event_type', 'dedupe_hash'], unique=True)
    _backfill_notification_hashes()
    op.add_column('outbox_events', sa.Column('notification_id', sa.String(36), nullable=True))
    with op.batch_alter_table('outbox_events') as batch:
        batch.create_foreign_key('fk_outbox_events_notification_id_notifications', 'notifications', ['notification_id'], ['id'], ondelete='SET NULL')
    op.create_index('uq_outbox_notification_intent', 'outbox_events', ['notification_id'], unique=True)


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text('SELECT COUNT(*) FROM notifications WHERE delivery_version > 0')) or connection.scalar(
        sa.text('SELECT COUNT(*) FROM outbox_events WHERE notification_id IS NOT NULL')):
        raise RuntimeError('已有新版通知或郵件意圖，降版會失去防重複保護；請先完成資料保全')
    op.drop_index('uq_outbox_notification_intent', table_name='outbox_events')
    with op.batch_alter_table('outbox_events') as batch:
        batch.drop_constraint('fk_outbox_events_notification_id_notifications', type_='foreignkey')
        batch.drop_column('notification_id')
    op.drop_index('uq_notification_user_event_dedupe', table_name='notifications')
    op.drop_column('notifications', 'delivery_version')
    op.drop_column('notifications', 'dedupe_hash')
