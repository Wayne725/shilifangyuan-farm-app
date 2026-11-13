from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

import pytest
import time_machine
from sqlalchemy import func, select

from app.integrations.payment_service import PaymentApplicationError, SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.meal_booking import TAIPEI, meal_booking_window
from app.meal_schedules import generate_scheduled_meal_events
from app.models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order, OrderFulfillment, User, UserRole
from app.routers import meals
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_business_meals import business_body
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
    body = {"contact_phone": "0912345678", "contact_email": buyer.email, "items": [{"offering_id": data["offerings"][0]["id"], "quantity": 1}]}
    return data, body


@pytest.mark.parametrize("day", [6, 7, 8])
async def test_weekend_monday_quote_order_amendment_and_checkout_share_window(workday_context, fake_payment, day):
    client, session, _admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context)
    assert event["booking_policy"] == "workday"
    assert datetime.fromisoformat(event["booking_starts_at"].replace("Z", "+00:00")) == datetime(2026, 11, 6, tzinfo=TAIPEI)
    with time_machine.travel(datetime(2026, 11, day, 12, tzinfo=TAIPEI), tick=False):
        session.expunge_all()
        initial_quote = await client.post(f"/v1/meal-events/{event['id']}/quote", json=body)
        assert initial_quote.status_code == 200, initial_quote.text
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
        order = await client.post(f"/v1/meal-events/{ordered.id}/orders", json={"contact_phone": "0912345678", "contact_email":buyer.email,
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
        body = {"contact_phone": "0912345678", "contact_email": buyer.email, "items": [{"offering_id": ordered.offerings[0].id, "quantity": 1}]}
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
    session.expunge_all()
    reloaded = await client.get(f"/v1/meal-events/{event['id']}")
    assert reloaded.status_code == 200, reloaded.text
    persisted = reloaded.json()
    assert datetime.fromisoformat(persisted["pickup_starts_at"]) == datetime(2026, 11, 9, 11, tzinfo=TAIPEI)
    assert datetime.fromisoformat(persisted["pickup_ends_at"]) == datetime(2026, 11, 9, 13, 30, tzinfo=TAIPEI)


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


async def test_same_day_checkout_uses_eligible_slot_and_snapshots_its_deadline(workday_context):
    client, session, admin, buyer, meal = workday_context
    event, body = await create_event(workday_context, same_day_preparation_minutes=90,
                                    ordering_ends_at="2026-11-09T11:00:00+08:00")
    root = f"/v1/meal-events/{event['id']}"
    assert event["same_day_preparation_minutes"] == 90
    calendar = await client.get("/v1/meal-events/calendar")
    summary = next(item for item in calendar.json() if item["id"] == event["id"])
    assert summary["same_day_preparation_minutes"] == 90
    with time_machine.travel(datetime(2026, 11, 9, 10, tzinfo=TAIPEI), tick=False):
        headers = auth_headers(buyer)
        for path in ["quote", "orders"]:
            expired = await client.post(
                f"{root}/{path}",
                json={**body, "pickup_at": "2026-11-09T11:30:00+08:00"}, headers=headers,
            )
            assert expired.status_code == 409 and "時段已截止" in expired.json()["detail"]
            cash = await client.post(f"{root}/{path}", json={**body, "payment_method": "cash_on_pickup"}, headers=headers)
            assert cash.status_code == 422 and "未開放現場付款" in cash.json()["detail"]
        quote = await client.post(f"{root}/quote", json=body)
        assert quote.status_code == 200, quote.text
        assert datetime.fromisoformat(quote.json()["pickup_at"]) == datetime(2026, 11, 9, 12, tzinfo=TAIPEI)
        created = await client.post(f"{root}/orders", json=body, headers=headers)
        assert created.status_code == 201, created.text
        order = await session.get(Order, created.json()["id"])
        fulfillment = await session.scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == order.id))
        assert fulfillment is not None
        assert fulfillment.meal_ordering_ends_at.replace(tzinfo=timezone.utc) == datetime(2026, 11, 9, 10, 30, tzinfo=TAIPEI)
        blocked = await client.put(f"/v1/admin/meal-events/{event['id']}", json=event_body(meal), headers=auth_headers(admin))
        assert blocked.status_code == 409 and "已有訂單" in blocked.json()["detail"]
    with time_machine.travel(datetime(2026, 11, 9, 10, 30, tzinfo=TAIPEI), tick=False):
        assert (await submit(client, buyer, order, 2)).status_code == 409


