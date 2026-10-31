from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Literal
import unicodedata

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .domain import aware
from .models import MealEvent, Order, OrderFulfillment, OrderItem, PaymentStatus, SalesChannel, User, UserRole


class OrderHistoryQuery(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=100)
    before_created_at: datetime | None = None
    before_id: str | None = Field(default=None, min_length=1, max_length=36)
    q: str = ""
    payment_status: PaymentStatus | None = None
    own_only: bool = False
    history_scope: Literal["all", "general"] = "all"

    @model_validator(mode="after")
    def complete_cursor(self):
        if (self.before_created_at is None) != (self.before_id is None):
            raise ValueError("分頁游標必須同時提供時間與訂單識別")
        if self.before_id is not None and self.limit is None:
            raise ValueError("使用分頁游標時必須指定每頁筆數")
        return self


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower()


def _search_terms(value: str) -> list[str]:
    # Match the browser's ECMAScript whitespace, including pasted BOM characters.
    return [part for part in re.split(r"[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+", _normalized(value)) if part]


async def _matching_ids(session, conditions, filters, *, meal, terms):
    statement = select(Order.id, Order.order_number, OrderFulfillment.pickup_location, OrderItem.product_name,
        MealEvent.title if meal else literal(""), MealEvent.location if meal else literal(""))
    statement = statement.select_from(Order).outerjoin(OrderFulfillment, OrderFulfillment.order_id == Order.id)
    statement = statement.outerjoin(OrderItem, OrderItem.order_id == Order.id)
    if meal:
        statement = statement.outerjoin(MealEvent, Order.meal_event_id == MealEvent.id)
    statement = statement.where(*conditions).order_by(Order.created_at.desc(), Order.id.desc(), OrderItem.id)
    stream = await session.stream(statement.execution_options(yield_per=100))
    selected = []
    current_id, fragments = None, []

    def matches():
        text = _normalized(" ".join(fragments))
        return all(term in text for term in terms)

    try:
        async for order_id, number, pickup, product, title, location in stream:
            if order_id != current_id:
                if current_id is not None and matches():
                    selected.append(current_id)
                    if filters.limit is not None and len(selected) >= filters.limit:
                        break
                current_id = order_id
                fragments = [number, pickup or ""]
                if meal:
                    fragments = [number, title or "", pickup or location or ""]
            fragments.append(product or "")
        else:
            if current_id is not None and matches():
                selected.append(current_id)
    finally:
        await stream.close()
    return selected


async def read_order_history(session: AsyncSession, user: User, filters: OrderHistoryQuery, *, meal: bool = False) -> list[Order]:
    conditions = []
    if meal or filters.own_only or user.user_role != UserRole.ADMIN:
        conditions.append(Order.user_id == user.id)
    if meal:
        conditions.append(Order.sales_channel == SalesChannel.MEAL_PREORDER)
    elif filters.history_scope == "general":
        conditions.append(Order.sales_channel != SalesChannel.MEAL_PREORDER)
    if filters.payment_status is not None:
        conditions.append(Order.payment_status == filters.payment_status)
    if filters.before_created_at is not None:
        timestamp = aware(filters.before_created_at).astimezone(timezone.utc)
        conditions.append(or_(Order.created_at < timestamp,
            and_(Order.created_at == timestamp, Order.id < filters.before_id)))
    statement = select(Order).where(*conditions)
    terms = _search_terms(filters.q)
    if terms:
        ids = await _matching_ids(session, conditions, filters, meal=meal, terms=terms)
        if not ids:
            return []
        statement = statement.where(Order.id.in_(ids))
    statement = statement.order_by(Order.created_at.desc())
    if filters.limit is not None:
        statement = statement.order_by(Order.id.desc()).limit(filters.limit)
    options = [selectinload(Order.items).selectinload(OrderItem.selected_options),
        selectinload(Order.meal_event), selectinload(Order.fulfillment), selectinload(Order.invoice),
        selectinload(Order.cancellation_requests), selectinload(Order.amendments), selectinload(Order.payment_attempts)]
    if not meal:
        options.extend([selectinload(Order.group_campaign),
            selectinload(Order.fulfillment).selectinload(OrderFulfillment.shipment)])
    return list(await session.scalars(statement.options(*options)))
