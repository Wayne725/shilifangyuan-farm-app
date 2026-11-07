from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text

from app.models import (
    AdminAudit, Invoice, InvoiceStatus, MembershipType, Order, OrderKind,
    OutboxEvent, PaymentAttempt, PaymentStatus, User, UserRole,
)
from app.routers.invoice_reports import invoice_reports_router
from tests.support import api_test_context, auth_headers


PATH = '/v1/admin/manual-invoice-records'
NOW = datetime(2026, 11, 7, 4, tzinfo=timezone.utc)


@pytest.fixture
async def record_context(database_session):
    admin = User(email='private-admin@example.com', display_name='合成管理員',
        password_hash='private-password-hash', user_role=UserRole.ADMIN)
    member = User(email='private-member@example.com', display_name='合成社員',
        password_hash='private-member-password', membership_type=MembershipType.MEMBER)
    database_session.add_all([admin, member])
    await database_session.commit()
    async with api_test_context(database_session, [invoice_reports_router]) as client:
        yield SimpleNamespace(session=database_session, client=client, admin=admin, member=member)


async def add_order(c, **overrides):
    order = Order(**{
        'order_number': f'M{uuid4().hex[:16]}', 'order_kind': OrderKind.REGULAR,
        'user_id': c.member.id, 'membership_type_snapshot': MembershipType.NONMEMBER,
        'amount_total': 315, 'contact_email': 'private-customer@example.com',
        'payment_status': PaymentStatus.PAID, 'invoice_status': InvoiceStatus.PENDING,
        'invoice_handling': 'on_payment', **overrides,
    })
    c.session.add(order)
    await c.session.flush()
    return order


def add_record(c, order, *, action='invoice.arranged', data=None, **overrides):
    audit = AdminAudit(**{
        'actor_id': c.admin.id, 'action': action, 'aggregate_type': 'order',
        'aggregate_id': order.id, 'reason': '已核對本次開票安排', 'created_at': NOW,
        'data': {'handling': 'on_payment'} if data is None else data, **overrides,
    })
    c.session.add(audit)
    return audit


@pytest.mark.asyncio
async def test_manual_records_require_admin_and_are_not_cacheable(record_context):
    c = record_context
    assert (await c.client.get(PATH)).status_code == 401
    assert (await c.client.get(PATH, headers=auth_headers(c.member))).status_code == 403
    result = await c.client.get(PATH, headers=auth_headers(c.admin))
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    assert result.json() == {'items': [], 'total': 0}


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['limit=0', 'limit=31', 'limit=-1', 'offset=-1'])
async def test_manual_record_pagination_validates_bounds(record_context, query):
    c = record_context
    assert (await c.client.get(f'{PATH}?{query}', headers=auth_headers(c.admin))).status_code == 422


