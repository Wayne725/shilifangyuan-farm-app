from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.integrations.payment_service import PaymentApplicationError, SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.jobs import _reconcile_meal_events
from app.models import FulfillmentMethod, FulfillmentState, FulfillmentStatus, Meal, MealEvent, MealEventOffering, MealEventStatus, MembershipType, Order, OrderFulfillment, OrderItem, OrderKind, PaymentAttempt, PaymentStatus, Refund, SalesChannel, TaxType, User, UserRole
from app.routers.meals import meals_router
from app.schemas import MealEventCreate, MealScheduleInput
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


@pytest.mark.parametrize("period,start,cutoff,end", [("lunch", "11:00", "13:00", "14:00"), ("dinner", "17:00", "19:00", "20:00")])
def test_confirmed_lunch_and_dinner_overlap_are_valid(period, start, cutoff, end):
    schedule = MealScheduleInput(
        title=period, location="清華大學", meal_period=period,
        pickup_start_time=start, cutoff_time=cutoff, cutoff_days_before=0, pickup_end_time=end,
        offerings=[{"meal_id": "test", "price": 100, "capacity": 20}],
    )
    assert schedule.weekdays == [0, 1, 2, 3, 4]
    MealEventCreate(
        title=period, location="清華大學", ordering_starts_at="2026-09-16T00:00:00+08:00",
        ordering_ends_at=f"2026-09-17T{cutoff}:00+08:00",
        pickup_starts_at=f"2026-09-17T{start}:00+08:00", pickup_ends_at=f"2026-09-17T{end}:00+08:00",
        offerings=schedule.offerings,
    )


@pytest.mark.parametrize("cutoff", ["14:00", "15:00"])
def test_schedule_and_event_reject_cutoff_at_or_after_pickup_end(cutoff):
    offering = [{"meal_id": "test", "price": 100, "capacity": 20}]
    with pytest.raises(ValidationError, match="訂購截止必須早於取餐結束"):
        MealScheduleInput(title="午餐", location="測試", meal_period="lunch", pickup_start_time="11:00", cutoff_time=cutoff, cutoff_days_before=0, pickup_end_time="14:00", offerings=offering)
    with pytest.raises(ValidationError, match="訂購截止必須早於取餐結束"):
        MealEventCreate(title="午餐", location="測試", ordering_starts_at="2026-09-16T00:00:00+08:00", ordering_ends_at=f"2026-09-17T{cutoff}:00+08:00", pickup_starts_at="2026-09-17T11:00:00+08:00", pickup_ends_at="2026-09-17T14:00:00+08:00", offerings=offering)


def test_cross_midnight_template_compares_dates_not_only_clock():
    schedule = MealScheduleInput(
        title="跨夜", location="測試", meal_period="dinner", pickup_start_time="23:00", cutoff_time="23:30", cutoff_days_before=0, pickup_end_time="00:30",
        offerings=[{"meal_id": "test", "price": 100, "capacity": 20}],
    )
    assert schedule.cutoff_days_before == 0
    previous_day = schedule.model_copy(update={"cutoff_days_before": 1})
    assert MealScheduleInput.model_validate(previous_day.model_dump()).cutoff_days_before == 1


