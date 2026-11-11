from datetime import datetime, timedelta, timezone
from gzip import compress

from sqlalchemy import event as sql_event

from app import workday_calendar
from app.models import Meal, MealEvent, MealEventOffering, MealEventStatus, MealOption, MealOptionGroup, MealScheduleTemplate, User
from app.routers.meals import meals_router
from tests.support import api_test_context


async def test_public_calendar_preserves_booking_fields_and_reads_only_event_and_availability(database_session, monkeypatch):
    session = database_session
    monkeypatch.setattr(workday_calendar, "is_workday", lambda date: False)
    operator = User(email="calendar-admin@example.test", display_name="測試操作人員", password_hash="test")
    meal = Meal(
        slug="calendar-meal", name="測試便當", description="餐點內容及成分說明" * 25, price=120, is_active=False,
        option_groups=[MealOptionGroup(
            name="配菜選擇",
            options=[MealOption(name=f"配菜 {index}", price_delta=index) for index in range(1, 9)],
        )],
    )
    alternate = Meal(slug="calendar-alternate-meal", name="另一餐點", price=110)
    session.add_all([operator, meal, alternate])
    await session.flush()
    schedule = MealScheduleTemplate(
        created_by_id=operator.id, title="既有平日排程", location="測試地點", meal_period="lunch",
        pickup_start_time="11:00", pickup_end_time="13:00", cutoff_time="20:00", weekdays=[0], offerings=[],
    )
    session.add(schedule)
    await session.flush()

    def booking_event(day, status, *, dinner=False, **fields):
        starts = datetime(2030, 1, day, 8 if dinner else 3, 30 if dinner else 0, tzinfo=timezone.utc)
        return MealEvent(
            id=f"calendar-event-{day}", created_by_id=operator.id, title=f"場次 {day}", location="測試地點",
            ordering_starts_at=starts - timedelta(days=2), ordering_ends_at=starts - timedelta(hours=8),
            pickup_starts_at=starts, pickup_ends_at=starts + timedelta(hours=2), status=status, **fields,
        )

    legacy = booking_event(
        10, MealEventStatus.PUBLISHED,
        offerings=[MealEventOffering(meal=meal, price=120, capacity=4, paid_quantity=1, reserved_quantity=2)],
    )
    workday = booking_event(
        11, MealEventStatus.ORDERING_CLOSED, booking_policy="workday",
        offerings=[
            MealEventOffering(meal=meal, price=120, capacity=4, paid_quantity=2, reserved_quantity=2),
            MealEventOffering(meal=alternate, price=110, capacity=5, is_active=False),
        ],
    )
    special = booking_event(
        12, MealEventStatus.PICKUP_OPEN, booking_policy="special", dinner=True,
        pickup_slot_ends_at=datetime(2030, 1, 12, 10, tzinfo=timezone.utc),
        offerings=[MealEventOffering(meal=alternate, price=110, capacity=5, is_active=False)],
    )
    blocked_legacy = booking_event(13, MealEventStatus.PUBLISHED, schedule_template_id=schedule.id)
    session.add_all([
        special, blocked_legacy, workday, legacy,
        booking_event(14, MealEventStatus.DRAFT),
        booking_event(15, MealEventStatus.CANCELLED),
        booking_event(16, MealEventStatus.COMPLETED),
    ])
    await session.commit()
    session.expunge_all()

    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    async with api_test_context(session, [meals_router]) as client:
        full = await client.get("/v1/meal-events")
        assert full.status_code == 200, full.text
        sql_event.listen(session.bind.sync_engine, "before_cursor_execute", capture)
        try:
            calendar = await client.get("/v1/meal-events/calendar")
        finally:
            sql_event.remove(session.bind.sync_engine, "before_cursor_execute", capture)
        assert calendar.status_code == 200, calendar.text
        assert (await client.get(f"/v1/meal-events/{legacy.id}")).json() == full.json()[0]

    full_rows = full.json()
    rows = calendar.json()
    assert [row["id"] for row in rows] == [legacy.id, workday.id, special.id, blocked_legacy.id]
    assert [
        (row["has_active_offerings"], row["has_available_offerings"]) for row in rows
    ] == [(True, True), (True, False), (False, False), (False, False)]
    assert rows[0]["service_date"] == "2030-01-10"
    assert rows[0]["ordering_ends_at"] == rows[0]["booking_ends_at"] == "2030-01-09T12:00:00Z"
    assert rows[1]["booking_starts_at"] == rows[1]["ordering_starts_at"]
    assert rows[2]["meal_period"] == "dinner"
    assert rows[2]["pickup_slots"][-1] == rows[2]["pickup_slot_ends_at"] == "2030-01-12T10:00:00Z"
    assert rows[3]["new_orders_blocked_reason"]
    assert full_rows[0]["offerings"][0]["option_groups"][0]["options"]
    for summary, event in zip(rows, full_rows, strict=True):
        full_booking_fields = {
            key: value for key, value in event.items()
            if key not in {"offerings", "allow_cash_payment", "schedule_template_id", "can_edit"}
        }
        menu = [item for item in event["offerings"] if item["is_active"]]
        assert summary == {
            **full_booking_fields,
            "has_active_offerings": bool(menu),
            "has_available_offerings": any(item["available_quantity"] > 0 for item in menu),
        }

    assert len(calendar.content) < len(full.content)
    assert len(compress(calendar.content)) < len(compress(full.content))
    assert len(statements) == 1
    statement, parameters = statements[0]
    query = " ".join(statement.lower().split())
    assert query.count("exists (select 1 from meal_event_offerings") == 2
    assert query.count("meal_events.id = meal_event_offerings.meal_event_id") == 2
    assert " join " not in query
    assert "meal_option" not in query
    connection = await session.connection()
    plan = await connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)
    assert sum(
        "meal_event_offerings" in row[-1] and "meal_event_id=?" in row[-1]
        for row in plan
    ) == 2
