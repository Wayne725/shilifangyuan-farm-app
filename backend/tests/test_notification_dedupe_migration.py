import asyncio
import hashlib
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.database import create_engine
from app.integrations.notifications import NotificationCommand, NotificationService, SQLAlchemyNotificationRepository
from app.models import Notification, OutboxEvent
from tests.test_group_review_migration import MigrationDatabase, NOW


PREVIOUS = '0042_open_proposal_target'
REVISION = '0043_notification_dedupe'
PG_URL = os.environ.get('NOTIFICATION_MIGRATION_POSTGRES_URL')


@pytest.fixture(params=['sqlite'] + (['postgres'] if PG_URL else []))
def notification_migration_db(request, tmp_path, monkeypatch):
    database_name = None
    if request.param == 'postgres':
        import asyncpg
        base = make_url(PG_URL)
        if base.host not in {'localhost', '127.0.0.1'} or base.database != 'notification_migration_test':
            pytest.fail('Only the disposable local notification_migration_test database is allowed')
        database_name = f'notification_migration_test_{uuid4().hex[:12]}'
        admin_url = base.set(drivername='postgresql', database='postgres').render_as_string(hide_password=False)
        async def database_command(sql):
            connection = await asyncpg.connect(admin_url)
            try:
                await connection.execute(sql)
            finally:
                await connection.close()
        asyncio.run(database_command(f'CREATE DATABASE "{database_name}"'))
        url = base.set(drivername='postgresql+asyncpg', database=database_name).render_as_string(hide_password=False)
    else:
        url = f'sqlite+aiosqlite:///{tmp_path / "notification-dedupe.db"}'
    monkeypatch.setenv('DATABASE_URL', url)
    get_settings.cache_clear()
    try:
        yield MigrationDatabase(url)
    finally:
        get_settings.cache_clear()
        if database_name:
            asyncio.run(database_command(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))


def seed_history(database):
    command.upgrade(database.config, PREVIOUS)
    for user_id in ['legacy-user', 'other-user']:
        database.insert('users', id=user_id, email=f'{user_id}@example.com', display_name='舊通知社員', password_hash='unchanged',
            user_role='customer', membership_type='nonmember', is_active=True, token_version=0,
            pending_member_claim=False, created_at=NOW, updated_at=NOW)
    entries = [
        ('older', 'legacy-user', 'payment_succeeded', {'dedupe_key': 'history'}, NOW - timedelta(hours=1)),
        ('duplicate', 'legacy-user', 'payment_succeeded', {'dedupe_key': 'history'}, NOW),
        ('other-user-notice', 'other-user', 'payment_succeeded', {'dedupe_key': 'history'}, NOW),
        ('other-event', 'legacy-user', 'refund_completed', {'dedupe_key': 'history'}, NOW),
        ('no-key', 'legacy-user', 'payment_succeeded', {}, NOW),
    ]
    for notice_id, user_id, event_type, data, created_at in entries:
        database.insert('notifications', id=notice_id, user_id=user_id, event_type=event_type,
            title='不可改寫的歷史標題', body='不可刪除的歷史內容', data=data, created_at=created_at)
    for event_id, status in [('old-pending-email', 'pending'), ('old-completed-email', 'completed')]:
        database.insert('outbox_events', id=event_id, event_type='send_email', aggregate_type='user', aggregate_id='legacy-user',
            payload={'to_email': 'legacy-user@example.com', 'subject': '不可據此猜配對', 'text_content': '保留原意圖', 'event_type': 'payment_succeeded', 'data': {}},
            status=status, attempts=2, available_at=NOW, created_at=NOW)
    return database.rows('notifications'), database.rows('outbox_events')


