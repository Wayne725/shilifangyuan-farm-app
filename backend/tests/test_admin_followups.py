from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import jobs
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.models import (
    AdminAudit, FulfillmentState, FulfillmentStatus, GroupDecisionStatus,
    GroupIntakeStatus, InvoiceStatus, MealCancellationRequest, MealEventStatus,
    PaymentStatus, Refund, Shipment, ShipmentStatus, ShippingChannel, ShippingTemperature,
)
from tests.support import auth_headers, make_test_settings
from tests.test_group_review_window import group_fixture
from tests.test_meal_cancellation_review import mark_paid, review_context
from tests.test_order_payment_revisions import success_payload


def close_pickup(c):
    c.event.ordering_starts_at = c.now - timedelta(hours=3)
    c.event.ordering_ends_at = c.now - timedelta(hours=2)
    c.event.pickup_starts_at = c.now - timedelta(hours=1)
    c.event.pickup_ends_at = c.now - timedelta(seconds=1)
    c.event.status = MealEventStatus.COMPLETED


@pytest.mark.parametrize("payment_status", [PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED, PaymentStatus.PAID])
async def test_admin_cancels_expired_uncollected_meal_once_without_releasing_prepared_capacity(review_context, payment_status):
    c = review_context
    paid = payment_status == PaymentStatus.PAID
    if paid:
        await mark_paid(c)
    c.order.payment_status = payment_status
    c.order.contact_email = "release-buyer@example.com"
    close_pickup(c)
    await c.session.commit()
    admin_detail = await c.client.get(f"/v1/orders/{c.order.id}", headers=auth_headers(c.admin))
    buyer_detail = await c.client.get(f"/v1/meal-orders/{c.order.id}", headers=auth_headers(c.buyer))
    assert admin_detail.status_code == 200, admin_detail.text
    assert "cancel" in admin_detail.json()["available_actions"]
    assert "cancel" not in buyer_detail.json()["available_actions"]
    path = f"/v1/admin/meal-orders/{c.order.id}/cancel"
    assert (await c.client.post(path, json={}, headers=auth_headers(c.buyer))).status_code == 403
    first = await c.client.post(path, json={}, headers=auth_headers(c.admin))
    assert first.status_code == 200, first.text
    repeated = await c.client.post(path, json={}, headers=auth_headers(c.admin))
    assert repeated.status_code == 200 and first.json()["cancellation_request"] == repeated.json()["cancellation_request"]
    request = first.json()["cancellation_request"]
    assert request["reason"] == request["review_reason"] == ""
    assert request["reviewed_by_name"] == c.admin.display_name and request["reviewed_at"]
    assert await c.session.scalar(select(func.count(MealCancellationRequest.id))) == 1
    assert await c.session.scalar(select(func.count(Refund.id))) == int(paid)
    await c.session.refresh(c.offering)
    assert c.offering.reserved_quantity == 0 and c.offering.paid_quantity == (2 if paid else 0)
    assert c.order.fulfillment.meal_capacity_retained is paid


