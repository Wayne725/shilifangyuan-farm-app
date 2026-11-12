import asyncio
import os
from datetime import timedelta

import pytest
import time_machine
from sqlalchemy import func, select, text

from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.models import AdminAudit, MealEventOffering, Order, PaymentStatus
from app.routers.meals import meals_router
from pgtests.test_business_meal_concurrency import together
from pgtests.test_meal_ordering_pause_concurrency import BOOKING, ordering_pause_context
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Disposable local PostgreSQL required")


async def change_capacity(sessions, admin, event_id, offering_id, capacity, *, backend_pid=None):
    async with sessions() as session:
        if backend_pid is not None:
            backend_pid.set_result(await session.scalar(text("SELECT pg_backend_pid()")))
        async with api_test_context(session, [meals_router], settings=payment_settings()) as client:
            return await client.patch(
                f"/v1/admin/meal-events/{event_id}/offerings/{offering_id}/capacity",
                json={"expected_capacity": 4, "capacity": capacity},
                headers=auth_headers(admin),
            )


async def place_order(session, buyer, event_id, offering_id):
    async with api_test_context(session, [meals_router], settings=payment_settings()) as client:
        created = await client.post(
            f"/v1/meal-events/{event_id}/orders",
            json={"contact_phone": "0912345678", "contact_email": buyer.email, "items": [{"offering_id": offering_id, "quantity": 2}]},
            headers=auth_headers(buyer),
        )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_only_one_simultaneous_capacity_increase_wins(postgres_sessions, ordering_pause_context):
    admin, buyer, event_id, offering_id = ordering_pause_context
    with time_machine.travel(BOOKING, tick=False):
        async with postgres_sessions() as session:
            await place_order(session, buyer, event_id, offering_id)
        results = await together(
            change_capacity(postgres_sessions, admin, event_id, offering_id, 7),
            change_capacity(postgres_sessions, admin, event_id, offering_id, 9),
        )
    assert sorted(response.status_code for response in results) == [200, 409], [response.text for response in results]
    winner = next(response.json() for response in results if response.status_code == 200)
    capacity = winner["offerings"][0]["capacity"]
    assert capacity in {7, 9}
    async with postgres_sessions() as session:
        offering = await session.get(MealEventOffering, offering_id)
        assert offering.capacity == capacity and offering.price == 100
        assert await session.scalar(select(func.count(AdminAudit.id)).where(
            AdminAudit.aggregate_id == event_id,
            AdminAudit.action == "meal_event.offering_capacity_increased",
        )) == 1


async def test_capacity_increase_and_payment_keep_original_order_and_counters(
    postgres_sessions, ordering_pause_context, fake_payment,
):
    admin, buyer, event_id, offering_id = ordering_pause_context
    with time_machine.travel(BOOKING, tick=False):
        async with postgres_sessions() as session:
            order_id = await place_order(session, buyer, event_id, offering_id)
            attempt = await create_payment_attempt(session, order_id, buyer, payment_settings(), now=BOOKING)
            payload = successful_callback(attempt, BOOKING, "CAPACITY-PG-PAID")
            offering = await session.get(MealEventOffering, offering_id)
            assert (offering.capacity, offering.reserved_quantity, offering.paid_quantity) == (4, 2, 0)

        async def confirm():
            async with postgres_sessions() as session:
                return await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
                    "capacity-pg-payment", payload,
                )

        increased, result = await together(
            change_capacity(postgres_sessions, admin, event_id, offering_id, 7),
            confirm(),
        )
        assert increased.status_code == 200, increased.text
        assert result == "paid"

    async with postgres_sessions() as session:
        offering = await session.get(MealEventOffering, offering_id)
        assert (offering.capacity, offering.reserved_quantity, offering.paid_quantity) == (7, 0, 2)
        order = await session.get(Order, order_id)
        assert order.payment_status == PaymentStatus.PAID and order.amount_total == 200


async def test_capacity_increase_waiting_for_offering_lock_cannot_cross_cutoff(
    postgres_sessions, ordering_pause_context,
):
    admin, buyer, event_id, offering_id = ordering_pause_context
    with time_machine.travel(BOOKING, tick=False) as clock:
        async with postgres_sessions() as session:
            await place_order(session, buyer, event_id, offering_id)
        async with postgres_sessions() as blocker:
            await blocker.scalar(select(MealEventOffering).where(
                MealEventOffering.id == offering_id,
            ).with_for_update())
            backend_pid = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(change_capacity(
                postgres_sessions, admin, event_id, offering_id, 7, backend_pid=backend_pid,
            ))

            async def wait_for_blocked_update():
                pid = await backend_pid
                while not await blocker.scalar(text(
                    "SELECT cardinality(pg_blocking_pids(:pid)) > 0"
                ), {"pid": pid}):
                    await asyncio.sleep(0.02)

            try:
                await asyncio.wait_for(wait_for_blocked_update(), timeout=10)
                clock.move_to(BOOKING + timedelta(hours=1))
            finally:
                await blocker.rollback()
                result = await asyncio.wait_for(task, timeout=10)

    assert result.status_code == 409 and "時段" in result.json()["detail"], result.text
    async with postgres_sessions() as session:
        offering = await session.get(MealEventOffering, offering_id)
        assert offering.capacity == 4
