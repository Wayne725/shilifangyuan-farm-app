import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import app.jobs as jobs
from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.meal_schedules import TAIPEI
from app.models import (
    AdminAudit, FulfillmentMethod, FulfillmentState, FulfillmentStatus,
    InventoryReservation, Invoice, InvoiceStatus, Meal, MealCancellationRequest,
    MealEvent, MealEventOffering, MealEventStatus, MembershipType, Notification,
    Order, OrderFulfillment, OrderItem, OrderKind, OutboxEvent, PaymentAttempt,
    PaymentStatus, Refund, RefundStatus, ReservationStatus, SalesChannel, TaxType,
    User, UserRole,
)
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, fanyu_test_context, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_v2_payment_jobs import successful_callback


async def seed_cancellation_order(session):
    now = datetime.now(timezone.utc)
    admin = User(email="review-admin@example.test", display_name="退單管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="review-buyer@example.test", display_name="退單買家", password_hash="test")
    other = User(email="review-other@example.test", display_name="其他買家", password_hash="test")
    meal = Meal(slug="review-meal", name="退單測試便當", description="", price=100)
    session.add_all([admin, buyer, other, meal])
    await session.flush()
    offering = MealEventOffering(meal_id=meal.id, price=100, capacity=8, reserved_quantity=2)
    event = MealEvent(
        title="退單測試午餐", location="測試取餐區", created_by_id=admin.id,
        ordering_starts_at=now - timedelta(hours=1), ordering_ends_at=now + timedelta(hours=1),
        pickup_starts_at=now - timedelta(minutes=10), pickup_ends_at=now + timedelta(hours=2),
        status=MealEventStatus.PICKUP_OPEN, offerings=[offering],
    )
    session.add(event)
    await session.flush()
    order = Order(
        order_number="M-REVIEW-0001", order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user=buyer, meal_event=event, membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=200, contact_email=buyer.email, payment_status=PaymentStatus.PENDING,
        invoice_status=InvoiceStatus.NOT_ELIGIBLE, fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[OrderItem(product_name=meal.name, source_meal_offering_id=offering.id, unit_label="份", quantity=2, unit_price=100, subtotal=200, tax_type=TaxType.TAXABLE)],
        fulfillment=OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP, status=FulfillmentState.PENDING_CONFIRMATION, pickup_code="723456"),
    )
    reservation = InventoryReservation(order=order, source_meal_offering_id=offering.id, quantity=2, status=ReservationStatus.ACTIVE, expires_at=now + timedelta(minutes=15))
    session.add_all([order, reservation])
    await session.commit()
    return SimpleNamespace(session=session, admin=admin, buyer=buyer, other=other, event=event, order=order, offering=offering, reservation=reservation, now=now)


async def mark_paid(context, state=FulfillmentState.PENDING_CONFIRMATION):
    order = context.order
    order.payment_status = PaymentStatus.PAID
    order.paid_at = context.now - timedelta(hours=1)
    order.invoice_status = InvoiceStatus.ISSUED
    order.invoice_provider_context = fanyu_test_context()
    order.fulfillment.status = state
    order.fulfillment_status = FulfillmentStatus.PENDING_CONFIRMATION if state == FulfillmentState.PENDING_CONFIRMATION else FulfillmentStatus.PREPARING
    context.offering.reserved_quantity = 0
    context.offering.paid_quantity = 2
    context.reservation.status = ReservationStatus.CONSUMED
    attempt = PaymentAttempt(order=order, provider="raygate", merchant_trade_no="REVIEW-TRADE-0001", provider_trade_no="PROVIDER-0001", amount=200, status=PaymentStatus.PAID, expires_at=context.now + timedelta(hours=1))
    context.reservation.payment_attempt = attempt
    order.invoice = Invoice(order=order, provider="fanyu", relate_number="REVIEWINVOICE0001", status=InvoiceStatus.ISSUED, invoice_number="AB12345678", invoice_date=context.now, total_amount=200, provider_context=fanyu_test_context(), provider_request={"sellerID": "15989995"})
    context.session.add_all([attempt, order.invoice])
    await context.session.commit()
    return attempt


