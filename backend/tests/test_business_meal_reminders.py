from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.business_meal_reminders import PAYMENT_REMINDER, enqueue_business_payment_reminders, queued_reminder_is_current
from app.integrations.email_sender import EmailSendResult
from app.models import (FulfillmentMethod, FulfillmentState, FulfillmentStatus, InvoiceBuyerType,
    MealEvent, MealEventStatus, MembershipType, Notification, Order, OrderFulfillment,
    OrderItem, OrderKind, OutboxEvent, OutboxStatus, PaymentStatus, SalesChannel, TaxType, User)
from tests.support import make_test_settings


OPEN = datetime(2030, 1, 1, 16, tzinfo=timezone.utc)
MORNING = OPEN + timedelta(hours=8)
CLOSE = OPEN + timedelta(days=1)


async def seed_order(session, *, booking_type='business', payment_status=PaymentStatus.PENDING):
    marker = uuid4().hex[:12]
    buyer = User(email=f'{marker}@example.com', display_name='隔離訂餐人', password_hash='test')
    event = MealEvent(title='隔離會餐', location='隔離取餐點', created_by=buyer,
        ordering_starts_at=OPEN - timedelta(days=1), ordering_ends_at=OPEN - timedelta(hours=4),
        pickup_starts_at=OPEN + timedelta(hours=11), pickup_ends_at=OPEN + timedelta(hours=13, minutes=30),
        status=MealEventStatus.PUBLISHED)
    order = Order(order_number=f'M{marker}', user=buyer, order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        meal_booking_type=booking_type, meal_event=event, membership_type_snapshot=MembershipType.NONMEMBER,
        contact_email=buyer.email, amount_total=240, tax_amount=11, payment_status=payment_status,
        invoice_buyer_type=InvoiceBuyerType.COMPANY, invoice_buyer_tax_id='15989995',
        invoice_buyer_name='合成學校', invoice_buyer_email=buyer.email,
        invoice_handling='pickup_day', invoice_issue_after=OPEN,
        fulfillment=OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP,
            status=FulfillmentState.PENDING_CONFIRMATION, pickup_at=event.pickup_starts_at,
            pickup_starts_at=event.pickup_starts_at, pickup_ends_at=event.pickup_ends_at),
        items=[OrderItem(product_name='合成餐點', quantity=2, unit_label='份', unit_price=120, subtotal=240, tax_type=TaxType.TAXABLE)])
    session.add(order)
    await session.commit()
    return order


async def counts(session):
    return (await session.scalar(select(func.count(Notification.id))),
        await session.scalar(select(func.count(OutboxEvent.id))))


async def test_payment_reminder_queues_once_at_taipei_0800_with_original_order_link(database_session):
    session = database_session
    order = await seed_order(session)
    settings = make_test_settings()
    assert await enqueue_business_payment_reminders(session, settings, MORNING - timedelta(seconds=1)) == 0
    assert await enqueue_business_payment_reminders(session, settings, MORNING) == 1
    await session.commit()
    assert await enqueue_business_payment_reminders(session, settings, MORNING + timedelta(minutes=1)) == 0
    assert await counts(session) == (1, 1)
    notification = await session.scalar(select(Notification))
    event = await session.scalar(select(OutboxEvent))
    assert notification.event_type == PAYMENT_REMINDER
    assert notification.data['url'] == f'{settings.web_base_url.rstrip("/")}/meal-orders?order_id={order.id}'
    assert notification.data['url'] in event.payload['text_content']
    assert event.payload['to_email'] == order.contact_email
    assert event.payload['event_type'] == PAYMENT_REMINDER
    assert event.notification_id == notification.id


@pytest.mark.parametrize('clock', [OPEN - timedelta(hours=8), OPEN, CLOSE, CLOSE + timedelta(hours=8)])
async def test_reminders_are_not_sent_before_morning_or_on_another_date(database_session, clock):
    await seed_order(database_session)
    assert await enqueue_business_payment_reminders(database_session, make_test_settings(), clock) == 0
    assert await counts(database_session) == (0, 0)


