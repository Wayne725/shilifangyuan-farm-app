from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.models import (
    AdminAudit, FulfillmentState, FulfillmentStatus, GroupCampaign, GroupDecisionStatus, GroupIntakeStatus, InvoiceStatus, MealCancellationRequest,
    OrderKind, OutboxEvent, PaymentAttempt, PaymentStatus, Refund, ReservationStatus,
    Product, SalesChannel, Shipment, ShipmentStatus, ShippingChannel, ShippingTemperature, TargetType,
)
from tests.support import auth_headers
from tests.test_meal_cancellation_review import mark_paid, review_context, submit


async def prepare(c, channel, paid=False):
    c.order.sales_channel = SalesChannel(channel)
    c.order.order_kind = OrderKind.GROUP if channel == "group" else OrderKind.REGULAR
    c.order.contact_email = "phone-buyer@example.com"
    if paid:
        await mark_paid(c)
    else:
        await c.session.commit()
    return (
        f"/v1/admin/meal-orders/{c.order.id}/cancel"
        if channel == "meal_preorder" else f"/v1/orders/{c.order.id}/admin/cancel"
    )


async def count(c, model):
    return await c.session.scalar(select(func.count(model.id)))


@pytest.mark.parametrize("channel", ["regular", "meal_preorder"])
@pytest.mark.parametrize("paid", [False, True])
async def test_phone_cancellation_is_admin_only_atomic_and_idempotent(review_context, channel, paid):
    c = review_context
    path = await prepare(c, channel, paid)
    body = {"reason": "  電話確認行程變更  "}
    assert (await c.client.post(path, json=body)).status_code == 401
    assert (await c.client.post(path, json=body, headers=auth_headers(c.buyer))).status_code == 403
    assert await count(c, MealCancellationRequest) == await count(c, AdminAudit) == await count(c, Refund) == 0
    before = await c.client.get(f"/v1/orders/{c.order.id}", headers=auth_headers(c.admin))
    assert "cancel" in before.json()["available_actions"]
    first = await c.client.post(path, json=body, headers=auth_headers(c.admin))
    assert first.status_code == 200, first.text
    repeat = await c.client.post(path, json={"reason": "重複電話"}, headers=auth_headers(c.admin))
    assert repeat.status_code == 200, repeat.text
    assert first.json()["cancellation_request"] == repeat.json()["cancellation_request"]
    request = first.json()["cancellation_request"]
    assert request["status"] == "approved" and request["reason"] == "電話確認行程變更"
    assert request["reviewed_by_id"] == c.admin.id
    saved = await c.session.get(MealCancellationRequest, request["id"])
    assert saved.requested_by_id == c.admin.id
    assert c.order.payment_status == (PaymentStatus.REFUND_PENDING if paid else PaymentStatus.EXPIRED)
    assert c.order.cancelled_at and c.order.fulfillment.status == FulfillmentState.CANCELLED
    assert await count(c, MealCancellationRequest) == await count(c, AdminAudit) == 1
    assert await count(c, Refund) == int(paid)
    assert c.order.invoice_status == (InvoiceStatus.ISSUED if paid else InvoiceStatus.NOT_ELIGIBLE)
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.adjustment_required")) == 0
    audit = await c.session.scalar(select(AdminAudit))
    assert audit.data == {"request_id": request["id"], "source": "phone"}
    await c.session.refresh(c.offering)
    await c.session.refresh(c.reservation)
    assert c.offering.reserved_quantity == c.offering.paid_quantity == 0
    assert c.reservation.status == ReservationStatus.RELEASED


@pytest.mark.parametrize("paid", [False, True])
async def test_customer_cannot_cancel_after_pickup_starts(review_context, paid):
    c = review_context
    if paid:
        await mark_paid(c)
    paths = [f"/v1/orders/{c.order.id}/cancel", f"/v1/meal-orders/{c.order.id}/cancel", f"/v1/meal-orders/{c.order.id}/cancellation-requests"]
    before = (c.order.payment_status, c.order.invoice_status, c.reservation.status, c.offering.reserved_quantity, c.offering.paid_quantity)
    for actor in [c.buyer, c.other]:
        for path in paths:
            result = await c.client.post(path, json={"reason": "請求退單"}, headers=auth_headers(actor))
            if path.startswith("/v1/orders"):
                assert result.status_code == 403, result.text
            else:
                assert result.status_code == (409 if actor is c.buyer else 404), result.text
    assert before == (c.order.payment_status, c.order.invoice_status, c.reservation.status, c.offering.reserved_quantity, c.offering.paid_quantity)
    assert c.order.cancelled_at is None
    assert await count(c, MealCancellationRequest) == await count(c, Refund) == await count(c, AdminAudit) == 0
    assert await count(c, OutboxEvent) == 0
    detail = await c.client.get(f"/v1/meal-orders/{c.order.id}", headers=auth_headers(c.buyer))
    assert not {"cancel", "request_cancellation"}.intersection(detail.json()["available_actions"])