@pytest.fixture
async def review_context(database_session):
    context = await seed_cancellation_order(database_session)
    async with api_test_context(database_session, [meals_router, orders_router], settings=make_test_settings()) as client:
        context.client = client
        yield context


async def submit(context, reason="行程變更", legacy=False):
    request = await context.session.scalar(select(MealCancellationRequest).where(
        MealCancellationRequest.order_id == context.order.id, MealCancellationRequest.status == "pending",
    ))
    if request is None:
        request = MealCancellationRequest(order=context.order, requested_by_id=context.buyer.id, reason=reason.strip())
        context.session.add(request)
        await context.session.commit()
    from app.schemas import MealCancellationRequestRead
    return MealCancellationRequestRead.model_validate(request).model_dump(mode="json")


async def review(context, request, decision="approve", reason="已核對，可退單"):
    return await context.client.post(f"/v1/admin/meal-cancellation-requests/{request['id']}/review", json={"decision": decision, "reason": reason}, headers=auth_headers(context.admin))


async def count(context, model):
    return await context.session.scalar(select(func.count(model.id)))


@pytest.mark.parametrize("paid", [False, True])
@pytest.mark.asyncio
async def test_existing_pending_application_remains_visible_without_financial_effects(review_context, paid):
    c = review_context
    if paid:
        await mark_paid(c)
    request = await submit(c, "  行程變更  ")
    assert request["status"] == "pending" and request["reason"] == "行程變更"
    assert (await submit(c, "重複送出", legacy=True))["id"] == request["id"]
    assert await count(c, MealCancellationRequest) == 1
    assert await count(c, Refund) == 0
    assert c.order.cancelled_at is None
    assert c.order.payment_status == (PaymentStatus.PAID if paid else PaymentStatus.PENDING)
    assert c.order.invoice_status == (InvoiceStatus.ISSUED if paid else InvoiceStatus.NOT_ELIGIBLE)
    assert c.offering.paid_quantity == (2 if paid else 0)
    assert c.offering.reserved_quantity == (0 if paid else 2)
    detail = await c.client.get(f"/v1/meal-orders/{c.order.id}", headers=auth_headers(c.buyer))
    assert detail.json()["cancellation_request"] == request
    assert "request_cancellation" not in detail.json()["available_actions"]
    assert "cancel" not in detail.json()["available_actions"]
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.adjustment_required")) == 0


@pytest.mark.asyncio
async def test_rejection_preserves_funds_capacity_and_allows_a_new_application(review_context):
    c = review_context
    await mark_paid(c)
    request = await submit(c)
    result = await review(c, request, "reject", "餐點仍須依約取餐")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "rejected"
    assert result.json()["reviewed_by_id"] == c.admin.id
    assert c.order.payment_status == PaymentStatus.PAID and c.order.cancelled_at is None
    assert c.order.invoice_status == InvoiceStatus.ISSUED
    assert c.offering.paid_quantity == 2 and c.reservation.status == ReservationStatus.CONSUMED
    repeated = await review(c, request, "reject", "重複點擊不改原理由")
    assert repeated.json() == result.json()
    assert (await review(c, request)).status_code == 409
    assert await count(c, AdminAudit) == 1 and await count(c, Refund) == 0
    next_request = await submit(c, "補充突發狀況")
    assert next_request["id"] != request["id"]
    assert await count(c, MealCancellationRequest) == 2
    notifications = list(await c.session.scalars(select(Notification).where(Notification.event_type == "meal_cancellation_rejected")))
    assert len(notifications) == 1


@pytest.mark.asyncio
async def test_approval_of_unpaid_order_releases_capacity_once(review_context):
    c = review_context
    request = await submit(c)
    approved = await review(c, request)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert c.order.payment_status == PaymentStatus.EXPIRED
    assert c.order.fulfillment.status == FulfillmentState.CANCELLED
    assert c.order.cancelled_at is not None
    await c.session.refresh(c.offering)
    await c.session.refresh(c.reservation)
    assert c.offering.reserved_quantity == 0 and c.reservation.status == ReservationStatus.RELEASED
    assert (await review(c, request)).json() == approved.json()
    assert (await review(c, request, "reject")).status_code == 409
    assert await count(c, Refund) == 0 and await count(c, AdminAudit) == 1


