"""Invoice intent belongs to the order; the provider supplies the actual issue date."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from .models import Invoice, InvoiceStatus, OutboxEvent, OutboxStatus, PaymentMethod
from .recovery_work import INVOICE_ISSUE_EVENTS, outbox_is_held

TAIPEI = ZoneInfo("Asia/Taipei")


def invoice_handling(order) -> str:
    return order.invoice_handling or (
        "external" if order.payment_method == PaymentMethod.CASH_ON_PICKUP else "on_payment"
    )


def pickup_invoice_due(pickup_at: datetime) -> datetime:
    if pickup_at.tzinfo is None:
        pickup_at = pickup_at.replace(tzinfo=timezone.utc)
    return datetime.combine(pickup_at.astimezone(TAIPEI).date(), time.min, TAIPEI).astimezone(timezone.utc)


def invoice_not_due(order, now: datetime | None = None) -> bool:
    if invoice_handling(order) != "pickup_day":
        return False
    due = order.invoice_issue_after
    if due is None:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) < due


async def cancel_unissued_invoice_jobs(session, order) -> None:
    if order.__dict__.get("invoice") is not None or await session.scalar(select(Invoice.id).where(Invoice.order_id == order.id)):
        return
    await session.execute(update(OutboxEvent).where(
        OutboxEvent.aggregate_id == order.id,
        OutboxEvent.event_type.in_(INVOICE_ISSUE_EVENTS),
        OutboxEvent.status == OutboxStatus.PENDING,
        ~outbox_is_held(),
    ).values(status=OutboxStatus.COMPLETED, processed_at=datetime.now(timezone.utc), last_error="cancelled_before_invoice_issue"))
    if invoice_handling(order) != "external":
        order.invoice_status = InvoiceStatus.NOT_ELIGIBLE
