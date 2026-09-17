from datetime import datetime, timedelta, timezone

import pytest

from app.domain import order_available_actions
from app.models import (
    FulfillmentMethod, FulfillmentState, FulfillmentStatus, MealEvent, MealEventStatus,
    MembershipType, Order, OrderFulfillment, OrderItem, OrderKind, PaymentAttempt,
    PaymentStatus, SalesChannel, TaxType, User,
)
from app.routers import payments
from tests.support import api_test_context
from tests.test_payment_return import payment_settings


NOW = datetime(2030, 1, 1, 4, 0, tzinfo=timezone.utc)


def meal_order(status=MealEventStatus.PICKUP_OPEN):
    event = MealEvent(
        title="午餐取餐中仍可訂購", location="隔離測試取餐區", status=status,
        ordering_starts_at=NOW - timedelta(days=1), ordering_ends_at=NOW + timedelta(hours=1),
        pickup_starts_at=NOW - timedelta(hours=1), pickup_ends_at=NOW + timedelta(hours=2),
    )
    return Order(
        order_number="OVERLAP-CHECKOUT", order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        meal_event=event, membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=100, contact_email="isolated@example.test", payment_status=PaymentStatus.PENDING,
        fulfillment_status=FulfillmentStatus.PREPARING,
        fulfillment=OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP, status=FulfillmentState.PREPARING),
        items=[OrderItem(product_name="測試便當", unit_label="份", quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE)],
    )


@pytest.mark.parametrize("status", [MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN])
def test_pickup_in_progress_before_cutoff_retains_payment_action(status):
    assert "pay" in order_available_actions(meal_order(status), now=NOW)


@pytest.mark.parametrize("marker", ["cutoff", "after_cutoff", "not_started", "closed", "cancelled", "completed"])
def test_pending_meal_payment_action_matches_ordering_window(marker):
    order = meal_order()
    if marker == "cutoff":
        order.meal_event.ordering_ends_at = NOW
    elif marker == "after_cutoff":
        order.meal_event.ordering_ends_at = NOW - timedelta(seconds=1)
    elif marker == "not_started":
        order.meal_event.ordering_starts_at = NOW + timedelta(seconds=1)
    else:
        order.meal_event.status = {
            "closed": MealEventStatus.ORDERING_CLOSED, "cancelled": MealEventStatus.CANCELLED,
            "completed": MealEventStatus.COMPLETED,
        }[marker]
    assert "pay" not in order_available_actions(order, now=NOW)


@pytest.mark.parametrize("state", [FulfillmentState.PICKED_UP, FulfillmentState.NO_SHOW, FulfillmentState.CANCELLED])
def test_fulfilled_meal_never_advertises_self_cancel(state):
    order = meal_order()
    order.payment_status = PaymentStatus.PAID
    order.paid_at = NOW - timedelta(minutes=5)
    order.fulfillment.status = state
    assert "cancel" not in order_available_actions(order, now=NOW)


@pytest.mark.parametrize("payment_status", [PaymentStatus.PENDING, PaymentStatus.PAID])
@pytest.mark.parametrize("marker", ["legacy_picked_up", "shipped", "delivered"])
def test_meal_actions_keep_existing_irreversible_fulfillment_guard(payment_status, marker):
    order = meal_order()
    order.payment_status = payment_status
    order.paid_at = NOW - timedelta(minutes=5)
    if marker == "legacy_picked_up":
        order.fulfillment_status = FulfillmentStatus.PICKED_UP
    else:
        order.fulfillment.status = {
            "shipped": FulfillmentState.SHIPPED,
            "delivered": FulfillmentState.DELIVERED,
        }[marker]
    assert not {"pay", "cancel", "refund"} & set(order_available_actions(order, viewer_is_admin=True, now=NOW))


def test_unredeemed_paid_meal_keeps_existing_cancel_deadline():
    order = meal_order()
    order.payment_status = PaymentStatus.PAID
    order.paid_at = NOW - timedelta(minutes=5)
    assert "cancel" in order_available_actions(order, now=NOW)
    assert "cancel" not in order_available_actions(order, now=NOW + timedelta(minutes=26))
    order.paid_at = NOW - timedelta(minutes=1)
    order.meal_event.ordering_ends_at = NOW
    assert "cancel" in order_available_actions(order, now=NOW)
    assert "cancel" not in order_available_actions(order, now=NOW + timedelta(microseconds=1))


@pytest.fixture
async def checkout_context(database_session, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(payments, "datetime", FixedDateTime)
    buyer = User(email="isolated-checkout@example.test", display_name="隔離測試買家", password_hash="unused")
    database_session.add(buyer)
    await database_session.flush()
    order = meal_order()
    order.user = buyer
    order.meal_event.created_by_id = buyer.id
    attempt = PaymentAttempt(
        order=order, provider="ecpay", merchant_trade_no="OVERLAPCHECKOUT000001", amount=100,
        status=PaymentStatus.PENDING, expires_at=NOW + timedelta(hours=2),
        checkout_payload={"MerchantTradeNo": "OVERLAPCHECKOUT000001", "TradeAmt": "100"},
    )
    database_session.add_all([order, attempt])
    await database_session.commit()
    async with api_test_context(database_session, [payments.payments_router], settings=payment_settings()) as client:
        yield client, database_session, order, attempt


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN])
async def test_existing_checkout_remains_open_before_cutoff(checkout_context, status):
    client, session, order, attempt = checkout_context
    order.meal_event.status = status
    await session.commit()
    response = await client.get(f"/payments/{attempt.id}/checkout")
    assert response.status_code == 200, response.text
    assert "OVERLAPCHECKOUT000001" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["cutoff", "after_cutoff", "not_started", "closed", "cancelled", "completed"])
async def test_existing_unexpired_checkout_cannot_cross_ordering_window(checkout_context, marker):
    client, session, order, attempt = checkout_context
    if marker == "cutoff":
        order.meal_event.ordering_ends_at = NOW
    elif marker == "after_cutoff":
        order.meal_event.ordering_ends_at = NOW - timedelta(seconds=1)
    elif marker == "not_started":
        order.meal_event.ordering_starts_at = NOW + timedelta(seconds=1)
    else:
        order.meal_event.status = {
            "closed": MealEventStatus.ORDERING_CLOSED, "cancelled": MealEventStatus.CANCELLED,
            "completed": MealEventStatus.COMPLETED,
        }[marker]
    await session.commit()
    response = await client.get(f"/payments/{attempt.id}/checkout")
    assert response.status_code == 410, response.text
    assert "location" not in response.headers
    assert "<form" not in response.text
    assert attempt.status == PaymentStatus.PENDING
    assert attempt.expires_at == NOW + timedelta(hours=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("at_cutoff", [False, True])
async def test_raygate_redirect_stops_exactly_at_cutoff(checkout_context, at_cutoff):
    client, session, order, attempt = checkout_context
    redirect_url = f"{payment_settings().raygate_payment_base_url.rstrip('/')}/calc/pay_encrypt/isolated-test"
    attempt.provider = "raygate"
    attempt.checkout_payload = {"redirect_url": redirect_url}
    order.meal_event.ordering_ends_at = NOW if at_cutoff else NOW + timedelta(microseconds=1)
    await session.commit()
    response = await client.get(f"/payments/{attempt.id}/checkout")
    assert response.status_code == (410 if at_cutoff else 303), response.text
    assert response.headers.get("location") == (None if at_cutoff else redirect_url)