@pytest.mark.parametrize("state,retained", [(FulfillmentState.PENDING_CONFIRMATION, False), (FulfillmentState.PREPARING, True), (FulfillmentState.READY_FOR_PICKUP, True)])
@pytest.mark.asyncio
async def test_paid_approval_queues_one_refund_and_preserves_prepared_meal_capacity(review_context, state, retained):
    c = review_context
    attempt = await mark_paid(c, state)
    request = await submit(c)
    approved = await review(c, request)
    assert approved.status_code == 200, approved.text
    assert c.order.payment_status == PaymentStatus.REFUND_PENDING
    assert c.order.invoice.status == c.order.invoice_status == InvoiceStatus.ISSUED
    assert c.order.fulfillment.meal_capacity_retained is retained
    await c.session.refresh(c.offering)
    await c.session.refresh(c.reservation)
    assert c.offering.paid_quantity == (2 if retained else 0)
    assert c.reservation.status == (ReservationStatus.CONSUMED if retained else ReservationStatus.RELEASED)
    assert (await review(c, request)).json() == approved.json()
    assert await count(c, Refund) == 1
    refund = await c.session.scalar(select(Refund))
    assert refund.status == RefundStatus.PENDING and refund.amount == 200
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 1
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.adjustment_required")) == 0
    repository = SQLAlchemyPaymentCallbackRepository(c.session)
    await repository._apply_provider_refund(attempt, {"TradeNo": "PROVIDER-0001", "RayGateAssociatedOrderID": "REFUND-0001"})
    await c.session.commit()
    await c.session.refresh(c.offering)
    assert c.offering.paid_quantity == (2 if retained else 0)
    assert c.order.payment_status == PaymentStatus.REFUNDED
    adjustment = await c.session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "invoice.adjustment_required"))
    assert adjustment.payload["auto_void"] is True
    assert c.order.invoice.status == InvoiceStatus.VOID_PENDING


@pytest.mark.asyncio
async def test_approved_refund_flows_to_confirmed_invoice_void(review_context, monkeypatch):
    c = review_context
    attempt = await mark_paid(c)
    request = await submit(c)
    assert (await review(c, request)).status_code == 200
    await SQLAlchemyPaymentCallbackRepository(c.session)._apply_provider_refund(attempt, {"TradeNo": "PROVIDER-0001", "RayGateAssociatedOrderID": "REFUND-0001"})
    await c.session.commit()
    adjustment = await c.session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "invoice.adjustment_required"))
    calls, voided = [], False
    invoice_date = c.now.astimezone(TAIPEI).strftime("%Y%m%d")

    async def transport(url, envelope, _headers, _timeout):
        nonlocal voided
        path = url.rsplit("/", 1)[-1]
        calls.append(path)
        if path == "cancelInvoice":
            voided = True
        return HTTPResponse(200, json.dumps({"statusCode": "0", "respData": {"invNo": "AB12345678", "invDate": invoice_date, "status": "1" if voided else "0"}}), {})

    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: FanyuInvoiceAdapter(fanyu_settings(), transport=transport))
    await jobs._dispatch_outbox_event(c.session, make_test_settings(invoice_provider="fanyu"), adjustment.id)
    await c.session.commit()
    assert calls == ["queryInvoice", "cancelInvoice", "queryInvoice"]
    assert c.order.invoice_status == c.order.invoice.status == InvoiceStatus.VOIDED


