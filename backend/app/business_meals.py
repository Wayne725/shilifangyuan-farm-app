from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import InvoiceStatus, MealEventStatus, PaymentStatus, SalesChannel

TAIPEI = ZoneInfo('Asia/Taipei')


def is_business_meal(order) -> bool:
    return order.sales_channel == SalesChannel.MEAL_PREORDER and getattr(order, 'meal_booking_type', 'standard') == 'business'


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def business_payment_window(order) -> tuple[datetime, datetime] | None:
    if not is_business_meal(order):
        return None
    fulfillment = order.__dict__.get('fulfillment')
    event = order.__dict__.get('meal_event')
    pickup = ((fulfillment.pickup_at or fulfillment.pickup_starts_at) if fulfillment is not None else None)
    pickup = pickup or (event.pickup_starts_at if event is not None else None)
    if pickup is None:
        return None
    opening = datetime.combine(_aware(pickup).astimezone(TAIPEI).date(), time.min, TAIPEI)
    return opening.astimezone(timezone.utc), (opening + timedelta(days=1)).astimezone(timezone.utc)


def business_payment_blocked_reason(order, now: datetime) -> str | None:
    window = business_payment_window(order)
    if window is None:
        return '會餐取餐日期未提供，請聯絡合作社'
    if _aware(now) < window[0]:
        return '會餐可於取餐當日付款'
    if _aware(now) >= window[1]:
        return '會餐付款日期已過，請聯絡合作社'
    event = order.__dict__.get('meal_event')
    if event is None or event.status in {MealEventStatus.DRAFT, MealEventStatus.CANCELLED}:
        return '會餐場次已取消或尚未開放，請聯絡合作社'
    return None


def business_payment_fields(order, now: datetime | None = None) -> dict:
    current = _aware(now or datetime.now(timezone.utc))
    window = business_payment_window(order)
    unpaid = order.payment_status in {PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED}
    return {
        'meal_booking_type': getattr(order, 'meal_booking_type', None) or 'standard',
        'payment_opens_at': window[0] if window else None,
        'payment_closes_at': window[1] if window else None,
        'payment_followup_required': bool(window and unpaid and order.cancelled_at is None and current >= window[1]),
        'invoice_followup_required': business_invoice_followup_required(order, current),
    }


def business_invoice_window_closed(order, now: datetime | None = None) -> bool:
    if not is_business_meal(order):
        return False
    window = business_payment_window(order)
    if window is not None:
        closes_at = window[1]
    elif order.invoice_issue_after is not None:
        # The scheduled day is stored on the order for callers without loaded relationships.
        closes_at = _aware(order.invoice_issue_after) + timedelta(days=1)
    else:
        return False
    return _aware(now or datetime.now(timezone.utc)) >= closes_at


def business_invoice_date_error(order, invoice) -> str | None:
    window = business_payment_window(order)
    if window is None or invoice.status != InvoiceStatus.ISSUED:
        return None
    if order.paid_at is not None and not window[0] <= _aware(order.paid_at) < window[1]:
        return '會餐收款日期已超過取餐日，請由合作社查核發票'
    if invoice.invoice_date is None:
        return '會餐發票日期未提供，請由合作社查核'
    if _aware(invoice.invoice_date).astimezone(TAIPEI).date() != window[0].astimezone(TAIPEI).date():
        return '會餐發票日期與取餐日不符，請由合作社查核'
    return None


def business_invoice_followup_required(order, now: datetime | None = None) -> bool:
    if not is_business_meal(order) or order.cancelled_at is not None or order.payment_status != PaymentStatus.PAID:
        return False
    invoice = order.__dict__.get('invoice')
    window = business_payment_window(order)
    if window and order.paid_at is not None and not window[0] <= _aware(order.paid_at) < window[1]:
        return True
    if invoice is not None and invoice.status == InvoiceStatus.ISSUED:
        return business_invoice_date_error(order, invoice) is not None
    return business_invoice_window_closed(order, now)
