import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .meal_schedules import TAIPEI
from .order_categories import OrderCategory, order_category_condition
from .models import (
    FulfillmentState, FulfillmentStatus, MealEventOffering, Order, OrderFulfillment,
    OrderItem, OrderItemOption, PaymentMethod, PaymentStatus, Product,
)


class OrderSummaryFilters(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    starts_on: Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]
    ends_on: Annotated[date, Field(ge=date(2, 1, 1), le=date(9998, 12, 31))]
    date_basis: Literal["order", "pickup"] = "order"
    sales_channel: Literal["all", "meal_preorder", "regular", "group"] = "all"
    order_category: OrderCategory = "all"
    status: Literal["active", "paid", "pending", "inactive", "all"] = "active"
    include_unpaid_business: bool = False
    search: str = Field(default="", max_length=100)
    category: str = Field(default="", max_length=100)
    limit: int = Field(default=25, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.ends_on < self.starts_on:
            raise ValueError("結束日期不可早於開始日期")
        if self.date_basis == "pickup" and self.order_category != "meal":
            raise ValueError("取餐日期僅適用便當訂單")
        return self


def print_summary_filters(filters: OrderSummaryFilters) -> dict:
    values = filters.model_copy(update={"status": "paid"}).model_dump(mode="json", exclude={"limit", "offset"})
    if not values["include_unpaid_business"]:
        values.pop("include_unpaid_business")
    return values


def _summary_lines(filters: OrderSummaryFilters, *, confirmed_paid: bool = False):
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
    date_column = (
        func.coalesce(OrderFulfillment.pickup_at, OrderFulfillment.pickup_starts_at)
        if filters.date_basis == "pickup" else Order.created_at
    )
    query = select(
        Order.id.label("order_id"), Order.sales_channel.label("sales_channel"),
        Order.revision.label("order_revision"), Order.order_number.label("order_number"),
        Order.kitchen_note.label("note"), OrderItem.id.label("item_id"),
        OrderItem.product_name.label("name"), OrderItem.unit_label.label("unit"),
        OrderItem.quantity.label("quantity"), category.label("category"),
        source_kind.label("source_kind"), source_id.label("source_id"), state.label("state"),
        Order.meal_booking_type.label("meal_booking_type"),
        case((Order.meal_booking_type == "business", state), else_="all").label("payment_status"),
    ).select_from(OrderItem).join(Order, Order.id == OrderItem.order_id).outerjoin(
        OrderFulfillment, OrderFulfillment.order_id == Order.id,
    ).outerjoin(Product, Product.id == OrderItem.source_product_id).outerjoin(
        MealEventOffering, MealEventOffering.id == OrderItem.source_meal_offering_id,
    ).where(date_column >= start, date_column < end, order_category_condition(filters.order_category))
    if confirmed_paid:
        payment_allowed = and_(Order.payment_status == PaymentStatus.PAID, Order.paid_at.is_not(None))
        if filters.include_unpaid_business:
            payment_allowed = or_(payment_allowed, and_(Order.meal_booking_type == "business",
                Order.payment_method.in_({PaymentMethod.ONLINE, PaymentMethod.CASH_ON_PICKUP}),
                Order.payment_status == PaymentStatus.PENDING,
                OrderFulfillment.status.in_([FulfillmentState.PENDING_CONFIRMATION, FulfillmentState.PREPARING, FulfillmentState.READY_FOR_PICKUP])))
        query = query.where(active, payment_allowed)
    if filters.sales_channel != "all":
        query = query.where(Order.sales_channel == filters.sales_channel)
    if filters.status == "active":
        query = query.where(active)
    elif filters.status != "all" and not (confirmed_paid and filters.include_unpaid_business and filters.status == "paid"):
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
    group_columns = [lines.c.sales_channel, lines.c.source_kind, lines.c.source_id, lines.c.name, lines.c.unit, lines.c.category,
                     lines.c.meal_booking_type, lines.c.payment_status]
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
         **{field: row[field] for field in ["sales_channel", "category", "name", "unit", "quantity", "order_count", "meal_booking_type", "payment_status"]}}
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


