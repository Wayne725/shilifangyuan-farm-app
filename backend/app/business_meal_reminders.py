from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .business_meals import business_payment_window, is_business_meal
from .config import Settings
from .integrations.notifications import NotificationCommand, NotificationService, SQLAlchemyNotificationRepository
from .models import FulfillmentStatus, MealEventStatus, Notification, Order, OrderFulfillment, PaymentStatus


TAIPEI = ZoneInfo('Asia/Taipei')
PAYMENT_REMINDER = 'meal.business_payment_reminder'


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def reminder_is_current(order: Order, now: datetime) -> bool:
    if not is_business_meal(order) or order.cancelled_at is not None:
        return False
    if order.fulfillment_status == FulfillmentStatus.CANCELLED or order.payment_status not in {
        PaymentStatus.PENDING, PaymentStatus.FAILED,
    }:
        return False
    event = order.__dict__.get('meal_event')
    if event is not None and event.status == MealEventStatus.CANCELLED:
        return False
    window = business_payment_window(order)
    if window is None:
        return False
    opens_at, closes_at = window
    reminder_at = datetime.combine(_aware(opens_at).astimezone(TAIPEI).date(), time(8), TAIPEI)
    return reminder_at <= _aware(now) < _aware(closes_at)


def _email_address(*values: str | None) -> str | None:
    for value in values:
        if not value or not value.strip():
            continue
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized
        except EmailNotValidError:
            continue
    return None


async def enqueue_business_payment_reminders(
    session: AsyncSession, settings: Settings, now: datetime, limit: int = 100,
) -> int:
    current = _aware(now)
    local = current.astimezone(TAIPEI)
    if local.time() < time(8):
        return 0
    today = datetime.combine(local.date(), time.min, TAIPEI).astimezone(timezone.utc)
    tomorrow = today + timedelta(days=1)
    pickup_at = OrderFulfillment.pickup_at
    pickup_start = OrderFulfillment.pickup_starts_at
    already_notified = exists(select(Notification.id).where(
        Notification.user_id == Order.user_id,
        Notification.event_type == PAYMENT_REMINDER,
        Notification.data['order_id'].as_string() == Order.id,
    ))
    orders = list(await session.scalars(select(Order).join(Order.fulfillment).where(
        Order.meal_booking_type == 'business', Order.user_id.is_not(None),
        Order.payment_status.in_({PaymentStatus.PENDING, PaymentStatus.FAILED}),
        Order.cancelled_at.is_(None), Order.fulfillment_status != FulfillmentStatus.CANCELLED,
        or_((pickup_at >= today) & (pickup_at < tomorrow),
            pickup_at.is_(None) & (pickup_start >= today) & (pickup_start < tomorrow)),
        ~already_notified,
    ).options(selectinload(Order.fulfillment), selectinload(Order.meal_event), selectinload(Order.user))
        .order_by(Order.created_at, Order.id).limit(limit).with_for_update(of=Order)))
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    queued = 0
    for order in orders:
        if not reminder_is_current(order, current):
            continue
        email = _email_address(order.contact_email, order.invoice_buyer_email,
            order.user.notification_email, order.user.email)
        link = f"{settings.web_base_url.rstrip('/')}/meal-orders?{urlencode({'order_id': order.id})}"
        await service.publish(NotificationCommand(
            user_id=order.user_id, event_type=PAYMENT_REMINDER, title='會餐取餐日付款提醒',
            body=f'您的會餐訂單 {order.order_number} 尚待付款，金額 NT${order.amount_total}。'
                 f'請於今天 23:59 前完成線上付款，並至訂單頁確認付款與發票狀態。\n{link}',
            email=email, data={'order_id': order.id, 'url': link},
            dedupe_key=f'business-payment-reminder:{order.id}', created_at=current,
        ))
        queued += 1
    return queued


async def queued_reminder_is_current(session: AsyncSession, payload: dict, now: datetime) -> bool:
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('order_id'), str):
        return False
    order = await session.scalar(select(Order).where(Order.id == data['order_id'])
        .options(selectinload(Order.fulfillment), selectinload(Order.meal_event)))
    return order is not None and reminder_is_current(order, now)
