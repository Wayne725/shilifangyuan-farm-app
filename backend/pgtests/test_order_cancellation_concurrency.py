import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminAudit, MealCancellationRequest, OutboxEvent, Refund, RefundStatus, SalesChannel
from app.routers.orders import orders_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import mark_paid, seed_cancellation_order
from tests.test_raygate_regressions import _callback_payload
from tests.test_raygate import FakeRayGateRefundAdapter, _make_pending_raygate_refund


async def post(factory, user, path, body):
    async with factory() as session:
        async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
            return await client.post(path, json=body, headers=auth_headers(user))


@pytest.mark.asyncio
@pytest.mark.parametrize("historical_request", [False, True])
async def test_concurrent_admin_cancellations_and_legacy_approvals_have_one_refund(postgres_sessions, historical_request):
    async with postgres_sessions() as session:
        c = await seed_cancellation_order(session)
        c.order.sales_channel = SalesChannel.REGULAR
        c.order.contact_email = "review-buyer@example.com"
        await mark_paid(c)
    results = await asyncio.wait_for(asyncio.gather(*(post(postgres_sessions, c.buyer, f"/v1/orders/{c.order.id}/cancel", {"reason": "重複申請"}) for _ in range(4))), timeout=15)
    assert all(result.status_code == 403 for result in results), [result.text for result in results]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Refund.id))) == 0
        assert await session.scalar(select(func.count(MealCancellationRequest.id))) == 0
        if historical_request:
            request = MealCancellationRequest(order_id=c.order.id, requested_by_id=c.buyer.id, reason="歷史退單申請")
            session.add(request)
            await session.commit()
            legacy_path = f"/v1/orders/{c.order.id}/admin/cancellation-requests/{request.id}/review"
    path = f"/v1/orders/{c.order.id}/admin/cancel"
    operations = [post(postgres_sessions, c.admin, path, {"reason": "電話核對退單"}) for _ in range(4)]
    if historical_request:
        operations.extend(post(postgres_sessions, c.admin, legacy_path, {"decision": "approve", "reason": "核准歷史申請"}) for _ in range(2))
    results = await asyncio.wait_for(asyncio.gather(*operations), timeout=15)
    assert all(result.status_code == 200 for result in results), [result.text for result in results]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(MealCancellationRequest.id))) == 1
        assert await session.scalar(select(func.count(Refund.id))) == 1
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "order_cancellation.approved")) == 1


@pytest.mark.parametrize("channel", [SalesChannel.REGULAR, SalesChannel.MEAL_PREORDER])
@pytest.mark.asyncio
async def test_admin_cancel_and_provider_refund_callback_share_lock_order(postgres_sessions, monkeypatch, channel):
    import app.integrations.payment_service as payment_service
    import app.routers.meals as meals
    import app.routers.orders as orders

    async with postgres_sessions() as session:
        c = await seed_cancellation_order(session)
        c.order.sales_channel = channel
        c.order.contact_email = "callback-race@example.com"
        attempt = await mark_paid(c)
        payload = _callback_payload(attempt, status=3, return_code="0000", message="退款成功", associated_order_id="CANCEL-RACE-REFUND")

    callback_entered, release_callback, admin_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_callback = payment_service.SQLAlchemyPaymentCallbackRepository._apply_provider_refund
    original_refund = payment_service.create_provider_aware_refund

    async def gated_callback(self, *args, **kwargs):
        callback_entered.set()
        await release_callback.wait()
        return await original_callback(self, *args, **kwargs)

    async def observed_admin_refund(*args, **kwargs):
        admin_entered.set()
        return await original_refund(*args, **kwargs)

    monkeypatch.setattr(payment_service.SQLAlchemyPaymentCallbackRepository, "_apply_provider_refund", gated_callback)
    monkeypatch.setattr(meals if channel == SalesChannel.MEAL_PREORDER else orders, "create_provider_aware_refund", observed_admin_refund)

    async def callback():
        async with postgres_sessions() as session:
            return await payment_service.SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("cancel-race-refund", payload)

    async def cancel():
        async with postgres_sessions() as session:
            router = meals.meals_router if channel == SalesChannel.MEAL_PREORDER else orders.orders_router
            path = f"/v1/admin/meal-orders/{c.order.id}/cancel" if channel == SalesChannel.MEAL_PREORDER else f"/v1/orders/{c.order.id}/admin/cancel"
            async with api_test_context(session, [router], settings=make_test_settings()) as client:
                return await client.post(path, json={"reason": "電話退單與金流回傳同時到達"}, headers=auth_headers(c.admin))

    callback_task = asyncio.create_task(callback())
    await asyncio.wait_for(callback_entered.wait(), timeout=5)
    cancel_task = asyncio.create_task(cancel())
    try:
        # A consistent lock order blocks the cancellation before its refund helper.
        await asyncio.wait_for(admin_entered.wait(), timeout=0.5)
    except TimeoutError:
        pass
    release_callback.set()
    results = await asyncio.wait_for(asyncio.gather(callback_task, cancel_task, return_exceptions=True), timeout=15)
    assert not any(isinstance(result, Exception) for result in results), [str(result) for result in results]
    assert results[0] == "refunded"
    assert results[1].status_code == 200, results[1].text
    async with postgres_sessions() as session:
        refunds = list(await session.scalars(select(Refund)))
        assert len(refunds) == 1
        assert refunds[0].status == RefundStatus.COMPLETED