@pytest.fixture
async def overlap_context(database_session):
    session = database_session
    now = datetime.now(timezone.utc)
    admin = User(email="overlap-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="overlap-buyer@example.com", display_name="買家", password_hash="test")
    meal = Meal(slug="overlap-meal", name="重疊時段便當", description="", price=100)
    session.add_all([admin, buyer, meal])
    await session.flush()
    event = MealEvent(
        title="午餐取餐11至14點13點截單", location="取餐區", created_by_id=admin.id,
        ordering_starts_at=now - timedelta(days=1), ordering_ends_at=now + timedelta(hours=1),
        pickup_starts_at=now - timedelta(hours=1), pickup_ends_at=now + timedelta(hours=2),
        status=MealEventStatus.PUBLISHED, offerings=[MealEventOffering(meal_id=meal.id, price=100, capacity=20)],
    )
    session.add(event)
    await session.commit()
    body = {"contact_phone": "0912345678", "contact_email": buyer.email, "items": [{"offering_id": event.offerings[0].id, "quantity": 1}], "pickup_at": (now + timedelta(minutes=20)).isoformat()}
    async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
        yield client, session, admin, buyer, event, body, now


@pytest.mark.asyncio
@pytest.mark.parametrize("event_status", [MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN])
async def test_new_same_day_orders_are_blocked_but_legacy_orders_can_pay_before_cutoff(overlap_context, fake_payment, event_status):
    client, session, _admin, buyer, event, body, now = overlap_context
    event.status = event_status
    await session.commit()
    quote = await client.post(f"/v1/meal-events/{event.id}/quote", json=body)
    assert quote.status_code == 409, quote.text
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 409, created.text
    assert await session.scalar(select(func.count(Order.id))) == 0
    order = await seed_legacy_order(session, buyer, event)
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=now)
    assert attempt.status == PaymentStatus.PENDING
    repeated = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=now)
    assert repeated.id == attempt.id
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == 1


@pytest.mark.asyncio
async def test_omitting_pickup_time_cannot_bypass_previous_day_rule(overlap_context):
    client, _session, _admin, buyer, event, body, now = overlap_context
    body.pop("pickup_at")
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 409, created.text
    assert "提前一天" in created.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_status", [MealEventStatus.ORDERING_CLOSED, MealEventStatus.CANCELLED, MealEventStatus.COMPLETED])
async def test_closed_states_never_allow_orders_or_new_payments(overlap_context, fake_payment, event_status):
    client, session, _admin, buyer, event, body, now = overlap_context
    order = await seed_legacy_order(session, buyer, event)
    event.status = event_status
    await session.commit()
    for path in ("quote", "orders"):
        denied = await client.post(f"/v1/meal-events/{event.id}/{path}", json=body, headers=auth_headers(buyer))
        assert denied.status_code == 409
    with pytest.raises(PaymentApplicationError):
        await create_payment_attempt(session, order.id, buyer, payment_settings(), now=now)
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == 0


@pytest.mark.asyncio
async def test_pickup_open_cutoff_blocks_orders_and_pending_attempt_reuse(overlap_context, fake_payment):
    client, session, _admin, buyer, event, body, now = overlap_context
    event.status = MealEventStatus.PICKUP_OPEN
    await session.commit()
    order = await seed_legacy_order(session, buyer, event)
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=now)
    event.ordering_ends_at = now - timedelta(seconds=1)
    await session.commit()
    for path in ("quote", "orders"):
        denied = await client.post(f"/v1/meal-events/{event.id}/{path}", json=body, headers=auth_headers(buyer))
        assert denied.status_code == 409
    with pytest.raises(PaymentApplicationError):
        await create_payment_attempt(session, order.id, buyer, payment_settings(), now=now)
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == 1
    assert attempt.status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_reconcile_preserves_legacy_event_window_but_new_same_day_order_is_rejected(overlap_context):
    client, session, _admin, buyer, event, body, now = overlap_context
    cutoff = event.ordering_ends_at
    assert await _reconcile_meal_events(session, now, 100) == 1
    assert event.status == MealEventStatus.PICKUP_OPEN
    assert event.ordering_ends_at == cutoff
    assert (await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))).status_code == 409
    assert await _reconcile_meal_events(session, now, 100) == 0
    assert await _reconcile_meal_events(session, cutoff, 100) == 0
    assert event.status == MealEventStatus.PICKUP_OPEN
    assert await _reconcile_meal_events(session, event.pickup_ends_at, 100) == 1
    assert event.status == MealEventStatus.COMPLETED