@pytest.mark.parametrize("marker", ["picked_up", "no_show", "event_cancelled", "cancelled"])
@pytest.mark.asyncio
async def test_review_rechecks_fulfillment_and_rejection_can_close_stale_requests(review_context, marker):
    c = review_context
    await mark_paid(c, FulfillmentState.READY_FOR_PICKUP)
    request = await submit(c)
    if marker in {"picked_up", "no_show"}:
        c.order.fulfillment.status = FulfillmentState.PICKED_UP if marker == "picked_up" else FulfillmentState.NO_SHOW
        c.order.fulfillment_status = FulfillmentStatus.PICKED_UP
    elif marker == "event_cancelled":
        c.event.status = MealEventStatus.CANCELLED
    else:
        c.order.cancelled_at = c.now
    await c.session.commit()
    assert (await review(c, request)).status_code == 409
    assert await count(c, Refund) == 0
    queue = await c.client.get("/v1/admin/meal-cancellation-requests", headers=auth_headers(c.admin))
    assert queue.json()["items"][0]["can_approve"] is False
    assert (await review(c, request, "reject", "已結束履約，不能退單")).status_code == 200


@pytest.mark.asyncio
async def test_pending_request_does_not_prevent_pickup_and_cannot_approve_after_redemption(review_context):
    c = review_context
    await mark_paid(c, FulfillmentState.READY_FOR_PICKUP)
    request = await submit(c)
    redeem = await c.client.post(f"/v1/admin/meal-events/{c.event.id}/redeem", json={"pickup_code": "723456"}, headers=auth_headers(c.admin))
    assert redeem.status_code == 200, redeem.text
    assert (await review(c, request)).status_code == 409
    assert c.order.fulfillment.status == FulfillmentState.PICKED_UP
    assert await count(c, Refund) == 0


@pytest.mark.asyncio
async def test_failed_refund_creation_keeps_application_and_order_unchanged(review_context):
    c = review_context
    await mark_paid(c)
    attempt = await c.session.scalar(select(PaymentAttempt))
    attempt.provider_trade_no = None
    await c.session.commit()
    request = await submit(c)
    denied = await review(c, request)
    assert denied.status_code == 409
    assert c.order.payment_status == PaymentStatus.PAID and c.order.cancelled_at is None
    assert c.offering.paid_quantity == 2
    assert await count(c, Refund) == 0 and await count(c, AdminAudit) == 0
    assert (await c.session.get(MealCancellationRequest, request["id"])).status == "pending"


@pytest.mark.asyncio
async def test_permissions_validation_queue_search_and_generic_cancel_bypass(review_context):
    c = review_context
    path = f"/v1/meal-orders/{c.order.id}/cancellation-requests"
    assert (await c.client.post(path, json={"reason": "他人訂單"}, headers=auth_headers(c.other))).status_code == 403
    for reason in ["", "  ", "x" * 2001]:
        assert (await c.client.post(path, json={"reason": reason}, headers=auth_headers(c.buyer))).status_code == 422
    request = await submit(c)
    review_path = f"/v1/admin/meal-cancellation-requests/{request['id']}/review"
    assert (await c.client.post(review_path, json={"decision": "approve", "reason": "自行核准"}, headers=auth_headers(c.buyer))).status_code == 403
    assert (await c.client.get("/v1/admin/meal-cancellation-requests", headers=auth_headers(c.buyer))).status_code == 403
    assert (await c.client.post(review_path, json={"decision": "approve", "reason": "x" * 2001}, headers=auth_headers(c.admin))).status_code == 422
    assert (await c.client.post(f"/v1/orders/{c.order.id}/cancel", json={"reason": "繞過審核"}, headers=auth_headers(c.buyer))).status_code == 403
    await mark_paid(c)
    assert (await c.client.post(f"/v1/orders/{c.order.id}/cancel", json={"reason": "已付款仍不能繞過"}, headers=auth_headers(c.buyer))).status_code == 403
    await review(c, request, "reject", "請補充資料")
    await submit(c, "新申請")
    for status, expected in [("pending", 1), ("rejected", 1), ("approved", 0), ("all", 2)]:
        response = await c.client.get(f"/v1/admin/meal-cancellation-requests?review_status={status}&search=M-REVIEW&limit=1", headers=auth_headers(c.admin))
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected
        assert len(response.json()["items"]) == min(1, expected)
    empty = await c.client.get("/v1/admin/meal-cancellation-requests?search=NO-SUCH-ORDER", headers=auth_headers(c.admin))
    assert empty.json()["total"] == 0


