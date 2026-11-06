from dataclasses import replace
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.error import URLError
import socket

import pytest
from sqlalchemy import func, select

import app.jobs as jobs
from app.integrations.common import HTTPResponse, IntegrationError
from app.integrations.email_sender import EmailMessage, EmailSendResult, FailoverEmailSender
from app.integrations.mailersend import MailerSendAdapter, MailerSendSettings
from app.integrations.resend import ResendAdapter, ResendSettings
from app.models import AdminAudit, OutboxEvent, OutboxStatus, PaymentStatus, UserRole
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_integrations import make_regular_order
from tests.test_notification_dedupe import notification_command, notification_context
from app.integrations.notifications import NotificationService, SQLAlchemyNotificationRepository


MESSAGE = EmailMessage(to_email='recipient@example.com', subject='隔離送達驗證', text_content='沒有真寄信', idempotency_key='isolated-email-intent')


def adapters(mode, calls):
    async def primary(url, payload, headers, timeout):
        calls.append('primary')
        if mode == 'timeout':
            raise IntegrationError('isolated lost response') from TimeoutError()
        if mode == 'dns':
            raise IntegrationError('isolated no request sent') from URLError(socket.gaierror(-2, 'isolated'))
        status, body = mode
        return HTTPResponse(status, body, {})
    async def backup(url, payload, headers, timeout):
        calls.append('backup')
        return HTTPResponse(202, '', {'x-message-id': 'isolated-backup-id'})
    return ResendAdapter(ResendSettings(api_key='dummy', sender_email='sender@example.com'), transport=primary), MailerSendAdapter(MailerSendSettings(api_token='dummy', sender_email='sender@example.com'), transport=backup)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['timeout', (503, '{"name":"service_unavailable"}'), (500, '{"name":"application_error"}'),
    (409, '{"name":"concurrent_idempotent_requests"}'), (409, '{"name":"invalid_idempotent_request"}'), (200, '{}'), (200, 'broken json')])
async def test_ambiguous_resend_result_never_falls_back(mode):
    calls = []
    primary, backup = adapters(mode, calls)
    with pytest.raises(IntegrationError) as error:
        await FailoverEmailSender([primary, backup]).send(MESSAGE)
    assert type(error.value).__name__ == 'EmailDeliveryUnknown'
    assert calls == ['primary']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', [(422, '{"name":"validation_error"}'), 'dns'])
async def test_definitive_rejection_and_proven_pre_request_failure_keep_normal_fallback(mode):
    calls = []
    primary, backup = adapters(mode, calls)
    result = await FailoverEmailSender([primary, backup]).send(MESSAGE)
    assert result.accepted and result.provider == 'mailersend'
    assert calls == ['primary', 'backup']


@pytest.mark.asyncio
async def test_mailersend_success_without_message_id_is_unknown_but_all_suppressed_is_known_not_sent():
    async def no_id(*args):
        return HTTPResponse(202, '', {})
    sender = MailerSendAdapter(MailerSendSettings(api_token='dummy', sender_email='sender@example.com'), transport=no_id)
    with pytest.raises(IntegrationError) as error:
        await sender.send(MESSAGE)
    assert type(error.value).__name__ == 'EmailDeliveryUnknown'
    async def suppressed(*args):
        return HTTPResponse(202, '{"warnings":[{"type":"ALL_SUPPRESSED"}]}', {})
    sender = MailerSendAdapter(MailerSendSettings(api_token='dummy', sender_email='sender@example.com'), transport=suppressed)
    with pytest.raises(IntegrationError) as error:
        await sender.send(MESSAGE)
    assert type(error.value).__name__ == 'EmailDeliveryRejected'
    assert error.value.allow_failover is False


async def queue_email(context):
    session, users = context
    await NotificationService(SQLAlchemyNotificationRepository(session)).publish(notification_command(users[0]))
    await session.commit()
    return await session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == 'send_email'))


