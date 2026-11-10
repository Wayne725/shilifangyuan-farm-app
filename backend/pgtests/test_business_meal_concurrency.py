import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import time_machine
from sqlalchemy import func, select

from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.models import (InventoryReservation, Meal, MealEvent, MealEventOffering, MealEventStatus,
    Notification, Order, PaymentAttempt, PaymentStatus, Refund, ReservationStatus, User, UserRole)
from app.routers.meals import meals_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, tomorrow_meal_timeline
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


pytestmark = pytest.mark.skipif(not os.environ.get('TEST_POSTGRES_URL'), reason='Disposable local PostgreSQL required')
BOOKING = datetime(2030, 1, 1, 11, tzinfo=timezone.utc)
PAYMENT = datetime(2030, 1, 2, 1, tzinfo=timezone.utc)


@pytest.fixture
async def business_pg(postgres_sessions):
    async with postgres_sessions() as session:
        admin = User(email='business-pg-admin@example.com', display_name='隔離管理員', password_hash='test', user_role=UserRole.ADMIN)
        buyers = [User(email=f'business-pg-{i}@example.com', display_name=f'隔離訂餐人{i}', password_hash='test') for i in range(2)]
        meal = Meal(slug='business-pg-meal', name='隔離會餐', price=100)
        event = MealEvent(title='隔離會餐最後三份', location='隔離取餐點', created_by=admin,
            **tomorrow_meal_timeline(BOOKING), status=MealEventStatus.PUBLISHED,
            offerings=[MealEventOffering(meal=meal, price=100, capacity=3)])
        session.add_all([event, *buyers])
        await session.commit()
        offering_id, event_id = event.offerings[0].id, event.id
    async def create(buyer, request_id=None):
        async with postgres_sessions() as session:
            async with api_test_context(session, [meals_router], settings=payment_settings()) as client:
                body = {'meal_booking_type': 'business', 'invoice_buyer_type': 'company',
                    'invoice_buyer_tax_id': '15989995', 'invoice_buyer_name': '合成學校',
                    'invoice_buyer_email': buyer.email, 'contact_email': buyer.email,
                    'items': [{'offering_id': offering_id, 'quantity': 2}]}
                if request_id:
                    body['client_request_id'] = request_id
                return await client.post(f'/v1/meal-events/{event_id}/orders', json=body, headers=auth_headers(buyer))
    return SimpleNamespace(sessions=postgres_sessions, buyers=buyers, offering_id=offering_id, create=create)


async def together(*operations):
    start = asyncio.Event()
    async def run(operation):
        await start.wait()
        return await operation
    tasks = [asyncio.create_task(run(operation)) for operation in operations]
    start.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)


async def test_business_last_portions_are_reserved_once_without_overselling(business_pg):
    c = business_pg
    with time_machine.travel(BOOKING, tick=False):
        responses = await together(*(c.create(buyer) for buyer in c.buyers))
    assert sorted(response.status_code for response in responses) == [201, 409], [response.text for response in responses]
    async with c.sessions() as session:
        offering = await session.get(MealEventOffering, c.offering_id)
        assert offering.reserved_quantity == 2 and offering.paid_quantity == 0
        assert await session.scalar(select(func.count(Order.id))) == 1
        reservations = list(await session.scalars(select(InventoryReservation)))
        assert len(reservations) == 1 and reservations[0].quantity == 2
        assert reservations[0].payment_attempt_id is None


async def test_replayed_business_booking_and_concurrent_payment_preserve_one_hold(business_pg, fake_payment):
    c = business_pg
    request_id = str(uuid4())
    with time_machine.travel(BOOKING, tick=False):
        responses = await together(c.create(c.buyers[0], request_id), c.create(c.buyers[0], request_id))
    assert all(response.status_code in {200, 201} for response in responses), [response.text for response in responses]
    assert responses[0].json()['id'] == responses[1].json()['id']
    order_id = responses[0].json()['id']
    async def pay():
        async with c.sessions() as session:
            return await create_payment_attempt(session, order_id, c.buyers[0], payment_settings(), now=PAYMENT)
    with time_machine.travel(PAYMENT, tick=False):
        attempts = await together(pay(), pay())
        assert attempts[0].id == attempts[1].id
        payload = successful_callback(attempts[0], PAYMENT, 'BUSINESS-PG-PAID')
        async def callback(key):
            async with c.sessions() as session:
                return await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(key, payload)
        results = await together(callback('business-pg-first'), callback('business-pg-repeated'))
    assert all(result in {'paid', 'duplicate'} for result in results)
    async with c.sessions() as session:
        offering = await session.get(MealEventOffering, c.offering_id)
        assert offering.reserved_quantity == 0 and offering.paid_quantity == 2
        assert await session.scalar(select(func.count(Order.id))) == 1
        assert await session.scalar(select(func.count(PaymentAttempt.id))) == 1
        assert await session.scalar(select(func.count(Refund.id))) == 0
        reservations = list(await session.scalars(select(InventoryReservation)))
        assert len(reservations) == 1 and reservations[0].status == ReservationStatus.CONSUMED


async def test_concurrent_business_reminder_workers_persist_one_email_intent(business_pg):
    from app.business_meal_reminders import enqueue_business_payment_reminders
    c = business_pg
    with time_machine.travel(BOOKING, tick=False):
        assert (await c.create(c.buyers[0])).status_code == 201
    async def remind():
        async with c.sessions() as session:
            await enqueue_business_payment_reminders(session, payment_settings(), PAYMENT)
            await session.commit()
    await together(remind(), remind())
    async with c.sessions() as session:
        from app.models import OutboxEvent
        assert await session.scalar(select(func.count(Notification.id)).where(Notification.event_type == 'meal.business_payment_reminder')) == 1
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == 'send_email')) == 1