async def test_automatic_no_show_is_not_actual_pickup_and_can_be_refunded_by_admin(review_context):
    c = review_context
    attempt = await mark_paid(c, FulfillmentState.READY_FOR_PICKUP)
    close_pickup(c)
    c.event.status = MealEventStatus.PICKUP_OPEN
    await c.session.commit()
    await jobs._reconcile_meal_events(c.session, datetime.now(timezone.utc), 10)
    assert c.order.fulfillment.status == FulfillmentState.NO_SHOW
    assert c.order.fulfillment_status == FulfillmentStatus.PICKED_UP
    response = await c.client.post(f"/v1/admin/meal-orders/{c.order.id}/cancel", json={}, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    assert c.order.payment_status == PaymentStatus.REFUND_PENDING
    assert c.order.fulfillment.meal_capacity_retained
    await c.session.refresh(c.offering)
    assert c.offering.paid_quantity == 2
    assert c.order.invoice_status == InvoiceStatus.ISSUED
    await SQLAlchemyPaymentCallbackRepository(c.session)._apply_provider_refund(attempt, {
        "TradeNo": "PROVIDER-0001", "RayGateAssociatedOrderID": "NO-SHOW-REFUND",
    })
    await c.session.commit()
    await c.session.refresh(c.offering)
    assert c.order.payment_status == PaymentStatus.REFUNDED
    assert c.order.invoice.status == InvoiceStatus.VOID_PENDING
    assert c.offering.paid_quantity == 2


@pytest.mark.parametrize("action", ["meal_order.redeem", "order.fulfillment.picked_up", "order.fulfillment.delivered", "order.fulfillment.shipped"])
async def test_delivery_audit_blocks_admin_refund_even_if_no_show_state_is_inconsistent(review_context, action):
    c = review_context
    await mark_paid(c, FulfillmentState.NO_SHOW)
    close_pickup(c)
    c.session.add(AdminAudit(actor_id=c.admin.id, action=action, aggregate_type="order", aggregate_id=c.order.id))
    await c.session.commit()
    response = await c.client.post(f"/v1/admin/meal-orders/{c.order.id}/cancel", json={}, headers=auth_headers(c.admin))
    assert response.status_code == 409 and "交付紀錄" in response.json()["detail"]
    assert c.order.cancelled_at is None and c.order.payment_status == PaymentStatus.PAID
    assert await c.session.scalar(select(func.count(Refund.id))) == 0


@pytest.mark.parametrize("state", [FulfillmentState.PICKED_UP, FulfillmentState.SHIPPED, FulfillmentState.DELIVERED])
async def test_actual_delivered_expired_meal_remains_nonrefundable(review_context, state):
    c = review_context
    await mark_paid(c, state)
    close_pickup(c)
    await c.session.commit()
    response = await c.client.post(f"/v1/admin/meal-orders/{c.order.id}/cancel", json={}, headers=auth_headers(c.admin))
    assert response.status_code == 409 and c.order.cancelled_at is None
    assert await c.session.scalar(select(func.count(Refund.id))) == 0


@pytest.mark.parametrize("status", [ShipmentStatus.CREATED, ShipmentStatus.IN_TRANSIT, ShipmentStatus.DELIVERED, ShipmentStatus.EXCEPTION])
async def test_no_show_does_not_bypass_existing_logistics_handoff(review_context, status):
    c = review_context
    await mark_paid(c, FulfillmentState.NO_SHOW)
    close_pickup(c)
    c.order.fulfillment.shipment = Shipment(channel=ShippingChannel.HOME_DELIVERY,
        temperature=ShippingTemperature.AMBIENT, status=status)
    await c.session.commit()
    response = await c.client.post(f"/v1/admin/meal-orders/{c.order.id}/cancel", json={}, headers=auth_headers(c.admin))
    assert response.status_code == 409 and c.order.cancelled_at is None
    assert await c.session.scalar(select(func.count(Refund.id))) == 0


async def test_zero_paid_campaign_closes_only_after_all_current_attempts_are_verified_unpaid(database_session):
    session = database_session
    _, campaign, _, order, old, current = await group_fixture(session)
    now = datetime.now(timezone.utc)
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 10)
    assert campaign.intake_status == GroupIntakeStatus.SETTLING
    await jobs._record_group_payment_verification(session, current.id, now, verified_unpaid=True)
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 10)
    assert campaign.decision_status == GroupDecisionStatus.FAILED_UNMET
    assert campaign.intake_status == GroupIntakeStatus.CLOSED and campaign.reserved_quantity == 0
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 10)
    assert campaign.decision_status == GroupDecisionStatus.FAILED_UNMET
    # A verified-unpaid result can later be corrected by the provider; do not revive a closed group.
    payload = success_payload(current)
    payload["PaymentDate"] = (current.expires_at - timedelta(seconds=1)).isoformat()
    await SQLAlchemyPaymentCallbackRepository(session).apply_query_result(current, payload)
    assert order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert campaign.paid_quantity == 0
    assert await session.scalar(select(func.count(Refund.id))) == 1


async def test_zero_counter_does_not_auto_close_existing_financial_followup(database_session):
    session = database_session
    _, campaign, _, order, _, _ = await group_fixture(session, paid=True)
    campaign.paid_quantity = 0
    order.payment_status = PaymentStatus.REFUND_PENDING
    await session.commit()
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION
