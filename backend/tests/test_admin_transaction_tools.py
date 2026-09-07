import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Order, OutboxEvent, OutboxStatus, PaymentAttempt, PaymentStatus, User, UserRole
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_integrations import make_regular_order

pytestmark = pytest.mark.asyncio


async def test_admin_order_search_is_paginated_and_role_protected(database_session):
    buyer, _product, first = await make_regular_order(database_session)
    first.contact_email = "buyer@example.com"
    admin = User(email="operations@example.com", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    second = Order(order_number="SLF-SECOND", user_id=buyer.id, order_kind=first.order_kind, membership_type_snapshot=first.membership_type_snapshot, amount_total=10, contact_email="second@example.com")
    database_session.add_all([admin, second])
    await database_session.commit()
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        page = await client.get('/v1/admin/order-search?limit=1', headers=auth_headers(admin))
        assert page.status_code == 200, page.text
        assert page.json()['total'] == 2
        assert len(page.json()['items']) == 1
        next_page = await client.get('/v1/admin/order-search?limit=1&offset=1', headers=auth_headers(admin))
        assert page.json()['items'][0]['id'] != next_page.json()['items'][0]['id']
        found = await client.get('/v1/admin/order-search?q=SECOND', headers=auth_headers(admin))
        assert [row['id'] for row in found.json()['items']] == [second.id]
        denied = await client.get('/v1/admin/order-search', headers=auth_headers(buyer))
        assert denied.status_code == 403
        invalid = await client.get('/v1/admin/order-search?limit=1000', headers=auth_headers(admin))
        assert invalid.status_code == 422


async def test_failed_email_retry_requires_reason_and_cannot_repeat_or_retry_invoice_mail(database_session):
    buyer, _product, _order = await make_regular_order(database_session)
    buyer.user_role = UserRole.ADMIN
    _order.payment_status = PaymentStatus.PAID
    event = OutboxEvent(event_type="send_email", aggregate_type="user", aggregate_id=buyer.id, status=OutboxStatus.FAILED, attempts=8, last_error="APIKey=must-not-leak timeout", payload={"event_type": "payment_succeeded", "to_email": "buyer@example.com", "subject": "付款成功通知", "text_content": "已付款"})
    invoice_mail = OutboxEvent(event_type="send_email", aggregate_type="user", aggregate_id=buyer.id, status=OutboxStatus.FAILED, payload={"event_type": "invoice_issued"})
    database_session.add_all([event, invoice_mail])
    event.payload = {**event.payload, "data": {"order_id": _order.id}}
    await database_session.commit()
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        result = await client.get('/v1/admin/failed-jobs', headers=auth_headers(buyer))
        assert result.status_code == 200, result.text
        assert 'must-not-leak' not in result.text
        assert 'to_email' not in result.text
        missing_reason = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(buyer), json={"reason": "  "})
        assert missing_reason.status_code == 422
        retry = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(buyer), json={"reason": "已修正寄信設定"})
        assert retry.status_code == 200, retry.text
        assert retry.json()['status'] == 'pending'
        again = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(buyer), json={"reason": "重複按鈕"})
        assert again.status_code == 409
        rejected = await client.post(f'/v1/admin/failed-jobs/{invoice_mail.id}/retry', headers=auth_headers(buyer), json={"reason": "測試"})
        assert rejected.status_code == 409


@pytest.mark.parametrize('unavailable', [False, True])
async def test_admin_payment_query_requires_reason_and_only_calls_supplier_query(database_session, monkeypatch, unavailable):
    import app.routers.payments as routes
    from app.integrations.common import HTTPResponse
    from app.integrations.raygate import RayGateAdapter
    from tests.test_raygate import payment_result, raygate_settings

    buyer, _product, order = await make_regular_order(database_session)
    admin = User(email='audit@example.com', display_name='管理員', password_hash='unused', user_role=UserRole.ADMIN)
    attempt = PaymentAttempt(order=order, provider='raygate', merchant_trade_no='ADMINQUERY001', amount=order.amount_total,
                             expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
    database_session.add_all([admin, attempt])
    await database_session.commit()
    calls = []

    async def transport(url, envelope, _headers, _timeout):
        calls.append(url)
        assert '/api/query/' in url
        assert adapter.decrypt_transaction(envelope['TransactionData']) == {'pos_order_number': 'ADMINQUERY001'}
        if unavailable:
            raise TimeoutError('APIKey=not-for-clients')
        data = payment_result(pos_order_number='ADMINQUERY001', amount=order.amount_total, status=0)
        return HTTPResponse(200, json.dumps({'ErrorCode': '0000', 'Data': [data]}), {})

    adapter = RayGateAdapter(raygate_settings(), transport=transport)
    monkeypatch.setattr(routes, 'raygate_payment_adapter_from_settings', lambda _: adapter)
    path = f'/v1/admin/orders/{order.id}/payment/query'
    async with api_test_context(database_session, [routes.payments_router], settings=make_test_settings()) as client:
        denied = await client.post(path, headers=auth_headers(buyer), json={'reason': '查詢'})
        assert denied.status_code == 403
        missing = await client.post(path, headers=auth_headers(admin), json={'reason': ' '})
        assert missing.status_code == 422
        result = await client.post(path, headers=auth_headers(admin), json={'reason': '買家反映付款結果尚未更新'})
        assert result.status_code == (502 if unavailable else 200), result.text
        assert 'not-for-clients' not in result.text
        if not unavailable:
            assert result.json()['status'] == 'pending'
            assert result.json()['attempt_id'] == attempt.id
        assert len(calls) == 1


@pytest.mark.parametrize('kind,order_status', [
    ('email_verification', PaymentStatus.PAID),
    ('payment_succeeded', PaymentStatus.REFUNDED),
    ('invoice.issue_requested', PaymentStatus.PENDING),
    ('invoice.issue_requested', PaymentStatus.REFUNDED),
])
async def test_retry_rejects_unprivileged_users_and_outdated_work(database_session, kind, order_status):
    buyer, _product, order = await make_regular_order(database_session)
    order.payment_status = order_status
    admin = User(email='operator@example.com', display_name='管理員', password_hash='unused', user_role=UserRole.ADMIN)
    event = OutboxEvent(event_type=kind if kind.startswith('invoice.') else 'send_email', aggregate_type='user',
                        aggregate_id=buyer.id, status=OutboxStatus.FAILED,
                        payload={'event_type': kind, 'order_id': order.id, 'data': {'order_id': order.id}})
    database_session.add_all([admin, event])
    await database_session.commit()
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        denied = await client.get('/v1/admin/failed-jobs', headers=auth_headers(buyer))
        assert denied.status_code == 403
        denied = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(buyer), json={'reason': '未授權操作'})
        assert denied.status_code == 403
        blocked = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(admin), json={'reason': '核對舊工作'})
        assert blocked.status_code == 409
        listing = await client.get('/v1/admin/failed-jobs', headers=auth_headers(admin))
        assert listing.json()['items'][0]['retryable'] is False
