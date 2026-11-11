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


async def test_admin_order_lookup_uses_exact_identity_without_changing_manual_search(database_session):
    buyer, _, first = await make_regular_order(database_session)
    first.order_number = "O2030010210"
    first.contact_email = "first@example.com"
    newer = Order(
        order_number="O20300102100", user_id=buyer.id, order_kind=first.order_kind,
        membership_type_snapshot=first.membership_type_snapshot, amount_total=600,
        contact_email="different@example.com", created_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    admin = User(email="exact-admin@example.test", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    database_session.add_all([newer, admin])
    await database_session.commit()
    path = "/v1/admin/order-search"
    params = {"q": first.order_number}
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        headers = auth_headers(admin)
        fuzzy = await client.get(path, params=params, headers=headers)
        assert [row["id"] for row in fuzzy.json()["items"]] == [newer.id, first.id]
        selected = await client.get(path, params=params | {"order_id": first.id, "limit": 1}, headers=headers)
        assert selected.status_code == 200, selected.text
        assert selected.headers["cache-control"] == "no-store"
        assert selected.json()["total"] == 1
        assert [row["id"] for row in selected.json()["items"]] == [first.id]
        assert selected.json()["items"][0]["contact_email"] == first.contact_email
        missing = await client.get(path, params=params | {"order_id": "unknown"}, headers=headers)
        assert missing.json()["total"] == 0 and missing.json()["items"] == []
        wrong_category = await client.get(path, params=params | {"order_id": first.id, "order_category": "meal"}, headers=headers)
        assert wrong_category.json()["total"] == 0
        assert (await client.get(path, params={"order_id": first.id})).status_code == 401
        assert (await client.get(path, params={"order_id": first.id}, headers=auth_headers(buyer))).status_code == 403
        assert (await client.get(path, params={"order_id": "x" * 37}, headers=headers)).status_code == 422


async def test_failed_email_retry_requires_confirmation_but_optional_reason(database_session):
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
        assert missing_reason.status_code == 409
        retry = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(buyer), json={"reason": "已修正寄信設定"})
        assert retry.status_code == 409, retry.text
        retry = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(buyer),
            json={"reason": "  ", "confirm_delivery_risk": True})
        assert retry.status_code == 200, retry.text
        assert retry.json()['status'] == 'pending'
        again = await client.post(f'/v1/admin/failed-jobs/{event.id}/retry', headers=auth_headers(buyer), json={"reason": "重複按鈕"})
        assert again.status_code == 409
        rejected = await client.post(f'/v1/admin/failed-jobs/{invoice_mail.id}/retry', headers=auth_headers(buyer), json={"reason": "測試"})
        assert rejected.status_code == 409


@pytest.mark.parametrize('unavailable', [False, True])
async def test_admin_payment_query_accepts_optional_reason_and_only_calls_supplier_query(database_session, monkeypatch, unavailable):
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
        result = await client.post(path, headers=auth_headers(admin), json={})
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


async def test_admin_search_category_payment_and_item_search_cover_all_pages(database_session):
    from app.models import OrderItem, SalesChannel, TaxType

    buyer, _product, regular = await make_regular_order(database_session)
    buyer.display_name = "分類查詢買家"
    buyer.email = regular.contact_email = "category-buyer@example.com"
    admin = User(email="category-admin@example.test", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    group = Order(order_number="CATEGORY-GROUP", user_id=buyer.id, order_kind=regular.order_kind,
                  sales_channel=SalesChannel.GROUP, membership_type_snapshot=regular.membership_type_snapshot,
                  amount_total=100, contact_email=buyer.email, payment_status=PaymentStatus.PAID,
                  items=[OrderItem(product_name="分類共同購買品項", unit_label="份", unit_price=100, subtotal=100, quantity=1, tax_type=TaxType.TAXABLE)])
    meal = Order(order_number="CATEGORY-MEAL", user_id=buyer.id, order_kind=regular.order_kind,
                 sales_channel=SalesChannel.MEAL_PREORDER, membership_type_snapshot=regular.membership_type_snapshot,
                 amount_total=100, contact_email=buyer.email)
    database_session.add_all([admin, group, meal])
    await database_session.commit()
    async with api_test_context(database_session, [operations_router], settings=make_test_settings()) as client:
        headers = auth_headers(admin)
        params = {"order_category": "general", "q": buyer.display_name, "limit": 1}
        first = await client.get("/v1/admin/order-search", params=params, headers=headers)
        second = await client.get("/v1/admin/order-search", params=params | {"offset": 1}, headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.json()["total"] == second.json()["total"] == 2
        assert {first.json()["items"][0]["id"], second.json()["items"][0]["id"]} == {regular.id, group.id}
        found = await client.get("/v1/admin/order-search", params={"order_category": "general", "q": "分類共同", "payment_status": "paid"}, headers=headers)
        assert [row["id"] for row in found.json()["items"]] == [group.id]
        assert (await client.get("/v1/admin/order-search", params={"order_category": "meal", "q": "分類共同"}, headers=headers)).json()["total"] == 0
        meal_page = await client.get("/v1/admin/order-search", params={"order_category": "meal"}, headers=headers)
        assert [row["id"] for row in meal_page.json()["items"]] == [meal.id]
        for params in [{"order_category": "bad"}, {"payment_status": "bad"}]:
            assert (await client.get("/v1/admin/order-search", params=params, headers=headers)).status_code == 422
