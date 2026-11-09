from datetime import datetime, time, timedelta, timezone
import sys

import pytest
from sqlalchemy import func, select

from app.domain import meal_payment_window_open
from app.integrations.payment_service import PaymentApplicationError, SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.jobs import _reconcile_meal_events
from app.meal_booking import TAIPEI, meal_booking_window, meal_pickup_slots
from app.meal_schedules import generate_scheduled_meal_events
from app.models import Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order, PaymentAttempt, PaymentStatus, Refund, User, UserRole
from app.routers import meals, payments
from app.schemas import MealScheduleInput
from tests.support import api_test_context, auth_headers, make_test_settings, tomorrow_meal_timeline
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


NOW = datetime(2030, 1, 1, 11, 59, 59, tzinfo=timezone.utc)


@pytest.fixture
async def booking_context(database_session, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(meals, "datetime", FixedDateTime)
    session = database_session
    buyer = User(email="booking-buyer@example.com", display_name="預訂買家", password_hash="test")
    admin = User(email="booking-admin@example.test", display_name="預訂管理員", password_hash="test", user_role=UserRole.ADMIN)
    meal = Meal(slug="booking-meal", name="半小時便當", price=100)
    event = MealEvent(
        title="明天午餐", location="合作社",
        **{**tomorrow_meal_timeline(NOW), "ordering_ends_at": NOW + timedelta(hours=13)},
        status=MealEventStatus.PUBLISHED,
        meal_period="lunch", created_by=admin,
        offerings=[MealEventOffering(meal=meal, price=100, capacity=50)],
    )
    session.add_all([buyer, event])
    await session.commit()
    body = {"contact_email": buyer.email, "items": [{"offering_id": event.offerings[0].id, "quantity": 1}]}
    async with api_test_context(session, [meals.meals_router, payments.payments_router], settings=make_test_settings()) as client:
        yield client, session, buyer, admin, event, body


@pytest.mark.parametrize("period,start,end", [("lunch", "11:00", "13:00"), ("dinner", "16:30", "18:00")])
def test_new_schedule_defaults_match_confirmed_periods(period, start, end):
    schedule = MealScheduleInput(title=period, location="合作社", meal_period=period, offerings=[{"meal_id": "meal", "price": 100, "capacity": 10}])
    assert schedule.pickup_start_time.isoformat(timespec="minutes") == start
    assert schedule.pickup_end_time.isoformat(timespec="minutes") == end
    assert schedule.cutoff_time == time(20)
    assert schedule.cutoff_days_before == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("period,clocks", [
    ("lunch", ["11:00", "11:30", "12:00", "12:30", "13:00"]),
    ("dinner", ["16:30", "17:00", "17:30", "18:00"]),
])
async def test_tomorrow_quotes_and_orders_accept_every_half_hour_including_last_slot(booking_context, period, clocks):
    client, session, buyer, _admin, event, body = booking_context
    if period == "dinner":
        event.meal_period = period
        event.pickup_starts_at = datetime(2030, 1, 2, 16, 30, tzinfo=TAIPEI)
        event.pickup_ends_at = datetime(2030, 1, 2, 18, 30, tzinfo=TAIPEI)
        await session.commit()
    detail = (await client.get(f"/v1/meal-events/{event.id}")).json()
    assert [datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TAIPEI).strftime("%H:%M") for value in detail["pickup_slots"]] == clocks
    assert datetime.fromisoformat(detail["booking_ends_at"].replace("Z", "+00:00")) == datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    for clock in clocks:
        selected = datetime.fromisoformat(f"2030-01-02T{clock}:00+08:00")
        body["pickup_at"] = selected.isoformat()
        quote = await client.post(f"/v1/meal-events/{event.id}/quote", json=body)
        created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
        assert quote.status_code == 200, quote.text
        assert created.status_code == 201, created.text
        order = await session.get(Order, created.json()["id"])
        await session.refresh(order.fulfillment)
        assert meals._aware(order.fulfillment.pickup_at) == selected
        assert order.fulfillment.meal_ordering_ends_at.replace(tzinfo=timezone.utc) == datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", ["10:30:00", "11:01:00", "11:10:00", "11:30:01", "11:30:00.000001", "13:30:00", "16:30:00"])
