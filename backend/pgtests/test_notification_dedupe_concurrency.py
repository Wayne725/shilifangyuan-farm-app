import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.integrations.notifications import NotificationService, SQLAlchemyNotificationRepository
from app.models import Notification, OutboxEvent, SystemSetting, User
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.test_notification_dedupe import notification_command


async def seed_users(factory):
    async with factory() as session:
        users = [User(email=f'pg-notification-{i}@example.com', display_name=f'PG通知{i}', password_hash='unused') for i in range(2)]
        session.add_all(users)
        await session.commit()
        return users


@pytest.mark.asyncio
async def test_concurrent_same_scope_creates_one_notification_and_one_email_intent(postgres_sessions):
    users = await seed_users(postgres_sessions)
    barrier = asyncio.Barrier(8)
    async def publish(worker):
        async with postgres_sessions() as session:
            await barrier.wait()
            session.add(SystemSetting(key=f'notification-caller-{worker}', value={'worker': worker}))
            row = await NotificationService(SQLAlchemyNotificationRepository(session)).publish(notification_command(users[0]))
            await session.commit()
            return row.id
    ids = await asyncio.gather(*(publish(worker) for worker in range(8)))
    assert len(set(ids)) == 1
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Notification.id))) == 1
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == 'send_email')) == 1
        assert await session.scalar(select(func.count(SystemSetting.key)).where(SystemSetting.key.like('notification-caller-%'))) == 8
        assert (await session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == 'send_email'))).notification_id == ids[0]


@pytest.mark.asyncio
async def test_concurrent_keys_remain_scoped_to_user_and_event(postgres_sessions):
    users = await seed_users(postgres_sessions)
    commands = [replace(notification_command(user), event_type=event_type) for user in users for event_type in ['payment_succeeded', 'refund_completed']]
    barrier = asyncio.Barrier(8)
    async def publish(command):
        async with postgres_sessions() as session:
            await barrier.wait()
            row = await NotificationService(SQLAlchemyNotificationRepository(session)).publish(command)
            await session.commit()
            return row.id
    ids = await asyncio.gather(*(publish(command) for command in commands for _ in range(2)))
    assert len(set(ids)) == 4
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Notification.id))) == 4
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == 'send_email')) == 4


@pytest.mark.asyncio
async def test_concurrent_none_key_keeps_each_notification_and_email(postgres_sessions):
    users = await seed_users(postgres_sessions)
    barrier = asyncio.Barrier(8)
    async def publish():
        async with postgres_sessions() as session:
            await barrier.wait()
            row = await NotificationService(SQLAlchemyNotificationRepository(session)).publish(replace(notification_command(users[0]), dedupe_key=None))
            await session.commit()
            return row.id
    ids = await asyncio.gather(*(publish() for _ in range(8)))
    assert len(set(ids)) == 8
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Notification.id))) == 8
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == 'send_email')) == 8


@pytest.mark.asyncio
async def test_caller_rollback_keeps_no_postgres_notification_or_email_intent(postgres_sessions):
    users = await seed_users(postgres_sessions)
    async with postgres_sessions() as session:
        session.add(SystemSetting(key='notification-rolled-back', value={'pending': True}))
        await NotificationService(SQLAlchemyNotificationRepository(session)).publish(notification_command(users[0]))
        await session.rollback()
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Notification.id))) == 0
        assert await session.scalar(select(func.count(OutboxEvent.id))) == 0
        assert await session.get(SystemSetting, 'notification-rolled-back') is None
