import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

import app.meal_kitchen as kitchen
import app.routers.meals as meals
from app.models import AdminAudit, FulfillmentState, MealEventOffering, Order, OrderFulfillment, Refund
from app.routers.meal_kitchen import meal_kitchen_router
from app.routers.meals import meals_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import mark_paid, seed_cancellation_order


async def _seed(factory):
    async with factory() as session:
        c = await seed_cancellation_order(session)
        await mark_paid(c)
        return c


async def _change(factory, c):
    async with factory() as session:
        async with api_test_context(session, [meal_kitchen_router], settings=make_test_settings()) as client:
            return await client.patch(f"/v1/admin/meal-kitchen/orders/{c.order.id}/status", json={"status": "preparing"}, headers=auth_headers(c.admin))


async def _request(factory, c):
    async with factory() as session:
        async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
            response = await client.post(f"/v1/meal-orders/{c.order.id}/cancellation-requests", json={"reason": "測試退單"}, headers=auth_headers(c.buyer))
            assert response.status_code == 201
            return response.json()


async def _approve(factory, c, request_id):
    async with factory() as session:
        async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
            return await client.post(f"/v1/admin/meal-cancellation-requests/{request_id}/review", json={"decision": "approve", "reason": "核准退單"}, headers=auth_headers(c.admin))


@pytest.mark.asyncio
async def test_concurrent_start_buttons_change_once_and_keep_one_audit(postgres_sessions):
    c = await _seed(postgres_sessions)
    results = await asyncio.wait_for(asyncio.gather(*(_change(postgres_sessions, c) for _ in range(4))), timeout=15)
    assert all(response.status_code == 200 for response in results), [response.text for response in results]
    async with postgres_sessions() as session:
        fulfillment = await session.scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == c.order.id))
        assert fulfillment.status == FulfillmentState.PREPARING
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "meal_kitchen.status_changed")) == 1


@pytest.mark.parametrize("first", ["prepare", "approve"])
@pytest.mark.asyncio
async def test_preparation_and_cancellation_serialize_and_preserve_prepared_capacity(postgres_sessions, monkeypatch, first):
    c = await _seed(postgres_sessions)
    request = await _request(postgres_sessions, c)
    entered, release = asyncio.Event(), asyncio.Event()
    if first == "prepare":
        original = kitchen._locked_kitchen_order
        async def gated(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        monkeypatch.setattr(kitchen, "_locked_kitchen_order", gated)
    else:
        original = meals._cancel_approved_meal_order
        async def gated(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(meals, "_cancel_approved_meal_order", gated)
    lead = asyncio.create_task(_change(postgres_sessions, c) if first == "prepare" else _approve(postgres_sessions, c, request["id"]))
    await asyncio.wait_for(entered.wait(), timeout=10)
    follower = asyncio.create_task(_approve(postgres_sessions, c, request["id"]) if first == "prepare" else _change(postgres_sessions, c))
    release.set()
    lead_response, follower_response = await asyncio.wait_for(asyncio.gather(lead, follower), timeout=15)
    assert lead_response.status_code == 200, lead_response.text
    assert follower_response.status_code == (200 if first == "prepare" else 409), follower_response.text
    async with postgres_sessions() as session:
        fulfillment = await session.scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == c.order.id))
        offering = await session.get(MealEventOffering, c.offering.id)
        assert fulfillment.status == FulfillmentState.CANCELLED
        assert fulfillment.meal_capacity_retained is (first == "prepare")
        assert offering.paid_quantity == (2 if first == "prepare" else 0)
        assert await session.scalar(select(func.count(Refund.id))) == 1


@pytest.mark.asyncio
async def test_waiting_kitchen_change_refreshes_pickup_result_instead_of_overwriting_it(postgres_sessions):
    c = await _seed(postgres_sessions)
    async with postgres_sessions() as session:
        order = await session.scalar(select(Order).where(Order.id == c.order.id).with_for_update())
        fulfillment = await session.scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == order.id).with_for_update())
        fulfillment.status = FulfillmentState.PICKED_UP
        fulfillment.fulfilled_at = datetime.now(timezone.utc)
        waiting = asyncio.create_task(_change(postgres_sessions, c))
        await asyncio.sleep(0.1)
        assert not waiting.done()
        await session.commit()
        response = await asyncio.wait_for(waiting, timeout=15)
        assert response.status_code == 409, response.text
    async with postgres_sessions() as session:
        fulfillment = await session.scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == c.order.id))
        assert fulfillment.status == FulfillmentState.PICKED_UP
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "meal_kitchen.status_changed")) == 0