async def test_same_day_opt_in_preserves_business_previous_evening_cutoff(workday_context):
    client, _session, _admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context, same_day_preparation_minutes=90)
    root = f"/v1/meal-events/{event['id']}"
    company = business_body(body)
    cutoff = datetime(2026, 11, 8, 20, tzinfo=TAIPEI)
    with time_machine.travel(cutoff - timedelta(seconds=1), tick=False):
        allowed = await client.post(f"{root}/quote", json=company)
        assert allowed.status_code == 200, allowed.text
    with time_machine.travel(cutoff, tick=False):
        for path in ["quote", "orders"]:
            blocked = await client.post(f"{root}/{path}", json=company, headers=auth_headers(buyer))
            assert blocked.status_code == 409 and "前一天晚上 20:00" in blocked.json()["detail"]
        assert (await client.post(f"{root}/quote", json=body)).status_code == 200


@pytest.mark.parametrize("overrides,reason", [
    ({"same_day_preparation_minutes": 0}, "same_day_preparation_minutes"),
    ({"same_day_preparation_minutes": 1440}, "same_day_preparation_minutes"),
    ({"same_day_preparation_minutes": 90, "allow_cash_payment": True}, "僅支援線上付款"),
    ({"same_day_preparation_minutes": 90, "booking_policy": "legacy"}, "工作日或特殊日期"),
    ({"same_day_preparation_minutes": 90, "ordering_ends_at": "2026-11-08T20:00:00+08:00"}, "截止時間必須在取餐當日"),
    ({"same_day_preparation_minutes": 1439}, "沒有可預訂的取餐時段"),
])
async def test_admin_same_day_requires_explicit_valid_configuration(workday_context, overrides, reason):
    client, _session, admin, _buyer, meal = workday_context
    rejected = await client.post("/v1/admin/meal-events", json=event_body(meal, **overrides), headers=auth_headers(admin))
    assert rejected.status_code == 422
    assert reason in rejected.text


async def test_same_day_update_preserves_omitted_opt_in_and_allows_explicit_disable(workday_context):
    client, _session, admin, _buyer, meal = workday_context
    event, _ = await create_event(workday_context, same_day_preparation_minutes=90)
    path = f"/v1/admin/meal-events/{event['id']}"
    headers = auth_headers(admin)
    cash = await client.patch(f"{path}/payment-options", json={"allow_cash_payment": True}, headers=headers)
    assert cash.status_code == 422
    updated = await client.put(path, json=event_body(meal, title="更新後標題"), headers=headers)
    assert updated.status_code == 200 and updated.json()["same_day_preparation_minutes"] == 90
    disabled = await client.put(path, json=event_body(meal, same_day_preparation_minutes=None), headers=headers)
    assert disabled.status_code == 200 and disabled.json()["same_day_preparation_minutes"] is None
    assert datetime.fromisoformat(disabled.json()["booking_ends_at"]) == datetime(2026, 11, 8, 20, tzinfo=TAIPEI)


async def test_same_day_cannot_publish_after_last_preparation_deadline(workday_context):
    client, _session, admin, _buyer, meal = workday_context
    created = await client.post(
        "/v1/admin/meal-events",
        json=event_body(meal, same_day_preparation_minutes=120, ordering_ends_at="2026-11-09T12:00:00+08:00"),
        headers=auth_headers(admin),
    )
    assert created.status_code == 201, created.text
    path = f"/v1/admin/meal-events/{created.json()['id']}/publish"
    with time_machine.travel(datetime(2026, 11, 9, 11, tzinfo=TAIPEI), tick=False):
        response = await client.post(path, headers=auth_headers(admin))
    assert response.status_code == 409, response.text
    assert "已無可預訂的取餐時段" in response.json()["detail"]