async def test_invalid_clock_or_precision_cannot_create_order_or_quote(booking_context, selected):
    client, session, buyer, _admin, event, body = booking_context
    body["pickup_at"] = f"2030-01-02T{selected}+08:00"
    for action in ("quote", "orders"):
        response = await client.post(f"/v1/meal-events/{event.id}/{action}", json=body, headers=auth_headers(buyer))
        assert response.status_code == 422, response.text
        assert "半小時" in response.json()["detail"]
    assert await session.scalar(select(func.count(Order.id))) == 0


@pytest.mark.asyncio
async def test_shortened_service_window_overrides_a_later_explicit_last_slot(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    event.pickup_slot_ends_at = datetime(2030, 1, 2, 13, tzinfo=TAIPEI)
    event.pickup_ends_at = datetime(2030, 1, 2, 12, tzinfo=TAIPEI)
    await session.commit()
    detail = (await client.get(f"/v1/meal-events/{event.id}")).json()
    assert [datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TAIPEI).strftime("%H:%M") for value in detail["pickup_slots"]] == ["11:00", "11:30"]
    for clock in ("12:00", "12:30", "13:00"):
        body["pickup_at"] = f"2030-01-02T{clock}:00+08:00"
        for action in ("quote", "orders"):
            response = await client.post(f"/v1/meal-events/{event.id}/{action}", json=body, headers=auth_headers(buyer))
            assert response.status_code == 422
    assert await session.scalar(select(func.count(Order.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("period,start,close,expected", [
    ("lunch", "11:00", "13:00", ["11:00", "11:30", "12:00", "12:30"]),
    ("dinner", "16:30", "18:00", ["16:30", "17:00", "17:30"]),
])
async def test_legacy_service_end_cannot_be_offered_or_booked_as_a_pickup_slot(booking_context, period, start, close, expected):
    client, session, buyer, _admin, event, body = booking_context
    event.meal_period = period
    event.pickup_starts_at = datetime.fromisoformat(f"2030-01-02T{start}:00+08:00").astimezone(timezone.utc)
    service_end = datetime.fromisoformat(f"2030-01-02T{close}:00+08:00")
    event.pickup_ends_at = service_end.astimezone(timezone.utc)
    event.pickup_slot_ends_at = None
    await session.commit()
    detail = await client.get(f"/v1/meal-events/{event.id}")
    assert detail.status_code == 200, detail.text
    slots = [datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TAIPEI).strftime("%H:%M") for value in detail.json()["pickup_slots"]]
    assert slots == expected
    body["pickup_at"] = service_end.isoformat()
    for action in ("quote", "orders"):
        response = await client.post(f"/v1/meal-events/{event.id}/{action}", json=body, headers=auth_headers(buyer))
        assert response.status_code == 422, response.text
    assert await session.scalar(select(func.count(Order.id))) == 0
    await session.refresh(event)
    assert meals._aware(event.pickup_ends_at) == service_end
    assert event.pickup_slot_ends_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-1, 1])
async def test_today_and_day_after_tomorrow_cannot_be_booked(booking_context, offset):
    client, session, buyer, _admin, event, body = booking_context
    event.pickup_starts_at += timedelta(days=offset)
    event.pickup_ends_at += timedelta(days=offset)
    if offset < 0:
        event.ordering_starts_at -= timedelta(days=2)
        event.ordering_ends_at = event.pickup_ends_at - timedelta(minutes=1)
    await session.commit()
    for action in ("quote", "orders"):
        response = await client.post(f"/v1/meal-events/{event.id}/{action}", json=body, headers=auth_headers(buyer))
        assert response.status_code == 409, response.text
    assert await session.scalar(select(func.count(Order.id))) == 0


