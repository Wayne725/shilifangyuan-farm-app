from datetime import date, datetime, time, timedelta, timezone

import pytest
import time_machine
from sqlalchemy import func, select

from app.integrations.payment_service import PaymentApplicationError, SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.meal_booking import TAIPEI, meal_booking_window
from app.meal_schedules import generate_scheduled_meal_events
from app.models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order, User, UserRole
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_order_quantity_amendments import submit
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


FRIDAY = datetime(2026, 11, 6, 12, tzinfo=TAIPEI)


@pytest.fixture
async def workday_context(database_session):
    with time_machine.travel(FRIDAY, tick=False):
        session = database_session
        admin = User(email="calendar-admin@example.test", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        buyer = User(email="calendar-buyer@example.com", display_name="買家", password_hash="test")
        meal = Meal(slug="workday-lunch", name="工作日便當", price=100)
        session.add_all([admin, buyer, meal])
        await session.commit()
        async with api_test_context(session, [meals_router, orders_router, payments_router], settings=make_test_settings()) as client:
            yield client, session, admin, buyer, meal


def event_body(meal, service="2026-11-09", **overrides):
    return {"title": "工作日午餐", "location": "合作社", "ordering_starts_at": "2026-11-01T00:00:00+08:00",
            "ordering_ends_at": f"{service}T10:00:00+08:00", "pickup_starts_at": f"{service}T11:00:00+08:00",
            "pickup_ends_at": f"{service}T13:30:00+08:00",
            "offerings": [{"meal_id": meal.id, "price": 100, "capacity": 50}], **overrides}


async def create_event(context, service="2026-11-09", **overrides):
    client, session, admin, buyer, meal = context
    response = await client.post("/v1/admin/meal-events", json=event_body(meal, service, **overrides), headers=auth_headers(admin))
    assert response.status_code == 201, response.text
    published = await client.post(f"/v1/admin/meal-events/{response.json()['id']}/publish", headers=auth_headers(admin))
    assert published.status_code == 200, published.text
    data = published.json()
    body = {"contact_email": buyer.email, "items": [{"offering_id": data["offerings"][0]["id"], "quantity": 1}]}
    return data, body


@pytest.mark.parametrize("day", [6, 7, 8])
async def test_weekend_monday_quote_order_amendment_and_checkout_share_window(workday_context, fake_payment, day):
    client, session, _admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context)
    assert event["booking_policy"] == "workday"
    assert datetime.fromisoformat(event["booking_starts_at"].replace("Z", "+00:00")) == datetime(2026, 11, 6, tzinfo=TAIPEI)
    with time_machine.travel(datetime(2026, 11, day, 12, tzinfo=TAIPEI), tick=False):
        assert (await client.post(f"/v1/meal-events/{event['id']}/quote", json=body)).status_code == 200
        created = await client.post(f"/v1/meal-events/{event['id']}/orders", json=body, headers=auth_headers(buyer))
        assert created.status_code == 201, created.text
        order = await session.get(Order, created.json()["id"])
        quote = await submit(client, buyer, order, 2)
        assert quote.status_code == 200, quote.text
        changed = await submit(client, buyer, order, 2, total=200)
        assert changed.status_code == 200, changed.text
        attempt = await create_payment_attempt(session, order.id, buyer, payment_settings())
        assert attempt.amount == 200
        assert (await client.get(f"/payments/{attempt.id}/checkout")).status_code == 200


@pytest.mark.parametrize("payment_second,expected", [(-1, "paid"), (0, "late_paid_refund_required")])
async def test_monday_exact_sunday_cutoff_applies_to_orders_amendment_checkout_and_callback(workday_context, fake_payment, payment_second, expected):
    client, session, _admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context)
    cutoff = datetime(2026, 11, 8, 20, tzinfo=TAIPEI)
    with time_machine.travel(cutoff - timedelta(seconds=1), tick=False):
        created = await client.post(f"/v1/meal-events/{event['id']}/orders", json=body, headers=auth_headers(buyer))
        assert created.status_code == 201
        order = await session.get(Order, created.json()["id"])
        attempt = await create_payment_attempt(session, order.id, buyer, payment_settings())
        assert attempt.expires_at.replace(tzinfo=timezone.utc) == cutoff
    with time_machine.travel(cutoff, tick=False):
        for action in ["quote", "orders"]:
            assert (await client.post(f"/v1/meal-events/{event['id']}/{action}", json=body, headers=auth_headers(buyer))).status_code == 409
        assert (await submit(client, buyer, order, 2)).status_code == 409
        assert (await client.get(f"/payments/{attempt.id}/checkout")).status_code == 410
        with pytest.raises(PaymentApplicationError, match="已截止"):
            await create_payment_attempt(session, order.id, buyer, payment_settings())
        result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
            f"workday-boundary-{payment_second}", successful_callback(attempt, cutoff + timedelta(seconds=payment_second), "CALENDAR-TRADE"))
        assert result == expected