def test_migration_preserves_history_canonicalizes_keys_and_never_guesses_email_links(notification_migration_db):
    database = notification_migration_db
    before_notifications, before_outbox = seed_history(database)
    command.upgrade(database.config, REVISION)
    database.assert_schema_matches()
    notices = {row['id']: row for row in database.rows('notifications')}
    assert len(notices) == len(before_notifications)
    for before in before_notifications:
        assert all(notices[before['id']][key] == value for key, value in before.items())
        assert notices[before['id']]['delivery_version'] == 0
    token = hashlib.sha256(b'history').hexdigest()
    assert notices['older']['dedupe_hash'] == token
    assert notices['duplicate']['dedupe_hash'] is None
    assert notices['other-user-notice']['dedupe_hash'] == token
    assert notices['other-event']['dedupe_hash'] == token
    assert notices['no-key']['dedupe_hash'] is None
    for before, after in zip(before_outbox, database.rows('outbox_events')):
        assert all(after[key] == value for key, value in before.items())
        assert after['notification_id'] is None

    async def replay():
        engine = create_engine(database.url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                service = NotificationService(SQLAlchemyNotificationRepository(session))
                command_value = NotificationCommand(user_id='legacy-user', event_type='payment_succeeded',
                    title='回放不可覆寫歷史', body='回放不可猜測補寄', email='legacy-user@example.com', dedupe_key='history')
                row = await service.publish(command_value)
                await session.commit()
                assert row.id == 'older'
                assert await session.scalar(sa.select(sa.func.count(Notification.id))) == 5
                assert await session.scalar(sa.select(sa.func.count(OutboxEvent.id))) == 2
        finally:
            await engine.dispose()
    asyncio.run(replay())
    command.downgrade(database.config, PREVIOUS)
    assert database.rows('notifications') == before_notifications
    assert database.rows('outbox_events') == before_outbox


def test_migration_protects_new_local_notification_and_email_dedupe_data_from_downgrade(notification_migration_db):
    database = notification_migration_db
    seed_history(database)
    command.upgrade(database.config, REVISION)
    async def create_new():
        engine = create_engine(database.url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await NotificationService(SQLAlchemyNotificationRepository(session)).publish(NotificationCommand(
                    user_id='legacy-user', event_type='payment_succeeded', title='新通知', body='新意圖不可失去防重複保護',
                    email='legacy-user@example.com', dedupe_key='new-key'))
                await session.commit()
        finally:
            await engine.dispose()
    asyncio.run(create_new())
    with pytest.raises(RuntimeError, match='防重複保護'):
        command.downgrade(database.config, PREVIOUS)
    assert len(database.rows('notifications')) == 6
    assert len(database.rows('outbox_events')) == 3
    assert sum(row['notification_id'] is not None for row in database.rows('outbox_events')) == 1


def test_backfill_keeps_duplicates_across_batches_without_dropping_history(notification_migration_db):
    database = notification_migration_db
    seed_history(database)
    table = database.table('notifications')
    database.run(lambda connection: connection.execute(table.insert(), [
        {'id': f'batch-{i:04d}', 'user_id': 'legacy-user', 'event_type': 'payment_succeeded',
         'title': '批次保留通知', 'body': '保留每筆歷史', 'data': {'dedupe_key': f'batch-key-{i % 300}'},
         'created_at': NOW + timedelta(seconds=i)} for i in range(600)
    ]))
    command.upgrade(database.config, REVISION)
    notices = database.rows('notifications')
    batched = [row for row in notices if row['id'].startswith('batch-')]
    assert len(batched) == 600
    assert sum(row['dedupe_hash'] is not None for row in batched) == 300
    assert all(row['dedupe_hash'] is not None for row in batched[:300])
    assert all(row['dedupe_hash'] is None for row in batched[300:])
    assert all(row['delivery_version'] == 0 for row in batched)


def test_empty_database_upgrade_downgrade_and_metadata_match(notification_migration_db):
    database = notification_migration_db
    command.upgrade(database.config, REVISION)
    database.assert_schema_matches()
    assert database.rows('notifications') == []
    assert database.rows('outbox_events') == []
    command.downgrade(database.config, PREVIOUS)
