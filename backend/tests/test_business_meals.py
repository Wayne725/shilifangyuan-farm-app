from datetime import datetime, timedelta, timezone

import pytest
import time_machine
from uuid import uuid4
from sqlalchemy import func, select

from app.integrations import payment_service
from app.integrations.payment_service import PaymentApplicationError, SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.business_meals import business_payment_fields
from app.jobs import _reconcile_meal_events
from app.models import AdminAudit, InventoryReservation, MealEventStatus, MealOption, MealOptionGroup, Order, PaymentAttempt, PaymentStatus, Refund, ReservationStatus, TaxType
from app.routers import meals
from tests.support import auth_headers
from tests.test_meal_booking_policy import NOW, booking_context
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


OPEN = datetime(2030, 1, 1, 16, tzinfo=timezone.utc)
CLOSE = OPEN + timedelta(days=1)


def business_body(body, **overrides):
    return {**body, 'meal_booking_type': 'business', 'invoice_buyer_type': 'company',
        'invoice_buyer_tax_id': '15989995', 'invoice_buyer_name': '合成學校',
        'invoice_buyer_email': 'school@example.com',
        'items': [{**body['items'][0], 'quantity': 2}], **overrides}


@pytest.mark.parametrize('overrides', [
    {'invoice_buyer_type': 'personal', 'invoice_buyer_tax_id': None, 'invoice_buyer_name': None},
    {'invoice_buyer_tax_id': '12345678'}, {'invoice_buyer_name': ''},
    {'payment_method': 'cash_on_delivery'},
    {'payment_method': 'cash_on_pickup', 'invoice_handling': 'external'},
])
async def test_business_requires_company_and_supported_payment(booking_context, overrides):
    client, session, buyer, _admin, event, body = booking_context
    result = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body, **overrides), headers=auth_headers(buyer))
    assert result.status_code == 422, result.text
    assert await session.scalar(select(func.count(Order.id))) == 0


async def test_business_minimum_two_and_forced_invoice_policy(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    result = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body, items=body['items']), headers=auth_headers(buyer))
    assert result.status_code == 422 and '至少' in result.text
    result = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body, invoice_handling='on_payment'), headers=auth_headers(buyer))
    assert result.status_code == 201, result.text
    assert result.json()['meal_booking_type'] == 'business'
    assert result.json()['invoice_handling'] == 'pickup_day'
    assert datetime.fromisoformat(result.json()['payment_opens_at'].replace('Z', '+00:00')) == OPEN
    order = await session.get(Order, result.json()['id'])
    reservations = list(await session.scalars(select(InventoryReservation).where(InventoryReservation.order_id == order.id)))
    assert len(reservations) == 1 and reservations[0].payment_attempt_id is None
    assert reservations[0].quantity == 2 and reservations[0].status == ReservationStatus.ACTIVE
    assert meals._aware(reservations[0].expires_at) == meals._aware(event.pickup_ends_at)
    await session.refresh(event.offerings[0])
    assert event.offerings[0].reserved_quantity == 2


async def test_business_portions_keep_separate_choices_and_share_capacity(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    offering = event.offerings[0]
    offering.capacity = 2
    group = MealOptionGroup(meal_id=offering.meal_id, name="主食", min_selections=1, options=[
        MealOption(name="米飯"), MealOption(name="麵", price_delta=10),
    ])
    session.add(group)
    await session.commit()
    rice, noodles = group.options
    portions = [{**body["items"][0], "option_ids": [option.id]} for option in (rice, noodles)]
    url = f"/v1/meal-events/{event.id}/orders"
    headers = auth_headers(buyer)
    extra = await client.post(url, json=business_body(body, items=[{**portions[0], "quantity": 2}, portions[1]]), headers=headers)
    assert extra.status_code == 409, extra.text
    duplicate = await client.post(url, json=business_body(body, items=[portions[0], portions[0]]), headers=headers)
    assert duplicate.status_code == 422, duplicate.text
    created = await client.post(url, json=business_body(body, items=portions), headers=headers)
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["amount_total"] == 210
    assert {(item["quantity"], item["selections"][0]["option_name"]) for item in order["items"]} == {(1, "米飯"), (1, "麵")}
    await session.refresh(offering)
    assert offering.reserved_quantity == 2


@pytest.mark.parametrize('clock', [NOW, CLOSE])
async def test_business_payment_day_is_enforced(booking_context, fake_payment, clock):
    client, session, buyer, _admin, event, body = booking_context
    result = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    assert result.status_code == 201, result.text
    with pytest.raises(PaymentApplicationError, match='會餐'):
        await create_payment_attempt(session, result.json()['id'], buyer, payment_settings(), now=clock)
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == 0


@pytest.mark.parametrize('clock', [OPEN, OPEN + timedelta(hours=16), CLOSE - timedelta(seconds=1)])
async def test_business_uses_existing_hold_and_accepts_pickup_day_payment(booking_context, fake_payment, clock):
    client, session, buyer, _admin, event, body = booking_context
    result = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    assert result.status_code == 201, result.text
    offering = event.offerings[0]
    await _reconcile_meal_events(session, clock, 100)
    attempt = await create_payment_attempt(session, result.json()['id'], buyer, payment_settings(), now=clock)
    await session.refresh(offering)
    assert offering.reserved_quantity == 2
    assert meals._aware(attempt.expires_at) <= CLOSE
    result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        'business-payment', successful_callback(attempt, clock, 'BUSINESS-TRADE'))
    assert result == 'paid'
    await session.refresh(offering)
    assert offering.reserved_quantity == 0 and offering.paid_quantity == 2
    assert await session.scalar(select(func.count(Refund.id))) == 0
    repeat = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        'business-payment-repeat', successful_callback(attempt, clock, 'BUSINESS-TRADE'))
    assert repeat in {'paid', 'duplicate'}
    assert await session.scalar(select(func.count(Refund.id))) == 0


