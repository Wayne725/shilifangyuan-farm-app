import asyncio

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, FulfillmentState, MealCancellationRequest, Order, OutboxEvent, Refund, PaymentAttempt, PaymentStatus
from app.routers.meals import meals_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import mark_paid, seed_cancellation_order
from tests.test_raygate_regressions import _callback_payload


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
    async with factory() as session:
        request = MealCancellationRequest(order_id=context.order.id, requested_by_id=context.buyer.id, reason="歷史退單申請")
        session.add(request)
        await session.commit()
        return {"id": request.id}


@pytest.mark.asyncio
async def test_concurrent_buyer_requests_are_forbidden_without_side_effects(postgres_sessions):
    c = await _seed(postgres_sessions)
    results = await asyncio.wait_for(asyncio.gather(*(_post(postgres_sessions, c.buyer, f"/v1/meal-orders/{c.order.id}/{suffix}", {"reason": "測試退單"}) for suffix in ["cancel", "cancellation-requests"] * 3)), timeout=15)
    assert all(result.status_code == 403 for result in results)
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(MealCancellationRequest.id))) == 0
        assert await session.scalar(select(func.count(Refund.id))) == 0
        assert (await session.get(Order, c.order.id)).cancelled_at is None


@pytest.mark.parametrize("paid", [False, True])
@pytest.mark.asyncio
async def test_concurrent_direct_admin_cancellations_have_one_result(postgres_sessions, paid):
    c = await _seed(postgres_sessions, paid=paid)
    path = f"/v1/admin/meal-orders/{c.order.id}/cancel"
    results = await asyncio.wait_for(asyncio.gather(*(_post(postgres_sessions, c.admin, path, {"reason": "電話確認退單"}) for _ in range(5))), timeout=15)
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    assert len({result.json()["cancellation_request"]["id"] for result in results}) == 1
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(MealCancellationRequest.id))) == 1
        assert await session.scalar(select(func.count(Refund.id))) == int(paid)
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == int(paid)
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "meal_cancellation.approved")) == 1


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


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("first", ["approve", "pickup"])
@pytest.mark.asyncio
async def test_approval_and_pickup_serialize_on_order_and_cannot_both_succeed(postgres_sessions, monkeypatch, first, direct):
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
        if direct:
            return await _post(postgres_sessions, c.admin, f"/v1/admin/meal-orders/{c.order.id}/cancel", {"reason": "電話確認退單"})
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


@pytest.mark.asyncio
async def test_late_payment_and_whole_event_cancellation_share_lock_order(postgres_sessions, monkeypatch):
    import app.integrations.payment_service as payments
    import app.routers.meals as meals

    async with postgres_sessions() as session:
        c = await seed_cancellation_order(session)
        attempt = PaymentAttempt(order=c.order, provider="raygate", merchant_trade_no="EVENT-RACE-ATTEMPT", provider_trade_no="EVENT-RACE-PROVIDER", amount=200, status=PaymentStatus.PENDING, expires_at=c.reservation.expires_at)
        c.reservation.payment_attempt = attempt
        session.add(attempt)
        await session.commit()
        payload = _callback_payload(attempt, status=2, return_code="0000", message="付款成功")
    order_locked, release_callback, event_locked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_reload = payments.SQLAlchemyPaymentCallbackRepository._reload_locked_attempt_reservations
    original_load = meals._load_event

    async def gated_reload(self, *args, **kwargs):
        order_locked.set()
        await release_callback.wait()
        return await original_reload(self, *args, **kwargs)

    async def observed_event(*args, **kwargs):
        result = await original_load(*args, **kwargs)
        if kwargs.get("for_update"):
            event_locked.set()
        return result

    monkeypatch.setattr(payments.SQLAlchemyPaymentCallbackRepository, "_reload_locked_attempt_reservations", gated_reload)
    monkeypatch.setattr(meals, "_load_event", observed_event)

    async def callback():
        async with postgres_sessions() as session:
            return await payments.SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("event-race-late-paid", payload)

    callback_task = asyncio.create_task(callback())
    await asyncio.wait_for(order_locked.wait(), timeout=5)
    cancel_task = asyncio.create_task(_post(postgres_sessions, c.admin, f"/v1/admin/meal-events/{c.event.id}/cancel", {"reason": "電話確認整場取消"}))
    try:
        await asyncio.wait_for(event_locked.wait(), timeout=0.5)
    except TimeoutError:
        pass
    release_callback.set()
    results = await asyncio.wait_for(asyncio.gather(callback_task, cancel_task, return_exceptions=True), timeout=15)
    assert not any(isinstance(result, Exception) for result in results), [str(result) for result in results]
    assert results[1].status_code == 200, results[1].text
    async with postgres_sessions() as session:
        order = await session.get(Order, c.order.id)
        assert order.cancelled_at is not None
        assert await session.scalar(select(func.count(Refund.id))) == 1
