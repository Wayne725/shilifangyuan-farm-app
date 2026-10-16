import pytest

from app.models import Order, OutboxEvent, OutboxStatus, PaymentStatus, UserRole
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_integrations import make_regular_order


async def exercise_failed_job_search(session):
    admin, _product, paid_order = await make_regular_order(session)
    admin.user_role = UserRole.ADMIN
    paid_order.payment_status = PaymentStatus.PAID
    refunded_order = Order(order_number="SLF-SEARCH-REFUNDED", user_id=admin.id, order_kind=paid_order.order_kind,
                           membership_type_snapshot=paid_order.membership_type_snapshot, amount_total=10,
                           payment_status=PaymentStatus.REFUNDED, contact_email="test@example.test")
    session.add(refunded_order)
    await session.flush()
    jobs = [
        OutboxEvent(event_type="invoice.issue_requested", aggregate_type="order", aggregate_id=refunded_order.id,
                    payload={"order_id": refunded_order.id}, last_error="unrecognised secret=must-not-leak"),
        OutboxEvent(event_type="send_email", aggregate_type="user", aggregate_id=admin.id,
                    payload={"event_type": "payment_succeeded", "data": {"order_id": paid_order.id}, "to_email": "private@example.test"}, last_error="HTTP 503 private-access-token"),
        OutboxEvent(event_type="refund.requested", aggregate_type="refund", aggregate_id="refund-test",
                    payload={"order_id": refunded_order.id}, last_error="connection failed with private-token"),
        OutboxEvent(event_type="unclassified.task", aggregate_type="order", aggregate_id=paid_order.id,
                    payload={}, last_error="timeout private-token"),
    ]
    for job in jobs:
        job.status = OutboxStatus.FAILED
        job.attempts = 8
    session.add_all(jobs)
    await session.commit()
    headers = auth_headers(admin)
    async with api_test_context(session, [operations_router], settings=make_test_settings()) as client:
        result = await client.get('/v1/admin/failed-jobs?limit=1', headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()['total'] == 4
        first_id = result.json()['items'][0]['id']
        next_page = await client.get('/v1/admin/failed-jobs?limit=1&offset=1', headers=headers)
        assert next_page.json()['items'][0]['id'] != first_id
        found = await client.get('/v1/admin/failed-jobs', params={'q': refunded_order.order_number, 'kind': 'invoice'}, headers=headers)
        assert [row['id'] for row in found.json()['items']] == [jobs[0].id]
        row = found.json()['items'][0]
        assert row['order_number'] == refunded_order.order_number
        assert row['title'] == '電子發票開立'
        assert row['created_at']
        assert row['description'] and row['next_action']
        assert row['retryable'] is False
        assert '已退款' in row['retry_blocker']
        for term, expected in [('付款成功', jobs[1]), ('連線', jobs[2]), ('逾時', jobs[3]), (jobs[0].id, jobs[0])]:
            response = await client.get('/v1/admin/failed-jobs', params={'q': term}, headers=headers)
            assert [item['id'] for item in response.json()['items']] == [expected.id]
        for kind, expected in [('refund', jobs[2]), ('email', jobs[1]), ('other', jobs[3])]:
            response = await client.get('/v1/admin/failed-jobs', params={'kind': kind}, headers=headers)
            assert [item['id'] for item in response.json()['items']] == [expected.id]
            assert response.json()['items'][0]['order_id']
        empty = await client.get('/v1/admin/failed-jobs', params={'q': '%'}, headers=headers)
        assert empty.json()['total'] == 0
        invalid = await client.get('/v1/admin/failed-jobs?kind=unknown', headers=headers)
        assert invalid.status_code == 422
        invalid = await client.get('/v1/admin/failed-jobs', params={'q': 'x' * 121}, headers=headers)
        assert invalid.status_code == 422
        all_items = await client.get('/v1/admin/failed-jobs', headers=headers)
        assert all(word not in all_items.text for word in ['must-not-leak', 'private-token', 'private-access-token', 'to_email', 'private@example.test'])
        admin.user_role = UserRole.CUSTOMER
        await session.commit()
        denied = await client.get('/v1/admin/failed-jobs?q=SLF', headers=headers)
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_failed_job_search_has_safe_context_filters_and_real_pagination(database_session):
    await exercise_failed_job_search(database_session)
