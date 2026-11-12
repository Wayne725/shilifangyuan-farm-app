import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
import time_machine
from sqlalchemy import func, select, text

from app.models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealScheduleTemplate, Order, User, UserRole
from app.routers import meals
from pgtests.test_amendment_fulfillment_concurrency import wait_for_lock_waiter
from pgtests.test_business_meal_concurrency import together
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings, tomorrow_meal_timeline


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Disposable local PostgreSQL required")
BOOKING = datetime(2030, 1, 1, 11, tzinfo=timezone.utc)


@pytest.fixture
async def ordering_pause_context(postgres_sessions):
    async with postgres_sessions() as session:
        admin = User(email="pause-admin@example.test", display_name="隔離管理員", password_hash="test", user_role=UserRole.ADMIN)
        buyer = User(email="pause-buyer@example.com", display_name="隔離訂餐人", password_hash="test")
        meal = Meal(slug="ordering-pause-meal", name="隔離便當", price=100)
        timing = {**tomorrow_meal_timeline(BOOKING), "ordering_ends_at": BOOKING + timedelta(hours=1)}
        event = MealEvent(
            title="暫停接單驗收", location="隔離取餐點", booking_policy="special", created_by=admin,
            status=MealEventStatus.PUBLISHED, **timing,
            offerings=[MealEventOffering(meal=meal, price=100, capacity=4)],
        )
        session.add_all([event, buyer])
        await session.commit()
    return admin, buyer, event.id, event.offerings[0].id


async def set_pause(sessions, admin, event_id, paused, expected_version):
    async with sessions() as session:
        async with api_test_context(session, [meals.meals_router], settings=make_test_settings()) as client:
            return await client.patch(
                f"/v1/admin/meal-events/{event_id}/ordering-pause",
                json={"paused": paused, "expected_version": expected_version},
                headers=auth_headers(admin),
            )


async def test_simultaneous_pause_keeps_one_audit_and_rejects_stale_version(postgres_sessions, ordering_pause_context):
    admin, _buyer, event_id, _offering_id = ordering_pause_context
    with time_machine.travel(BOOKING, tick=False):
        attempts = await together(*(set_pause(postgres_sessions, admin, event_id, True, 0) for _ in range(2)))
        assert sorted(response.status_code for response in attempts) == [200, 409], [response.text for response in attempts]
        retry = await set_pause(postgres_sessions, admin, event_id, True, 1)
        assert retry.status_code == 200 and retry.json()["ordering_pause_version"] == 1
        resume = await set_pause(postgres_sessions, admin, event_id, False, 1)
        assert resume.status_code == 200 and resume.json()["ordering_pause_version"] == 2
        stale = await set_pause(postgres_sessions, admin, event_id, True, 0)
        assert stale.status_code == 409, stale.text
    async with postgres_sessions() as session:
        event = await session.get(MealEvent, event_id)
        assert event.ordering_paused_at is None and event.ordering_pause_version == 2
        assert await session.scalar(select(func.count(AdminAudit.id)).where(
            AdminAudit.aggregate_id == event_id, AdminAudit.action == "meal_event.ordering_pause",
        )) == 2


async def test_order_uses_current_pause_state_after_lock_with_cached_event(
    postgres_sessions, ordering_pause_context, monkeypatch,
):
    admin, buyer, event_id, offering_id = ordering_pause_context
    warmed, proceed = asyncio.Event(), asyncio.Event()
    original = meals._load_event

    async def load(session, requested_id, **options):
        if options.get("for_update") and session.info.get("buyer_order"):
            cached = await session.get(MealEvent, event_id)
            assert cached.ordering_paused_at is None
            session.info["cached_event"] = cached
            warmed.set()
            await proceed.wait()
        return await original(session, requested_id, **options)

    monkeypatch.setattr(meals, "_load_event", load)

    async def create():
        async with postgres_sessions() as session:
            session.info["buyer_order"] = True
            async with api_test_context(session, [meals.meals_router], settings=make_test_settings()) as client:
                return await client.post(
                    f"/v1/meal-events/{event_id}/orders",
                    json={"contact_email": buyer.email, "items": [{"offering_id": offering_id, "quantity": 1}]},
                    headers=auth_headers(buyer),
                )

    with time_machine.travel(BOOKING, tick=False):
        task = asyncio.create_task(create())
        try:
            await asyncio.wait_for(warmed.wait(), timeout=10)
            pause = await set_pause(postgres_sessions, admin, event_id, True, 0)
            assert pause.status_code == 200, pause.text
        finally:
            proceed.set()
            result = await asyncio.wait_for(task, timeout=10)
    assert result.status_code == 409 and "暫停" in result.json()["detail"], result.text
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Order.id)).where(Order.meal_event_id == event_id)) == 0
        offering = await session.get(MealEventOffering, offering_id)
        assert offering.reserved_quantity == offering.paid_quantity == 0


