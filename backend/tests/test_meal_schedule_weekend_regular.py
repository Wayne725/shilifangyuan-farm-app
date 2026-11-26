import time_machine
from sqlalchemy import select

from app.meal_booking import meal_booking_window
from app.models import AdminAudit, MealScheduleTemplate
from tests.support import auth_headers
from tests.test_meal_schedule_wizard import BASE, FRIDAY, booking_body, preview_and_save, scheduled_events, wizard_entry
from tests.test_meal_schedules import schedule_context  # noqa: F401


async def test_weekend_can_open_regular_and_business_meals_together(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    with time_machine.travel(FRIDAY, tick=False):
        conflict = wizard_entry(meal, weekdays=[0, 1, 2, 3, 4, 5])
        response = await client.post(f"{BASE}/preview", json={"schedules": [conflict]}, headers=auth_headers(admin))
        assert response.status_code == 422, response.text

        entry = wizard_entry(meal, weekdays=[0, 1, 2, 3, 4, 5], saturday_business_only=False)
        _preview, result = await preview_and_save(client, admin, {"schedules": [entry]})
        assert result["schedules"][0]["weekdays"] == [0, 1, 2, 3, 4, 5]
        events = await scheduled_events(session)
        weekend = {event.service_date.day: event for event in events if event.service_date.day in (21, 22)}
        assert [(day, weekend[day].business_only, weekend[day].booking_policy) for day in (21, 22)] == [
            (21, False, "special"), (22, True, "special")]
        saturday = weekend[21]
        assert meal_booking_window(saturday) == (FRIDAY.replace(hour=0), FRIDAY.replace(hour=20))
        for business in (False, True):
            created = await client.post(f"/v1/meal-events/{saturday.id}/orders", json=booking_body(saturday, buyer, business=business),
                                        headers=auth_headers(buyer))
            assert created.status_code == 201, created.text


async def test_switching_weekend_mode_updates_generated_events(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    with time_machine.travel(FRIDAY, tick=False):
        _preview, result = await preview_and_save(client, admin, {"schedules": [wizard_entry(meal)]})
        saturday = next(event for event in await scheduled_events(session) if event.service_date.day == 21)
        assert saturday.business_only
        business = await client.post(f"/v1/meal-events/{saturday.id}/orders", json=booking_body(saturday, buyer, business=True),
                                     headers=auth_headers(buyer))
        assert business.status_code == 201, business.text

        template = result["schedules"][0]
        opened = {**wizard_entry(meal, weekdays=[0, 1, 2, 3, 4, 5], saturday_business_only=False),
                  "id": template["id"], "expected_updated_at": template["updated_at"]}
        _preview, result = await preview_and_save(client, admin, {"schedules": [opened]})
        events = {event.service_date.day: event for event in await scheduled_events(session)}
        assert [events[21].business_only, events[22].business_only] == [False, True]
        regular = await client.post(f"/v1/meal-events/{saturday.id}/orders", json=booking_body(saturday, buyer),
                                    headers=auth_headers(buyer))
        assert regular.status_code == 201, regular.text

        current = await session.scalar(select(MealScheduleTemplate).execution_options(populate_existing=True))
        closed = {**wizard_entry(meal), "id": current.id, "expected_updated_at": result["schedules"][0]["updated_at"]}
        await preview_and_save(client, admin, {"schedules": [closed]})
        events = {event.service_date.day: event for event in await scheduled_events(session)}
        assert events[21].business_only is False
        audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.action == "meal_event.weekend_access_update")))
        assert [(audit.aggregate_id, audit.data["after"]["business_only"]) for audit in audits] == [(saturday.id, False)]