async def test_same_day_online_checkout_rejects_zero_total_without_creating_order(workday_context):
    client, session, _admin, buyer, meal = workday_context
    other_meal = Meal(slug="another-workday-lunch", name="另一款便當", price=100)
    session.add(other_meal)
    await session.commit()
    event, body = await create_event(
        workday_context, same_day_preparation_minutes=90,
        offerings=[{"meal_id": item.id, "price": 0, "capacity": 50} for item in (meal, other_meal)],
    )
    body["items"].append({"offering_id": event["offerings"][1]["id"], "quantity": 1})
    with time_machine.travel(datetime(2026, 11, 9, 9, tzinfo=TAIPEI), tick=False):
        for path in ["quote", "orders"]:
            response = await client.post(f"/v1/meal-events/{event['id']}/{path}", json=body, headers=auth_headers(buyer))
            assert response.status_code == 422
            assert "須有大於零的應付金額" in response.json()["detail"]
    assert await session.scalar(select(func.count(Order.id))) == 0


async def test_saturday_business_only_requires_company_and_preserves_cutoff_and_invoice(workday_context):
    client, session, admin, buyer, meal = workday_context
    event, body = await create_event(workday_context, "2026-11-07", booking_policy="special", business_only=True)
    root = f"/v1/meal-events/{event['id']}"
    headers = auth_headers(buyer)
    assert event["business_only"] is True
    detail = await client.get(root)
    calendar = await client.get("/v1/meal-events/calendar")
    assert detail.json()["business_only"] is True
    assert next(item for item in calendar.json() if item["id"] == event["id"])["business_only"] is True

    for action in ("quote", "orders"):
        standard = await client.post(f"{root}/{action}", json=body, headers=headers)
        assert standard.status_code == 422
        assert standard.json()["detail"] == "此場次僅開放會餐"
        personal = await client.post(f"{root}/{action}", json={**body, "meal_booking_type": "business"}, headers=headers)
        assert personal.status_code == 422
        external = await client.post(
            f"{root}/{action}", json=business_body(body, payment_method="cash_on_pickup", invoice_handling="external"), headers=headers,
        )
        assert external.status_code == 422
    assert await session.scalar(select(func.count(Order.id))) == 0

    cutoff = datetime(2026, 11, 6, 20, tzinfo=TAIPEI)
    company = business_body(body, client_request_id=str(uuid4()))
    with time_machine.travel(cutoff - timedelta(seconds=1), tick=False):
        headers = auth_headers(buyer)
        quote = await client.post(f"{root}/quote", json=company, headers=headers)
        assert quote.status_code == 200, quote.text
        cash = business_body(body, payment_method="cash_on_pickup", client_request_id=str(uuid4()))
        cash_quote = await client.post(f"{root}/quote", json=cash, headers=headers)
        assert cash_quote.status_code == 200, cash_quote.text
        cash_created = await client.post(f"{root}/orders", json=cash, headers=headers)
        assert cash_created.status_code == 201, cash_created.text
        assert cash_created.json()["payment_method"] == "cash_on_pickup"
        assert cash_created.json()["invoice_handling"] == "pickup_day"
        created = await client.post(f"{root}/orders", json=company, headers=headers)
        assert created.status_code == 201, created.text
        order = created.json()
        assert order["invoice_handling"] == "pickup_day"
        assert datetime.fromisoformat(order["payment_opens_at"]) == datetime(2026, 11, 7, tzinfo=TAIPEI)
        fulfillment = await session.scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == order["id"]))
        assert fulfillment.meal_ordering_ends_at.replace(tzinfo=timezone.utc) == cutoff
        changed = await client.put(f"/v1/admin/meal-events/{event['id']}",
                                   json=event_body(meal, "2026-11-07", booking_policy="special", business_only=False),
                                   headers=auth_headers(admin))
        assert changed.status_code == 409
    with time_machine.travel(cutoff, tick=False):
        headers = auth_headers(buyer)
        for action in ("quote", "orders"):
            late = await client.post(f"{root}/{action}", json={**company, "client_request_id": str(uuid4())}, headers=headers)
            assert late.status_code == 409, late.text
        replay = await client.post(f"{root}/orders", json=company, headers=headers)
        assert replay.status_code == 201 and replay.json()["id"] == order["id"]
    assert await session.scalar(select(func.count(Order.id))) == 2