@pytest.mark.parametrize("channel", ["regular", "meal_preorder"])
async def test_phone_cancellation_approves_existing_pending_without_creating_another_request(review_context, channel):
    c = review_context
    path = await prepare(c, channel, paid=True)
    pending = await submit(c, "既有買家退單原因")
    result = await c.client.post(path, json={"reason": "電話補充核對完成"}, headers=auth_headers(c.admin))
    assert result.status_code == 200, result.text
    assert result.json()["cancellation_request"]["id"] == pending["id"]
    assert result.json()["cancellation_request"]["review_reason"] == "電話補充核對完成"
    assert result.json()["cancellation_request"]["reason"] == "既有買家退單原因"
    assert (await c.client.post(path, json={"reason": "重複確認"}, headers=auth_headers(c.admin))).status_code == 200
    assert await count(c, Refund) == await count(c, MealCancellationRequest) == await count(c, AdminAudit) == 1


@pytest.mark.parametrize("state", [FulfillmentState.PREPARING, FulfillmentState.READY_FOR_PICKUP])
async def test_phone_meal_refund_retains_prepared_capacity_and_voids_only_after_refund_confirmation(review_context, state):
    c = review_context
    attempt = await mark_paid(c, state)
    path = f"/v1/admin/meal-orders/{c.order.id}/cancel"
    result = await c.client.post(path, json={"reason": "電話退單，餐點已製作"}, headers=auth_headers(c.admin))
    assert result.status_code == 200, result.text
    await c.session.refresh(c.offering)
    assert c.offering.paid_quantity == 2
    assert c.order.fulfillment.meal_capacity_retained is True
    assert c.order.invoice.status == c.order.invoice_status == InvoiceStatus.ISSUED
    await SQLAlchemyPaymentCallbackRepository(c.session)._apply_provider_refund(attempt, {"TradeNo": "PROVIDER-0001", "RayGateAssociatedOrderID": "PHONE-REFUND"})
    await c.session.commit()
    assert c.order.payment_status == PaymentStatus.REFUNDED
    assert c.order.invoice.status == InvoiceStatus.VOID_PENDING
    await c.session.refresh(c.offering)
    assert c.offering.paid_quantity == 2
    assert (await c.client.post(path, json={"reason": "已退再次送出"}, headers=auth_headers(c.admin))).status_code == 200
    assert await count(c, Refund) == 1


@pytest.mark.parametrize("channel", ["regular", "meal_preorder"])
@pytest.mark.parametrize("state", [FulfillmentState.PICKED_UP, FulfillmentState.SHIPPED, FulfillmentState.DELIVERED, FulfillmentState.NO_SHOW])
async def test_finished_fulfillment_cannot_be_cancelled(review_context, channel, state):
    c = review_context
    path = await prepare(c, channel, paid=True)
    c.order.fulfillment.status = state
    await c.session.commit()
    response = await c.client.post(path, json={"reason": "電話退單"}, headers=auth_headers(c.admin))
    assert response.status_code == 409, response.text
    assert c.order.cancelled_at is None and c.order.payment_status == PaymentStatus.PAID
    assert await count(c, MealCancellationRequest) == await count(c, Refund) == await count(c, AdminAudit) == 0


@pytest.mark.parametrize("paid", [False, True])
async def test_created_logistics_blocks_phone_cancellation_even_with_stale_order_status(review_context, paid):
    c = review_context
    path = await prepare(c, "regular", paid)
    c.order.fulfillment.shipment = Shipment(channel=ShippingChannel.HOME_DELIVERY, temperature=ShippingTemperature.AMBIENT, status=ShipmentStatus.CREATED)
    await c.session.commit()
    response = await c.client.post(path, json={"reason": "電話退單"}, headers=auth_headers(c.admin))
    assert response.status_code == 409
    assert c.order.fulfillment.shipment.status == ShipmentStatus.CREATED
    assert await count(c, MealCancellationRequest) == await count(c, Refund) == await count(c, AdminAudit) == 0


@pytest.mark.parametrize("channel", ["regular", "meal_preorder"])
async def test_failed_provider_refund_validation_does_not_create_a_phone_cancellation_record(review_context, channel):
    c = review_context
    path = await prepare(c, channel, paid=True)
    attempt = await c.session.scalar(select(PaymentAttempt))
    attempt.provider_trade_no = None
    await c.session.commit()
    response = await c.client.post(path, json={"reason": "電話退單"}, headers=auth_headers(c.admin))
    assert response.status_code == 409
    assert c.order.cancelled_at is None and c.order.payment_status == PaymentStatus.PAID
    assert c.order.fulfillment.status == FulfillmentState.PENDING_CONFIRMATION
    assert await count(c, MealCancellationRequest) == await count(c, Refund) == await count(c, AdminAudit) == 0


