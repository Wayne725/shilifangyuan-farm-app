from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.jobs as jobs
from app.integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository, create_payment_attempt,
    create_provider_aware_refund, remaining_subject_payment_status,
)
from app.models import (
    FulfillmentStatus, InventoryReservation, Invoice, Order, OrderItem,
    Notification, OutboxEvent, PaymentAttempt, PaymentStatus, PointAccount, PointSourceType,
    PointTransaction, Product, Refund, RefundStatus, ReservationStatus,
)
from app.routers.payments import payments_router
from tests.support import api_test_context, make_test_settings
from tests.test_integrations import make_regular_order, payment_settings
from tests.test_raygate import FakeRayGateRefundAdapter
from tests.test_raygate_regressions import _callback_payload


async def make_revised_order(session, *, current_paid=False, old_status=PaymentStatus.EXPIRED, current_attempt=True):
    user, product, order = await make_regular_order(session)
    now = datetime.now(timezone.utc)
    order.revision = 2
    order.amount_total = 300
    order.items[0].quantity = 3
    order.items[0].subtotal = 300
    order.payment_status = PaymentStatus.PAID if current_paid else PaymentStatus.PENDING
    order.fulfillment_status = FulfillmentStatus.PREPARING if current_paid else FulfillmentStatus.PENDING_CONFIRMATION
    order.paid_at = now if current_paid else None
    product.stock_quantity = 7 if current_attempt else 10
    old = PaymentAttempt(
        order=order, order_revision=1, superseded_at=now,
        provider="raygate", merchant_trade_no="OLD-REVISION-1", provider_trade_no="RG-OLD-1",
        amount=200, status=old_status, expires_at=now + timedelta(minutes=10),
        checkout_payload={"redirect_url": "https://pay.example.test/calc/pay_encrypt/old"},
    )
    old_reservation = InventoryReservation(
        order=order, payment_attempt=old, source_product_id=product.id, quantity=2,
        status=ReservationStatus.RELEASED, expires_at=old.expires_at, released_at=now,
    )
    session.add_all([old, old_reservation])
    latest = None
    if current_attempt:
        latest = PaymentAttempt(
            order=order, order_revision=2, provider="raygate", merchant_trade_no="NEW-REVISION-2",
            provider_trade_no="RG-NEW-2", amount=300,
            status=PaymentStatus.PAID if current_paid else PaymentStatus.PENDING,
            expires_at=now + timedelta(minutes=10), paid_at=now if current_paid else None,
            checkout_payload={"redirect_url": "https://pay.example.test/calc/pay_encrypt/new"},
        )
        session.add_all([latest, InventoryReservation(
            order=order, payment_attempt=latest, source_product_id=product.id, quantity=3,
            status=ReservationStatus.CONSUMED if current_paid else ReservationStatus.ACTIVE,
            expires_at=latest.expires_at,
        )])
    if current_paid:
        account = PointAccount(user_id=user.id)
        session.add(account)
        await session.flush()
        session.add(PointTransaction(account_id=account.id, amount=3, source_type=PointSourceType.PURCHASE, reference_id=order.id))
    await session.commit()
    return user, product, order, old, latest


def success_payload(attempt):
    payload = _callback_payload(attempt, status=2, return_code="0000", message="付款成功")
    payload["PaymentDate"] = datetime.now(timezone.utc).isoformat()
    return payload


