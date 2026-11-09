from datetime import date, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import select

from app.meal_booking import TAIPEI, configured_booking_window, meal_booking_window
from app.meal_schedules import generate_scheduled_meal_events
from app.models import MealEvent, MealEventOffering, MealEventStatus
from tests.support import auth_headers
from tests.test_meal_schedules import schedule_body
from tests.test_workday_meal_booking import create_event, template_for, workday_context


@pytest.mark.parametrize("cutoff,expected_start,expected_end", [
    ("2026-11-09T20:00:00+08:00", "2026-11-09T00:00:00+08:00", "2026-11-09T20:00:00+08:00"),
    ("2026-11-09T00:00:00+08:00", "2026-11-06T00:00:00+08:00", "2026-11-09T00:00:00+08:00"),
    ("2026-11-08T20:00:00+08:00", "2026-11-06T00:00:00+08:00", "2026-11-08T20:00:00+08:00"),
    ("2026-11-10T00:00:00+08:00", "2026-11-09T00:00:00+08:00", "2026-11-10T00:00:00+08:00"),
    ("2026-11-10T18:00:00+08:00", "2026-11-10T00:00:00+08:00", "2026-11-10T18:00:00+08:00"),
    ("2026-11-11T10:00:00+08:00", "2026-11-10T00:00:00+08:00", "2026-11-10T20:00:00+08:00"),
])
def test_workday_early_cutoff_keeps_a_valid_window(cutoff, expected_start, expected_end):
    window = configured_booking_window(date(2026, 11, 11), "workday",
        datetime(2026, 11, 1, tzinfo=TAIPEI), datetime.fromisoformat(cutoff))
    assert window == (datetime.fromisoformat(expected_start), datetime.fromisoformat(expected_end))


def test_holiday_cutoff_and_explicit_later_start_remain_validated():
    early = datetime(2026, 10, 1, tzinfo=TAIPEI)
    cutoff = datetime(2026, 10, 9, 20, tzinfo=TAIPEI)
    assert configured_booking_window(date(2026, 10, 14), "workday", early, cutoff) == (
        datetime(2026, 10, 8, tzinfo=TAIPEI), cutoff)
    later = datetime(2026, 11, 9, 12, tzinfo=TAIPEI)
    end = datetime(2026, 11, 9, 20, tzinfo=TAIPEI)
    assert configured_booking_window(date(2026, 11, 11), "workday", later, end) == (later, end)
    with pytest.raises(ValueError, match="截止必須晚於"):
        configured_booking_window(date(2026, 11, 11), "workday", end, end)
    assert configured_booking_window(date(2026, 11, 11), "special", early, end) == (early, end)


async def test_manual_wednesday_early_cutoff_allows_monday_quote_and_order(workday_context):
    client, _session, _admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context, "2026-11-11", ordering_ends_at="2026-11-09T20:00:00+08:00",
        pickup_starts_at="2026-11-11T03:00:00Z", pickup_ends_at="2026-11-11T05:30:00Z")
    body["pickup_at"] = "2026-11-11T11:00:00+08:00"
    start, end = (datetime.fromisoformat(event[name].replace("Z", "+00:00")) for name in ("booking_starts_at", "booking_ends_at"))
    assert start == datetime(2026, 11, 9, tzinfo=TAIPEI)
    assert end == datetime(2026, 11, 9, 20, tzinfo=TAIPEI)
    with time_machine.travel(start - timedelta(seconds=1), tick=False):
        assert (await client.post(f"/v1/meal-events/{event['id']}/quote", json=body)).status_code == 409
    with time_machine.travel(start + timedelta(hours=12), tick=False):
        quoted = await client.post(f"/v1/meal-events/{event['id']}/quote", json=body)
        assert quoted.status_code == 200, quoted.text
        created = await client.post(f"/v1/meal-events/{event['id']}/orders", json=body, headers=auth_headers(buyer))
        assert created.status_code == 201, created.text
    with time_machine.travel(end, tick=False):
        for action in ("quote", "orders"):
            assert (await client.post(f"/v1/meal-events/{event['id']}/{action}", json=body, headers=auth_headers(buyer))).status_code == 409


async def test_two_day_cutoff_schedule_generates_bookable_wednesday(workday_context):
    client, session, admin, buyer, meal = workday_context
    with time_machine.travel(datetime(2026, 11, 9, 12, tzinfo=TAIPEI), tick=False):
        response = await client.post("/v1/admin/meal-schedules", headers=auth_headers(admin), json=schedule_body(
            meal.id, weekdays=[2], advance_days=3, cutoff_days_before=2, cutoff_time="20:00", enabled=True, auto_publish=True))
        assert response.status_code == 201, response.text
        events = list(await session.scalars(select(MealEvent)))
        assert len(events) == 1
        event = events[0]
        assert event.service_date == date(2026, 11, 11)
        assert meal_booking_window(event) == (datetime(2026, 11, 9, tzinfo=TAIPEI), datetime(2026, 11, 9, 20, tzinfo=TAIPEI))
        offering = await session.scalar(select(MealEventOffering).where(MealEventOffering.meal_event_id == event.id))
        body = {"contact_email": buyer.email, "pickup_at": "2026-11-11T11:30:00+08:00",
                "items": [{"offering_id": offering.id, "quantity": 1}]}
        quoted = await client.post(f"/v1/meal-events/{event.id}/quote", json=body)
        assert quoted.status_code == 200, quoted.text
        assert await generate_scheduled_meal_events(session, datetime.now(TAIPEI)) == []


async def test_legacy_early_cutoff_conversion_uses_same_window(workday_context):
    _client, session, admin, _buyer, meal = workday_context
    template = template_for(admin, meal, weekdays=[2], advance_days=3, cutoff_days_before=2)
    session.add(template)
    await session.flush()
    event = MealEvent(title="原提前截單場次", location="合作社", schedule_template_id=template.id,
        service_date=date(2026, 11, 11), meal_period="lunch", booking_policy="legacy", created_by_id=admin.id,
        ordering_starts_at=datetime(2026, 11, 8, tzinfo=TAIPEI), ordering_ends_at=datetime(2026, 11, 9, 20, tzinfo=TAIPEI),
        pickup_starts_at=datetime(2026, 11, 11, 11, tzinfo=TAIPEI), pickup_ends_at=datetime(2026, 11, 11, 13, 30, tzinfo=TAIPEI),
        status=MealEventStatus.PUBLISHED)
    session.add(event)
    await session.commit()
    report = {}
    assert await generate_scheduled_meal_events(session, datetime(2026, 11, 9, 12, tzinfo=TAIPEI), report=report) == []
    assert report["converted"] == 1 and event.booking_policy == "workday"
    assert meal_booking_window(event) == (datetime(2026, 11, 9, tzinfo=TAIPEI), datetime(2026, 11, 9, 20, tzinfo=TAIPEI))