async def test_overdue_business_is_not_auto_cancelled(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    result = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    await _reconcile_meal_events(session, CLOSE + timedelta(hours=1), 100)
    order = await session.get(Order, result.json()['id'])
    assert order.payment_status == PaymentStatus.PENDING and order.cancelled_at is None


async def test_business_order_retries_preserve_identity_and_standard_compatibility(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    request_id = str(uuid4())
    standard = {**body, 'client_request_id': request_id}
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=standard, headers=auth_headers(buyer))
    again = await client.post(f'/v1/meal-events/{event.id}/orders', json={**standard, 'meal_booking_type': 'standard'}, headers=auth_headers(buyer))
    assert created.status_code == again.status_code == 201
    assert again.json()['id'] == created.json()['id']
    changed = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(standard), headers=auth_headers(buyer))
    assert changed.status_code == 409
    business = business_body(body, client_request_id=str(uuid4()))
    first = await client.post(f'/v1/meal-events/{event.id}/orders', json=business, headers=auth_headers(buyer))
    repeat = await client.post(f'/v1/meal-events/{event.id}/orders', json=business, headers=auth_headers(buyer))
    assert first.json()['id'] == repeat.json()['id']
    await session.refresh(event.offerings[0])
    assert event.offerings[0].reserved_quantity == 2


async def test_business_payment_action_and_http_guard_after_service_end(booking_context, fake_payment):
    client, session, buyer, _admin, event, body = booking_context
    result = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    order_id = result.json()['id']
    before = await client.post(f'/v1/orders/{order_id}/payment-attempts', json={}, headers=auth_headers(buyer))
    assert before.status_code == 409 and '會餐' in before.text
    await session.refresh(buyer)
    clock = OPEN + timedelta(hours=16)
    with time_machine.travel(clock, tick=False):
        await _reconcile_meal_events(session, clock, 100)
        detail = await client.get(f'/v1/meal-orders/{order_id}', headers=auth_headers(buyer))
        assert 'pay' in detail.json()['available_actions'], detail.text
        opened = await client.post(f'/v1/orders/{order_id}/payment-attempts', json={}, headers=auth_headers(buyer))
        assert opened.status_code == 201, opened.text
        attempt_id = opened.json()['id']
        assert (await client.get(f'/payments/{attempt_id}/checkout')).status_code == 200
    with time_machine.travel(CLOSE, tick=False):
        closed = await client.post(f'/v1/orders/{order_id}/payment-attempts', json={}, headers=auth_headers(buyer))
        assert closed.status_code == 409 and '聯絡合作社' in closed.text
        assert (await client.get(f'/payments/{attempt_id}/checkout')).status_code == 410
        await session.refresh(buyer)
        detail = await client.get(f'/v1/meal-orders/{order_id}', headers=auth_headers(buyer))
        assert 'pay' not in detail.json()['available_actions']
        assert detail.json()['payment_followup_required'] is True


@pytest.mark.parametrize("blocked_stage", ["new_order", "reuse_order", "new_reservations"])
async def test_business_payment_waiting_for_lock_cannot_start_after_midnight(
    booking_context, fake_payment, monkeypatch, blocked_stage,
):
    client, session, buyer, _admin, event, body = booking_context
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=business_body(body), headers=auth_headers(buyer))
    assert created.status_code == 201
    order_id = created.json()["id"]
    existing = None
    if blocked_stage == "reuse_order":
        existing = await create_payment_attempt(session, order_id, buyer, payment_settings(), now=CLOSE - timedelta(minutes=1))

    lock_name = "_business_meal_reservations" if blocked_stage == "new_reservations" else "lock_order_payment_subject"
    lock = getattr(payment_service, lock_name)
    with time_machine.travel(CLOSE - timedelta(seconds=1), tick=False) as clock:
        async def cross_midnight_after_lock(*args):
            result = await lock(*args)
            clock.move_to(CLOSE)
            return result

        monkeypatch.setattr(payment_service, lock_name, cross_midnight_after_lock)
        result = await client.post(f"/v1/orders/{order_id}/payment-attempts", json={}, headers=auth_headers(buyer))
        assert result.status_code == 409, result.text
        assert result.json()["detail"] == "會餐付款日期已過，請聯絡合作社"

    attempts = list(await session.scalars(select(PaymentAttempt).where(PaymentAttempt.order_id == order_id)))
    assert len(attempts) == int(existing is not None)
    if existing is not None:
        assert attempts[0].id == existing.id and attempts[0].status == PaymentStatus.PENDING
    reservation = await session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == order_id))
    assert reservation.status == ReservationStatus.ACTIVE and reservation.payment_attempt_id is None