async def assert_current_order_untouched(session, order_id, *, paid, stock):
    order = await session.get(Order, order_id, populate_existing=True)
    assert order.revision == 2 and order.amount_total == 300
    assert order.payment_status == (PaymentStatus.PAID if paid else PaymentStatus.PENDING)
    assert order.fulfillment_status == (FulfillmentStatus.PREPARING if paid else FulfillmentStatus.PENDING_CONFIRMATION)
    assert order.cancelled_at is None
    assert (order.paid_at is not None) is paid
    assert await session.scalar(select(Product.stock_quantity)) == stock
    reservations = list(await session.scalars(select(InventoryReservation).order_by(InventoryReservation.quantity)))
    assert reservations[0].status == ReservationStatus.RELEASED
    if len(reservations) > 1:
        assert reservations[1].status == (ReservationStatus.CONSUMED if paid else ReservationStatus.ACTIVE)
    assert await session.scalar(select(func.count(Invoice.id))) == 0
    assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type.in_({"invoice.issue", "invoice.issue_requested", "invoice.adjustment_required"}))) == 0
    assert await session.scalar(select(func.coalesce(func.sum(PointTransaction.amount), 0))) == (3 if paid else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_paid", [False, True])
@pytest.mark.parametrize("old_status", [PaymentStatus.PENDING, PaymentStatus.FAILED, PaymentStatus.EXPIRED])
@pytest.mark.parametrize("completion", ["callback", "worker"])
async def test_obsolete_payment_and_refund_do_not_change_current_revision(database_session, monkeypatch, current_paid, old_status, completion):
    _, _, order, old, _ = await make_revised_order(database_session, current_paid=current_paid, old_status=old_status)
    repository = SQLAlchemyPaymentCallbackRepository(database_session)
    assert await repository.apply_raygate_payment_callback("old-paid", success_payload(old)) == "late_paid_refund_required"
    assert await repository.apply_raygate_payment_callback("old-paid-repeat", success_payload(old)) == "duplicate"
    refunds = list(await database_session.scalars(select(Refund)))
    assert len(refunds) == 1
    refund = refunds[0]
    assert refund.amount == 200 and refund.purpose == "obsolete_order_revision"
    await assert_current_order_untouched(database_session, order.id, paid=current_paid, stock=7)
    if completion == "callback":
        refunded = _callback_payload(old, status=3, return_code="0000", message="退款成功", associated_order_id="OLD-REFUND")
        assert await repository.apply_raygate_payment_callback("old-refunded", refunded) == "refunded"
        assert await repository.apply_raygate_payment_callback("old-refunded-repeat", refunded) == "duplicate"
    else:
        adapter = FakeRayGateRefundAdapter(success_payload(old))
        monkeypatch.setattr(jobs, "refund_adapter_from_settings", lambda *_: adapter)
        event = await database_session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "refund.requested"))
        await jobs._process_refund_event(database_session, event, make_test_settings(payment_provider="raygate"))
        await jobs._process_refund_event(database_session, event, make_test_settings(payment_provider="raygate"))
        assert sum(call[0] == "refund" for call in adapter.calls) == 1
    await database_session.refresh(refund)
    assert refund.status == RefundStatus.COMPLETED
    assert old.status == PaymentStatus.REFUNDED and old.amount == 200
    notification = await database_session.scalar(select(Notification).where(Notification.event_type == "refund_completed"))
    assert "第 1 版" in notification.body and "NT$200" in notification.body and "最新狀態" in notification.body
    await assert_current_order_untouched(database_session, order.id, paid=current_paid, stock=7)


@pytest.mark.asyncio
async def test_refund_email_falls_back_to_account_without_order_contact(database_session):
    owner, _, order, old, _ = await make_revised_order(database_session)
    order.contact_email = ""
    await database_session.commit()
    repository = SQLAlchemyPaymentCallbackRepository(database_session)
    assert await repository.apply_raygate_payment_callback("old-paid", success_payload(old)) == "late_paid_refund_required"
    refunded = _callback_payload(old, status=3, return_code="0000", message="已退款", associated_order_id="OLD-REFUND")
    assert await repository.apply_raygate_payment_callback("old-refunded", refunded) == "refunded"
    emails = list(await database_session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "send_email")))
    refund_email = next(event for event in emails if event.payload["event_type"] == "refund_completed")
    assert refund_email.payload["to_email"] == owner.email


@pytest.mark.asyncio
async def test_external_obsolete_refund_without_local_request_does_not_bind_current_refund(database_session):
    _, _, order, old, latest = await make_revised_order(database_session, current_paid=True)
    unrelated = Refund(order=order, amount=300, status=RefundStatus.PENDING, reason="目前版本待退", requested_by_id=order.user_id)
    database_session.add(unrelated)
    await database_session.commit()
    refunded = _callback_payload(old, status=3, return_code="0000", message="已退款", associated_order_id="EXTERNAL-OLD-REFUND")
    await SQLAlchemyPaymentCallbackRepository(database_session).apply_raygate_payment_callback("old-external-refund", refunded)
    await database_session.refresh(unrelated)
    assert unrelated.payment_attempt_id is None and unrelated.status == RefundStatus.PENDING
    old_refund = await database_session.scalar(select(Refund).where(Refund.payment_attempt_id == old.id))
    assert old_refund.purpose == "obsolete_order_revision" and old_refund.amount == 200
    await assert_current_order_untouched(database_session, order.id, paid=True, stock=7)


