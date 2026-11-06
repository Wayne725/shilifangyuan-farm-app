from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, case, exists, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .domain import aware
from .meal_schedules import TAIPEI
from .order_categories import OrderCategory, order_category_condition
from .models import Order, OrderItem, PaymentAttempt, PaymentStatus, Refund, RefundStatus, SalesChannel, User


class MealSalesFilters(BaseModel):
    starts_on: Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]
    ends_on: Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]
    search: str = Field(default="", max_length=100)
    sales_channel: Literal["all", "meal_preorder", "regular", "group"] = "meal_preorder"
    order_category: OrderCategory = "all"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_period(self):
        if self.ends_on < self.starts_on:
            raise ValueError("結束日期不能早於開始日期")
        return self

    def bounds(self):
        start = datetime.combine(self.starts_on, time.min, TAIPEI).astimezone(timezone.utc)
        end = datetime.combine(self.ends_on + timedelta(days=1), time.min, TAIPEI).astimezone(timezone.utc)
        return start, end


def _channel_scope(filters):
    channel = True if filters.sales_channel == "all" else Order.sales_channel == SalesChannel(filters.sales_channel)
    return and_(channel, order_category_condition(filters.order_category))


def _receipts(filters):
    received = or_(PaymentAttempt.paid_at.is_not(None), PaymentAttempt.status.in_({
        PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED,
    }))
    attempts = select(
        Order.id.label("order_id"), PaymentAttempt.amount.label("amount"),
        func.coalesce(PaymentAttempt.paid_at, case((and_(PaymentAttempt.order_revision == Order.revision, PaymentAttempt.amount == Order.amount_total, PaymentAttempt.superseded_at.is_(None)), Order.paid_at))).label("occurred_at"),
    ).join(PaymentAttempt, PaymentAttempt.order_id == Order.id).where(
        _channel_scope(filters), received,
    )
    legacy = select(Order.id.label("order_id"), Order.amount_total.label("amount"), Order.paid_at.label("occurred_at")).where(
        _channel_scope(filters), Order.paid_at.is_not(None),
        ~exists().where(PaymentAttempt.order_id == Order.id, received),
    )
    return union_all(attempts, legacy).cte("meal_receipts")


def _search_condition(search):
    if not (term := search.strip()):
        return True
    return or_(Order.order_number.icontains(term, autoescape=True), Order.contact_email.icontains(term, autoescape=True),
               User.email.icontains(term, autoescape=True), User.display_name.icontains(term, autoescape=True),
               Order.customer_name_snapshot.icontains(term, autoescape=True))


def _day_expression(session, column):
    if session.bind.dialect.name == "postgresql":
        return func.to_char(func.timezone("Asia/Taipei", column), "YYYY-MM-DD")
    return func.strftime("%Y-%m-%d", column, "+8 hours")


def _order_scope(filters, receipts):
    start, end = filters.bounds()
    return select(Order).outerjoin(User, User.id == Order.user_id).where(
        _channel_scope(filters), _search_condition(filters.search), or_(
            and_(Order.created_at >= start, Order.created_at < end),
            exists().where(receipts.c.order_id == Order.id, receipts.c.occurred_at >= start, receipts.c.occurred_at < end),
            exists().where(Refund.order_id == Order.id, or_(
                and_(Refund.created_at >= start, Refund.created_at < end),
                and_(Refund.status == RefundStatus.COMPLETED, Refund.completed_at >= start, Refund.completed_at < end),
            )),
        ),
    )


def _money_in_period(at, start, end):
    return at is not None and start <= aware(at) < end