async def test_business_failed_checkout_retry_does_not_reserve_again(booking_context, fake_payment):
    client, session, buyer, _admin, event, body = booking_context
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    offering = event.offerings[0]
    attempt = await create_payment_attempt(session, created.json()['id'], buyer, payment_settings(), now=OPEN)
    failed = {**successful_callback(attempt, OPEN, 'BIZ-FAILED'), 'RtnCode': '0'}
    assert await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback('business-failed', failed) == 'failed'
    retry = await create_payment_attempt(session, created.json()['id'], buyer, payment_settings(), now=OPEN + timedelta(minutes=30))
    assert retry.id != attempt.id
    await session.refresh(offering)
    assert offering.reserved_quantity == 2
    assert await session.scalar(select(func.count(InventoryReservation.id))) == 1


async def test_business_old_checkout_paid_next_day_records_money_without_auto_refund(booking_context, fake_payment):
    client, session, buyer, _admin, event, body = booking_context
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    offering = event.offerings[0]
    attempt = await create_payment_attempt(session, created.json()['id'], buyer, payment_settings(), now=CLOSE - timedelta(minutes=1))
    actual_payment = CLOSE + timedelta(minutes=2)
    with time_machine.travel(actual_payment, tick=False):
        result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
            'business-cross-day', successful_callback(attempt, actual_payment, 'BIZ-CROSS-DAY'))
        assert result == 'paid'
        await session.refresh(offering)
        order = await session.get(Order, created.json()['id'])
        assert meals._aware(order.paid_at) == actual_payment
        assert order.cancelled_at is None
        assert business_payment_fields(order)['invoice_followup_required'] is True
        assert offering.reserved_quantity == 0 and offering.paid_quantity == 2
        assert await session.scalar(select(func.count(Refund.id))) == 0


async def test_admin_can_close_overdue_unpaid_business_without_loosening_customer_permissions(booking_context):
    client, session, buyer, admin, event, body = booking_context
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    order_id = created.json()['id']
    offering = event.offerings[0]
    clock = CLOSE + timedelta(hours=1)
    with time_machine.travel(clock, tick=False):
        await _reconcile_meal_events(session, clock, 100)
        await session.refresh(admin)
        await session.refresh(buyer)
        admin_headers, buyer_headers = auth_headers(admin), auth_headers(buyer)
        own = await client.get(f'/v1/meal-orders/{order_id}', headers=buyer_headers)
        assert 'cancel' not in own.json()['available_actions']
        path = f'/v1/admin/meal-orders/{order_id}/cancel'
        assert (await client.post(path, json={}, headers=buyer_headers)).status_code == 403
        for _ in range(2):
            result = await client.post(path, json={}, headers=admin_headers)
            assert result.status_code == 200, result.text
        await session.refresh(offering)
        assert offering.reserved_quantity == 0
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == 'meal_cancellation.approved')) == 1
        reservation = await session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == order_id))
        assert reservation.status == ReservationStatus.RELEASED


@pytest.mark.parametrize('tax_type,expected', [(TaxType.TAXABLE, 26), (TaxType.TAX_EXEMPT, 0)])
async def test_business_quote_and_new_order_estimate_match_company_rounding(booking_context, tax_type, expected):
    client, session, buyer, _admin, event, body = booking_context
    offering = event.offerings[0]
    offering.price = 109
    offering.meal.tax_type = tax_type
    await session.commit()
    request = business_body(body, items=[{**body['items'][0], 'quantity': 5}])
    quote = await client.post(f'/v1/meal-events/{event.id}/quote', json=request, headers=auth_headers(buyer))
    assert quote.status_code == 200 and quote.json()['tax_amount'] == expected
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=request, headers=auth_headers(buyer))
    assert created.status_code == 201 and created.json()['tax_amount'] == expected


async def test_business_unpaid_admin_filter_keeps_regular_orders_out(booking_context):
    from app.routers.operations import operations_router
    from tests.support import api_test_context, make_test_settings
    client, session, buyer, admin, event, body = booking_context
    business = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    regular = await client.post(f'/v1/meal-events/{event.id}/orders', json=body, headers=auth_headers(buyer))
    assert regular.status_code == 201
    async with api_test_context(session, [operations_router], settings=make_test_settings()) as admin_client:
        result = await admin_client.get('/v1/admin/order-search?business_unpaid=true', headers=auth_headers(admin))
        assert result.status_code == 200, result.text
        assert result.json()['total'] == 1
        assert [item['id'] for item in result.json()['items']] == [business.json()['id']]
        assert result.json()['items'][0]['meal_booking_type'] == 'business'
        assert (await admin_client.get('/v1/admin/order-search?business_unpaid=true', headers=auth_headers(buyer))).status_code == 403