@pytest.mark.asyncio
async def test_unknown_email_stops_automatic_retry_and_keeps_one_local_intent(notification_context, monkeypatch):
    session, _ = notification_context
    event = await queue_email(notification_context)
    event_id = event.id
    calls = []
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: FailoverEmailSender(adapters('timeout', calls)))
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    current = await session.get(OutboxEvent, event_id)
    assert current.status == OutboxStatus.FAILED
    assert current.payload['email_delivery']['state'] == 'unknown'
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc) + timedelta(days=2), 1)
    assert calls == ['primary']
    assert await session.scalar(select(func.count(OutboxEvent.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('after_real_commit', [False, True])
async def test_provider_accepted_then_final_db_commit_error_never_sends_again(notification_context, monkeypatch, after_real_commit):
    session, _ = notification_context
    event = await queue_email(notification_context)
    event_id = event.id
    sent = []
    class Accepting:
        async def send(self, message):
            sent.append(message.idempotency_key)
            return EmailSendResult(True, 'isolated-provider-id', 202, 'mailersend')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: Accepting())
    original_commit = session.commit
    failed = False
    async def fail_final_commit_once():
        nonlocal failed
        finishing = any(isinstance(row, OutboxEvent) and row.id == event_id and row.status == OutboxStatus.COMPLETED for row in session.dirty)
        if finishing and not failed:
            failed = True
            if after_real_commit:
                await original_commit()
            raise RuntimeError('isolated lost final database acknowledgement')
        await original_commit()
    monkeypatch.setattr(session, 'commit', fail_final_commit_once)
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    current = await session.get(OutboxEvent, event_id)
    if after_real_commit:
        assert current.status == OutboxStatus.COMPLETED
    else:
        assert current.status == OutboxStatus.FAILED
        assert current.payload['email_delivery']['state'] == 'unknown'
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc) + timedelta(days=2), 1)
    assert sent == [f'outbox-{event_id}']


@pytest.mark.asyncio
async def test_complete_db_outage_after_acceptance_leaves_processing_then_stale_unknown(notification_context, monkeypatch):
    session, _ = notification_context
    event = await queue_email(notification_context)
    event_id = event.id
    sent = []
    class Accepting:
        async def send(self, message):
            sent.append(message.idempotency_key)
            return EmailSendResult(True, 'isolated-provider-id', 202, 'mailersend')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: Accepting())
    original_commit = session.commit
    async def db_down_after_external_acceptance():
        if sent:
            raise RuntimeError('isolated database unavailable')
        await original_commit()
    monkeypatch.setattr(session, 'commit', db_down_after_external_acceptance)
    with pytest.raises(RuntimeError, match='database unavailable'):
        await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    await session.rollback()
    assert (await session.get(OutboxEvent, event_id)).status == OutboxStatus.PROCESSING
    monkeypatch.setattr(session, 'commit', original_commit)
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc) + timedelta(minutes=11), 1)
    current = await session.get(OutboxEvent, event_id)
    assert current.status == OutboxStatus.FAILED
    assert current.payload['email_delivery']['state'] == 'unknown'
    assert len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('event_type', ['send_email', 'auth.email_verification_requested', 'auth.password_reset_requested'])
async def test_stale_processing_mail_is_unknown_and_never_automatically_resent(notification_context, monkeypatch, event_type):
    session, users = notification_context
    event = OutboxEvent(event_type=event_type, aggregate_type='user', aggregate_id=users[0].id,
        payload={'recipient': users[0].email, 'verification_token': 'dummy', 'reset_token': 'dummy', 'to_email': users[0].email, 'subject': '隔離', 'text_content': '不該重寄'},
        status=OutboxStatus.PROCESSING, attempts=1, available_at=datetime.now(timezone.utc) - timedelta(minutes=11))
    session.add(event)
    await session.commit()
    event_id = event.id
    class UnexpectedSender:
        async def send(self, message):
            pytest.fail('stale mail must not reach a provider')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: UnexpectedSender())
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    current = await session.get(OutboxEvent, event_id)
    assert current.status == OutboxStatus.FAILED
    assert current.payload['email_delivery']['state'] == 'unknown'
    assert current.attempts == 1


@pytest.mark.asyncio
async def test_admin_unknown_mail_retry_requires_explicit_confirmation_and_audits_it(database_session):
    session = database_session
    admin, _product, order = await make_regular_order(session)
    admin.user_role = UserRole.ADMIN
    order.payment_status = PaymentStatus.PAID
    event = OutboxEvent(event_type='send_email', aggregate_type='user', aggregate_id=admin.id, status=OutboxStatus.FAILED,
        payload={'event_type': 'payment_succeeded', 'data': {'order_id': order.id}, 'to_email': admin.email,
                 'email_delivery': {'state': 'unknown', 'provider': 'resend', 'idempotency_key': 'outbox-isolated'}},
        last_error='Email delivery result unknown')
    session.add(event)
    await session.commit()
    event_id = event.id
    async with api_test_context(session, [operations_router], settings=make_test_settings()) as client:
        headers = auth_headers(admin)
        listed = await client.get('/v1/admin/failed-jobs', headers=headers)
        row = next(row for row in listed.json()['items'] if row['id'] == event_id)
        assert row['delivery_unknown'] is True
        assert row['requires_delivery_confirmation'] is True
        missing_confirmation = await client.post(f'/v1/admin/failed-jobs/{event_id}/retry', headers=headers, json={'reason': '尚未明確確認'})
        assert missing_confirmation.status_code == 409
        confirmed = await client.post(f'/v1/admin/failed-jobs/{event_id}/retry', headers=headers,
            json={'reason': '已於供應商後台核對未寄出', 'confirm_delivery_risk': True})
        assert confirmed.status_code == 200
        assert confirmed.json()['id'] == event_id
        audit = await session.scalar(select(AdminAudit).where(AdminAudit.action == 'outbox.retry_requested', AdminAudit.aggregate_id == event_id))
        assert audit.data['confirm_delivery_risk'] is True
        assert audit.data['previous_delivery']['state'] == 'unknown'
        repeated = await client.post(f'/v1/admin/failed-jobs/{event_id}/retry', headers=headers,
            json={'reason': '不得重複排入', 'confirm_delivery_risk': True})
        assert repeated.status_code == 409


