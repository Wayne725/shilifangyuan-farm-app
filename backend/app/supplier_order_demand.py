from datetime import date, datetime, time, timezone

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .meal_schedules import TAIPEI
from .models import (
    FulfillmentState, FulfillmentStatus, GroupCampaign, GroupDecisionStatus,
    Order, OrderFulfillment, OrderItem, PaymentStatus, Product, Refund,
    SalesChannel, Supplier, TargetType,
)


def product_procurement_snapshot(product: Product) -> dict[str, str | None]:
    return {
        "product_id": product.id,
        "product_number": product.product_number,
        "sku": product.sku,
        "supplier_id": product.supplier_id,
        "temperature": product.shipping_temperature.value if product.shipping_temperature else None,
    }


async def supplier_order_demand_rows(
    session: AsyncSession, starts_on: date, ends_on: date, *, supplier_id: str | None,
) -> list:
    start = datetime.combine(starts_on, time.min, TAIPEI).astimezone(timezone.utc)
    end = datetime.combine(ends_on, time.max, TAIPEI).astimezone(timezone.utc)
    snapshot = OrderItem.procurement_snapshot
    captured_supplier = snapshot["supplier_id"].as_string()
    refund_recorded = exists(select(Refund.id).where(
        Refund.order_id == Order.id, Refund.purpose != "obsolete_order_revision",
    ))
    cancelled = exists(select(OrderFulfillment.id).where(
        OrderFulfillment.order_id == Order.id, OrderFulfillment.status == FulfillmentState.CANCELLED,
    ))
    lines = (select(
        captured_supplier.label("supplier_id"),
        func.coalesce(snapshot["product_id"].as_string(), OrderItem.source_product_id).label("product_id"),
        snapshot["product_number"].as_string().label("product_number"),
        snapshot["sku"].as_string().label("sku"),
        snapshot["temperature"].as_string().label("temperature"),
        OrderItem.product_name.label("product_name"), OrderItem.unit_label.label("unit"),
        OrderItem.quantity.label("quantity"),
        case(
            (refund_recorded, "退款待核對"),
            (snapshot.is_(None), "歷史品項未留供應者快照"),
            (captured_supplier.is_(None), "下單時未指定供應者"),
            (Supplier.id.is_(None), "供應者主檔已不存在"),
        ).label("review_reason"),
    ).join(Order, Order.id == OrderItem.order_id)
        .outerjoin(GroupCampaign, GroupCampaign.id == Order.group_campaign_id)
        .outerjoin(Supplier, Supplier.id == captured_supplier)
        .where(
            Order.customer_kind == "account",
            Order.payment_status == PaymentStatus.PAID,
            Order.fulfillment_status != FulfillmentStatus.CANCELLED,
            ~cancelled,
            Order.paid_at.between(start, end),
            OrderItem.source_bundle_id.is_(None),
            OrderItem.source_meal_offering_id.is_(None),
            or_(Order.sales_channel == SalesChannel.REGULAR, and_(
                Order.sales_channel == SalesChannel.GROUP,
                GroupCampaign.target_type == TargetType.PRODUCT,
                GroupCampaign.decision_status == GroupDecisionStatus.CONFIRMED,
            )),
        ).subquery())
    if supplier_id is None:
        fields = [lines.c.product_id, lines.c.product_number, lines.c.sku,
                  lines.c.product_name, lines.c.unit, lines.c.review_reason]
        condition = lines.c.review_reason.is_not(None)
    else:
        fields = [lines.c.product_id, lines.c.product_number, lines.c.sku,
                  lines.c.product_name, lines.c.unit, lines.c.temperature]
        condition = and_(lines.c.supplier_id == supplier_id, lines.c.review_reason.is_(None))
    return list((await session.execute(
        select(*fields, func.sum(lines.c.quantity))
        .where(condition).group_by(*fields).order_by(lines.c.product_name, lines.c.product_id, lines.c.unit),
    )).all())
