import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.jobs as jobs
from app.integrations.email_sender import EmailSendResult
from app.integrations.notifications import NotificationService, SQLAlchemyNotificationRepository
from app.models import OutboxEvent, OutboxStatus, User
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import make_test_settings
from tests.test_notification_dedupe import notification_command


async def seed_email(factory):
    async with factory() as session:
        user = User(email='pg-email-unknown@example.com', display_name='PG送達驗證', password_hash='unused')
        session.add(user)
        await session.flush()
        await NotificationService(SQLAlchemyNotificationRepository(session)).publish(notification_command(user))
        await session.commit()
        return (await session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == 'send_email'))).id


@pytest.mark.asyncio
async def test_concurrent_pg_workers_send_a_normal_email_only_once(postgres_sessions, monkeypatch):
    event_id = await seed_email(postgres_sessions)
    sent = []
    class Sender:
        async def send(self, message):
            sent.append(message.idempotency_key)
            await asyncio.sleep(.02)
            return EmailSendResult(True, 'pg-provider-id', 202, 'mailersend')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: Sender())
    barrier = asyncio.Barrier(8)
    async def run():
        async with postgres_sessions() as session:
            await barrier.wait()
            return await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    results = await asyncio.gather(*(run() for _ in range(8)))
    assert sum(result[0] for result in results) == 1
    assert sent == [f'outbox-{event_id}']
    async with postgres_sessions() as session:
        event = await session.get(OutboxEvent, event_id)
        assert event.status == OutboxStatus.COMPLETED
        assert event.payload['email_delivery']['state'] == 'accepted'


@pytest.mark.asyncio
@pytest.mark.parametrize('after_real_commit', [False, True])
async def test_pg_acceptance_then_lost_commit_ack_never_automatically_resends(postgres_sessions, monkeypatch, after_real_commit):
    event_id = await seed_email(postgres_sessions)
    sent = []
    class Sender:
        async def send(self, message):
            sent.append(message.idempotency_key)
            return EmailSendResult(True, 'pg-provider-id', 202, 'mailersend')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: Sender())
    async with postgres_sessions() as session:
        original_commit = session.commit
        failed = False
        async def lost_commit_ack():
            nonlocal failed
            finishing = any(isinstance(row, OutboxEvent) and row.id == event_id and row.status == OutboxStatus.COMPLETED for row in session.dirty)
            if finishing and not failed:
                failed = True
                if after_real_commit:
                    await original_commit()
                raise RuntimeError('isolated pg commit acknowledgement lost')
            await original_commit()
        monkeypatch.setattr(session, 'commit', lost_commit_ack)
        await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    async with postgres_sessions() as session:
        event = await session.get(OutboxEvent, event_id)
        if after_real_commit:
            assert event.status == OutboxStatus.COMPLETED
        else:
            assert event.status == OutboxStatus.FAILED
            assert event.payload['email_delivery']['state'] == 'unknown'
        await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc) + timedelta(days=2), 1)
    assert sent == [f'outbox-{event_id}']