@pytest.mark.asyncio
async def test_sender_configuration_failure_is_not_unknown_and_can_retry_normally(notification_context, monkeypatch):
    from app.integrations.common import IntegrationConfigurationError
    session, _ = notification_context
    event = await queue_email(notification_context)
    event_id = event.id
    def unconfigured(_):
        raise IntegrationConfigurationError('isolated missing provider configuration')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', unconfigured)
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    current = await session.get(OutboxEvent, event_id)
    assert current.status == OutboxStatus.PENDING
    assert current.payload['email_delivery']['state'] != 'unknown'
    sent = []
    class Accepting:
        async def send(self, message):
            sent.append(message.idempotency_key)
            return EmailSendResult(True, 'configured-now', 200, 'resend')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: Accepting())
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc) + timedelta(minutes=5), 1)
    assert sent == [f'outbox-{event_id}']
    assert (await session.get(OutboxEvent, event_id)).status == OutboxStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize('event_type', ['auth.email_verification_requested', 'auth.password_reset_requested'])
async def test_normal_auth_mail_still_sends_once_and_removes_credentials(notification_context, monkeypatch, event_type):
    session, users = notification_context
    event = OutboxEvent(event_type=event_type, aggregate_type='user', aggregate_id=users[0].id,
        payload={'recipient': users[0].email, 'verification_token': 'isolated-token', 'reset_token': 'isolated-reset'})
    session.add(event)
    await session.commit()
    event_id = event.id
    sent = []
    class Accepting:
        async def send(self, message):
            sent.append(message)
            return EmailSendResult(True, 'normal-auth', 202, 'mailersend')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: Accepting())
    assert await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1) == (1, 0)
    assert len(sent) == 1 and sent[0].idempotency_key == f'outbox-{event_id}'
    current = await session.get(OutboxEvent, event_id)
    assert current.payload['email_delivery']['state'] == 'accepted'
    assert 'verification_token' not in current.payload and 'reset_token' not in current.payload


@pytest.mark.asyncio
async def test_auth_unknown_is_visible_but_requires_fresh_user_request(database_session):
    admin, _product, _order = await make_regular_order(database_session)
    admin.user_role = UserRole.ADMIN
    event = OutboxEvent(event_type='auth.email_verification_requested', aggregate_type='user', aggregate_id=admin.id,
        status=OutboxStatus.FAILED, payload={'email_delivery': {'state': 'unknown'}}, last_error='must-not-leak-secret')
    database_session.add(event)
    await database_session.commit()
    event_id = event.id
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        headers = auth_headers(admin)
        response = await client.get('/v1/admin/failed-jobs?q=寄送結果未知', headers=headers)
        row = next(row for row in response.json()['items'] if row['id'] == event_id)
        assert row['delivery_unknown'] is True and row['retryable'] is False
        assert '重新申請' in row['retry_blocker']
        assert 'must-not-leak-secret' not in response.text
        rejected = await client.post(f'/v1/admin/failed-jobs/{event_id}/retry', headers=headers,
            json={'reason': '不能重送舊憑證', 'confirm_delivery_risk': True})
        assert rejected.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,status', [('payment_succeeded', PaymentStatus.REFUNDED), ('refund_completed', PaymentStatus.PAID)])
async def test_unknown_retry_does_not_bypass_original_order_state_gates(database_session, kind, status):
    admin, _product, order = await make_regular_order(database_session)
    admin.user_role = UserRole.ADMIN
    order.payment_status = status
    event = OutboxEvent(event_type='send_email', aggregate_type='user', aggregate_id=admin.id, status=OutboxStatus.FAILED,
        payload={'event_type': kind, 'data': {'order_id': order.id}, 'email_delivery': {'state': 'unknown'}})
    database_session.add(event)
    await database_session.commit()
    event_id = event.id
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        response = await client.post(f'/v1/admin/failed-jobs/{event_id}/retry', headers=auth_headers(admin),
            json={'reason': '已查詢但訂單不符合', 'confirm_delivery_risk': True})
        assert response.status_code == 409
        assert (await database_session.get(OutboxEvent, event_id)).status == OutboxStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize('attempts,metadata,should_send', [(1, None, False), (0, None, True), (1, {'state': 'rejected'}, True), (1, 'legacy-unstructured', False)])