@pytest.mark.asyncio
async def test_old_refund_pending_does_not_block_or_replace_new_payment(database_session):
    _, _, order, old, latest = await make_revised_order(database_session)
    repository = SQLAlchemyPaymentCallbackRepository(database_session)
    await repository.apply_query_result(old, success_payload(old))
    assert await remaining_subject_payment_status(database_session, latest) is None
    assert await repository.apply_query_result(latest, success_payload(latest)) == "paid"
    assert order.payment_status == PaymentStatus.PAID
    assert await database_session.scalar(select(func.sum(PointTransaction.amount))) == 3
    assert await database_session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == 1
    assert old.amount == 200 and latest.amount == 300
    refund, queued = await create_provider_aware_refund(database_session, order=order, amount=300, reason="整單取消", requested_by_id=order.user_id)
    assert refund.payment_attempt_id == latest.id and queued


@pytest.mark.asyncio
async def test_pending_new_revision_is_preserved_without_any_new_attempt(database_session, monkeypatch):
    _, _, order, old, _ = await make_revised_order(database_session, current_attempt=False)
    repository = SQLAlchemyPaymentCallbackRepository(database_session)
    await repository.apply_query_result(old, success_payload(old))
    adapter = FakeRayGateRefundAdapter(success_payload(old))
    monkeypatch.setattr(jobs, "refund_adapter_from_settings", lambda *_: adapter)
    event = await database_session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "refund.requested"))
    await jobs._process_refund_event(database_session, event, make_test_settings(payment_provider="raygate"))
    await assert_current_order_untouched(database_session, order.id, paid=False, stock=10)


@pytest.mark.asyncio
async def test_new_attempt_uses_current_total_and_old_checkout_is_gone(database_session):
    user, product, order, old, _ = await make_revised_order(database_session, current_attempt=False, old_status=PaymentStatus.PENDING)
    latest = await create_payment_attempt(database_session, order.id, user, payment_settings())
    assert latest.id != old.id and latest.order_revision == 2 and latest.amount == 300
    assert old.amount == 200 and old.checkout_payload["redirect_url"].endswith("/old")
    assert (await create_payment_attempt(database_session, order.id, user, payment_settings())).id == latest.id
    async with api_test_context(database_session, [payments_router], settings=payment_settings()) as client:
        response = await client.get(f"/payments/{old.id}/checkout")
    assert response.status_code == 410 and "數量已修改" in response.json()["detail"]
    assert await database_session.scalar(select(Product.stock_quantity).where(Product.id == product.id)) == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("old_status", [PaymentStatus.FAILED, PaymentStatus.EXPIRED])
async def test_background_reconciliation_obsolete_success_is_refunded_separately(database_session, monkeypatch, old_status):
    _, _, order, old, _ = await make_revised_order(database_session, old_status=old_status, current_attempt=False)
    payload = success_payload(old)
    class Provider:
        async def query_order(self, trade_no):
            assert trade_no == old.merchant_trade_no
            return payload
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *_: Provider())
    assert await jobs._reconcile_expired_payments(database_session, make_test_settings(payment_provider="raygate"), datetime.now(timezone.utc), 10) == 1
    refund = await database_session.scalar(select(Refund))
    assert refund.amount == 200 and refund.purpose == "obsolete_order_revision"
    await assert_current_order_untouched(database_session, order.id, paid=False, stock=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("new_checkout_first", [False, True])
async def test_same_revision_shipping_amount_change_retires_old_payment_and_stock(database_session, new_checkout_first):
    user, product, order = await make_regular_order(database_session)
    old = await create_payment_attempt(database_session, order.id, user, payment_settings())
    old.provider = "raygate"
    old.provider_trade_no = "RG-OLD-SHIPPING"
    old.status = PaymentStatus.EXPIRED
    order.amount_total = 260
    await database_session.commit()
    latest = None
    if new_checkout_first:
        latest = await create_payment_attempt(database_session, order.id, user, payment_settings())
        assert latest.id != old.id and latest.amount == 260 and latest.order_revision == old.order_revision == 1
        assert old.superseded_at is not None
        assert await database_session.scalar(select(Product.stock_quantity)) == 1
    repository = SQLAlchemyPaymentCallbackRepository(database_session)
    assert await repository.apply_query_result(old, success_payload(old)) == "late_paid_refund_required"
    await database_session.refresh(order)
    assert order.payment_status == PaymentStatus.PENDING and order.amount_total == 260
    refund = await database_session.scalar(select(Refund))
    assert refund.amount == 200 and refund.purpose == "obsolete_order_revision"
    assert old.superseded_at is not None
    assert await database_session.scalar(select(Product.stock_quantity)) == (1 if new_checkout_first else 3)
    if latest:
        assert await remaining_subject_payment_status(database_session, latest) is None
        assert await database_session.scalar(select(func.sum(InventoryReservation.quantity)).where(
            InventoryReservation.payment_attempt_id == latest.id, InventoryReservation.status == ReservationStatus.ACTIVE)) == 2
