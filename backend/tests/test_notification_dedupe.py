from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.jobs as jobs
from app.integrations.notifications import NotificationCommand, NotificationService, SQLAlchemyNotificationRepository
from app.models import Notification, OutboxEvent, OutboxStatus, SystemSetting, User
from tests.support import make_test_settings


@pytest.fixture
async def notification_context(database_session):
    users = [User(email=f'notification-{i}@example.com', display_name=f'通知測試{i}', password_hash='unused') for i in range(2)]
    database_session.add_all(users)
    await database_session.commit()
    return database_session, users


def notification_command(user, **changes):
    return NotificationCommand(user_id=user.id, event_type='payment_succeeded', title='隔離通知',
        body='只供本機回放驗證', email=user.email, dedupe_key='same-event', **changes)


async def notification_counts(session):
    return (await session.scalar(select(func.count(Notification.id))),
        await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == 'send_email')))


@pytest.mark.asyncio
async def test_replaying_same_scope_preserves_one_notification_and_email_intent(notification_context):
    session, users = notification_context
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    command = notification_command(users[0])
    first = await service.publish(command)
    await session.commit()
    second = await service.publish(replace(command, title='回放不覆寫原通知'))
    await session.commit()
    assert first.id == second.id
    assert await notification_counts(session) == (1, 1)
    assert second.title == '隔離通知'
    assert second.data['dedupe_key'] == 'same-event'


@pytest.mark.asyncio
async def test_equal_keys_in_other_users_or_event_types_remain_distinct(notification_context):
    session, users = notification_context
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    for user in users:
        for event_type in ['payment_succeeded', 'refund_completed']:
            command = replace(notification_command(user), event_type=event_type)
            await service.publish(command)
            await service.publish(command)
    await session.commit()
    assert await notification_counts(session) == (4, 4)


@pytest.mark.asyncio
async def test_none_key_keeps_the_original_non_deduplicated_behavior(notification_context):
    session, users = notification_context
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    command = replace(notification_command(users[0]), dedupe_key=None)
    await service.publish(command)
    await service.publish(command)
    await session.commit()
    assert await notification_counts(session) == (2, 2)


@pytest.mark.asyncio
async def test_caller_rollback_removes_notification_email_and_other_pending_writes(notification_context):
    session, users = notification_context
    session.add(SystemSetting(key='caller-rollback', value={'pending': True}))
    await NotificationService(SQLAlchemyNotificationRepository(session)).publish(notification_command(users[0]))
    await session.rollback()
    assert await notification_counts(session) == (0, 0)
    assert await session.get(SystemSetting, 'caller-rollback') is None


@pytest.mark.asyncio
async def test_duplicate_does_not_rollback_unrelated_caller_changes(notification_context):
    session, users = notification_context
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    command = notification_command(users[0])
    await service.publish(command)
    await session.commit()
    session.add(SystemSetting(key='caller-kept', value={'pending': True}))
    await service.publish(command)
    await session.commit()
    assert (await session.get(SystemSetting, 'caller-kept')).value == {'pending': True}
    assert await notification_counts(session) == (1, 1)


@pytest.mark.asyncio
async def test_domain_conversion_never_commits_the_callers_transaction(notification_context):
    session, users = notification_context
    user_id = users[0].id
    event = OutboxEvent(event_type='proposal.approved', aggregate_type='vote_proposal', aggregate_id='isolated-proposal', payload={'user_id': user_id})
    session.add(event)
    await session.commit()
    event_id = event.id
    await jobs._process_domain_event(session, event)
    await session.rollback()
    assert await notification_counts(session) == (0, 0)
    assert (await session.get(OutboxEvent, event_id)).status == OutboxStatus.PENDING


@pytest.mark.asyncio
async def test_worker_failure_before_source_completion_rolls_back_effects_then_replays_once(notification_context, monkeypatch):
    session, users = notification_context
    event = OutboxEvent(event_type='proposal.approved', aggregate_type='vote_proposal', aggregate_id='isolated-worker-proposal', payload={'user_id': users[0].id})
    session.add(event)
    await session.commit()
    event_id = event.id
    original = jobs._dispatch_outbox_event
    failing = True
    async def fail_after_dispatch(session, settings, current_id):
        await original(session, settings, current_id)
        if failing:
            raise RuntimeError('isolated crash after domain effect before completed')
    monkeypatch.setattr(jobs, '_dispatch_outbox_event', fail_after_dispatch)
    assert await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1) == (0, 1)
    assert await notification_counts(session) == (0, 0)
    failing = False
    source = await session.get(OutboxEvent, event_id)
    source.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    assert await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1) == (1, 0)
    assert await notification_counts(session) == (1, 1)
    assert (await session.get(OutboxEvent, event_id)).status == OutboxStatus.COMPLETED


@pytest.mark.asyncio
async def test_empty_key_is_still_a_key_and_user_data_cannot_override_it(notification_context):
    session, users = notification_context
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    command = replace(notification_command(users[0]), dedupe_key='', data={'dedupe_key': 'spoofed', 'value': 1})
    first = await service.publish(command)
    second = await service.publish(command)
    await session.commit()
    assert first.id == second.id
    assert first.data['dedupe_key'] == ''
    assert await notification_counts(session) == (1, 1)


@pytest.mark.asyncio
async def test_group_wrapper_does_not_suppress_another_event_with_the_same_key(notification_context):
    from app.group_review import publish_campaign_notification_once
    session, users = notification_context
    first = notification_command(users[0])
    assert await publish_campaign_notification_once(session, first) is True
    assert await publish_campaign_notification_once(session, replace(first, event_type='refund_completed')) is True
    assert await publish_campaign_notification_once(session, first) is False
    await session.commit()
    assert await notification_counts(session) == (2, 2)


@pytest.mark.asyncio
async def test_replaying_an_already_committed_effect_with_pending_source_never_requeues_email(notification_context):
    session, users = notification_context
    event = OutboxEvent(event_type='proposal.approved', aggregate_type='vote_proposal', aggregate_id='already-converted', payload={'user_id': users[0].id})
    session.add(event)
    await session.commit()
    event_id = event.id
    original = await NotificationService(SQLAlchemyNotificationRepository(session)).publish(
        replace(notification_command(users[0]), event_type=event.event_type, dedupe_key=f'outbox:{event_id}'))
    email = await session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == 'send_email'))
    email.available_at = datetime.now(timezone.utc) + timedelta(days=1)
    await session.commit()
    original_id = original.id
    assert await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1) == (1, 0)
    assert await notification_counts(session) == (1, 1)
    assert (await session.get(Notification, original_id)).title == '隔離通知'
    assert (await session.get(OutboxEvent, event_id)).status == OutboxStatus.COMPLETED
