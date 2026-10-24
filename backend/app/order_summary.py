import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .meal_schedules import TAIPEI
from .order_categories import OrderCategory, order_category_condition
from .models import (
    FulfillmentState, FulfillmentStatus, MealEventOffering, Order, OrderFulfillment,
    OrderItem, PaymentStatus, Product,
)


class OrderSummaryFilters(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    starts_on: Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]
    ends_on: Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]
    sales_channel: Literal["all", "meal_preorder", "regular", "group"] = "all"
    order_category: OrderCategory = "all"
    status: Literal["active", "paid", "pending", "inactive", "all"] = "active"
    search: str = Field(default="", max_length=100)
    category: str = Field(default="", max_length=100)
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.ends_on < self.starts_on:
            raise ValueError("結束日期不可早於開始日期")
        return self


def _summary_lines(filters: OrderSummaryFilters):
    start = datetime.combine(filters.starts_on, time.min, TAIPEI).astimezone(timezone.utc)
    end = datetime.combine(filters.ends_on + timedelta(days=1), time.min, TAIPEI).astimezone(timezone.utc)
    active = and_(
        Order.cancelled_at.is_(None),
        Order.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.PAID]),
        case(
            (OrderFulfillment.id.is_not(None), OrderFulfillment.status != FulfillmentState.CANCELLED),
            else_=Order.fulfillment_status != FulfillmentStatus.CANCELLED,
        ),
    )
    state = case(
        (and_(active, Order.payment_status == PaymentStatus.PAID), "paid"),
        (and_(active, Order.payment_status == PaymentStatus.PENDING), "pending"),
        else_="inactive",
    )
    category = case(
        (OrderItem.source_product_id.is_not(None), func.coalesce(Product.category, "未分類")),
        (MealEventOffering.meal_id.is_not(None), "便當"),
        (OrderItem.source_bundle_id.is_not(None), "共同購買組合"),
        else_="未分類",
    )
    source_kind = case(
        (OrderItem.source_product_id.is_not(None), "product"),
        (MealEventOffering.meal_id.is_not(None), "meal"),
        (OrderItem.source_bundle_id.is_not(None), "bundle"),
        else_="snapshot",
    )
    source_id = func.coalesce(OrderItem.source_product_id, MealEventOffering.meal_id, OrderItem.source_bundle_id, "")
    query = select(
        Order.id.label("order_id"), Order.sales_channel.label("sales_channel"),
        OrderItem.product_name.label("name"), OrderItem.unit_label.label("unit"),
        OrderItem.quantity.label("quantity"), category.label("category"),
        source_kind.label("source_kind"), source_id.label("source_id"), state.label("state"),
    ).select_from(OrderItem).join(Order, Order.id == OrderItem.order_id).outerjoin(
        OrderFulfillment, OrderFulfillment.order_id == Order.id,
    ).outerjoin(Product, Product.id == OrderItem.source_product_id).outerjoin(
        MealEventOffering, MealEventOffering.id == OrderItem.source_meal_offering_id,
    ).where(Order.created_at >= start, Order.created_at < end, order_category_condition(filters.order_category))
    if filters.sales_channel != "all":
        query = query.where(Order.sales_channel == filters.sales_channel)
    if filters.status == "active":
        query = query.where(active)
    elif filters.status != "all":
        query = query.where(state == filters.status)
    if filters.search:
        query = query.where(OrderItem.product_name.icontains(filters.search, autoescape=True))
    return query.subquery()


async def read_order_summary(session: AsyncSession, filters: OrderSummaryFilters):
    available_lines = _summary_lines(filters)
    category_options = list(await session.scalars(
        select(available_lines.c.category).distinct().order_by(available_lines.c.category)
    ))
    lines = (
        select(available_lines).where(available_lines.c.category == filters.category).subquery()
        if filters.category else available_lines
    )
    summary = dict((await session.execute(select(
        func.count(func.distinct(lines.c.order_id)).label("order_count"),
        func.coalesce(func.sum(lines.c.quantity), 0).label("quantity"),
        *(func.count(func.distinct(case((lines.c.state == state, lines.c.order_id)))).label(f"{state}_order_count")
          for state in ["paid", "pending", "inactive"]),
    ))).one()._mapping)
    group_columns = [lines.c.sales_channel, lines.c.source_kind, lines.c.source_id, lines.c.name, lines.c.unit, lines.c.category]
    products = select(
        *group_columns, func.sum(lines.c.quantity).label("quantity"),
        func.count(func.distinct(lines.c.order_id)).label("order_count"),
    ).group_by(*group_columns).subquery()
    product_count = int(await session.scalar(select(func.count()).select_from(products)) or 0)
    product_rows = (await session.execute(select(products).order_by(
        products.c.quantity.desc(), products.c.name, products.c.sales_channel,
        products.c.source_kind, products.c.source_id, products.c.unit, products.c.category,
    ).limit(filters.limit).offset(filters.offset))).mappings()
    items = [
        {"key": json.dumps([row[column.key] for column in group_columns], ensure_ascii=False, separators=(",", ":")),
         **{field: row[field] for field in ["sales_channel", "category", "name", "unit", "quantity", "order_count"]}}
        for row in product_rows
    ]
    categories = (await session.execute(select(
        lines.c.category, func.count(func.distinct(lines.c.order_id)).label("order_count"),
        func.sum(lines.c.quantity).label("quantity"),
    ).group_by(lines.c.category).order_by(func.sum(lines.c.quantity).desc(), lines.c.category))).mappings().all()
    channels = (await session.execute(select(
        lines.c.sales_channel, func.count(func.distinct(lines.c.order_id)).label("order_count"),
        func.sum(lines.c.quantity).label("quantity"),
    ).group_by(lines.c.sales_channel).order_by(lines.c.sales_channel))).mappings().all()
    return {
        **filters.model_dump(exclude={"limit", "offset"}),
        "summary": {**summary, "product_count": product_count},
        "categories": [dict(row) for row in categories], "channels": [dict(row) for row in channels],
        "category_options": category_options,
        "products": {"items": items, "total": product_count, "limit": filters.limit, "offset": filters.offset},
    }
