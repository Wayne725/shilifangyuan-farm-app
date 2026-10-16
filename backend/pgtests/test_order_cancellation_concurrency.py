import asyncio

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, MealCancellationRequest, OutboxEvent, Refund, SalesChannel
from app.routers.orders import orders_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import mark_paid, seed_cancellation_order


async def post(factory, user, path, body):
    async with factory() as session:
        async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
            return await client.post(path, json=body, headers=auth_headers(user))


@pytest.mark.asyncio
async def test_concurrent_customer_requests_and_admin_approvals_have_one_refund(postgres_sessions):
    async with postgres_sessions() as session:
        c = await seed_cancellation_order(session)
        c.order.sales_channel = SalesChannel.REGULAR
        c.order.contact_email = "review-buyer@example.com"
        await mark_paid(c)
    results = await asyncio.wait_for(asyncio.gather(*(post(postgres_sessions, c.buyer, f"/v1/orders/{c.order.id}/cancel", {"reason": "重複申請"}) for _ in range(4))), timeout=15)
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    ids = {result.json()["cancellation_request"]["id"] for result in results}
    assert len(ids) == 1
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Refund.id))) == 0
    path = f"/v1/orders/{c.order.id}/admin/cancellation-requests/{ids.pop()}/review"
    results = await asyncio.wait_for(asyncio.gather(*(post(postgres_sessions, c.admin, path, {"decision": "approve", "reason": "核對後核准"}) for _ in range(4))), timeout=15)
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(MealCancellationRequest.id))) == 1
        assert await session.scalar(select(func.count(Refund.id))) == 1
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "order_cancellation.approved")) == 1