@pytest.mark.asyncio
async def test_database_enforces_one_pending_request_per_order(review_context):
    c = review_context
    await submit(c)
    c.session.add(MealCancellationRequest(order_id=c.order.id, requested_by_id=c.buyer.id, reason="重複申請"))
    with pytest.raises(IntegrityError):
        await c.session.flush()
    await c.session.rollback()


@pytest.mark.parametrize("state", [FulfillmentState.PENDING_CONFIRMATION, FulfillmentState.PREPARING, FulfillmentState.READY_FOR_PICKUP])
@pytest.mark.asyncio
async def test_generic_admin_refund_cannot_bypass_meal_review_or_release_prepared_capacity(review_context, state):
    c = review_context
    await mark_paid(c, state)
    result = await c.client.post(
        f"/v1/orders/{c.order.id}/admin/refund",
        json={"reason": "直接退款，繞過便當退單審核"},
        headers=auth_headers(c.admin),
    )
    assert result.status_code == 409, result.text
    assert "退單審核" in result.json()["detail"]
    await c.session.refresh(c.offering)
    await c.session.refresh(c.reservation)
    await c.session.refresh(c.order.fulfillment)
    assert c.offering.reserved_quantity == 0 and c.offering.paid_quantity == 2
    assert c.reservation.status == ReservationStatus.CONSUMED
    assert c.order.payment_status == PaymentStatus.PAID and c.order.cancelled_at is None
    assert c.order.fulfillment.status == state
    assert c.order.invoice_status == InvoiceStatus.ISSUED
    assert await count(c, Refund) == 0 and await count(c, AdminAudit) == 0
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 0


@pytest.mark.asyncio
async def test_payment_arriving_after_unpaid_approval_is_refunded_without_reopening_order(review_context):
    c = review_context
    attempt = PaymentAttempt(order=c.order, provider="ecpay", merchant_trade_no="REVIEW-LATE-PAYMENT", amount=200, status=PaymentStatus.PENDING, expires_at=c.now + timedelta(minutes=15))
    c.reservation.payment_attempt = attempt
    c.session.add(attempt)
    await c.session.commit()
    request = await submit(c)
    assert (await review(c, request)).status_code == 200
    result = await SQLAlchemyPaymentCallbackRepository(c.session).apply_ecpay_payment_callback("review-late-callback", successful_callback(attempt, c.now, "REVIEW-LATE-TRADE"))
    await c.session.commit()
    assert result == "late_paid_refund_required"
    assert c.order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert c.order.cancelled_at is not None and c.order.fulfillment.status == FulfillmentState.CANCELLED
    await c.session.refresh(c.offering)
    assert c.offering.reserved_quantity == c.offering.paid_quantity == 0
    assert await count(c, Refund) == 1
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == 0
    refund_event = await c.session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "refund.requested"))
    await jobs._process_refund_event(c.session, refund_event, make_test_settings())
    await c.session.commit()
    assert c.order.payment_status == PaymentStatus.REFUNDED
    assert c.order.fulfillment.status == FulfillmentState.CANCELLED


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize("note", [{}, {"reason": " \t "}])
@pytest.mark.asyncio
async def test_admin_review_optional_note_keeps_customer_reason_and_review_consistency(review_context, decision, note):
    c = review_context
    await mark_paid(c)
    requested = await submit(c, "原顧客退單說明")
    response = await c.client.post(f"/v1/admin/meal-cancellation-requests/{requested['id']}/review",
        json={"decision": decision, **note}, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    saved = await c.session.get(MealCancellationRequest, requested["id"])
    assert saved.reason == "原顧客退單說明" and saved.review_reason == ""
    assert saved.reviewed_by_id == c.admin.id and saved.reviewed_at is not None
    assert saved.status == ("approved" if decision == "approve" else "rejected")
    assert c.order.payment_status == (PaymentStatus.REFUND_PENDING if decision == "approve" else PaymentStatus.PAID)
    assert await count(c, Refund) == int(decision == "approve")