@pytest.mark.parametrize("lock_number", [1, 2], ids=["initial", "after-submission-marker"])
@pytest.mark.asyncio
async def test_refund_worker_and_callback_share_lock_order(postgres_sessions, monkeypatch, lock_number):
    import app.jobs as jobs
    import app.integrations.payment_service as payment_service

    async with postgres_sessions() as session:
        order, attempt, refund, event = await _make_pending_raygate_refund(session, suffix="WORKERRACE")
        event_id = event.id
        paid_payload = _callback_payload(attempt, status=2, return_code="0000", message="付款成功")
        refunded_payload = _callback_payload(attempt, status=3, return_code="0000", message="退款成功", associated_order_id="WORKER-RACE-REFUND")
    adapter = FakeRayGateRefundAdapter(paid_payload)
    monkeypatch.setattr(jobs, "refund_adapter_from_settings", lambda *args: adapter)
    worker_locked, release_worker, callback_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_scalar = AsyncSession.scalar
    original_callback = payment_service.SQLAlchemyPaymentCallbackRepository._apply_provider_refund

    async def observed_scalar(self, statement, *args, **kwargs):
        result = await original_scalar(self, statement, *args, **kwargs)
        descriptions = getattr(statement, "column_descriptions", [])
        if self.info.get("refund_worker") and getattr(statement, "_for_update_arg", None) is not None and descriptions and descriptions[0].get("entity") is Refund:
            self.info["refund_locks"] = self.info.get("refund_locks", 0) + 1
            if self.info["refund_locks"] == lock_number:
                worker_locked.set()
                await release_worker.wait()
        return result

    async def observed_callback(self, *args, **kwargs):
        callback_entered.set()
        return await original_callback(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", observed_scalar)
    monkeypatch.setattr(payment_service.SQLAlchemyPaymentCallbackRepository, "_apply_provider_refund", observed_callback)

    async def worker():
        async with postgres_sessions() as session:
            session.info["refund_worker"] = True
            event = await session.get(OutboxEvent, event_id)
            await jobs._process_refund_event(session, event, make_test_settings(payment_provider="raygate"))
            await session.commit()

    async def callback():
        async with postgres_sessions() as session:
            return await payment_service.SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("worker-race-refunded", refunded_payload)

    worker_task = asyncio.create_task(worker())
    await asyncio.wait_for(worker_locked.wait(), timeout=5)
    callback_task = asyncio.create_task(callback())
    try:
        await asyncio.wait_for(callback_entered.wait(), timeout=0.5)
    except TimeoutError:
        pass
    release_worker.set()
    results = await asyncio.wait_for(asyncio.gather(worker_task, callback_task, return_exceptions=True), timeout=15)
    assert not any(isinstance(result, Exception) for result in results), [str(result) for result in results]
    assert results[1] in {"refunded", "duplicate"}
    assert sum(call[0] == "refund" for call in adapter.calls) <= 1
    async with postgres_sessions() as session:
        refunds = list(await session.scalars(select(Refund)))
        assert len(refunds) == 1 and refunds[0].status == RefundStatus.COMPLETED
