import csv
import io
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from app.models import AdminAudit, MemberRosterEntry, Membership, MembershipOfflineReceipt, MembershipStatus, Notification, Order, PaymentAttempt, Refund
from app.routers.member_reports import member_reports_router
from app.routers.platform import platform_router
from tests.support import auth_headers
from tests.test_meal_booking_policy import booking_context
from tests.test_member_shares import shares_context, claim_roster


def include(client, router):
    client._transport.app.include_router(router)


@pytest.mark.asyncio
async def test_platform_closure_blocks_new_orders_and_can_reopen(booking_context):
    client, session, buyer, admin, event, body = booking_context
    include(client, platform_router)
    public = await client.get('/v1/platform')
    assert public.json()['orders_open'] is True
    assert public.headers['cache-control'] == 'no-store'
    assert (await client.put('/v1/admin/platform', headers=auth_headers(buyer), json={'orders_open': False, 'reason': '暫停'})).status_code == 403
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=body, headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    assert (await client.put('/v1/admin/platform', headers=auth_headers(admin), json={'orders_open': False, 'closed_message': '今日暫停接單'})).status_code == 200
    blocked = await client.post(f'/v1/meal-events/{event.id}/orders', json=body, headers=auth_headers(buyer))
    assert blocked.status_code == 409 and blocked.json()['detail'] == '今日暫停接單'
    assert (await client.get(f'/v1/meal-orders/{created.json()["id"]}', headers=auth_headers(buyer))).status_code == 200
    assert await session.scalar(select(func.count(Order.id))) == 1
    await client.put('/v1/admin/platform', headers=auth_headers(admin), json={'orders_open': True, 'reason': '恢復'})
    assert (await client.post(f'/v1/meal-events/{event.id}/orders', json=body, headers=auth_headers(buyer))).status_code == 201
    assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == 'platform.settings_updated')) == 2


@pytest.mark.asyncio
async def test_register_export_deduplicates_claimed_member_and_preserves_suspension(shares_context):
    c = shares_context
    include(c['client'], member_reports_router)
    client, headers = c['client'], c['admin_headers']
    assert (await client.get('/v1/admin/member-register.csv', headers=c['owner_headers'])).status_code == 403
    before = await client.get('/v1/admin/member-register', headers=headers)
    assert before.json()['total'] == 1
    assert before.json()['items'][0]['share_certificate_number'] == 'LEGACY-SHARES-001'
    member_id = await claim_roster(c)
    after = await client.get('/v1/admin/member-register', headers=headers)
    assert after.json()['total'] == 1 and after.json()['items'][0]['source'] == 'membership'
    exported = await client.get('/v1/admin/member-register.csv', headers=headers)
    rows = list(csv.reader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert len(rows) == 2 and '股號' in rows[0] and 'LEGACY-SHARES-001' in rows[1]
    member = await c['session'].get(Membership, member_id)
    member.status = MembershipStatus.SUSPENDED
    await c['session'].commit()
    assert (await client.get('/v1/admin/member-register', headers=headers)).json()['total'] == 0
    assert (await client.get('/v1/admin/member-register?status=all', headers=headers)).json()['total'] == 1


@pytest.mark.asyncio
async def test_register_newest_before_pagination_while_export_keeps_member_numbers(shares_context):
    c = shares_context
    include(c['client'], member_reports_router)
    client, session, headers = c['client'], c['session'], c['admin_headers']
    membership = await session.get(Membership, await claim_roster(c))
    now = datetime.now(timezone.utc)
    membership.created_at = now - timedelta(days=1)
    new_ids = []
    for number in ('SHARES-002', 'SHARES-003'):
        result = await client.post('/v1/admin/member-roster', headers=headers, json={
            'member_number': number, 'legal_name': '查詢測試', 'email': f'{number}@example.com',
            'phone': '0912345678', 'share_count': 0, 'share_capital_amount': 0,
        })
        assert result.status_code == 201, result.text
        new_ids.append(result.json()['id'])
        (await session.get(MemberRosterEntry, new_ids[-1])).created_at = now
    await session.commit()
    items = []
    for offset in range(3):
        response = await client.get('/v1/admin/member-register', headers=headers,
                                    params={'q': 'SHARES', 'limit': 1, 'offset': offset})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 3
        items.extend(response.json()['items'])
    assert [row['id'] for row in items] == sorted(new_ids, reverse=True) + [membership.id]
    assert all('_created_at' not in row for row in items)
    filtered = await client.get('/v1/admin/member-register', headers=headers, params={'q': 'SHARES-001'})
    assert [row['id'] for row in filtered.json()['items']] == [membership.id]
    exported = await client.get('/v1/admin/member-register.csv', headers=headers)
    csv_rows = list(csv.reader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert [row[0] for row in csv_rows[1:]] == ['SHARES-001', 'SHARES-002', 'SHARES-003']


@pytest.mark.asyncio
async def test_receipts_idempotent_private_and_void_without_financial_side_effect(shares_context):
    c = shares_context
    include(c['client'], member_reports_router)
    client, session, headers = c['client'], c['session'], c['admin_headers']
    member_id = await claim_roster(c)
    body = {'idempotency_key': str(uuid4()), 'target_type': 'membership', 'target_id': member_id, 'kind': 'share_capital', 'amount': 5000, 'payment_method': 'bank_transfer', 'paid_at': (datetime.now(timezone.utc)-timedelta(days=1)).isoformat(), 'reference': '=PAYMENT12345', 'confirmed_received': True}
    assert (await client.post('/v1/admin/membership-offline-receipts', json=body, headers=c['owner_headers'])).status_code == 403
    created = await client.post('/v1/admin/membership-offline-receipts', json=body, headers=headers)
    assert created.status_code == 201, created.text
    assert (await client.post('/v1/admin/membership-offline-receipts', json=body, headers=headers)).json() == created.json()
    assert (await client.post('/v1/admin/membership-offline-receipts', json={**body, 'reference': 'different'}, headers=headers)).status_code == 409
    stored = await session.get(MembershipOfflineReceipt, created.json()['id'])
    assert 'PAYMENT12345' not in stored.payer_reference_encrypted
    assert await session.scalar(select(func.count(MembershipOfflineReceipt.id))) == 1
    assert await session.scalar(select(func.count(Notification.id)).where(Notification.event_type == 'membership_payment_recorded')) == 1
    report = (await client.get('/v1/admin/membership-payments', headers=headers)).json()
    assert report['summary'] == {'received': 5000, 'refunded': 0, 'net': 5000}
    exported = await client.get('/v1/admin/membership-payments.csv', headers=headers)
    assert "'=PAYMENT12345" in exported.text
    path = f'/v1/admin/membership-offline-receipts/{stored.id}/void'
    assert (await client.post(path, headers=headers, json={'reason': '  '})).status_code == 200
    await session.refresh(stored)
    assert stored.void_reason == ''
    assert (await client.get('/v1/admin/membership-payments', headers=headers)).json()['summary']['net'] == 0
    for model in (PaymentAttempt, Refund, Order):
        assert await session.scalar(select(func.count(model.id))) == 0
    member = await session.get(Membership, member_id)
    assert member.share_count == 0 and member.status == MembershipStatus.ACTIVE


def test_csv_neutralizes_formulas():
    from app.csv_export import csv_response
    body = csv_response('test.csv', ['姓名'], [[' \t=1+1'], ['+1'], ['@X'], [-10]]).body.decode()
    assert "' \t=1+1" in body and "'+1" in body and '-10' in body
