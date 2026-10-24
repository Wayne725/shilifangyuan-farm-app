from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .domain import aware
from .meal_schedules import TAIPEI
from .models import (
    AdminAudit, FulfillmentState, FulfillmentStatus, MealEvent, MealEventStatus,
    Order, OrderFulfillment, OrderItem, PaymentStatus, SalesChannel,
)


ACTIVE_STATES = {FulfillmentState.PENDING_CONFIRMATION, FulfillmentState.PREPARING, FulfillmentState.READY_FOR_PICKUP}
ACTIVE_EVENT_STATES = {MealEventStatus.PUBLISHED, MealEventStatus.ORDERING_CLOSED, MealEventStatus.PICKUP_OPEN}


class MealKitchenFilters(BaseModel):
    service_date: Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]
    meal_period: Literal["all", "lunch", "dinner", "unspecified"] = "all"
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class MealKitchenStatusChange(BaseModel):
    status: Literal["preparing", "ready_for_pickup"]


def _pickup_time():
    return func.coalesce(OrderFulfillment.pickup_at, MealEvent.pickup_starts_at)


def _load_options():
    return [selectinload(Order.user), selectinload(Order.meal_event), selectinload(Order.fulfillment),
            selectinload(Order.items).selectinload(OrderItem.selected_options), selectinload(Order.cancellation_requests)]


def kitchen_order_read(order):
    pending = next((row for row in order.cancellation_requests if row.status == "pending"), None)
    pickup_at = order.fulfillment.pickup_at or order.meal_event.pickup_starts_at
    return {
        "id": order.id, "order_number": order.order_number, "customer_name": order.user.display_name,
        "kitchen_note": order.kitchen_note,
        "meal_event_id": order.meal_event_id, "event_title": order.meal_event.title,
        "meal_period": order.meal_event.meal_period,
        "location": order.fulfillment.pickup_location or order.meal_event.location,
        "pickup_location_id": order.fulfillment.pickup_location_id,
        "pickup_address": order.fulfillment.pickup_address,
        "pickup_instructions": order.fulfillment.pickup_instructions,
        "needs_utensils": order.fulfillment.needs_utensils,
        "pickup_at": aware(pickup_at), "pickup_time_confirmed": order.fulfillment.pickup_at is not None,
        "fulfillment_status": order.fulfillment.status.value,
        "pending_cancellation": {"reason": pending.reason, "requested_at": aware(pending.requested_at)} if pending else None,
        "items": [{"name": item.product_name, "quantity": item.quantity, "unit": item.unit_label,
                   "options": [{"group_name": option.group_name, "name": option.option_name} for option in item.selected_options]}
                  for item in order.items],
    }


async def meal_kitchen_list(session: AsyncSession, filters: MealKitchenFilters):
    start = datetime.combine(filters.service_date, time.min, TAIPEI).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    now = datetime.now(timezone.utc)
    query = select(Order).join(OrderFulfillment, OrderFulfillment.order_id == Order.id).join(MealEvent, MealEvent.id == Order.meal_event_id).where(
        Order.sales_channel == SalesChannel.MEAL_PREORDER, Order.payment_status == PaymentStatus.PAID,
        Order.cancelled_at.is_(None), OrderFulfillment.status.in_(ACTIVE_STATES),
        MealEvent.status.in_(ACTIVE_EVENT_STATES), MealEvent.pickup_ends_at > now,
        _pickup_time() >= start, _pickup_time() < end,
    )
    if filters.meal_period == "unspecified":
        query = query.where(MealEvent.meal_period.is_(None))
    elif filters.meal_period != "all":
        query = query.where(MealEvent.meal_period == filters.meal_period)
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    order_ids = query.with_only_columns(Order.id).subquery()
    products = (await session.execute(select(OrderItem.product_name, OrderItem.unit_label, func.sum(OrderItem.quantity))
        .where(OrderItem.order_id.in_(select(order_ids.c.id))).group_by(OrderItem.product_name, OrderItem.unit_label).order_by(OrderItem.product_name))).all()
    orders = list(await session.scalars(query.options(*_load_options()).order_by(_pickup_time(), Order.id).limit(filters.limit).offset(filters.offset)))
    return {
        "service_date": filters.service_date, "meal_period": filters.meal_period, "total": total,
        "limit": filters.limit, "offset": filters.offset, "meal_quantity": sum(int(row[2]) for row in products),
        "products": [{"name": name, "unit": unit, "quantity": int(quantity)} for name, unit, quantity in products],
        "items": [kitchen_order_read(order) for order in orders],
    }


def _ensure_kitchen_allowed(order, fulfillment, now):
    event = order.meal_event
    if order.payment_status != PaymentStatus.PAID or order.cancelled_at or fulfillment is None or fulfillment.status not in ACTIVE_STATES:
        raise HTTPException(409, "訂單未付款、已取消或已結束取餐，請更新廚房清單")
    if event is None or event.status not in ACTIVE_EVENT_STATES or aware(event.pickup_ends_at) <= now:
        raise HTTPException(409, "供餐已取消或結束，不能變更製作狀態")


def _apply_kitchen_state(session, order, fulfillment, target, admin_id, now):
    _ensure_kitchen_allowed(order, fulfillment, now)
    next_state = FulfillmentState(target)
    if fulfillment.status == next_state:
        return
    allowed = {FulfillmentState.PENDING_CONFIRMATION: FulfillmentState.PREPARING, FulfillmentState.PREPARING: FulfillmentState.READY_FOR_PICKUP}
    if allowed.get(fulfillment.status) != next_state:
        raise HTTPException(409, "請依序標記製作中、待取餐；不能倒退狀態")
    previous = fulfillment.status.value
    fulfillment.status = next_state
    order.fulfillment_status = FulfillmentStatus(target)
    session.add(AdminAudit(actor_id=admin_id, action="meal_kitchen.status_changed", aggregate_type="order", aggregate_id=order.id,
                           reason="廚房更新製作狀態", data={"from": previous, "to": target}))


async def _locked_kitchen_order(session: AsyncSession, order_id: str):
    order = await session.scalar(select(Order).where(Order.id == order_id, Order.sales_channel == SalesChannel.MEAL_PREORDER)
        .options(*_load_options()).with_for_update(of=Order).execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(404, "找不到便當訂單")
    fulfillment = await session.scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == order.id)
        .with_for_update().execution_options(populate_existing=True))
    return order, fulfillment


async def change_meal_kitchen_status(session: AsyncSession, order_id: str, target: str, admin_id: str):
    order, fulfillment = await _locked_kitchen_order(session, order_id)
    _apply_kitchen_state(session, order, fulfillment, target, admin_id, datetime.now(timezone.utc))
    await session.commit()
    return kitchen_order_read(order)


async def meal_kitchen_ticket(session: AsyncSession, order_id: str):
    order = await session.scalar(select(Order).where(Order.id == order_id, Order.sales_channel == SalesChannel.MEAL_PREORDER)
        .options(*_load_options()).execution_options(populate_existing=True))
    if order is None:
        raise HTTPException(404, "找不到便當訂單")
    _ensure_kitchen_allowed(order, order.fulfillment, datetime.now(timezone.utc))
    return kitchen_order_read(order)