@pytest.mark.parametrize("overrides,reason", [
    ({"booking_policy": "legacy"}, "工作日或特殊日期"),
    ({"same_day_preparation_minutes": 90}, "須於取餐前預訂"),
    ({"allow_cash_payment": True}, "會餐現金付款不需另外啟用"),
    ({"ordering_starts_at": "2026-11-06T20:00:00+08:00"}, "有效訂購截止必須晚於開賣時間"),
    ({"pickup_starts_at": "2026-11-07T13:01:00+08:00"}, "沒有可預訂的取餐時段"),
])
async def test_business_only_requires_valid_event_configuration(workday_context, overrides, reason):
    client, _session, admin, _buyer, meal = workday_context
    values = {"booking_policy": "special", "business_only": True, **overrides}
    response = await client.post("/v1/admin/meal-events", json=event_body(meal, "2026-11-07", **values),
                                 headers=auth_headers(admin))
    assert response.status_code == 422
    assert reason in response.text


async def test_business_only_update_preserves_omission_and_can_disable_before_first_order(workday_context):
    client, _session, admin, buyer, meal = workday_context
    service = "2026-11-07"
    event, _body = await create_event(workday_context, service, booking_policy="special", business_only=True)
    path = f"/v1/admin/meal-events/{event['id']}"
    headers = auth_headers(admin)
    cash = await client.patch(f"{path}/payment-options", json={"allow_cash_payment": True}, headers=headers)
    assert cash.status_code == 422
    update = await client.put(path, json=event_body(meal, service, title="只調整場次名稱"), headers=headers)
    assert update.status_code == 200, update.text
    assert update.json()["business_only"] is True
    disabled = await client.put(path, json=event_body(meal, service, business_only=False), headers=headers)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["business_only"] is False
    body = {"contact_phone": "0912345678", "contact_email": buyer.email, "items": [{"offering_id": disabled.json()["offerings"][0]["id"], "quantity": 1}]}
    quote = await client.post(f"/v1/meal-events/{event['id']}/quote", json=body)
    assert quote.status_code == 200, quote.text


async def test_business_only_rechecks_cutoff_after_allocating_pickup_code(workday_context, monkeypatch):
    client, session, _admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context, "2026-11-07", booking_policy="special", business_only=True)
    cutoff = datetime(2026, 11, 6, 20, tzinfo=TAIPEI)
    original = meals._unique_pickup_code

    with time_machine.travel(cutoff - timedelta(seconds=1), tick=False) as clock:
        async def cross_cutoff(session):
            code = await original(session)
            clock.move_to(cutoff)
            return code

        monkeypatch.setattr(meals, "_unique_pickup_code", cross_cutoff)
        response = await client.post(f"/v1/meal-events/{event['id']}/orders", json=business_body(body),
                                     headers=auth_headers(buyer))
        assert response.status_code == 409 and "時段已截止" in response.json()["detail"]
        assert await session.scalar(select(func.count(Order.id))) == 0