async def test_schedule_disable_rechecks_order_count_after_waiting_for_checkout(
    postgres_sessions, ordering_pause_context, monkeypatch,
):
    admin, buyer, event_id, offering_id = ordering_pause_context
    async with postgres_sessions() as session:
        offering = await session.get(MealEventOffering, offering_id)
        template = MealScheduleTemplate(
            title="隔離每日排程", location="隔離取餐點", meal_period="lunch",
            pickup_start_time="11:00", pickup_end_time="13:00", cutoff_time="20:00",
            weekdays=[0, 1, 2, 3, 4], enabled=True, auto_publish=True,
            offerings=[{"meal_id": offering.meal_id, "price": 100, "capacity": 4}],
            created_by_id=admin.id,
        )
        session.add(template)
        await session.flush()
        event = await session.get(MealEvent, event_id)
        event.schedule_template_id = template.id
        event.allow_cash_payment = True
        await session.commit()
        template_id = template.id

    admin_path = f"/v1/admin/meal-schedules/{template_id}"
    order_path = f"/v1/meal-events/{event_id}/orders"
    order_body = {
        "contact_email": buyer.email, "payment_method": "cash_on_pickup",
        "items": [{"offering_id": offering_id, "quantity": 1}],
    }
    ready = asyncio.Event()
    release = asyncio.Event()
    disable_connected = asyncio.Event()
    blocker_pid = None

    async def place_order(*, hold_commit=False):
        nonlocal blocker_pid
        async with postgres_sessions() as session:
            if hold_commit:
                commit = session.commit

                async def controlled_commit():
                    nonlocal blocker_pid
                    blocker_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                    ready.set()
                    await release.wait()
                    await commit()

                monkeypatch.setattr(session, "commit", controlled_commit)
            async with api_test_context(session, [meals.meals_router], settings=make_test_settings()) as client:
                return await client.post(order_path, json=order_body, headers=auth_headers(buyer))

    async def get_preview():
        async with postgres_sessions() as session:
            async with api_test_context(session, [meals.meals_router], settings=make_test_settings()) as client:
                response = await client.get(f"{admin_path}/disable-preview", headers=auth_headers(admin))
                assert response.status_code == 200, response.text
                return response.json()

    async def disable(preview, *, observe_connection=False):
        async with postgres_sessions() as session:
            if observe_connection:
                await session.scalar(text("SELECT pg_backend_pid()"))
                disable_connected.set()
            async with api_test_context(session, [meals.meals_router], settings=make_test_settings()) as client:
                return await client.post(f"{admin_path}/disable", json={
                    "scope": "pause_generated", "preview_version": preview["preview_version"],
                }, headers=auth_headers(admin))

    with time_machine.travel(BOOKING, tick=False):
        initial = await get_preview()
        assert [(row["id"], row["order_count"], row["ordering_status"]) for row in initial["events"]] == [(event_id, 0, "open")]
        checkout_task = asyncio.create_task(place_order(hold_commit=True))
        disable_task = None
        try:
            await asyncio.wait_for(ready.wait(), timeout=10)
            disable_task = asyncio.create_task(disable(initial, observe_connection=True))
            await asyncio.wait_for(disable_connected.wait(), timeout=10)
            await wait_for_lock_waiter(postgres_sessions, blocker_pid)
            assert not disable_task.done()
        finally:
            release.set()
            ordered = await asyncio.wait_for(checkout_task, timeout=10)
            if disable_task is not None:
                refused = await asyncio.wait_for(disable_task, timeout=10)
        assert ordered.status_code == 201 and refused.status_code == 409, (ordered.text, refused.text)
        fresh = await get_preview()
        assert fresh["events"][0]["order_count"] == 1
        changed = await disable(fresh)
        assert changed.status_code == 200 and changed.json() == {"paused_event_count": 1}, changed.text
        blocked = await place_order()
        assert blocked.status_code == 409 and "暫停" in blocked.json()["detail"], blocked.text

    async with postgres_sessions() as session:
        template = await session.get(MealScheduleTemplate, template_id)
        event = await session.get(MealEvent, event_id)
        order = await session.get(Order, ordered.json()["id"])
        offering = await session.get(MealEventOffering, offering_id)
        assert template.enabled is False and template.auto_publish is True
        assert event.ordering_pause_version == 1 and event.ordering_paused_at is not None
        assert order.cancelled_at is None and order.amount_total == 100 and offering.reserved_quantity == 1
        assert await session.scalar(select(func.count(Order.id)).where(Order.meal_event_id == event_id)) == 1
