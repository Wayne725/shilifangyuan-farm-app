import asyncio

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, FulfillmentState, MealCancellationRequest, Order, OutboxEvent, Refund
from app.routers.meals import meals_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import mark_paid, seed_cancellation_order


async def _seed(factory, paid=False):
    async with factory() as session:
        context = await seed_cancellation_order(session)
        if paid:
            await mark_paid(context, FulfillmentState.READY_FOR_PICKUP)
        return context


async def _post(factory, user, path, body):
    async with factory() as session:
        async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
            return await client.post(path, json=body, headers=auth_headers(user))


async def _request(factory, context):
    result = await _post(factory, context.buyer, f"/v1/meal-orders/{context.order.id}/cancellation-requests", {"reason": "測試退單"})
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.asyncio
async def test_concurrent_buyer_requests_create_one_application(postgres_sessions):
    c = await _seed(postgres_sessions)
    results = await asyncio.wait_for(asyncio.gather(*(_request(postgres_sessions, c) for _ in range(5))), timeout=15)
    assert len({result["id"] for result in results}) == 1
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(MealCancellationRequest.id))) == 1


@pytest.mark.asyncio
async def test_concurrent_admin_approvals_queue_only_one_refund(postgres_sessions):
    c = await _seed(postgres_sessions, paid=True)
    request = await _request(postgres_sessions, c)
    path = f"/v1/admin/meal-cancellation-requests/{request['id']}/review"
    results = await asyncio.wait_for(asyncio.gather(*(_post(postgres_sessions, c.admin, path, {"decision": "approve", "reason": "管理員核准"}) for _ in range(5))), timeout=15)
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    assert all(result.json() == results[0].json() for result in results)
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Refund.id))) == 1
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "meal_cancellation.approved")) == 1


@pytest.mark.parametrize("first", ["approve", "pickup"])
@pytest.mark.asyncio
async def test_approval_and_pickup_serialize_on_order_and_cannot_both_succeed(postgres_sessions, monkeypatch, first):
    import app.routers.meals as meals

    c = await _seed(postgres_sessions, paid=True)
    request = await _request(postgres_sessions, c)
    entered, release = asyncio.Event(), asyncio.Event()
    if first == "approve":
        original = meals._cancel_approved_meal_order

        async def gated(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(meals, "_cancel_approved_meal_order", gated)
    else:
        original = meals.enqueue_invoice_issue

        def gated(*args, **kwargs):
            entered.set()
            return original(*args, **kwargs)

        monkeypatch.setattr(meals, "enqueue_invoice_issue", gated)

    async def approve():
        return await _post(postgres_sessions, c.admin, f"/v1/admin/meal-cancellation-requests/{request['id']}/review", {"decision": "approve", "reason": "核准退單"})

    async def pickup():
        return await _post(postgres_sessions, c.admin, f"/v1/admin/meal-events/{c.event.id}/redeem", {"pickup_code": "723456"})

    lead = asyncio.create_task(approve() if first == "approve" else pickup())
    await asyncio.wait_for(entered.wait(), timeout=10)
    follower = asyncio.create_task(pickup() if first == "approve" else approve())
    release.set()
    lead_result, follower_result = await asyncio.wait_for(asyncio.gather(lead, follower), timeout=15)
    assert lead_result.status_code == 200, lead_result.text
    assert follower_result.status_code in {404, 409}, follower_result.text
    async with postgres_sessions() as session:
        order = await session.get(Order, c.order.id)
        assert (order.cancelled_at is not None) is (first == "approve")
        assert await session.scalar(select(func.count(Refund.id))) == (1 if first == "approve" else 0)


@pytest.mark.asyncio
async def test_concurrent_opposite_decisions_preserve_only_the_first_result(postgres_sessions):
    c = await _seed(postgres_sessions, paid=True)
    request = await _request(postgres_sessions, c)
    path = f"/v1/admin/meal-cancellation-requests/{request['id']}/review"
    results = await asyncio.wait_for(asyncio.gather(*(_post(postgres_sessions, c.admin, path, {"decision": decision, "reason": decision}) for decision in ["approve", "reject"])), timeout=15)
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result.json() for result in results if result.status_code == 200)
    async with postgres_sessions() as session:
        stored = await session.get(MealCancellationRequest, request["id"])
        assert stored.status == winner["status"]
        assert await session.scalar(select(func.count(Refund.id))) == (1 if winner["status"] == "approved" else 0)
        assert await session.scalar(select(func.count(AdminAudit.id))) == 1
