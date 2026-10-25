"""Payment revisions must serialize on the current order, including old provider events."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import time_machine
from fastapi import HTTPException
from sqlalchemy import func, select

import app.jobs as jobs
from app.integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository, create_payment_attempt, lock_order_payment_subject,
)
from app.models import (
    InventoryReservation, MealEventOffering, Order, OrderAmendment, OrderItem, OutboxEvent,
    PaymentAttempt, PaymentStatus, PointTransaction, Product, Refund, RefundStatus,
    ReservationStatus, User,
)
from app.order_amendments import prepare_quantity_amendment
from app.schemas import OrderQuantityAmendmentConfirm
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import make_test_settings
from tests.test_cash_payments_concurrency import cash_api, concurrently, order_body
from tests.test_integrations import make_regular_order, payment_settings, signed_payment_callback
from tests.test_order_payment_revisions import make_revised_order, success_payload
from tests.test_raygate import FakeRayGateRefundAdapter
from tests.test_raygate_regressions import _callback_payload


@pytest.fixture(autouse=True)
def ordering_hours():
    with time_machine.travel(datetime.now(timezone.utc).replace(hour=4, minute=0, second=0, microsecond=0), tick=True):
        yield


async def run_together(*operations):
    start = asyncio.Event()
    async def run(operation):
        await start.wait()
        return await operation
    tasks = [asyncio.create_task(run(operation)) for operation in operations]
    start.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)


@pytest.mark.asyncio
async def test_old_success_and_new_success_only_pay_current_revision_once(postgres_sessions):
    async with postgres_sessions() as session:
        _, product, order, old, latest = await make_revised_order(session)
        order_id, product_id, old_id, latest_id = order.id, product.id, old.id, latest.id
        old_payload, new_payload = success_payload(old), success_payload(latest)

    async def apply(key, payload):
        async with postgres_sessions() as session:
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback(key, payload)

    results = await run_together(*(apply(f"old-{n}", old_payload) for n in range(3)), *(apply(f"new-{n}", new_payload) for n in range(3)))
    assert results.count("paid") == 1 and results.count("late_paid_refund_required") == 1
    async with postgres_sessions() as session:
        order = await session.get(Order, order_id)
        assert order.revision == 2 and order.payment_status == PaymentStatus.PAID and order.amount_total == 300
        assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 7
        assert (await session.get(PaymentAttempt, latest_id)).status == PaymentStatus.PAID
        refunds = list(await session.scalars(select(Refund)))
        assert len(refunds) == 1 and refunds[0].amount == 200 and refunds[0].payment_attempt_id == old_id
        assert refunds[0].purpose == "obsolete_order_revision"
        assert await session.scalar(select(func.sum(PointTransaction.amount))) == 3
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == 1


@pytest.mark.asyncio
async def test_old_refund_callback_racing_new_success_preserves_new_purchase(postgres_sessions):
    async with postgres_sessions() as session:
        _, product, order, old, latest = await make_revised_order(session)
        order_id, product_id = order.id, product.id
        await SQLAlchemyPaymentCallbackRepository(session).apply_query_result(old, success_payload(old))
        refunded = _callback_payload(old, status=3, return_code="0000", message="已退款", associated_order_id="REFUNDED-V1")
        new_payload = success_payload(latest)

    async def apply(key, payload):
        async with postgres_sessions() as session:
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback(key, payload)

    assert set(await run_together(apply("old-refund", refunded), apply("new-paid", new_payload))) == {"refunded", "paid"}
    async with postgres_sessions() as session:
        order = await session.get(Order, order_id)
        assert order.payment_status == PaymentStatus.PAID and order.cancelled_at is None
        assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 7
        assert await session.scalar(select(func.sum(PointTransaction.amount))) == 3
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.adjustment_required")) == 0
        refund = await session.scalar(select(Refund))
        assert refund.status == RefundStatus.COMPLETED and refund.amount == 200


async def make_amendable(session):
    user, product, order = await make_regular_order(session)
    attempt = await create_payment_attempt(session, order.id, user, payment_settings())
    body = OrderQuantityAmendmentConfirm(expected_revision=1, expected_total=100, client_request_id=uuid4(),
        items=[{"order_item_id": order.items[0].id, "quantity": 1}])
    return user.id, product.id, order.id, attempt.id, body


async def amend(sessions, user_id, order_id, body):
    async with sessions() as session:
        user = await session.get(User, user_id)
        try:
            await prepare_quantity_amendment(session, order_id, body, user, payment_settings(), confirm=True)
            await session.commit()
            return 200
        except HTTPException as exc:
            await session.rollback()
            return exc.status_code


@pytest.mark.asyncio
async def test_quantity_change_and_provider_success_serialize_without_wrong_total(postgres_sessions):
    async with postgres_sessions() as session:
        user_id, product_id, order_id, attempt_id, body = await make_amendable(session)
        attempt = await session.get(PaymentAttempt, attempt_id)
        payload = signed_payment_callback(attempt, datetime.now(timezone.utc))

    async def callback():
        async with postgres_sessions() as session:
            return await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback("amend-race", payload)

    changed, paid = await run_together(amend(postgres_sessions, user_id, order_id, body), callback())
    async with postgres_sessions() as session:
        order = await session.get(Order, order_id)
        if changed == 200:
            assert paid == "late_paid_refund_required"
            assert (order.revision, order.amount_total, order.payment_status) == (2, 100, PaymentStatus.PENDING)
            refund = await session.scalar(select(Refund))
            assert refund.amount == 200 and refund.purpose == "obsolete_order_revision"
            assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 3
        else:
            assert changed == 409 and paid == "paid"
            assert (order.revision, order.amount_total, order.payment_status) == (1, 200, PaymentStatus.PAID)
            assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 1
        assert (await session.get(PaymentAttempt, attempt_id)).amount == 200


@pytest.mark.asyncio
async def test_quantity_change_and_expiry_release_inventory_once(postgres_sessions, monkeypatch):
    async with postgres_sessions() as session:
        user_id, product_id, order_id, attempt_id, body = await make_amendable(session)
        attempt = await session.get(PaymentAttempt, attempt_id)
        attempt.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    class PendingProvider:
        async def query_order(self, trade_no):
            await asyncio.sleep(0.02)
            return {"MerchantTradeNo": trade_no, "TradeStatus": "0"}
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *_: PendingProvider())

    async def expire():
        async with postgres_sessions() as session:
            return await jobs._reconcile_expired_payments(session, payment_settings(), datetime.now(timezone.utc), 10)
    changed, _ = await run_together(amend(postgres_sessions, user_id, order_id, body), expire())
    assert changed == 200
    async with postgres_sessions() as session:
        order = await session.get(Order, order_id)
        assert order.revision == 2 and order.amount_total == 100 and order.payment_status == PaymentStatus.PENDING
        assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 3
        assert await session.scalar(select(func.count(InventoryReservation.id)).where(InventoryReservation.status == ReservationStatus.ACTIVE)) == 0


@pytest.mark.asyncio
async def test_quantity_change_and_new_checkout_never_reuse_obsolete_payload(postgres_sessions):
    async with postgres_sessions() as session:
        user_id, product_id, order_id, attempt_id, body = await make_amendable(session)
    async def checkout():
        async with postgres_sessions() as session:
            user = await session.get(User, user_id)
            attempt = await create_payment_attempt(session, order_id, user, payment_settings())
            return attempt.id
    changed, concurrent_id = await run_together(amend(postgres_sessions, user_id, order_id, body), checkout())
    assert changed == 200
    async with postgres_sessions() as session:
        user = await session.get(User, user_id)
        latest = await create_payment_attempt(session, order_id, user, payment_settings())
        assert latest.order_revision == 2 and latest.amount == 100 and latest.id != attempt_id
        assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 2
        assert await session.scalar(select(func.sum(InventoryReservation.quantity)).where(InventoryReservation.status == ReservationStatus.ACTIVE)) == 1


@pytest.mark.asyncio
async def test_cash_collection_racing_quantity_change_uses_one_amount(cash_api, postgres_sessions):
    request, buyer, other, admin, second_admin, event = cash_api
    async with postgres_sessions() as session:
        offering = await session.get(MealEventOffering, event["offerings"][0]["id"])
        offering.capacity = 3
        await session.commit()
    response = await request("POST", f"/v1/meal-events/{event['id']}/orders", buyer, order_body(event, buyer))
    assert response.status_code == 201, response.text
    order = response.json()
    async with postgres_sessions() as session:
        item_id = await session.scalar(select(OrderItem.id).where(OrderItem.order_id == order["id"]))
    body = {"expected_revision": 1, "expected_total": 200, "client_request_id": str(uuid4()), "items": [{"order_item_id": item_id, "quantity": 2}]}
    changed, collected = await concurrently(
        request("POST", f"/v1/orders/{order['id']}/quantity-amendments", buyer, body),
        request("POST", f"/v1/orders/{order['id']}/admin/cash-payment", admin, {"amount": 100, "reason": "隔離收款測試"}),
    )
    assert sorted([changed.status_code, collected.status_code]) == [200, 409], [changed.text, collected.text]
    async with postgres_sessions() as session:
        current = await session.get(Order, order["id"])
        offering = await session.get(MealEventOffering, event["offerings"][0]["id"])
        if changed.status_code == 200:
            assert (current.revision, current.amount_total, current.payment_status) == (2, 200, PaymentStatus.PENDING)
            assert (offering.reserved_quantity, offering.paid_quantity) == (2, 0)
        else:
            assert (current.revision, current.amount_total, current.payment_status) == (1, 100, PaymentStatus.PAID)
            assert (offering.reserved_quantity, offering.paid_quantity) == (0, 1)


@pytest.mark.asyncio
async def test_refund_provider_network_calls_hold_no_order_lock(postgres_sessions, monkeypatch):
    async with postgres_sessions() as session:
        _, _, order, old, latest = await make_revised_order(session)
        order_id = order.id
        await SQLAlchemyPaymentCallbackRepository(session).apply_query_result(old, success_payload(old))
        event_id = await session.scalar(select(OutboxEvent.id).where(OutboxEvent.event_type == "refund.requested"))
        payload = success_payload(old)
    checked = []
    class Adapter(FakeRayGateRefundAdapter):
        async def unlocked(self, stage):
            async with postgres_sessions() as other:
                await other.scalar(select(Order).where(Order.id == order_id).with_for_update(nowait=True))
                checked.append(stage)
        async def query_order(self, trade_no):
            await self.unlocked("query")
            return await super().query_order(trade_no)
        async def refund(self, **kwargs):
            await self.unlocked("refund")
            return await super().refund(**kwargs)
    monkeypatch.setattr(jobs, "refund_adapter_from_settings", lambda *_: Adapter(payload))
    async with postgres_sessions() as session:
        await jobs._process_refund_event(session, await session.get(OutboxEvent, event_id), make_test_settings(payment_provider="raygate"))
    assert checked == ["query", "refund"]