async def test_ordering_pause_only_stops_new_orders_in_selected_event(workday_context, fake_payment):
    client, session, admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context)
    other_event, other_body = await create_event(workday_context, "2026-11-10", booking_policy="special")
    root = f"/v1/meal-events/{event['id']}"
    admin_path = f"/v1/admin/meal-events/{event['id']}/ordering-pause"
    admin_headers = auth_headers(admin)
    buyer_headers = auth_headers(buyer)
    existing_body = {**body, "client_request_id": str(uuid4())}
    created = await client.post(f"{root}/orders", json=existing_body, headers=buyer_headers)
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    assert event["ordering_pause_version"] == 0 and event["ordering_paused"] is False

    payload = {"paused": True, "expected_version": 0, "reason": "僅供管理員查看"}
    for headers, code in [({}, 401), (buyer_headers, 403)]:
        denied = await client.patch(admin_path, json=payload, headers=headers)
        assert denied.status_code == code, denied.text
    pause = await client.patch(admin_path, json=payload, headers=admin_headers)
    assert pause.status_code == 200, pause.text
    assert pause.json()["status"] == "published"
    assert pause.json()["ordering_pause_version"] == 1 and pause.json()["ordering_paused"] is True
    stale = await client.patch(admin_path, json={"paused": False, "expected_version": 0}, headers=admin_headers)
    assert stale.status_code == 409 and "狀態已更新" in stale.json()["detail"]
    repeated = await client.patch(admin_path, json={**payload, "expected_version": 1}, headers=admin_headers)
    assert repeated.status_code == 200 and repeated.json()["ordering_pause_version"] == 1

    public = await client.get(root)
    calendar = await client.get("/v1/meal-events/calendar")
    listed = next(item for item in calendar.json() if item["id"] == event["id"])
    assert listed["ordering_paused"] is True and public.json()["ordering_paused"] is True
    assert "僅供管理員查看" not in public.text and "僅供管理員查看" not in calendar.text
    assert public.json()["new_orders_blocked_reason"] == "此場次暫停接受新訂單"
    for action in ("quote", "orders"):
        blocked = await client.post(f"{root}/{action}", json=body, headers=buyer_headers)
        assert blocked.status_code == 409 and blocked.json()["detail"] == "此場次暫停接受新訂單"
    replay = await client.post(f"{root}/orders", json=existing_body, headers=buyer_headers)
    assert replay.status_code == 201 and replay.json()["id"] == order_id
    unrelated = await client.post(f"/v1/meal-events/{other_event['id']}/orders", json=other_body, headers=buyer_headers)
    assert unrelated.status_code == 201, unrelated.text

    attempt = await create_payment_attempt(session, order_id, buyer, payment_settings())
    assert (await client.get(f"/payments/{attempt.id}/checkout")).status_code == 200
    paid = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        "paused-meal-order-payment", successful_callback(attempt, FRIDAY, "PAUSED-MEAL-TRADE"),
    )
    assert paid == "paid"

    resume = await client.patch(admin_path, json={"paused": False, "expected_version": 1}, headers=admin_headers)
    assert resume.status_code == 200, resume.text
    assert resume.json()["ordering_pause_version"] == 2 and resume.json()["ordering_paused"] is False
    assert (await client.post(f"{root}/quote", json=body)).status_code == 200
    audits = list(await session.scalars(
        select(AdminAudit).where(AdminAudit.action == "meal_event.ordering_pause").order_by(AdminAudit.id),
    ))
    assert len(audits) == 2
    pause_audit = next(item for item in audits if item.data["after"]["paused"])
    assert pause_audit.reason == payload["reason"]
    assert pause_audit.data == {"before": {"paused": False, "version": 0}, "after": {"paused": True, "version": 1}}


async def test_ordering_pause_allows_before_open_and_cannot_resume_after_last_preparation_deadline(workday_context):
    client, _session, admin, _buyer, _meal = workday_context
    event, body = await create_event(
        workday_context, booking_policy="special", ordering_starts_at="2026-11-06T17:00:00+08:00",
        same_day_preparation_minutes=120, ordering_ends_at="2026-11-09T12:00:00+08:00",
    )
    path = f"/v1/admin/meal-events/{event['id']}/ordering-pause"
    pause = await client.patch(path, json={"paused": True, "expected_version": 0}, headers=auth_headers(admin))
    assert pause.status_code == 200, pause.text
    with time_machine.travel(datetime(2026, 11, 9, 11, tzinfo=TAIPEI), tick=False):
        resume = await client.patch(path, json={"paused": False, "expected_version": 1}, headers=auth_headers(admin))
        assert resume.status_code == 409 and "無可預訂" in resume.json()["detail"]
        public = await client.get(f"/v1/meal-events/{event['id']}")
        assert public.json()["ordering_paused"] is True
        assert public.json()["ordering_pause_version"] == 1
        assert (await client.post(f"/v1/meal-events/{event['id']}/quote", json=body)).status_code == 409


async def test_ordering_pause_does_not_override_legacy_calendar(workday_context):
    client, session, admin, _buyer, meal = workday_context
    published, _body = await create_event(workday_context, "2026-11-07", booking_policy="special")
    event = await session.get(MealEvent, published["id"])
    template = template_for(admin, meal)
    session.add(template)
    await session.flush()
    event.booking_policy = "legacy"
    event.schedule_template_id = template.id
    await session.commit()
    response = await client.patch(
        f"/v1/admin/meal-events/{event.id}/ordering-pause",
        json={"paused": True, "expected_version": 0}, headers=auth_headers(admin),
    )
    assert response.status_code == 409 and "非供餐工作日" in response.json()["detail"]