async def test_legacy_pending_mail_requires_structured_proof_before_automatic_retry(notification_context, monkeypatch, attempts, metadata, should_send):
    session, _ = notification_context
    event = await queue_email(notification_context)
    event.attempts = attempts
    event.last_error = 'HTTP 422 text alone does not prove a previous request was not sent'
    if metadata is not None:
        event.payload = {**event.payload, 'email_delivery': metadata}
    await session.commit()
    event_id = event.id
    sent = []
    class Sender:
        async def send(self, message):
            sent.append(message.idempotency_key)
            return EmailSendResult(True, 'legacy-review-result', 202, 'mailersend')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: Sender())
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    current = await session.get(OutboxEvent, event_id)
    if should_send:
        assert sent == [f'outbox-{event_id}']
        assert current.status == OutboxStatus.COMPLETED
    else:
        assert sent == []
        assert current.status == OutboxStatus.FAILED
        assert current.payload['email_delivery']['state'] == 'unknown'
        assert current.attempts == attempts


@pytest.mark.asyncio
async def test_manual_resend_authorization_is_consumed_by_claim_and_not_reused_after_crash(notification_context, monkeypatch):
    session, _ = notification_context
    event = await queue_email(notification_context)
    event.attempts = 1
    event.payload = {**event.payload, 'email_delivery': {'state': 'resend_authorized', 'confirmed_by': 'isolated-admin'}}
    await session.commit()
    event_id = event.id
    sent = []
    class CrashAfterAcceptance:
        async def send(self, message):
            sent.append(message.idempotency_key)
            raise asyncio.CancelledError()
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _: CrashAfterAcceptance())
    with pytest.raises(asyncio.CancelledError):
        await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc), 1)
    await session.rollback()
    current = await session.get(OutboxEvent, event_id)
    assert current.status == OutboxStatus.PROCESSING
    assert current.payload['email_delivery']['state'] == 'sending'
    assert 'confirmed_by' not in current.payload['email_delivery']
    await jobs._process_outbox(session, make_test_settings(), datetime.now(timezone.utc) + timedelta(minutes=11), 1)
    current = await session.get(OutboxEvent, event_id)
    assert current.status == OutboxStatus.FAILED
    assert current.payload['email_delivery']['state'] == 'unknown'
    assert sent == [f'outbox-{event_id}']


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata,requires_confirmation', [(None, True), ({'state': 'rejected'}, False)])
async def test_legacy_failed_mail_is_unknown_but_new_known_rejection_keeps_normal_retry(database_session, metadata, requires_confirmation):
    admin, _product, order = await make_regular_order(database_session)
    admin.user_role = UserRole.ADMIN
    order.payment_status = PaymentStatus.PAID
    payload = {'event_type': 'payment_succeeded', 'data': {'order_id': order.id}, 'to_email': admin.email,
        'subject': '隔離確認', 'text_content': '不會真寄信'}
    if metadata is not None:
        payload['email_delivery'] = metadata
    event = OutboxEvent(event_type='send_email', aggregate_type='user', aggregate_id=admin.id,
        status=OutboxStatus.FAILED, attempts=8, payload=payload, last_error='HTTP 422 text must not replace structured proof')
    database_session.add(event)
    await database_session.commit()
    event_id = event.id
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        headers = auth_headers(admin)
        listed = await client.get('/v1/admin/failed-jobs', headers=headers)
        row = next(row for row in listed.json()['items'] if row['id'] == event_id)
        assert row['delivery_unknown'] is requires_confirmation
        assert row['requires_delivery_confirmation'] is requires_confirmation
        retried = await client.post(f'/v1/admin/failed-jobs/{event_id}/retry', headers=headers, json={'reason': '核對後重試'})
        if requires_confirmation:
            assert retried.status_code == 409
            retried = await client.post(f'/v1/admin/failed-jobs/{event_id}/retry', headers=headers,
                json={'reason': '已查詢供應商確認未寄出', 'confirm_delivery_risk': True})
            assert retried.status_code == 200
            current = await database_session.get(OutboxEvent, event_id)
            assert current.payload['email_delivery']['state'] == 'resend_authorized'
            audit = await database_session.scalar(select(AdminAudit).where(AdminAudit.aggregate_id == event_id, AdminAudit.action == 'outbox.retry_requested'))
            assert audit.data['previous_delivery']['legacy_unverified'] is True
        else:
            assert retried.status_code == 200