async def test_calendar_lookup_holiday_manual_override_and_access(workday_context):
    client, session, admin, buyer, meal = workday_context
    assert (await client.get("/v1/admin/meal-calendar", headers=auth_headers(buyer))).status_code == 403
    detail = await client.get("/v1/admin/meal-calendar?service_date=2026-10-12", headers=auth_headers(admin))
    assert detail.status_code == 200
    assert detail.json()["previous_workday"] == "2026-10-08"
    assert (await client.get("/v1/admin/meal-calendar?service_date=2028-01-01", headers=auth_headers(admin))).status_code == 503
    last = await client.get("/v1/admin/meal-calendar?service_date=2027-12-31", headers=auth_headers(admin))
    assert last.status_code == 200, last.text
    assert last.json()["next_workday"] is None
    assert last.json()["previous_workday"] == "2027-12-30"
    status = await client.get("/v1/admin/meal-schedules/calendar-status", headers=auth_headers(admin))
    assert status.status_code == 200 and status.json()["to_date"] == "2027-12-31"
    weekend = await client.post("/v1/admin/meal-events", json=event_body(meal, "2026-11-07"), headers=auth_headers(admin))
    assert weekend.status_code == 422 and "特殊日期" in weekend.json()["detail"]
    special, body = await create_event(workday_context, "2026-11-07", booking_policy="special")
    assert special["booking_policy"] == "special"
    created = await client.post(f"/v1/meal-events/{special['id']}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    with time_machine.travel(datetime(2026, 11, 7, 9, tzinfo=TAIPEI), tick=False):
        assert (await client.post(f"/v1/meal-events/{special['id']}/quote", json=body)).status_code == 409
    new_legacy = await client.post("/v1/admin/meal-events", json=event_body(meal, booking_policy="legacy"), headers=auth_headers(admin))
    assert new_legacy.status_code == 422


def template_for(admin, meal, **overrides):
    return MealScheduleTemplate(**{
        "title": "工作日午餐", "location": "合作社", "meal_period": "lunch",
        "pickup_start_time": "11:00", "pickup_end_time": "13:00", "cutoff_time": "20:00", "cutoff_days_before": 1,
        "advance_days": 2, "weekdays": list(range(5)), "enabled": True, "auto_publish": True, "created_by_id": admin.id,
        "offerings": [{"meal_id": meal.id, "price": 100, "capacity": 20}], **overrides,
    })


async def test_generation_crosses_lunar_holiday_and_unknown_year_fails_closed(workday_context):
    _client, session, admin, _buyer, meal = workday_context
    template = template_for(admin, meal)
    session.add(template)
    await session.commit()
    now = datetime(2026, 2, 13, 12, tzinfo=TAIPEI)
    ids = await generate_scheduled_meal_events(session, now)
    assert len(ids) == 1
    event = await session.get(MealEvent, ids[0])
    assert event.service_date == date(2026, 2, 23)
    assert meal_booking_window(event) == (datetime(2026, 2, 13, tzinfo=TAIPEI), datetime(2026, 2, 22, 20, tzinfo=TAIPEI))
    assert await generate_scheduled_meal_events(session, now) == []
    event.status = MealEventStatus.CANCELLED
    await session.commit()
    assert await generate_scheduled_meal_events(session, now) == []
    report = {}
    assert await generate_scheduled_meal_events(session, datetime(2028, 1, 1, tzinfo=TAIPEI), report=report) == []
    assert report["calendar_unavailable"] == 1


async def test_legacy_conversion_preserves_ordered_manual_and_cancelled_events(workday_context, fake_payment):
    client, session, admin, buyer, meal = workday_context
    template = template_for(admin, meal)
    session.add(template)
    await session.flush()
    def old_event(service, **overrides):
        day = date.fromisoformat(service)
        return MealEvent(title=service, location="原地點", schedule_template_id=template.id, service_date=day,
            meal_period="lunch", created_by_id=admin.id, ordering_starts_at=datetime.combine(day-timedelta(days=1), time.min, TAIPEI).astimezone(timezone.utc),
            ordering_ends_at=datetime.combine(day-timedelta(days=1), time(20), TAIPEI).astimezone(timezone.utc),
            pickup_starts_at=datetime.combine(day, time(11), TAIPEI).astimezone(timezone.utc), pickup_ends_at=datetime.combine(day, time(13,30), TAIPEI).astimezone(timezone.utc),
            status=overrides.pop("status", MealEventStatus.PUBLISHED), **overrides)
    monday, holiday, manual, cancelled, ordered = [old_event(day, **extra) for day, extra in [
        ("2026-11-09", {}), ("2026-11-07", {}), ("2026-11-10", {}),
        ("2026-11-11", {"status": MealEventStatus.CANCELLED}), ("2026-11-08", {})]]
    ordered.offerings = [MealEventOffering(meal_id=meal.id, price=100, capacity=10)]
    ordered.schedule_template_id = None
    session.add_all([monday, holiday, manual, cancelled, ordered])
    await session.flush()
    session.add(AdminAudit(actor_id=admin.id, action="meal_event.update", aggregate_type="meal_event", aggregate_id=manual.id))
    await session.commit()
    with time_machine.travel(datetime(2026,11,7,12,tzinfo=TAIPEI), tick=False):
        order = await client.post(f"/v1/meal-events/{ordered.id}/orders", json={"contact_email":buyer.email,
            "items":[{"offering_id":ordered.offerings[0].id,"quantity":1}]}, headers=auth_headers(buyer))
        assert order.status_code == 201, order.text
    original_order = order.json()
    ordered.schedule_template_id = template.id
    await session.commit()
    report = {}
    await generate_scheduled_meal_events(session, FRIDAY, report=report)
    assert monday.booking_policy == "workday"
    assert meal_booking_window(monday)[0] == datetime(2026,11,6,tzinfo=TAIPEI)
    assert holiday.status == MealEventStatus.DRAFT and holiday.booking_policy == "workday"
    assert manual.booking_policy == cancelled.booking_policy == ordered.booking_policy == "legacy"
    assert cancelled.status == MealEventStatus.CANCELLED
    assert report["converted"] == report["unpublished_holidays"] == report["held_for_orders"] == report["held_for_manual_edits"] == 1
    assert (await client.post(f"/v1/admin/meal-events/{holiday.id}/publish", headers=auth_headers(admin))).status_code == 409
    order_after = await client.get(f"/v1/meal-orders/{original_order['id']}", headers=auth_headers(buyer))
    for field in ["pickup_at", "pickup_start", "pickup_end", "amount_total", "items"]:
        assert order_after.json()[field] == original_order[field]
    assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "meal_event.workday_conversion")) == 2
    detail = (await client.get(f"/v1/meal-events/{ordered.id}")).json()
    assert "停止接受新預訂" in detail["new_orders_blocked_reason"]
    with time_machine.travel(datetime(2026, 11, 7, 12, tzinfo=TAIPEI), tick=False):
        body = {"contact_email": buyer.email, "items": [{"offering_id": ordered.offerings[0].id, "quantity": 1}]}
        for action in ("quote", "orders"):
            blocked = await client.post(f"/v1/meal-events/{ordered.id}/{action}", json=body, headers=auth_headers(buyer))
            assert blocked.status_code == 409 and "停止接受新預訂" in blocked.json()["detail"]
        attempt = await create_payment_attempt(session, original_order["id"], buyer, payment_settings())
        assert (await client.get(f"/payments/{attempt.id}/checkout")).status_code == 200
        result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
            "legacy-holiday-preserved", successful_callback(attempt, datetime.now(timezone.utc), "LEGACY-HOLIDAY"))
        assert result == "paid"


