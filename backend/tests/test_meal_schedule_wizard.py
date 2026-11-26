from datetime import date, datetime, timedelta, timezone

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.meal_booking import TAIPEI, _aware, meal_booking_window
from app.models import AdminAudit, FulfillmentStatus, Meal, MealEvent, MealScheduleTemplate, Order
from tests.support import auth_headers
from tests.test_meal_schedules import schedule_context  # noqa: F401


BASE = "/v1/admin/meal-schedules/wizard"
FRIDAY = datetime(2026, 11, 20, 10, tzinfo=TAIPEI)


def wizard_entry(meal, **settings):
    return {
        "title": "測試排程",
        "location": "清華大學",
        "meal_period": "lunch",
        "weekdays": list(range(5)),
        "saturday_business_only": True,
        "sunday_business_only": True,
        "offerings": [{"meal_id": meal.id, "capacity": 20}],
        **settings,
    }


async def preview_and_save(client, admin, body):
    preview = await client.post(f"{BASE}/preview", json=body, headers=auth_headers(admin))
    assert preview.status_code == 200, preview.text
    result = await client.post(BASE, json={**body, "preview_version": preview.json()["preview_version"]},
                               headers=auth_headers(admin))
    assert result.status_code == 200, result.text
    return preview.json(), result.json()


async def scheduled_events(session):
    query = select(MealEvent).order_by(MealEvent.service_date, MealEvent.meal_period).options(
        selectinload(MealEvent.offerings),
    ).execution_options(populate_existing=True)
    return list(await session.scalars(query))


def booking_body(event, buyer, *, business=False):
    settings = {
        "items": [{"offering_id": event.offerings[0].id, "quantity": 2 if business else 1}],
        "contact_email": buyer.email,
        "contact_phone": "0912345678",
    }
    if business:
        settings.update(meal_booking_type="business", invoice_buyer_type="company",
                        invoice_buyer_tax_id="12345675", invoice_buyer_name="測試公司", invoice_buyer_email=buyer.email)
    return settings


