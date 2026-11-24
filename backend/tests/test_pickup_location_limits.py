from pathlib import Path

import time_machine
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from app.meal_booking import _aware
from app.models import AdminAudit, MealScheduleTemplate, PickupLocation
from app.schemas import UNLIMITED_MEAL_CAPACITY, MealOfferingInput
from tests.support import auth_headers
from tests.test_business_meals import business_body
from tests.test_group_checkout_contract import group_checkout, join, join_body  # noqa: F401
from tests.test_meal_booking_policy import booking_context  # noqa: F401
from tests.test_meal_schedule_wizard import FRIDAY, BASE, preview_and_save, scheduled_events, wizard_entry
from tests.test_meal_schedules import schedule_context  # noqa: F401


async def add_locations(session):
    store = PickupLocation(code="store", name="合作社門市", sort_order=1)
    hall = PickupLocation(code="hall", name="行政大樓", sort_order=2)
    retired = PickupLocation(code="retired", name="舊取貨點", is_active=False)
    business = PickupLocation(code="meeting", name="會議室", business_meal_only=True)
    session.add_all([store, hall, retired, business])
    await session.commit()
    return store, hall, retired, business


async def test_group_buy_only_accepts_its_open_pickup_locations(group_checkout):
    c = group_checkout
    store, hall, retired, business = await add_locations(c.session)
    path = f"/v1/group-campaigns/{c.campaign_id}"
    for invalid in ([retired.id], [business.id], ["missing"], [store.id, store.id]):
        response = await c.client.patch(path, headers=c.admin_headers, json={"pickup_location_ids": invalid})
        assert response.status_code == 422, response.text
    edited = await c.client.patch(path, headers=c.admin_headers, json={"pickup_location_ids": [store.id]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["pickup_location_ids"] == [store.id]
    assert (await c.client.get(path)).json()["pickup_location_ids"] == [store.id]
    audit = await c.session.scalar(select(AdminAudit).where(AdminAudit.action == "campaign.update"))
    assert audit.data["changed_fields"] == ["pickup_location_ids"]

    other = await join(c, join_body(pickup_location_id=hall.id))
    missing = await join(c, join_body())
    assert [other.status_code, missing.status_code] == [422, 422]
    assert "指定的取貨地點" in other.json()["detail"]
    created = await join(c, join_body(pickup_location_id=store.id))
    assert created.status_code == 201, created.text

    reopened = await c.client.patch(path, headers=c.admin_headers, json={"pickup_location_ids": []})
    assert reopened.status_code == 200, reopened.text
    assert (await join(c, join_body(pickup_location_id=hall.id), buyer=1)).status_code == 201


async def test_meal_event_limits_regular_pickup_but_not_business_meals(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    store, hall, _retired, business = await add_locations(session)
    event.pickup_location_ids = [store.id]
    await session.commit()
    base = f"/v1/meal-events/{event.id}"
    headers = auth_headers(buyer)
    public = (await client.get(base)).json()
    assert public["pickup_location_ids"] == [store.id]
    for location_id in (hall.id, None):
        quote = await client.post(f"{base}/quote", json={**body, "pickup_location_id": location_id}, headers=headers)
        assert quote.status_code == 422 and "指定的取餐點" in quote.json()["detail"], quote.text
    allowed = await client.post(f"{base}/quote", json={**body, "pickup_location_id": store.id}, headers=headers)
    assert allowed.status_code == 200, allowed.text
    meeting = await client.post(f"{base}/quote", json=business_body(body, pickup_location_id=business.id), headers=headers)
    assert meeting.status_code == 200, meeting.text


async def test_admin_meal_events_validate_open_locations_and_default_to_unlimited(booking_context):
    client, session, _buyer, admin, event, _body = booking_context
    store, _hall, retired, _business = await add_locations(session)
    assert MealOfferingInput(meal_id="meal", price=100).capacity == UNLIMITED_MEAL_CAPACITY
    offering = event.offerings[0]
    payload = {
        "title": "限定取餐點場次", "location": "合作社",
        "ordering_starts_at": event.ordering_starts_at.isoformat(), "ordering_ends_at": event.ordering_ends_at.isoformat(),
        "pickup_starts_at": event.pickup_starts_at.isoformat(), "pickup_ends_at": event.pickup_ends_at.isoformat(),
        "booking_policy": "special", "offerings": [{"meal_id": offering.meal_id, "price": 100}],
    }
    headers = auth_headers(admin)
    rejected = await client.post("/v1/admin/meal-events", json={**payload, "pickup_location_ids": [retired.id]}, headers=headers)
    assert rejected.status_code == 422, rejected.text
    created = await client.post("/v1/admin/meal-events", json={**payload, "pickup_location_ids": [store.id]}, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["pickup_location_ids"] == [store.id]
    assert created.json()["offerings"][0]["capacity"] == UNLIMITED_MEAL_CAPACITY
    kept = await client.put(f"/v1/admin/meal-events/{created.json()['id']}", json=payload, headers=headers)
    assert kept.status_code == 200, kept.text
    assert kept.json()["pickup_location_ids"] == [store.id]


async def test_schedule_locations_reach_generated_and_future_events(schedule_context):
    client, session, admin, _buyer, meal = schedule_context
    store, hall, _retired, _business = await add_locations(session)
    with time_machine.travel(FRIDAY, tick=False):
        entry = {**wizard_entry(meal, offerings=[{"meal_id": meal.id}]), "pickup_location_ids": [store.id]}
        _preview, saved = await preview_and_save(client, admin, {"schedules": [entry]})
        template = saved["schedules"][0]
        assert template["pickup_location_ids"] == [store.id]
        assert template["offerings"][0]["capacity"] == UNLIMITED_MEAL_CAPACITY
        events = await scheduled_events(session)
        assert events and {tuple(item.pickup_location_ids) for item in events} == {(store.id,)}

        bad = {**entry, "id": template["id"], "expected_updated_at": template["updated_at"], "pickup_location_ids": ["missing"]}
        assert (await client.post(f"{BASE}/preview", json={"schedules": [bad]}, headers=auth_headers(admin))).status_code == 422
        moved = {**bad, "pickup_location_ids": [hall.id]}
        await preview_and_save(client, admin, {"schedules": [moved]})
        assert {tuple(item.pickup_location_ids) for item in await scheduled_events(session)} == {(hall.id,)}
        audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.action == "meal_event.pickup_locations_update")))
        assert len(audits) == len(events)

        current = await session.scalar(select(MealScheduleTemplate).execution_options(populate_existing=True))
        untouched = {key: value for key, value in moved.items() if key != "pickup_location_ids"}
        untouched["expected_updated_at"] = _aware(current.updated_at).isoformat()
        await preview_and_save(client, admin, {"schedules": [untouched]})
        current = await session.scalar(select(MealScheduleTemplate).execution_options(populate_existing=True))
        assert current.pickup_location_ids == [hall.id]


def test_migration_lifts_open_meal_capacities_and_adds_location_limits(tmp_path, monkeypatch):
    from app.config import Settings

    path = tmp_path / "pickup-limits.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{path}")
    previous = "0073_group_pickup_slots"
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO meal_schedule_templates (id, title, location, meal_period, pickup_start_time, pickup_end_time,
                    cutoff_time, cutoff_days_before, advance_days, weekdays, enabled, auto_publish, offerings,
                    created_by_id, created_at, updated_at)
                VALUES ('template', '午餐', '合作社', 'lunch', '11:00', '13:00', '20:00', 1, 7, '[0]', true, true,
                    '[{"meal_id": "meal", "price": 100, "capacity": 20, "position": 0}]', 'admin', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """))
            for event_id, status, pickup in (("future", "published", "2099-01-01 04:00:00"),
                                             ("draft", "draft", "2099-01-02 04:00:00"),
                                             ("past", "published", "2000-01-01 04:00:00"),
                                             ("cancelled", "cancelled", "2099-01-03 04:00:00")):
                connection.execute(text("""
                    INSERT INTO meal_events (id, title, location, ordering_starts_at, ordering_ends_at, pickup_starts_at,
                        pickup_ends_at, status, created_by_id, created_at, updated_at)
                    VALUES (:id, :id, '合作社', '1999-12-30 00:00:00', '1999-12-31 00:00:00', :pickup, :pickup_end,
                        :status, 'admin', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """), {"id": event_id, "status": status, "pickup": pickup, "pickup_end": pickup.replace("04:00", "05:00")})
                connection.execute(text("""
                    INSERT INTO meal_event_offerings (id, meal_event_id, meal_id, price, capacity, reserved_quantity, paid_quantity, position, is_active)
                    VALUES (:id, :id, 'meal', 100, 20, 0, 3, 0, true)
                """), {"id": event_id})
        command.upgrade(config, "head")
        with engine.begin() as connection:
            capacities = dict(connection.execute(text("SELECT meal_event_id, capacity FROM meal_event_offerings")).all())
            assert capacities == {"future": UNLIMITED_MEAL_CAPACITY, "draft": UNLIMITED_MEAL_CAPACITY, "past": 20, "cancelled": 20}
            offerings = connection.execute(text("SELECT offerings FROM meal_schedule_templates")).scalar_one()
            assert '"capacity": 100000' in offerings and '"capacity": 20' not in offerings
            for table in ("group_campaigns", "meal_events", "meal_schedule_templates"):
                columns = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}
                assert "pickup_location_ids" in columns
            assert connection.execute(text("SELECT pickup_location_ids FROM meal_events WHERE id = 'future'")).scalar_one() == "[]"
        command.downgrade(config, previous)
        with engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(meal_events)"))}
            assert "pickup_location_ids" not in columns
    finally:
        engine.dispose()