async def test_disabled_template_and_special_event_remain_untouched(workday_context):
    _client, session, admin, _buyer, meal = workday_context
    disabled = template_for(admin, meal, id="a-disabled", enabled=False)
    active = template_for(admin, meal, id="z-active")
    session.add_all([disabled, active])
    await session.flush()
    events = []
    for template, policy in [(disabled, "legacy"), (active, "special")]:
        event = MealEvent(title="保留人工設定", location="合作社", schedule_template_id=template.id,
            service_date=date(2026, 11, 7), booking_policy=policy, created_by_id=admin.id,
            ordering_starts_at=FRIDAY - timedelta(days=1), ordering_ends_at=FRIDAY + timedelta(hours=7),
            pickup_starts_at=FRIDAY + timedelta(days=1), pickup_ends_at=FRIDAY + timedelta(days=1, hours=1),
            status=MealEventStatus.PUBLISHED)
        session.add(event)
        events.append(event)
    await session.commit()
    before = [(event.booking_policy, event.status, meal_booking_window(event)) for event in events]
    report = {}
    generated = await generate_scheduled_meal_events(session, FRIDAY, limit=1, report=report)
    assert len(generated) == 1
    assert (await session.get(MealEvent, generated[0])).schedule_template_id == active.id
    assert [(event.booking_policy, event.status, meal_booking_window(event)) for event in events] == before
    assert not any(report.values())