async def test_wizard_creates_both_periods_and_uses_current_meal_price_and_fixed_window(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    lunch, dinner = wizard_entry(meal), wizard_entry(meal, meal_period="dinner")
    body = {"schedules": [lunch, dinner]}
    with time_machine.travel(FRIDAY, tick=False):
        assert (await client.post(f"{BASE}/preview", json=body, headers=auth_headers(buyer))).status_code == 403
        assert (await client.post(BASE, json={**body, "preview_version": "a" * 64},
                                  headers=auth_headers(buyer))).status_code == 403
        assert (await client.post(f"{BASE}/preview", json={"schedules": [lunch, {**dinner, "offerings": [
            {"meal_id": meal.id, "capacity": 19},
        ]}]}, headers=auth_headers(admin))).status_code == 422
        preview, result = await preview_and_save(client, admin, body)
        assert preview["canonical_offerings"] == [{"meal_id": meal.id, "capacity": 20, "price": 100}]
        assert result["updated_event_count"] == 0
        assert [(row["meal_period"], row["pickup_start_time"], row["pickup_end_time"]) for row in result["schedules"]] == [
            ("lunch", "11:00:00", "13:00:00"), ("dinner", "16:30:00", "18:00:00"),
        ]
        assert all(row["advance_days"] == 7 and row["sunday_business_only"] for row in result["schedules"])
        duplicate = await client.post(BASE, json={**body, "preview_version": preview["preview_version"]},
                                      headers=auth_headers(admin))
        assert duplicate.status_code == 409
        events = await scheduled_events(session)
        assert len(events) == 14
        first_weekend_and_monday = events[:6]
        assert [(row.service_date.day, row.business_only) for row in first_weekend_and_monday] == [
            (21, True), (21, True), (22, True), (22, True), (23, False), (23, False),
        ]
        for event in first_weekend_and_monday:
            starts, cutoff = meal_booking_window(event, business=event.business_only)
            assert starts == FRIDAY.replace(hour=0)
            # Weekend business meals close on Friday; Monday stays open until Sunday 20:00.
            expected_cutoff = FRIDAY.replace(hour=20) if event.business_only else FRIDAY.replace(day=22, hour=20)
            assert cutoff == expected_cutoff
            assert event.offerings[0].price == 100
        sunday = next(row for row in events if row.service_date.day == 22 and row.meal_period == "lunch")
        public = await client.get("/v1/meal-events/calendar")
        assert next(row for row in public.json() if row["id"] == sunday.id)["business_only"]
        for action in ("quote", "orders"):
            regular = await client.post(f"/v1/meal-events/{sunday.id}/{action}",
                                        json=booking_body(sunday, buyer), headers=auth_headers(buyer))
            assert regular.status_code == 422 and regular.json()["detail"] == "此場次僅開放會餐"
        created = await client.post(f"/v1/meal-events/{sunday.id}/orders", json=booking_body(sunday, buyer, business=True),
                                    headers=auth_headers(buyer))
        assert created.status_code == 201, created.text
        assert created.json()["invoice_handling"] == "pickup_day"
        assert datetime.fromisoformat(created.json()["payment_opens_at"]) == datetime(2026, 11, 22, tzinfo=TAIPEI)
        order = await session.get(Order, created.json()["id"])
        assert _aware(order.invoice_issue_after).astimezone(TAIPEI).date() == sunday.service_date
    with time_machine.travel(FRIDAY.replace(hour=20), tick=False):
        late = await client.post(f"/v1/meal-events/{sunday.id}/quote", json=booking_body(sunday, buyer, business=True),
                                 headers=auth_headers(buyer))
        assert late.status_code == 409


@pytest.mark.parametrize("now,open_days", [
    (datetime(2026, 10, 22, 12, tzinfo=TAIPEI), [date(2026, 10, 24)]),
    (datetime(2026, 10, 8, 12, tzinfo=TAIPEI), [date(2026, 10, 11)]),
    (datetime(2025, 2, 6, 12, tzinfo=TAIPEI), [date(2025, 2, 8), date(2025, 2, 9)]),
])
async def test_wizard_weekend_uses_official_holidays_but_keeps_makeup_saturday(schedule_context, now, open_days):
    client, session, admin, _, meal = schedule_context
    with time_machine.travel(now, tick=False):
        await preview_and_save(client, admin, {"schedules": [wizard_entry(meal, weekdays=[])]})
        events = await scheduled_events(session)
        assert [event.service_date for event in events] == open_days
        assert all(event.business_only for event in events)


@pytest.mark.parametrize("scope", ["empty_future", "new_only"])
async def test_wizard_only_changes_empty_future_menus_and_audits_changes(schedule_context, scope):
    client, session, admin, buyer, meal = schedule_context
    with time_machine.travel(FRIDAY, tick=False):
        _, result = await preview_and_save(client, admin, {"schedules": [wizard_entry(meal)]})
        template = result["schedules"][0]
        events = await scheduled_events(session)
        ordered_event, replace_event = events[0:2]
        order_response = await client.post(f"/v1/meal-events/{ordered_event.id}/orders",
                                           json=booking_body(ordered_event, buyer, business=True), headers=auth_headers(buyer))
        assert order_response.status_code == 201, order_response.text
        order = await session.get(Order, order_response.json()["id"])
        order.cancelled_at = FRIDAY
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        new_meal = Meal(slug="wizard-new-menu", name="新餐點", price=245)
        session.add(new_meal)
        await session.commit()
        draft = wizard_entry(meal, id=template["id"], expected_updated_at=template["updated_at"],
                             offerings=[{"meal_id": new_meal.id, "capacity": 40}])
        preview, update = await preview_and_save(client, admin, {"schedules": [draft], "scope": scope})
        assert preview["protected_events"] == [{
            "id": ordered_event.id, "title": ordered_event.title,
            "service_date": ordered_event.service_date.isoformat(), "meal_period": "lunch",
        }]
        changed_count = len(events) - 1 if scope == "empty_future" else 0
        assert len(preview["updated_events"]) == update["updated_event_count"] == changed_count
        assert update["schedules"][0]["offerings"][0] == {"meal_id": new_meal.id, "price": 245, "capacity": 40, "position": 0}
        checked = {event.id: event for event in await scheduled_events(session)}
        assert checked[ordered_event.id].offerings[0].meal_id == meal.id
        assert checked[ordered_event.id].offerings[0].capacity == 20
        assert order.amount_total == order_response.json()["amount_total"]
        affected = checked[replace_event.id].offerings[0]
        audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.action == "meal_event.schedule_menu_update")))
        assert len(audits) == changed_count
        if scope == "empty_future":
            assert (affected.meal_id, affected.price, affected.capacity) == (new_meal.id, 245, 40)
            assert all(row.actor_id == admin.id and row.data["before"]["offerings"][0]["capacity"] == 20 and
                       row.data["after"]["offerings"][0]["capacity"] == 40 for row in audits)
        else:
            assert affected.meal_id == meal.id