@pytest.mark.asyncio
async def test_only_explicit_manual_website_invoice_events_are_listed(record_context):
    c = record_context
    order = await add_order(c, invoice_handling='external')
    included = [
        add_record(c, order, data={'handling': handling})
        for handling in ('on_payment', 'pickup_day')
    ] + [
        add_record(c, order, action='walk_in_sale.collected', data={'invoice_handling': handling, 'amount': 315})
        for handling in ('on_payment', 'pickup_day')
    ]
    for data in ({'invoice_handling': 'external'}, {}, {'invoice_handling': None},
        {'invoice_handling': 'unexpected'}, {'handling': 'on_payment'}):
        add_record(c, order, action='walk_in_sale.collected', data=data)
    for data in ({'handling': 'external'}, {}, {'handling': None}):
        add_record(c, order, data=data)
    add_record(c, order, action='invoice.issued')
    add_record(c, order, aggregate_type='activity')
    add_record(c, order, aggregate_id=str(uuid4()))
    await add_order(c, invoice_handling='on_payment', invoice_status=InvoiceStatus.ISSUED)
    await c.session.commit()

    response = await c.client.get(PATH, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 4
    assert {item['id'] for item in response.json()['items']} == {audit.id for audit in included}
    assert {item['handling'] for item in response.json()['items']} == {'on_payment', 'pickup_day'}


@pytest.mark.asyncio
async def test_history_fields_are_distinct_from_current_order_and_only_safe_fields_are_returned(record_context):
    c = record_context
    current_due = NOW + timedelta(days=2)
    requested_due = (NOW + timedelta(days=1)).isoformat()
    order = await add_order(c, amount_total=999, invoice_handling='external',
        invoice_issue_after=current_due, invoice_status=InvoiceStatus.ISSUED,
        invoice_provider_context={'api_key': 'private-order-api-key'})
    invoice = Invoice(order_id=order.id, relate_number=f'R{uuid4().hex[:25]}',
        invoice_number='AB12345678', invoice_date=NOW, status=InvoiceStatus.ISSUED,
        buyer_email='private-invoice@example.com',
        provider_request={'secret': 'private-provider-secret'})
    c.session.add(invoice)
    arranged = add_record(c, order, data={
        'handling': 'pickup_day', 'issue_after': requested_due,
        'private': 'private-audit-data',
    })
    collected = add_record(c, order, action='walk_in_sale.collected', data={
        'invoice_handling': 'on_payment', 'amount': 315, 'private': 'private-audit-data',
    })
    await c.session.commit()

    response = await c.client.get(PATH, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    rows = {item['id']: item for item in response.json()['items']}
    assert set(rows[arranged.id]) == {
        'id', 'order_id', 'order_number', 'recorded_at', 'operator_name', 'action',
        'handling', 'amount', 'reason', 'invoice_status', 'invoice_number',
        'invoice_date', 'issue_after', 'requested_issue_after',
    }
    assert rows[arranged.id] == {
        'id': arranged.id, 'order_id': order.id, 'order_number': order.order_number,
        'recorded_at': NOW.isoformat(), 'operator_name': c.admin.display_name,
        'action': 'invoice.arranged', 'handling': 'pickup_day', 'amount': 999,
        'reason': arranged.reason, 'invoice_status': 'issued', 'invoice_number': 'AB12345678',
        'invoice_date': NOW.isoformat(),
        'issue_after': current_due.isoformat(),
        'requested_issue_after': requested_due,
    }
    assert rows[collected.id]['action'] == 'walk_in_sale.collected'
    assert rows[collected.id]['handling'] == 'on_payment'
    assert rows[collected.id]['amount'] == 315
    assert rows[collected.id]['requested_issue_after'] is None
    for private_value in ('private-', 'email', 'password', 'api_key', 'provider_request'):
        assert private_value not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [InvoiceStatus.PENDING, InvoiceStatus.ISSUED, InvoiceStatus.FAILED, InvoiceStatus.VOIDED])
async def test_records_include_current_pending_issued_failed_and_voided_states(record_context, status):
    c = record_context
    order = await add_order(c, invoice_status=status)
    add_record(c, order)
    if status != InvoiceStatus.PENDING:
        c.session.add(Invoice(order_id=order.id, relate_number=f'R{uuid4().hex[:25]}',
            invoice_number='AB12345678', invoice_date=NOW, status=InvoiceStatus.PENDING))
    await c.session.commit()
    response = await c.client.get(PATH, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    row = response.json()['items'][0]
    assert row['invoice_status'] == status.value
    assert row['invoice_number'] == (None if status == InvoiceStatus.PENDING else 'AB12345678')
    assert (row['invoice_date'] is None) == (status == InvoiceStatus.PENDING)


@pytest.mark.asyncio
async def test_records_preserve_missing_operator_without_exposing_an_identifier(record_context):
    c = record_context
    order = await add_order(c)
    await c.session.commit()
    # Imported audit history may outlive its operator; this isolated fixture simulates that legacy row.
    await c.session.execute(text('PRAGMA foreign_keys=OFF'))
    missing_actor = str(uuid4())
    audit = add_record(c, order, actor_id=missing_actor)
    await c.session.commit()
    await c.session.execute(text('PRAGMA foreign_keys=ON'))
    await c.session.commit()

    response = await c.client.get(PATH, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['id'] == audit.id
    assert response.json()['items'][0]['operator_name'] is None
    assert missing_actor not in response.text


@pytest.mark.asyncio
async def test_pagination_counts_and_limits_in_database_with_stable_same_time_order(record_context):
    c = record_context
    order = await add_order(c)
    records = [add_record(c, order, id=f'00000000-0000-0000-0000-{index:012d}') for index in range(37)]
    records[0].created_at = NOW + timedelta(minutes=1)
    await c.session.commit()
    expected = [records[0].id, *[record.id for record in reversed(records[1:])]]
    statements = []

    def capture_statement(_connection, _cursor, statement, parameters, _context, _many):
        statements.append((statement.lower(), parameters))

    event.listen(c.session.bind.sync_engine, 'before_cursor_execute', capture_statement)
    try:
        response = await c.client.get(PATH, headers=auth_headers(c.admin))
    finally:
        event.remove(c.session.bind.sync_engine, 'before_cursor_execute', capture_statement)
    assert response.status_code == 200, response.text
    assert response.json()['total'] == 37
    assert [item['id'] for item in response.json()['items']] == expected[:10]
    page_statements = [(sql, params) for sql, params in statements if 'order by admin_audits.created_at' in sql]
    assert len(page_statements) == 1
    page_sql, parameters = page_statements[0]
    assert 'admin_audits.created_at desc, admin_audits.id desc' in page_sql
    assert 'limit ? offset ?' in page_sql
    assert parameters[-2:] == (10, 0)
    assert any('count(admin_audits.id)' in sql for sql, _ in statements)

    for offset in (10, 20, 30):
        page = await c.client.get(f'{PATH}?limit=10&offset={offset}', headers=auth_headers(c.admin))
        assert page.json()['total'] == 37
        assert [item['id'] for item in page.json()['items']] == expected[offset:offset + 10]
    maximum = await c.client.get(f'{PATH}?limit=30', headers=auth_headers(c.admin))
    assert len(maximum.json()['items']) == 30
    exhausted = await c.client.get(f'{PATH}?offset=100', headers=auth_headers(c.admin))
    assert exhausted.json() == {'items': [], 'total': 37}


@pytest.mark.asyncio
async def test_reading_records_does_not_change_orders_invoices_audits_or_enqueue_work(record_context):
    c = record_context
    order = await add_order(c)
    add_record(c, order)
    await c.session.commit()
    models = (Order, Invoice, AdminAudit, PaymentAttempt, OutboxEvent, User)

    async def snapshot():
        return {
            model.__tablename__: deepcopy((await c.session.execute(
                select(model.__table__).order_by(model.id)
            )).mappings().all())
            for model in models
        }

    before = await snapshot()
    for _ in range(2):
        response = await c.client.get(PATH, headers=auth_headers(c.admin))
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 1
    assert await snapshot() == before
