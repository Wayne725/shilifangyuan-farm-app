from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.jobs import reconcile_once
from app.meal_schedules import TAIPEI, generate_scheduled_meal_events
from app.models import Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order, User, UserRole
from app.routers.meals import meals_router
from app.schemas import MealScheduleInput
from tests.support import api_test_context, auth_headers, make_test_settings


def schedule_body(meal_id: str, **overrides):
    return {
        "title": "每日午餐", "location": "測試取餐區", "meal_period": "lunch",
        "pickup_start_time": "11:30", "pickup_end_time": "12:30", "cutoff_time": "10:30",
        "advance_days": 3, "offerings": [{"meal_id": meal_id, "price": 100, "capacity": 20}],
        **overrides,
    }


@pytest.fixture
async def schedule_context(database_session):
    session = database_session
    admin = User(email="schedule-admin@example.com", display_name="排程管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="schedule-buyer@example.com", display_name="預訂買家", password_hash="test")
    meal = Meal(slug="scheduled-meal", name="每日餐", description="", price=100)
    session.add_all([admin, buyer, meal])
    await session.commit()
    async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
        yield client, session, admin, buyer, meal


@pytest.mark.parametrize("overrides", [
    {"weekdays": []}, {"weekdays": [0, 0]}, {"weekdays": [7]}, {"advance_days": 31},
    {"cutoff_days_before": 3}, {"pickup_start_time": "12:30"}, {"cutoff_time": "12:30"},
    {"pickup_start_time": "11:30:01"}, {"pickup_start_time": "11:30+08:00"},
])
def test_schedule_rejects_invalid_timeline_and_days(overrides):
    with pytest.raises(ValidationError):
        MealScheduleInput(**schedule_body("test-meal", **overrides))


@pytest.mark.asyncio
async def test_schedules_require_admin_and_default_to_disabled(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    body = schedule_body(meal.id)
    assert (await client.get("/v1/admin/meal-schedules", headers=auth_headers(buyer))).status_code == 403
    assert (await client.post("/v1/admin/meal-schedules", json=body, headers=auth_headers(buyer))).status_code == 403
    created = await client.post("/v1/admin/meal-schedules", json=body, headers=auth_headers(admin))
    assert created.status_code == 201, created.text
    template = created.json()
    assert template["enabled"] is False
    assert template["auto_publish"] is False
    assert template["timezone"] == "Asia/Taipei"
    assert await session.scalar(select(func.count(MealEvent.id))) == 0
    generated = await client.post(f"/v1/admin/meal-schedules/{template['id']}/generate", headers=auth_headers(admin))
    assert generated.status_code == 409
    assert await session.scalar(select(func.count(MealEvent.id))) == 0


@pytest.mark.asyncio
async def test_enabled_templates_create_drafts_until_explicit_auto_publish(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    created = await client.post("/v1/admin/meal-schedules", json=schedule_body(meal.id, enabled=True, weekdays=list(range(7))), headers=auth_headers(admin))
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    events = list(await session.scalars(select(MealEvent)))
    assert events and all(event.status == MealEventStatus.DRAFT for event in events)
    assert (await client.get("/v1/meal-events")).json() == []
    again = await client.post(f"/v1/admin/meal-schedules/{template_id}/generate", headers=auth_headers(admin))
    assert again.json() == {"created_count": 0, "created_event_ids": []}
    assert (await client.put(f"/v1/admin/meal-schedules/{template_id}", json=schedule_body(meal.id, enabled=True), headers=auth_headers(buyer))).status_code == 403
    updated = await client.put(f"/v1/admin/meal-schedules/{template_id}", json=schedule_body(meal.id, enabled=True, auto_publish=True, advance_days=4, weekdays=list(range(7))), headers=auth_headers(admin))
    assert updated.status_code == 200, updated.text
    await session.refresh(events[0])
    assert events[0].status == MealEventStatus.DRAFT
    public = (await client.get("/v1/meal-events")).json()
    assert len(public) == 1
    assert public[0]["status"] == "published"
    assert public[0]["meal_period"] == "lunch"
    assert public[0]["schedule_template_id"] == template_id
    disabled = await client.put(f"/v1/admin/meal-schedules/{template_id}", json=schedule_body(meal.id, enabled=False, auto_publish=True), headers=auth_headers(admin))
    assert disabled.status_code == 200
    assert len((await client.get("/v1/meal-events")).json()) == 1


@pytest.mark.asyncio
async def test_generation_uses_taipei_dates_cross_midnight_and_does_not_reopen_cancelled(schedule_context):
    client, session, admin, _buyer, meal = schedule_context
    response = await client.post("/v1/admin/meal-schedules", json=schedule_body(
        meal.id, meal_period="dinner", pickup_start_time="23:30", pickup_end_time="00:30", cutoff_time="22:30",
        weekdays=list(range(7)),
    ), headers=auth_headers(admin))
    template = await session.get(MealScheduleTemplate, response.json()["id"])
    template.enabled = True
    template.auto_publish = True
    await session.commit()
    now = datetime(2027, 1, 1, 16, 5, tzinfo=timezone.utc)
    created_ids = await generate_scheduled_meal_events(session, now)
    events = list(await session.scalars(select(MealEvent).order_by(MealEvent.pickup_starts_at)))
    assert len(created_ids) == 4
    assert events[0].service_date.isoformat() == "2027-01-02"
    assert events[0].pickup_starts_at.replace(tzinfo=timezone.utc).astimezone(TAIPEI).isoformat() == "2027-01-02T23:30:00+08:00"
    assert events[0].pickup_ends_at.replace(tzinfo=timezone.utc).astimezone(TAIPEI).isoformat() == "2027-01-03T00:30:00+08:00"
    events[0].status = MealEventStatus.CANCELLED
    await session.commit()
    assert await generate_scheduled_meal_events(session, now) == []
    await session.refresh(events[0])
    assert events[0].status == MealEventStatus.CANCELLED
    next_day = await generate_scheduled_meal_events(session, now + timedelta(days=1))
    assert len(next_day) == 1


@pytest.mark.asyncio
async def test_weekly_schedule_defaults_monday_to_saturday_and_never_drifts(schedule_context):
    client, session, admin, _buyer, meal = schedule_context
    response = await client.post("/v1/admin/meal-schedules", json=schedule_body(meal.id, advance_days=7), headers=auth_headers(admin))
    assert response.json()["weekdays"] == [0, 1, 2, 3, 4, 5]
    template = await session.get(MealScheduleTemplate, response.json()["id"])
    template.enabled = True
    await session.commit()
    monday = datetime(2027, 1, 4, 0, 0, tzinfo=timezone.utc)
    await generate_scheduled_meal_events(session, monday)
    await generate_scheduled_meal_events(session, monday + timedelta(days=7))
    events = list(await session.scalars(select(MealEvent)))
    assert len(events) == 13
    assert all(event.service_date.weekday() != 6 for event in events)
    assert {event.service_date.weekday() for event in events} == set(range(6))


@pytest.mark.asyncio
async def test_schedule_weekdays_cutoff_inactive_meals_and_reconcile_integration(schedule_context):
    client, session, admin, _buyer, meal = schedule_context
    response = await client.post("/v1/admin/meal-schedules", json=schedule_body(
        meal.id, weekdays=[0, 1], cutoff_days_before=1,
    ), headers=auth_headers(admin))
    template = await session.get(MealScheduleTemplate, response.json()["id"])
    template.enabled = True
    await session.commit()
    monday_after_cutoff = datetime(2027, 1, 4, 4, 0, tzinfo=timezone.utc)
    assert await generate_scheduled_meal_events(session, monday_after_cutoff) == []
    monday_before_cutoff = datetime(2027, 1, 4, 1, 0, tzinfo=timezone.utc)
    report = await reconcile_once(session, make_test_settings(), now=monday_before_cutoff)
    assert report.meal_events_created == 1
    event = await session.scalar(select(MealEvent))
    assert event.service_date.isoformat() == "2027-01-05"
    meal.is_active = False
    await session.commit()
    assert await generate_scheduled_meal_events(session, monday_before_cutoff + timedelta(days=7)) == []
    invalid = await client.post("/v1/admin/meal-schedules", json=schedule_body(meal.id, enabled=True), headers=auth_headers(admin))
    assert invalid.status_code == 422
    disabled = await client.put(f"/v1/admin/meal-schedules/{template.id}", json=schedule_body(meal.id, enabled=False), headers=auth_headers(admin))
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    unknown = await client.put(f"/v1/admin/meal-schedules/{template.id}", json=schedule_body("missing-meal", enabled=False), headers=auth_headers(admin))
    assert unknown.status_code == 422


@pytest.fixture
async def preorder_context(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    now = datetime.now(timezone.utc)
    event = MealEvent(
        title="可提前預訂", location="取餐區", ordering_starts_at=now - timedelta(days=1),
        ordering_ends_at=now + timedelta(days=2), pickup_starts_at=now + timedelta(days=2, hours=1),
        pickup_ends_at=now + timedelta(days=2, hours=2), status=MealEventStatus.PUBLISHED,
        created_by_id=admin.id, offerings=[MealEventOffering(meal_id=meal.id, price=100, capacity=10)],
    )
    session.add(event)
    await session.commit()
    body = {"items": [{"offering_id": event.offerings[0].id, "quantity": 1}], "contact_email": buyer.email}
    yield client, session, admin, buyer, event, body


@pytest.mark.asyncio
async def test_selected_pickup_time_is_saved_and_visible_to_buyer_and_admin(preorder_context):
    client, session, admin, buyer, event, body = preorder_context
    selected = event.pickup_starts_at + timedelta(minutes=20)
    body["pickup_at"] = selected.isoformat()
    quote = await client.post(f"/v1/meal-events/{event.id}/quote", json=body)
    assert quote.status_code == 200, quote.text
    assert datetime.fromisoformat(quote.json()["pickup_at"].replace("Z", "+00:00")) == selected
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    order = await session.get(Order, created.json()["id"])
    assert order.fulfillment.pickup_at.replace(tzinfo=timezone.utc) == selected
    detail = await client.get(f"/v1/meal-orders/{order.id}", headers=auth_headers(buyer))
    assert detail.json()["pickup_at"] == quote.json()["pickup_at"]
    admin_orders = await client.get(f"/v1/admin/meal-events/{event.id}/orders", headers=auth_headers(admin))
    assert admin_orders.json()[0]["pickup_at"] == quote.json()["pickup_at"]
    assert (await client.get(f"/v1/admin/meal-events/{event.id}/orders", headers=auth_headers(buyer))).status_code == 403


@pytest.mark.asyncio
async def test_legacy_client_defaults_to_pickup_start(preorder_context):
    client, _session, _admin, buyer, event, body = preorder_context
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201
    assert created.json()["pickup_at"] == created.json()["pickup_start"]


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["past", "before_start", "at_end", "after_end", "no_timezone"])
async def test_invalid_pickup_times_are_rejected_by_quote_and_order(preorder_context, marker):
    client, session, _admin, buyer, event, body = preorder_context
    selected = {
        "past": datetime.now(timezone.utc) - timedelta(minutes=1),
        "before_start": event.pickup_starts_at - timedelta(minutes=1),
        "at_end": event.pickup_ends_at,
        "after_end": event.pickup_ends_at + timedelta(minutes=1),
        "no_timezone": event.pickup_starts_at.replace(tzinfo=None),
    }[marker]
    body["pickup_at"] = selected.isoformat()
    for action in ("quote", "orders"):
        result = await client.post(f"/v1/meal-events/{event.id}/{action}", json=body, headers=auth_headers(buyer))
        assert result.status_code == 422, result.text
    assert await session.scalar(select(func.count(Order.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["closed", "cutoff", "sold_out", "not_open"])
async def test_pickup_choice_cannot_bypass_ordering_rules(preorder_context, marker):
    client, session, _admin, buyer, event, body = preorder_context
    body["pickup_at"] = event.pickup_starts_at.isoformat()
    if marker == "closed":
        event.status = MealEventStatus.ORDERING_CLOSED
    elif marker == "cutoff":
        event.ordering_ends_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    elif marker == "sold_out":
        event.offerings[0].paid_quantity = event.offerings[0].capacity
    else:
        event.ordering_starts_at = datetime.now(timezone.utc) + timedelta(hours=1)
    await session.commit()
    for action in ("quote", "orders"):
        response = await client.post(f"/v1/meal-events/{event.id}/{action}", json=body, headers=auth_headers(buyer))
        assert response.status_code == 409, response.text


def event_update_body(event, **overrides):
    return {
        "title": event.title, "location": event.location,
        "ordering_starts_at": event.ordering_starts_at.isoformat(),
        "ordering_ends_at": event.ordering_ends_at.isoformat(),
        "pickup_starts_at": event.pickup_starts_at.isoformat(),
        "pickup_ends_at": event.pickup_ends_at.isoformat(),
        "offerings": [{"meal_id": offering.meal_id, "price": offering.price, "capacity": offering.capacity} for offering in event.offerings],
        **overrides,
    }


@pytest.mark.asyncio
async def test_admin_can_update_unsold_published_event_but_buyer_cannot(preorder_context):
    client, session, admin, buyer, event, _body = preorder_context
    update = event_update_body(event, location="更新取餐區", title="手動菜單")
    update["offerings"][0].update(price=120, capacity=30)
    denied = await client.put(f"/v1/admin/meal-events/{event.id}", json=update, headers=auth_headers(buyer))
    assert denied.status_code == 403
    updated = await client.put(f"/v1/admin/meal-events/{event.id}", json=update, headers=auth_headers(admin))
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == event.id
    assert updated.json()["location"] == "更新取餐區"
    assert updated.json()["status"] == "published"
    assert updated.json()["can_edit"] is True
    assert updated.json()["offerings"][0]["price"] == 120
    assert updated.json()["offerings"][0]["capacity"] == 30
    assert await session.scalar(select(func.count(MealEvent.id))) == 1
    assert (await client.get(f"/v1/meal-events/{event.id}")).json()["can_edit"] is False


@pytest.mark.asyncio
async def test_any_existing_order_prevents_event_update(preorder_context):
    client, _session, admin, buyer, event, body = preorder_context
    update = event_update_body(event, location="不得覆寫")
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=body, headers=auth_headers(buyer))
    assert created.status_code == 201
    for cancel_first in (False, True):
        if cancel_first:
            cancel = await client.post(f"/v1/meal-orders/{created.json()['id']}/cancel", json={"reason": "測試取消"}, headers=auth_headers(buyer))
            assert cancel.status_code == 200
        response = await client.put(f"/v1/admin/meal-events/{event.id}", json=update, headers=auth_headers(admin))
        assert response.status_code == 409
    detail = await client.get(f"/v1/meal-events/{event.id}")
    assert detail.json()["location"] == event.location
    admin_events = await client.get("/v1/admin/meal-events", headers=auth_headers(admin))
    assert admin_events.json()[0]["can_edit"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [MealEventStatus.CANCELLED, MealEventStatus.COMPLETED, MealEventStatus.PICKUP_OPEN, MealEventStatus.ORDERING_CLOSED])
async def test_closed_event_cannot_be_manually_edited(preorder_context, status):
    client, session, admin, _buyer, event, _body = preorder_context
    event.status = status
    await session.commit()
    response = await client.put(f"/v1/admin/meal-events/{event.id}", json=event_update_body(event), headers=auth_headers(admin))
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_expired_published_event_cannot_be_reopened_by_extending_cutoff(preorder_context):
    client, session, admin, _buyer, event, _body = preorder_context
    event.ordering_ends_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()
    listed = await client.get("/v1/admin/meal-events", headers=auth_headers(admin))
    assert listed.json()[0]["status"] == "published"
    assert listed.json()[0]["can_edit"] is False
    unchanged = event_update_body(event, title="不得變更名稱")
    extended = event_update_body(event, ordering_ends_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    for update in (unchanged, extended):
        result = await client.put(f"/v1/admin/meal-events/{event.id}", json=update, headers=auth_headers(admin))
        assert result.status_code == 409
    await session.refresh(event)
    assert event.ordering_ends_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_expired_draft_can_be_rescheduled_to_future(preorder_context):
    client, session, admin, _buyer, event, _body = preorder_context
    now = datetime.now(timezone.utc)
    event.status = MealEventStatus.DRAFT
    event.ordering_starts_at = now - timedelta(hours=4)
    event.ordering_ends_at = now - timedelta(hours=3)
    event.pickup_starts_at = now - timedelta(hours=2)
    event.pickup_ends_at = now - timedelta(hours=1)
    await session.commit()
    listed = await client.get("/v1/admin/meal-events", headers=auth_headers(admin))
    assert listed.json()[0]["can_edit"] is True
    update = event_update_body(
        event,
        ordering_starts_at=now.isoformat(),
        ordering_ends_at=(now + timedelta(hours=1)).isoformat(),
        pickup_starts_at=(now + timedelta(hours=2)).isoformat(),
        pickup_ends_at=(now + timedelta(hours=3)).isoformat(),
    )
    result = await client.put(f"/v1/admin/meal-events/{event.id}", json=update, headers=auth_headers(admin))
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "draft"
    assert result.json()["can_edit"] is True


@pytest.mark.asyncio
async def test_generated_event_manual_changes_survive_schedule_and_cannot_move_date(schedule_context):
    client, session, admin, _buyer, meal = schedule_context
    response = await client.post("/v1/admin/meal-schedules", json=schedule_body(meal.id, enabled=True), headers=auth_headers(admin))
    template_id = response.json()["id"]
    listed = (await client.get("/v1/admin/meal-events", headers=auth_headers(admin))).json()
    event = await session.scalar(select(MealEvent).where(MealEvent.id == listed[-1]["id"]).options(selectinload(MealEvent.offerings)))
    update = event_update_body(event, title="單日臨時菜單", location="臨時取餐點")
    update["offerings"][0]["price"] = 180
    edited = await client.put(f"/v1/admin/meal-events/{event.id}", json=update, headers=auth_headers(admin))
    assert edited.status_code == 200, edited.text
    assert edited.json()["schedule_template_id"] == template_id
    assert (await client.post(f"/v1/admin/meal-schedules/{template_id}/generate", headers=auth_headers(admin))).json()["created_count"] == 0
    listed_after = (await client.get("/v1/admin/meal-events", headers=auth_headers(admin))).json()
    retained = next(item for item in listed_after if item["id"] == event.id)
    assert retained["title"] == "單日臨時菜單"
    assert retained["offerings"][0]["price"] == 180
    for name in ("ordering_starts_at", "ordering_ends_at", "pickup_starts_at", "pickup_ends_at"):
        update[name] = (datetime.fromisoformat(update[name]) + timedelta(days=1)).isoformat()
    moved = await client.put(f"/v1/admin/meal-events/{event.id}", json=update, headers=auth_headers(admin))
    assert moved.status_code == 422