async def test_admin_windows_are_normalized_audited_and_cannot_downgrade(workday_context):
    client, session, admin, _buyer, meal = workday_context
    event, _body = await create_event(workday_context)
    created_audit = await session.scalar(select(AdminAudit).where(
        AdminAudit.aggregate_id == event["id"], AdminAudit.action == "meal_event.create"))
    assert created_audit.data["booking_policy"] == "workday"
    assert created_audit.data["service_date"] == "2026-11-09"
    assert datetime.fromisoformat(created_audit.data["ordering_starts_at"]) == datetime(2026, 11, 6, tzinfo=TAIPEI)
    update = event_body(meal, ordering_starts_at="2026-11-07T09:00:00+08:00", ordering_ends_at="2026-11-08T18:00:00+08:00")
    edited = await client.put(f"/v1/admin/meal-events/{event['id']}", json=update, headers=auth_headers(admin))
    assert edited.status_code == 200, edited.text
    assert edited.json()["booking_policy"] == "workday"
    audit = await session.scalar(select(AdminAudit).where(
        AdminAudit.aggregate_id == event["id"], AdminAudit.action == "meal_event.update"))
    assert audit.data["after"]["booking_policy"] == "workday"
    assert datetime.fromisoformat(audit.data["after"]["ordering_ends_at"]) == datetime(2026, 11, 8, 18, tzinfo=TAIPEI)
    downgrade = await client.put(f"/v1/admin/meal-events/{event['id']}", json={**update, "booking_policy": "legacy"}, headers=auth_headers(admin))
    assert downgrade.status_code == 422
    same_day = await client.post("/v1/admin/meal-events", json=event_body(meal,
        booking_policy="special", ordering_starts_at="2026-11-09T00:00:00+08:00"), headers=auth_headers(admin))
    assert same_day.status_code == 422


async def test_workday_cash_reservation_survives_online_cutoff_until_pickup(workday_context):
    client, session, admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context, allow_cash_payment=True)
    body["payment_method"] = "cash_on_pickup"
    created = await client.post(f"/v1/meal-events/{event['id']}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    with time_machine.travel(datetime(2026, 11, 9, 11, tzinfo=TAIPEI), tick=False):
        assert (await client.post(f"/v1/meal-events/{event['id']}/orders", json=body, headers=auth_headers(buyer))).status_code == 409
        paid = await client.post(f"/v1/orders/{order_id}/admin/cash-payment",
            json={"amount": 100, "reason": "取餐時收款"}, headers=auth_headers(admin))
        assert paid.status_code == 200, paid.text
        assert paid.json()["payment_status"] == "paid"
        assert paid.json()["invoice_status"] == "external"