@pytest.mark.parametrize("channel", ["regular", "meal_preorder"])
@pytest.mark.parametrize("reason", ["字" * 2001])
async def test_phone_cancellation_rejects_oversized_reason(review_context, channel, reason):
    c = review_context
    path = await prepare(c, channel)
    response = await c.client.post(path, json={"reason": reason}, headers=auth_headers(c.admin))
    assert response.status_code == 422
    assert c.order.cancelled_at is None and await count(c, MealCancellationRequest) == 0


@pytest.mark.parametrize("channel", ["regular", "group"])
@pytest.mark.parametrize("paid", [False, True])
async def test_phone_cancellation_restores_product_stock_or_campaign_quantity_exactly_once(review_context, channel, paid):
    c = review_context
    path = await prepare(c, channel, paid)
    product = Product(slug="phone-rice", name="電話退單白米", category="米・雜糧", unit="包",
        member_price=100, nonmember_price=100, stock_quantity=3)
    c.session.add(product)
    await c.session.flush()
    c.order.meal_event = None
    item = c.order.items[0]
    item.source_meal_offering_id = None
    item.source_product_id = product.id
    item.product_name = product.name
    item.unit_label = product.unit
    c.reservation.source_meal_offering_id = None
    c.reservation.source_product_id = product.id if channel == "regular" else None
    c.order.fulfillment.status = FulfillmentState.PREPARING if paid else FulfillmentState.PENDING_CONFIRMATION
    c.order.fulfillment_status = FulfillmentStatus.PREPARING if paid else FulfillmentStatus.PENDING_CONFIRMATION
    if channel == "group":
        campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=product.id, title="白米共同購買",
            member_price=100, nonmember_price=100, min_paid_quantity=10, supply_cap=20,
            paid_quantity=12 if paid else 10, reserved_quantity=0 if paid else 2,
            deadline=c.now + timedelta(days=1), estimated_pickup_start=c.now + timedelta(days=2),
            estimated_pickup_end=c.now + timedelta(days=3), decision_status=GroupDecisionStatus.CONFIRMED,
            intake_status=GroupIntakeStatus.OPEN, confirmed_at=c.now - timedelta(hours=2), created_by_id=c.admin.id)
        c.session.add(campaign)
        await c.session.flush()
        c.order.group_campaign = campaign
        c.reservation.group_campaign_id = campaign.id
        if paid:
            c.order.paid_at = c.now - timedelta(hours=1)
    await c.session.commit()
    for _ in range(2):
        result = await c.client.post(path, json={"reason": "已電話核對，超過買家原取消期限仍由管理員辦理"}, headers=auth_headers(c.admin))
        assert result.status_code == 200, result.text
    await c.session.refresh(product)
    await c.session.refresh(c.reservation)
    assert c.reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == (5 if channel == "regular" else 3)
    if channel == "group":
        await c.session.refresh(campaign)
        assert campaign.paid_quantity == 10 and campaign.reserved_quantity == 0
        assert campaign.decision_status == GroupDecisionStatus.CONFIRMED
        assert campaign.threshold_version == (1 if paid else 0)
    assert await count(c, Refund) == int(paid)
    assert await count(c, MealCancellationRequest) == await count(c, AdminAudit) == 1


@pytest.mark.parametrize("channel", ["regular", "meal_preorder"])
@pytest.mark.parametrize("paid", [False, True])
@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": " \t "}, {"reason": None}])
async def test_admin_cancellation_optional_reason_preserves_audit_and_money_state(review_context, channel, paid, body):
    c = review_context
    path = await prepare(c, channel, paid)
    response = await c.client.post(path, json=body, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    request = await c.session.get(MealCancellationRequest, response.json()["cancellation_request"]["id"])
    assert request.status == "approved" and request.reason == request.review_reason == ""
    assert request.reviewed_by_id == c.admin.id and request.reviewed_at is not None
    assert c.order.cancelled_at and c.order.fulfillment.status == FulfillmentState.CANCELLED
    assert c.order.payment_status == (PaymentStatus.REFUND_PENDING if paid else PaymentStatus.EXPIRED)
    audit = await c.session.scalar(select(AdminAudit))
    assert audit.actor_id == c.admin.id and audit.reason == ""
    refund = await c.session.scalar(select(Refund))
    assert (refund is not None) == paid
    if paid:
        assert refund.amount == c.order.amount_total and refund.reason == ""
    repeat = await c.client.post(path, json=body, headers=auth_headers(c.admin))
    assert repeat.status_code == 200
    assert await count(c, MealCancellationRequest) == await count(c, AdminAudit) == 1
    assert await count(c, Refund) == int(paid)