@pytest.mark.parametrize('status', [PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED])
async def test_paid_or_reversed_business_orders_are_not_reminded(database_session, status):
    await seed_order(database_session, payment_status=status)
    assert await enqueue_business_payment_reminders(database_session, make_test_settings(), MORNING) == 0
    assert await counts(database_session) == (0, 0)


async def test_standard_and_cancelled_orders_are_not_reminded(database_session):
    await seed_order(database_session, booking_type='standard')
    cancelled = await seed_order(database_session)
    cancelled.cancelled_at = MORNING
    cancelled.fulfillment_status = FulfillmentStatus.CANCELLED
    await database_session.commit()
    assert await enqueue_business_payment_reminders(database_session, make_test_settings(), MORNING) == 0
    assert await counts(database_session) == (0, 0)


async def test_bounded_batches_progress_past_already_reminded_orders(database_session):
    for _ in range(3):
        await seed_order(database_session)
    for _ in range(3):
        assert await enqueue_business_payment_reminders(database_session, make_test_settings(), MORNING, limit=1) == 1
        await database_session.commit()
    assert await counts(database_session) == (3, 3)
    assert await enqueue_business_payment_reminders(database_session, make_test_settings(), MORNING, limit=1) == 0


async def test_rollback_removes_reminder_and_email_intent_together(database_session):
    await seed_order(database_session)
    await enqueue_business_payment_reminders(database_session, make_test_settings(), MORNING)
    await database_session.rollback()
    assert await counts(database_session) == (0, 0)


async def test_meal_reconciliation_enqueues_reminder_and_worker_delivers_once(database_session, monkeypatch):
    import app.jobs as jobs
    session = database_session
    await seed_order(session)
    settings = make_test_settings()
    await jobs._reconcile_meal_operations(session, settings, MORNING, 100)
    await jobs._reconcile_meal_operations(session, settings, MORNING, 100)
    assert await counts(session) == (1, 1)
    sends = []
    class Sender:
        async def send(self, message):
            sends.append(message)
            return EmailSendResult(accepted=True, provider_message_id='synthetic', status_code=200, provider='fake')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _settings: Sender())
    assert await jobs._process_outbox(session, settings, MORNING, 100) == (1, 0)
    assert await jobs._process_outbox(session, settings, MORNING + timedelta(minutes=1), 100) == (0, 0)
    assert len(sends) == 1
    event = await session.scalar(select(OutboxEvent))
    assert event.payload['email_delivery']['state'] == 'accepted'


@pytest.mark.parametrize('change', ['paid', 'cancelled', 'next_day'])
async def test_stale_queued_reminder_is_suppressed_before_email_delivery(database_session, monkeypatch, change):
    import app.jobs as jobs
    session = database_session
    order = await seed_order(session)
    settings = make_test_settings()
    await enqueue_business_payment_reminders(session, settings, MORNING)
    await session.commit()
    if change == 'paid':
        order.payment_status = PaymentStatus.PAID
        order.paid_at = MORNING
    elif change == 'cancelled':
        order.cancelled_at = MORNING
    await session.commit()
    now = CLOSE + timedelta(hours=8) if change == 'next_day' else MORNING
    event = await session.scalar(select(OutboxEvent))
    assert not await queued_reminder_is_current(session, event.payload, now)
    sends = []
    class Sender:
        async def send(self, message):
            sends.append(message)
            return EmailSendResult(accepted=True, provider_message_id='synthetic', status_code=200, provider='fake')
    monkeypatch.setattr(jobs, 'email_sender_from_settings', lambda _settings: Sender())
    assert await jobs._process_outbox(session, settings, now, 100) == (1, 0)
    assert sends == []
    await session.refresh(event)
    assert event.status == OutboxStatus.COMPLETED
    assert event.payload['email_delivery']['state'] == 'suppressed'