@pytest.mark.asyncio
async def test_old_nonoverlap_event_closes_ordering_then_auto_opens_pickup(overlap_context):
    _client, session, _admin, _buyer, event, _body, now = overlap_context
    event.pickup_starts_at = now + timedelta(minutes=30)
    event.ordering_ends_at = now
    await session.commit()
    assert await _reconcile_meal_events(session, now, 100) == 1
    assert event.status == MealEventStatus.ORDERING_CLOSED
    assert await _reconcile_meal_events(session, now + timedelta(minutes=30), 100) == 1
    assert event.status == MealEventStatus.PICKUP_OPEN


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["before_start", "at_end", "after_end"])
async def test_manual_open_and_redemption_cannot_bypass_pickup_window(overlap_context, marker):
    client, session, admin, _buyer, event, _body, now = overlap_context
    if marker == "before_start":
        event.pickup_starts_at = now + timedelta(hours=1)
    else:
        event.ordering_ends_at = now - timedelta(hours=1)
        event.pickup_starts_at = now - timedelta(hours=2)
        event.pickup_ends_at = now if marker == "at_end" else now - timedelta(seconds=1)
    await session.commit()
    opened = await client.post(f"/v1/admin/meal-events/{event.id}/open-pickup", headers=auth_headers(admin))
    assert opened.status_code == 409
    event.status = MealEventStatus.PICKUP_OPEN
    await session.commit()
    redeemed = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": "123456"}, headers=auth_headers(admin))
    assert redeemed.status_code == 409


@pytest.mark.asyncio
async def test_paid_redeemed_order_cannot_cancel_while_cutoff_is_still_future(overlap_context):
    client, session, admin, buyer, event, body, now = overlap_context
    order = await seed_legacy_order(session, buyer, event)
    order.payment_status = PaymentStatus.PAID
    order.paid_at = now
    await session.commit()
    assert await _reconcile_meal_events(session, now, 100) == 1
    assert order.fulfillment.status == FulfillmentState.READY_FOR_PICKUP
    redeemed = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": order.fulfillment.pickup_code}, headers=auth_headers(admin))
    assert redeemed.status_code == 200
    cancelled = await client.post(f"/v1/meal-orders/{order.id}/cancel", json={"reason": "已取餐不可退"}, headers=auth_headers(buyer))
    assert cancelled.status_code == 403
    assert order.payment_status == PaymentStatus.PAID
    assert order.fulfillment_status == FulfillmentStatus.PICKED_UP
    assert await session.scalar(select(func.count(Refund.id))) == 0
    duplicate = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": order.fulfillment.pickup_code}, headers=auth_headers(admin))
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_payment_after_pickup_opens_is_redeemable_without_another_state_transition(overlap_context, fake_payment):
    client, session, admin, buyer, event, body, now = overlap_context
    assert await _reconcile_meal_events(session, now, 100) == 1
    order = await seed_legacy_order(session, buyer, event)
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=now)
    result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        "overlap-payment-during-pickup", successful_callback(attempt, now + timedelta(seconds=1), "OVERLAP-PAID-TRADE"),
    )
    assert result == "paid"
    await session.refresh(order)
    assert order.payment_status == PaymentStatus.PAID
    detail = await client.get(f"/v1/meal-orders/{order.id}", headers=auth_headers(buyer))
    assert detail.json()["fulfillment_status"] == "pending"
    assert await _reconcile_meal_events(session, now + timedelta(seconds=2), 100) == 0
    redeemed = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": detail.json()["pickup_code"]}, headers=auth_headers(admin))
    assert redeemed.status_code == 200, redeemed.text
    assert order.payment_status == PaymentStatus.PAID


async def seed_legacy_order(session, buyer, event):
    offering = event.offerings[0]
    order = Order(
        order_number="LEGACY-OVERLAP-001", order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user=buyer, meal_event=event, membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=100, contact_email=buyer.email, payment_status=PaymentStatus.PENDING,
        fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[OrderItem(source_meal_offering_id=offering.id, product_name="舊制便當", unit_label="份", quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE)],
        fulfillment=OrderFulfillment(
            method=FulfillmentMethod.EVENT_PICKUP, status=FulfillmentState.PENDING_CONFIRMATION,
            pickup_location=event.location, pickup_starts_at=event.pickup_starts_at,
            pickup_ends_at=event.pickup_ends_at, pickup_code="123456", pickup_qr_token_hash="legacy-overlap-credential",
        ),
    )
    session.add(order)
    await session.commit()
    return order