@pytest.mark.asyncio
async def test_20_clock_closes_new_booking_and_snapshot_blocks_payment_even_if_event_stays_open(booking_context, fake_payment, monkeypatch):
    client, session, buyer, _admin, event, body = booking_context
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201
    order = await session.get(Order, created.json()["id"])
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=NOW)
    midnight = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    assert meals._aware(attempt.expires_at) == midnight
    assert meal_payment_window_open(order, NOW)
    assert not meal_payment_window_open(order, midnight)
    with pytest.raises(PaymentApplicationError, match="已截止"):
        await create_payment_attempt(session, order.id, buyer, payment_settings(), now=midnight)
    class Midnight(datetime):
        @classmethod
        def now(cls, tz=None):
            return midnight.astimezone(tz) if tz else midnight.replace(tzinfo=None)
    monkeypatch.setattr(meals, "datetime", Midnight)
    monkeypatch.setattr(payments, "datetime", Midnight)
    for action in ("quote", "orders"):
        response = await client.post(f"/v1/meal-events/{event.id}/{action}", json=body, headers=auth_headers(buyer))
        assert response.status_code == 409, response.text
    checkout = await client.get(f"/payments/{attempt.id}/checkout")
    assert checkout.status_code == 410
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_time,expected", [(NOW, "paid"), (NOW + timedelta(seconds=1), "late_paid_refund_required")])
async def test_callback_uses_provider_payment_time_at_exclusive_booking_deadline(booking_context, fake_payment, payment_time, expected):
    client, session, buyer, _admin, event, body = booking_context
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    offering = event.offerings[0]
    order = await session.get(Order, created.json()["id"])
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=NOW)
    result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        "booking-midnight-payment", successful_callback(attempt, payment_time, "BOOKING-TIME-TRADE"),
    )
    assert result == expected
    await session.refresh(offering)
    assert offering.reserved_quantity == 0
    assert offering.paid_quantity == (1 if expected == "paid" else 0)
    assert await session.scalar(select(func.count(Refund.id))) == (0 if expected == "paid" else 1)


@pytest.mark.asyncio
async def test_existing_order_price_and_pickup_snapshots_survive_event_and_menu_changes(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    body["pickup_at"] = "2030-01-02T12:30:00+08:00"
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    event.location = "更新後地點"
    event.pickup_starts_at += timedelta(minutes=30)
    event.pickup_ends_at += timedelta(hours=1)
    event.offerings[0].price = 500
    await session.commit()
    detail = await client.get(f"/v1/meal-orders/{created.json()['id']}", headers=auth_headers(buyer))
    assert detail.status_code == 200, detail.text
    for field in ("venue_name", "pickup_start", "pickup_end", "pickup_at", "amount_total", "items"):
        assert detail.json()[field] == created.json()[field]


@pytest.mark.asyncio
async def test_new_generated_event_workday_window_and_last_slot_has_half_hour_to_redeem(booking_context, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "NOW", datetime(2026, 11, 6, 12, tzinfo=TAIPEI))
    client, session, buyer, admin, old_event, body = booking_context
    template = MealScheduleTemplate(
        title="標準午餐", location="合作社", meal_period="lunch", pickup_start_time="11:00",
        pickup_end_time="13:00", cutoff_time="00:00", cutoff_days_before=0,
        advance_days=7, weekdays=list(range(7)), enabled=True, auto_publish=True,
        created_by_id=admin.id, offerings=[{"meal_id": old_event.offerings[0].meal_id, "price": 100, "capacity": 20}],
    )
    session.add(template)
    await session.commit()
    created_ids = await generate_scheduled_meal_events(session, NOW, template_id=template.id)
    assert len(created_ids) == 5
    event = await meals._load_event(session, created_ids[0])
    assert event.service_date.isoformat() == "2026-11-09"
    assert meal_booking_window(event) == (
        datetime(2026, 11, 6, 0, tzinfo=TAIPEI), datetime(2026, 11, 8, 20, tzinfo=TAIPEI),
    )
    assert event.pickup_slot_ends_at.replace(tzinfo=timezone.utc).astimezone(TAIPEI).hour == 13
    assert event.pickup_ends_at.replace(tzinfo=timezone.utc).astimezone(TAIPEI).strftime("%H:%M") == "13:30"
    assert len(meal_pickup_slots(event)) == 5
    assert await generate_scheduled_meal_events(session, NOW, template_id=template.id) == []
    assert old_event.pickup_slot_ends_at is None
    body["items"][0]["offering_id"] = event.offerings[0].id
    body["pickup_at"] = "2026-11-09T13:00:00+08:00"
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    order = await session.get(Order, created.json()["id"])
    order.payment_status = PaymentStatus.PAID
    await session.commit()
    last_slot = datetime(2026, 11, 9, 13, tzinfo=TAIPEI)
    await _reconcile_meal_events(session, last_slot, 100)
    assert event.status == MealEventStatus.PICKUP_OPEN
    await _reconcile_meal_events(session, last_slot + timedelta(minutes=29), 100)
    assert event.status == MealEventStatus.PICKUP_OPEN
    await _reconcile_meal_events(session, last_slot + timedelta(minutes=30), 100)
    assert event.status == MealEventStatus.COMPLETED
