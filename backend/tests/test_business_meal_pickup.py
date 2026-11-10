from datetime import datetime, timedelta

import time_machine

from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.jobs import _reconcile_meal_events
from app.models import FulfillmentState, Order, PaymentStatus
from app.routers import meals
from tests.support import auth_headers
from tests.test_business_meals import CLOSE, OPEN, business_body
from tests.test_meal_booking_policy import booking_context
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


async def test_business_requires_payment_before_credential_and_redemption(booking_context, fake_payment, monkeypatch):
    client, session, buyer, admin, event, body = booking_context
    buyer_headers, admin_headers = auth_headers(buyer), auth_headers(admin)
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=buyer_headers)
    assert created.status_code == 201, created.text
    order_id, event_id = created.json()['id'], event.id
    order = await session.get(Order, order_id)
    code = order.fulfillment.pickup_code
    monkeypatch.setattr(meals, 'datetime', datetime)
    pickup = OPEN + timedelta(hours=11)
    with time_machine.travel(pickup, tick=False):
        buyer_headers, admin_headers = auth_headers(buyer), auth_headers(admin)
        await _reconcile_meal_events(session, pickup, 100)
        credential = await client.get(f'/v1/meal-orders/{order_id}/pickup-credential', headers=buyer_headers)
        assert credential.status_code == 409
        blocked = await client.post(f'/v1/admin/meal-events/{event_id}/redeem', json={'pickup_code': code}, headers=admin_headers)
        assert blocked.status_code == 404
        await session.refresh(buyer)
        attempt = await create_payment_attempt(session, order_id, buyer, payment_settings(), now=pickup)
        assert await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
            'business-pickup-payment', successful_callback(attempt, pickup, 'BUSINESS-PICKUP')) == 'paid'
        credential = await client.get(f'/v1/meal-orders/{order_id}/pickup-credential', headers=buyer_headers)
        assert credential.status_code == 200 and credential.json()['pickup_code'] == code
        redeemed = await client.post(f'/v1/admin/meal-events/{event_id}/redeem', json={'pickup_code': code}, headers=admin_headers)
        assert redeemed.status_code == 200, redeemed.text
        repeated = await client.post(f'/v1/admin/meal-events/{event_id}/redeem', json={'pickup_code': code}, headers=admin_headers)
        assert repeated.status_code == 409
        await session.refresh(order)
        await session.refresh(order.fulfillment)
        assert order.payment_status == PaymentStatus.PAID
        assert order.fulfillment.status == FulfillmentState.PICKED_UP


async def test_paid_business_not_collected_keeps_existing_no_show_handling(booking_context, fake_payment):
    client, session, buyer, _admin, event, body = booking_context
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    order_id = created.json()['id']
    attempt = await create_payment_attempt(session, order_id, buyer, payment_settings(), now=OPEN)
    await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        'business-no-show-payment', successful_callback(attempt, OPEN, 'BUSINESS-NO-SHOW'))
    await _reconcile_meal_events(session, CLOSE, 100)
    order = await session.get(Order, order_id)
    await session.refresh(order.fulfillment)
    assert order.payment_status == PaymentStatus.PAID and order.cancelled_at is None
    assert order.fulfillment.status == FulfillmentState.NO_SHOW