async def meal_sales_details(session: AsyncSession, filters: MealSalesFilters):
    receipts = _receipts(filters)
    query = _order_scope(filters, receipts)
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    orders = list(await session.scalars(query.options(
        selectinload(Order.user), selectinload(Order.items).selectinload(OrderItem.selected_options),
        selectinload(Order.meal_event), selectinload(Order.fulfillment), selectinload(Order.invoice),
        selectinload(Order.payment_attempts), selectinload(Order.refunds), selectinload(Order.cancellation_requests),
    ).order_by(Order.created_at.desc(), Order.id.desc()).limit(filters.limit).offset(filters.offset)))
    start, end = filters.bounds()
    receipt_rows = (await session.execute(select(receipts).where(receipts.c.order_id.in_([order.id for order in orders])))).all() if orders else []
    period_receipts = defaultdict(int)
    for order_id, amount, occurred_at in receipt_rows:
        if _money_in_period(occurred_at, start, end):
            period_receipts[order_id] += amount
    details = []
    for order in orders:
        refunds = sorted(order.refunds, key=lambda row: (aware(row.created_at), row.id), reverse=True)
        period_refunds = sum(row.amount for row in refunds if row.status == RefundStatus.COMPLETED and _money_in_period(row.completed_at, start, end))
        latest_request = max(order.cancellation_requests, key=lambda row: (aware(row.requested_at), row.id), default=None)
        fulfillment = order.fulfillment
        event = order.meal_event
        details.append({
            "id": order.id, "order_number": order.order_number, "sales_channel": order.sales_channel.value, "customer_name": order.user.display_name if order.user else order.customer_name_snapshot or "現場客人",
            "customer_email": order.contact_email, "membership_type": order.membership_type_snapshot.value,
            "revision": order.revision,
            "receipts": [{"merchant_trade_no": attempt.merchant_trade_no, "order_revision": attempt.order_revision,
                          "amount": attempt.amount, "status": attempt.status.value,
                          "received_at": aware(attempt.paid_at) if attempt.paid_at else None,
                          "obsolete_revision": attempt.superseded_at is not None or attempt.order_revision != order.revision or attempt.amount != order.amount_total}
                         for attempt in sorted(order.payment_attempts, key=lambda row: (aware(row.created_at), row.id), reverse=True)
                         if attempt.paid_at or attempt.status in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING,
                             PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED}],
            "created_at": aware(order.created_at), "paid_at": aware(order.paid_at) if order.paid_at else None, "order_amount": order.amount_total,
            "tax_amount": order.tax_amount, "period_received": period_receipts[order.id], "period_refunded": period_refunds,
            "period_net": period_receipts[order.id] - period_refunds, "payment_status": order.payment_status.value,
            "fulfillment_status": fulfillment.status.value if fulfillment else order.fulfillment_status.value,
            "pickup_at": aware(fulfillment.pickup_at) if fulfillment and fulfillment.pickup_at else aware(event.pickup_starts_at) if event else None,
            "pickup_time_confirmed": bool(fulfillment and fulfillment.pickup_at),
            "event_title": event.title if event else None, "meal_period": event.meal_period if event else None,
            "location": fulfillment.pickup_location if fulfillment and fulfillment.pickup_location else event.location if event else None,
            "invoice_status": order.invoice_status.value, "invoice_number": order.invoice.invoice_number if order.invoice else None,
            "cancellation_status": latest_request.status if latest_request else None,
            "items": [{"name": item.product_name, "unit": item.unit_label, "quantity": item.quantity,
                       "unit_price": item.unit_price, "subtotal": item.subtotal, "tax_type": item.tax_type.value,
                       "options": [{"group_name": option.group_name, "name": option.option_name, "price_delta": option.price_delta} for option in item.selected_options]}
                      for item in order.items],
            "refunds": [{"purpose": row.purpose, "payment_attempt_id": row.payment_attempt_id, "amount": row.amount, "status": row.status.value, "reason": row.reason,
                         "requested_at": aware(row.created_at), "completed_at": aware(row.completed_at) if row.completed_at else None} for row in refunds],
        })
    return {"items": details, "total": total, "limit": filters.limit, "offset": filters.offset}