async def test_wizard_rechecks_new_orders_and_leaves_all_selected_schedules_unchanged(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    with time_machine.travel(FRIDAY, tick=False):
        _, initial = await preview_and_save(client, admin, {"schedules": [wizard_entry(meal), wizard_entry(meal, meal_period="dinner")]})
        drafts = {"schedules": [wizard_entry(meal, id=row["id"], meal_period=row["meal_period"],
                                           expected_updated_at=row["updated_at"], offerings=[{"meal_id": meal.id, "capacity": 40}])
                                for row in initial["schedules"]]}
        preview = await client.post(f"{BASE}/preview", json=drafts, headers=auth_headers(admin))
        assert preview.status_code == 200
        events = await scheduled_events(session)
        target = next(event for event in events if event.meal_period == "lunch")
        order = await client.post(f"/v1/meal-events/{target.id}/orders", json=booking_body(target, buyer, business=True),
                                  headers=auth_headers(buyer))
        assert order.status_code == 201, order.text
        response = await client.post(BASE, json={**drafts, "preview_version": preview.json()["preview_version"]},
                                     headers=auth_headers(admin))
        assert response.status_code == 409 and "場次或訂單已變動" in response.json()["detail"]
        schedules = list(await session.scalars(select(MealScheduleTemplate)))
        assert {schedule.offerings[0]["capacity"] for schedule in schedules} == {20}
        assert {event.offerings[0].capacity for event in await scheduled_events(session)} == {20}


async def test_wizard_rechecks_price_changed_in_meal_master(schedule_context):
    client, session, admin, _, meal = schedule_context
    with time_machine.travel(FRIDAY, tick=False):
        body = {"schedules": [wizard_entry(meal)]}
        preview = await client.post(f"{BASE}/preview", json=body, headers=auth_headers(admin))
        assert preview.json()["canonical_offerings"][0]["price"] == 100
        adjusted = await client.patch(f"/v1/admin/meals/{meal.id}", headers=auth_headers(admin), json={
            "name": meal.name, "description": meal.description, "price": 125, "tax_type": meal.tax_type.value,
            "is_active": True, "expected_updated_at": _aware(meal.updated_at).isoformat(),
        })
        assert adjusted.status_code == 200, adjusted.text
        stale = await client.post(BASE, json={**body, "preview_version": preview.json()["preview_version"]},
                                  headers=auth_headers(admin))
        assert stale.status_code == 409
        assert await session.scalar(select(MealScheduleTemplate.id)) is None
        preview, result = await preview_and_save(client, admin, body)
        assert preview["canonical_offerings"][0]["price"] == result["schedules"][0]["offerings"][0]["price"] == 125
        assert {event.offerings[0].price for event in await scheduled_events(session)} == {125}
