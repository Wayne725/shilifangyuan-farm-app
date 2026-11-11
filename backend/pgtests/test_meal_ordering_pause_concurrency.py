import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
import time_machine
from sqlalchemy import func, select

from app.models import AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, Order, User, UserRole
from app.routers import meals
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