MAX_PRINT_SUMMARY_ITEMS = 5000
MAX_PRINT_SUMMARY_OPTIONS = 30000
_PRODUCT_FIELDS = ("sales_channel", "source_kind", "source_id", "name", "unit", "category", "meal_booking_type", "payment_status")


async def build_print_summary(session: AsyncSession, filters: OrderSummaryFilters) -> dict:
    filters = filters.model_copy(update={"status": "paid"})
    available_lines = _summary_lines(filters, confirmed_paid=True)
    lines = (
        select(available_lines).where(available_lines.c.category == filters.category).subquery()
        if filters.category else available_lines
    )
    rows = (await session.execute(select(lines).order_by(
        lines.c.order_id, lines.c.item_id,
    ).limit(MAX_PRINT_SUMMARY_ITEMS + 1))).mappings().all()
    if len(rows) > MAX_PRINT_SUMMARY_ITEMS:
        raise HTTPException(422, "符合條件的訂單明細超過 5000 筆，請縮小查詢範圍後再列印")
    options = (await session.execute(select(
        OrderItemOption.order_item_id, OrderItemOption.group_name, OrderItemOption.option_name,
    ).join(lines, lines.c.item_id == OrderItemOption.order_item_id).order_by(
        OrderItemOption.order_item_id, OrderItemOption.group_name, OrderItemOption.option_name, OrderItemOption.id,
    ).limit(MAX_PRINT_SUMMARY_OPTIONS + 1))).all()
    if len(options) > MAX_PRINT_SUMMARY_OPTIONS:
        raise HTTPException(422, "符合條件的餐點選項過多，請縮小查詢範圍後再列印")
    options_by_item: dict[str, list[str]] = {}
    for item_id, group_name, option_name in options:
        options_by_item.setdefault(item_id, []).append(f"{group_name}：{option_name}")

    products: dict[tuple, dict] = {}
    notes: dict[str, dict] = {}
    versions: dict[str, int] = {}
    for row in rows:
        identity = tuple(row[field] for field in _PRODUCT_FIELDS)
        product = products.setdefault(identity, {
            "key": json.dumps(identity, ensure_ascii=False, separators=(",", ":")),
            "name": row["name"], "unit": row["unit"], "quantity": 0, "variants": {},
            "meal_booking_type": row["meal_booking_type"], "payment_status": row["payment_status"],
        })
        quantity = row["quantity"]
        product["quantity"] += quantity
        variant = tuple(sorted(options_by_item.get(row["item_id"], [])))
        product["variants"][variant] = product["variants"].get(variant, 0) + quantity
        versions[row["order_id"]] = row["order_revision"]
        if row["note"]:
            note = notes.setdefault(row["order_id"], {
                "order_number": row["order_number"], "note": row["note"], "items": {},
            })
            item_key = (row["name"], row["unit"])
            note["items"][item_key] = note["items"].get(item_key, 0) + quantity

    product_rows = []
    for identity, product in sorted(products.items(), key=lambda pair: (
        -pair[1]["quantity"], pair[0][3], *pair[0][:3], *pair[0][4:],
    )):
        product_rows.append({**product, "variants": [
            {"options": list(options), "quantity": quantity}
            for options, quantity in sorted(product["variants"].items())
        ]})
    counts = {}
    if filters.include_unpaid_business:
        counts = {"paid_order_count": len({row['order_id'] for row in rows if row['state'] == 'paid'}),
                  "pending_business_order_count": len({row['order_id'] for row in rows if row['meal_booking_type'] == 'business' and row['state'] == 'pending'})}
    return {
        "filters": print_summary_filters(filters),
        "summary": {"order_count": len(versions), "quantity": sum(row["quantity"] for row in rows), "product_count": len(products), **counts},
        "products": product_rows,
        "notes": [{**note, "items": [
            {"name": name, "quantity": quantity, "unit": unit}
            for (name, unit), quantity in sorted(note["items"].items())
        ]} for _, note in sorted(notes.items(), key=lambda pair: (pair[1]["order_number"], pair[0]))],
        "source_versions": [{"id": order_id, "revision": revision} for order_id, revision in sorted(versions.items())],
    }
