from datetime import datetime, timedelta

import pytest
import time_machine
from sqlalchemy import func, select

from app.integrations import payment_service
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.meal_booking import TAIPEI
from app.models import Order, OutboxEvent, PaymentAttempt, Refund
from app.routers.meals import _aware
from tests.support import auth_headers
from tests.test_meal_booking_policy import booking_context
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


PICKUP = datetime(2030, 1, 2, 11, tzinfo=TAIPEI)
DEADLINE = PICKUP - timedelta(hours=1)
BEFORE_DEADLINE = DEADLINE - timedelta(seconds=1)


async def create_order(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    event.booking_policy = "special"
    event.ordering_ends_at = PICKUP + timedelta(hours=1)
    event.same_day_preparation_minutes = 60
    await session.commit()
    created = await client.post(
        f"/v1/meal-events/{event.id}/orders",
        json={**body, "pickup_at": PICKUP.isoformat()},
        headers=auth_headers(buyer),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


@pytest.mark.parametrize("confirmed_at, expected", [
    (BEFORE_DEADLINE, "paid"),
    (DEADLINE, "late_paid_refund_required"),
])
async def test_same_day_requires_provider_and_system_confirmation_before_preparation(
    booking_context, fake_payment, confirmed_at, expected,
):
    client, session, buyer, _admin, event, _body = booking_context
    order_id = await create_order(booking_context)
    order = await session.get(Order, order_id)
    await session.refresh(order, ["fulfillment"])
    assert _aware(order.fulfillment.meal_ordering_ends_at) == DEADLINE
    attempt = await create_payment_attempt(
        session, order_id, buyer, payment_settings(), now=BEFORE_DEADLINE,
    )
    assert _aware(attempt.expires_at) == DEADLINE
    with time_machine.travel(confirmed_at, tick=False):
        checkout = await client.get(f"/payments/{attempt.id}/checkout")
        assert checkout.status_code == (200 if expected == "paid" else 410)
        result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
            "same-day-preparation-payment", successful_callback(attempt, BEFORE_DEADLINE, "SAME-DAY-TRADE"),
        )
    assert result == expected
    if expected == "paid":
        with time_machine.travel(DEADLINE, tick=False):
            replay = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
                "same-day-preparation-payment", successful_callback(attempt, BEFORE_DEADLINE, "SAME-DAY-TRADE"),
            )
        assert replay in {"duplicate", "paid"}
    offering = event.offerings[0]
    await session.refresh(offering)
    assert offering.reserved_quantity == 0
    assert offering.paid_quantity == int(expected == "paid")
    refunds = await session.scalar(select(func.count(Refund.id)).where(Refund.order_id == order_id))
    assert refunds == int(expected != "paid")
    if expected != "paid":
        assert await session.scalar(select(OutboxEvent.id).where(
            OutboxEvent.aggregate_id == order_id,
            OutboxEvent.event_type == "invoice.issue_requested",
        )) is None


async def test_same_day_payment_waiting_for_order_lock_rechecks_preparation_deadline(
    booking_context, fake_payment, monkeypatch,
):
    client, session, buyer, _admin, event, _body = booking_context
    order_id = await create_order(booking_context)
    lock = payment_service.lock_order_payment_subject
    with time_machine.travel(BEFORE_DEADLINE, tick=False) as clock:
        async def wait_past_deadline(*args):
            await lock(*args)
            clock.move_to(DEADLINE)

        monkeypatch.setattr(payment_service, "lock_order_payment_subject", wait_past_deadline)
        response = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=auth_headers(buyer))
        assert response.status_code == 409, response.text

    assert await session.scalar(select(func.count(PaymentAttempt.id)).where(PaymentAttempt.order_id == order_id)) == 0
    await session.refresh(event, ["offerings"])
    assert event.offerings[0].reserved_quantity == 0
