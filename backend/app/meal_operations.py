from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .meal_schedules import TAIPEI
from .models import (
    FulfillmentState, Invoice, InvoiceStatus, MealCancellationRequest, MealEvent,
    MealEventStatus, Order, OrderFulfillment, OrderItem, OutboxEvent, OutboxStatus,
    PaymentStatus, Refund, RefundStatus, SalesChannel, User,
)
from .schemas import MealOperationsSummary, MealOrderSearchFilters


def _date_start(value: date) -> datetime:
    return datetime.combine(value, time.min, TAIPEI).astimezone(timezone.utc)


def _pickup_at():
    return func.coalesce(OrderFulfillment.pickup_at, MealEvent.pickup_starts_at)


def _work_conditions(now: datetime):
    today = now.astimezone(TAIPEI).date()
    def failed_invoice_job(event_types):
        return exists().where(
            OutboxEvent.status == OutboxStatus.FAILED,
            OutboxEvent.event_type.in_(event_types),
            or_(OutboxEvent.aggregate_id == Order.id, OutboxEvent.payload["order_id"].as_string() == Order.id),
        )

    return {
        "pending_cancellation": exists().where(MealCancellationRequest.order_id == Order.id, MealCancellationRequest.status == "pending"),
        "refund_attention": or_(
            Order.payment_status.in_({PaymentStatus.REFUND_PENDING, PaymentStatus.LATE_PAID_REFUND_REQUIRED}),
            exists().where(Refund.order_id == Order.id, or_(
                Refund.status == RefundStatus.PENDING,
                and_(Refund.status == RefundStatus.FAILED, Order.payment_status != PaymentStatus.REFUNDED),
            )),
        ),
        "invoice_attention": and_(
            Order.invoice_status != InvoiceStatus.VOIDED,
            or_(
                Order.invoice_status == InvoiceStatus.FAILED,
                exists().where(Invoice.order_id == Order.id, Invoice.error_message.is_not(None), Invoice.error_message != ""),
                and_(Order.invoice_status != InvoiceStatus.ISSUED, failed_invoice_job({"invoice.issue", "invoice.issue_requested", "issue_invoice"})),
                failed_invoice_job({"invoice.adjustment_required"}),
            ),
        ),
        "today_pickup": and_(
            _pickup_at() >= _date_start(today), _pickup_at() < _date_start(today + timedelta(days=1)),
            Order.payment_status == PaymentStatus.PAID, Order.cancelled_at.is_(None),
            OrderFulfillment.status.in_({FulfillmentState.PENDING_CONFIRMATION, FulfillmentState.PREPARING, FulfillmentState.READY_FOR_PICKUP}),
            MealEvent.status.not_in({MealEventStatus.CANCELLED, MealEventStatus.COMPLETED}),
            MealEvent.pickup_ends_at > now,
        ),
    }


def _orders_query():
    return select(Order).join(MealEvent, Order.meal_event_id == MealEvent.id).join(OrderFulfillment, OrderFulfillment.order_id == Order.id).join(User, Order.user_id == User.id).where(Order.sales_channel == SalesChannel.MEAL_PREORDER)


async def search_meal_orders(session: AsyncSession, filters: MealOrderSearchFilters):
    query = _orders_query()
    if filters.search.strip():
        query = query.where(or_(*(column.icontains(filters.search.strip(), autoescape=True) for column in [Order.order_number, Order.contact_email, User.email, User.display_name])))
    if filters.date_from:
        query = query.where(_pickup_at() >= _date_start(filters.date_from))
    if filters.date_to:
        query = query.where(_pickup_at() < _date_start(filters.date_to + timedelta(days=1)))
    if filters.meal_period == "unspecified":
        query = query.where(MealEvent.meal_period.is_(None))
    elif filters.meal_period != "all":
        query = query.where(MealEvent.meal_period == filters.meal_period)
    if filters.pickup_time:
        local_time = (
            func.to_char(func.timezone("Asia/Taipei", _pickup_at()), "HH24:MI")
            if session.get_bind().dialect.name == "postgresql"
            else func.strftime("%H:%M", _pickup_at(), "+8 hours")
        )
        query = query.where(local_time == filters.pickup_time)
    for column, value in [(Order.payment_status, filters.payment_status), (OrderFulfillment.status, filters.fulfillment_status), (Order.invoice_status, filters.invoice_status)]:
        if value is not None:
            query = query.where(column == value)
    if filters.review_status != "all":
        latest_status = select(MealCancellationRequest.status).where(MealCancellationRequest.order_id == Order.id).order_by(MealCancellationRequest.requested_at.desc(), MealCancellationRequest.id.desc()).limit(1).correlate(Order).scalar_subquery()
        query = query.where(latest_status.is_(None) if filters.review_status == "none" else latest_status == filters.review_status)
    if filters.work_queue != "all":
        query = query.where(_work_conditions(datetime.now(timezone.utc))[filters.work_queue])
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    orders = list(await session.scalars(query.options(
        selectinload(Order.user), selectinload(Order.meal_event), selectinload(Order.fulfillment),
        selectinload(Order.invoice), selectinload(Order.cancellation_requests),
        selectinload(Order.amendments), selectinload(Order.payment_attempts),
        selectinload(Order.items).selectinload(OrderItem.selected_options),
    ).order_by(Order.created_at.desc(), Order.id.desc()).offset(filters.offset).limit(filters.limit)))
    return orders, total or 0


async def meal_operations_summary(session: AsyncSession) -> MealOperationsSummary:
    now = datetime.now(timezone.utc)
    conditions = _work_conditions(now)
    columns = [func.coalesce(func.sum(case((condition, 1), else_=0)), 0).label(name) for name, condition in conditions.items()]
    counts = (await session.execute(_orders_query().with_only_columns(*columns, maintain_column_froms=True))).one()._mapping
    return MealOperationsSummary(today=now.astimezone(TAIPEI).date(), **dict(counts))