async def meal_sales_report(session: AsyncSession, filters: MealSalesFilters):
    start, end = filters.bounds()
    receipts = _receipts(filters)
    scope = select(Order.id).outerjoin(User, User.id == Order.user_id).where(
        _channel_scope(filters), _search_condition(filters.search),
    ).cte("selected_meal_orders")
    receipt_day = _day_expression(session, receipts.c.occurred_at)
    receipt_rows = (await session.execute(select(receipt_day, func.sum(receipts.c.amount), func.count()).where(
        receipts.c.order_id.in_(select(scope.c.id)), receipts.c.occurred_at >= start, receipts.c.occurred_at < end,
    ).group_by(receipt_day).order_by(receipt_day))).all()
    refund_day = _day_expression(session, Refund.completed_at)
    refund_rows = (await session.execute(select(refund_day, func.sum(Refund.amount), func.count()).where(
        Refund.order_id.in_(select(scope.c.id)), Refund.status == RefundStatus.COMPLETED,
        Refund.completed_at >= start, Refund.completed_at < end,
    ).group_by(refund_day).order_by(refund_day))).all()
    daily = {}
    def day_row(day):
        return daily.setdefault(day, {"period": day, "received": 0, "refunded": 0, "net": 0, "receipt_count": 0, "refund_count": 0})
    for day, amount, count in receipt_rows:
        day_row(day).update(received=int(amount), receipt_count=int(count))
    for day, amount, count in refund_rows:
        day_row(day).update(refunded=int(amount), refund_count=int(count))
    for row in daily.values():
        row["net"] = row["received"] - row["refunded"]
    def group_periods(mode):
        grouped = {}
        for day, values in sorted(daily.items(), reverse=True):
            day_date = date.fromisoformat(day)
            key = day[:7] if mode == "month" else (day_date - timedelta(days=day_date.weekday())).isoformat()
            row = grouped.setdefault(key, {"period": key, "received": 0, "refunded": 0, "net": 0, "receipt_count": 0, "refund_count": 0})
            for field in ("received", "refunded", "net", "receipt_count", "refund_count"):
                row[field] += values[field]
        return list(grouped.values())
    products = (await session.execute(select(
        OrderItem.product_name, OrderItem.unit_label, func.sum(OrderItem.quantity), func.sum(OrderItem.subtotal), func.count(func.distinct(Order.id)),
    ).join(Order, Order.id == OrderItem.order_id).where(Order.id.in_(select(scope.c.id)), Order.paid_at >= start, Order.paid_at < end)
        .group_by(OrderItem.product_name, OrderItem.unit_label).order_by(func.sum(OrderItem.quantity).desc(), OrderItem.product_name))).all()
    relevant_ids = _order_scope(filters, receipts).with_only_columns(Order.id).subquery()
    pending_refunds = int(await session.scalar(select(func.coalesce(func.sum(Refund.amount), 0)).where(
        Refund.order_id.in_(select(relevant_ids.c.id)), Refund.status == RefundStatus.PENDING,
    )) or 0)
    details = await meal_sales_details(session, filters)
    received = sum(row["received"] for row in daily.values())
    refunded = sum(row["refunded"] for row in daily.values())
    return {
        "starts_on": filters.starts_on, "ends_on": filters.ends_on,
        "summary": {"received": received, "refunded": refunded, "net": received - refunded,
                    "receipt_count": sum(row["receipt_count"] for row in daily.values()),
                    "refund_count": sum(row["refund_count"] for row in daily.values()),
                    "pending_refund_amount": pending_refunds, "paid_meal_quantity": sum(int(row[2]) for row in products)},
        "daily": sorted(daily.values(), key=lambda row: row["period"], reverse=True), "weekly": group_periods("week"), "monthly": group_periods("month"),
        "products": [{"name": name, "unit": unit, "quantity": int(quantity), "sales_amount": int(amount), "order_count": int(count)}
                     for name, unit, quantity, amount, count in products],
        "orders": details,
    }
