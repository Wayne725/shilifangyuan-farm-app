"""Atomic quantity revisions preserve the original pricing and payment evidence."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .domain import aware, included_tax_amount, order_amendment_blocked_reason, order_shipping_tax_type
from .integrations.payment_service import lock_order_payment_subject, release_attempt_reservations
from .models import (
    AdminAudit, FulfillmentStatus, GroupBundle, GroupBundleItem, InventoryReservation, MealEventOffering,
    Order, OrderAmendment, OrderFulfillment, OrderItem, PaymentAttempt,
    PaymentMethod, PaymentStatus, PointTransaction, Product, Promotion, Refund,
    ReservationStatus, SalesChannel, TargetType, UserRole,
)
from .platform_settings import platform_settings
from .promotions import lock_promotions
from .schemas import OrderQuantityAmendmentQuote, OrderQuantityAmendmentQuoteLine


def order_snapshot(order):
    fulfillment = order.fulfillment
    shipment = fulfillment.shipment if fulfillment else None
    return {
        "revision": order.revision, "amount_total": order.amount_total,
        "tax_amount": order.tax_amount, "shipping_fee": shipment.shipping_fee if shipment else 0,
        "promotion": order.promotion_snapshot,
        "items": [{
            "order_item_id": item.id, "product_name": item.product_name,
            "unit_label": item.unit_label, "quantity": item.quantity,
            "unit_price": item.unit_price, "subtotal": item.subtotal,
            "tax_type": item.tax_type.value,
            "selected_options": [{"group_name": option.group_name, "option_name": option.option_name,
                                  "price_delta": option.price_delta} for option in item.selected_options],
        } for item in order.items],
    }


def _quantities(order, body):
    requested = {item.order_item_id: item.quantity for item in body.items}
    known = {item.id for item in order.items}
    if len(requested) != len(body.items) or set(requested) != known:
        raise HTTPException(422, "請提供此訂單全部品項，每個品項限填一次")
    if not any(requested.values()):
        raise HTTPException(422, "訂單至少保留一個品項；整單取消請聯絡管理員")
    maximum = 99 if order.sales_channel == SalesChannel.MEAL_PREORDER else 999
    if any(quantity > maximum for quantity in requested.values()):
        raise HTTPException(422, f"每個品項數量不可超過 {maximum}")
    if all(requested[item.id] == item.quantity for item in order.items):
        raise HTTPException(409, "數量沒有變更")
    return requested


async def _load_locked_order(session, order_id, user):
    await lock_order_payment_subject(session, order_id)
    order = await session.scalar(select(Order).where(Order.id == order_id).options(
        selectinload(Order.items).selectinload(OrderItem.selected_options),
        selectinload(Order.meal_event), selectinload(Order.group_campaign),
        selectinload(Order.invoice), selectinload(Order.cancellation_requests),
        selectinload(Order.amendments), selectinload(Order.user),
        selectinload(Order.fulfillment).selectinload(OrderFulfillment.shipment),
    ).with_for_update().execution_options(populate_existing=True))
    if order is None or (order.user_id != user.id and user.user_role != UserRole.ADMIN):
        raise HTTPException(404, "找不到訂單")
    attempts = list(await session.scalars(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id)
        .order_by(PaymentAttempt.id).options(selectinload(PaymentAttempt.reservations))
        .with_for_update().execution_options(populate_existing=True)))
    reservations = list(await session.scalars(select(InventoryReservation).where(InventoryReservation.order_id == order.id)
        .order_by(InventoryReservation.id).with_for_update().execution_options(populate_existing=True)))
    from sqlalchemy.orm.attributes import set_committed_value
    set_committed_value(order, "payment_attempts", attempts)
    set_committed_value(order, "reservations", reservations)
    return order


async def _check_recorded_payment(session, order):
    current_attempts = [attempt for attempt in order.payment_attempts if attempt.order_revision == order.revision]
    if any(attempt.paid_at or attempt.status in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING,
            PaymentStatus.LATE_PAID_REFUND_REQUIRED, PaymentStatus.REFUNDED} for attempt in current_attempts):
        raise HTTPException(409, "付款已有收款或退款紀錄，請由管理員查核")
    if await session.scalar(select(Refund.id).where(Refund.order_id == order.id,
            Refund.purpose != "obsolete_order_revision").limit(1)):
        raise HTTPException(409, "訂單已有退款紀錄，請由管理員處理")
    if await session.scalar(select(PointTransaction.id).where(PointTransaction.reference_id == order.id,
            PointTransaction.amount > 0).limit(1)):
        raise HTTPException(409, "訂單已有積點入帳紀錄，請由管理員查核")


async def _check_resources(session, order, quantities, now):
    active = [row for row in order.reservations if row.status == ReservationStatus.ACTIVE]
    owned = defaultdict(int)
    for row in active:
        owned[row.group_campaign_id or row.source_meal_offering_id or row.source_product_id] += row.quantity
    if order.group_campaign_id:
        campaign = order.group_campaign
        requested = sum(quantities.values())
        if requested > sum(item.quantity for item in order.items):
            target_model = Product if campaign.target_type == TargetType.PRODUCT else GroupBundle
            target = await session.scalar(select(target_model).where(target_model.id == campaign.target_id)
                .with_for_update().execution_options(populate_existing=True))
            if target is None or not target.is_active:
                raise HTTPException(409, "共同購買品項已停用，只能減少數量")
            if campaign.target_type == TargetType.BUNDLE:
                products = await session.scalars(select(Product).join(GroupBundleItem)
                    .where(GroupBundleItem.bundle_id == target.id).order_by(Product.id)
                    .with_for_update(of=Product).execution_options(populate_existing=True))
                if any(not product.is_active for product in products):
                    raise HTTPException(409, "套組包含已停止販售的商品，只能減少數量")
        others = await session.scalar(select(func.coalesce(func.sum(OrderItem.quantity), 0)).join(Order).where(
            Order.group_campaign_id == campaign.id, Order.user_id == order.user_id, Order.id != order.id,
            Order.fulfillment_status != FulfillmentStatus.CANCELLED,
            Order.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.PAID]),
        ))
        if others + requested > campaign.per_user_cap:
            raise HTTPException(409, f"本次共同購買每人上限 {campaign.per_user_cap} 份，其他訂單已選 {others} 份")
        if campaign.paid_quantity + campaign.reserved_quantity - owned[campaign.id] + requested > campaign.supply_cap:
            raise HTTPException(409, "共同購買剩餘數量不足")
        return
    ids = sorted({item.source_meal_offering_id or item.source_product_id for item in order.items
                  if item.source_meal_offering_id or item.source_product_id})
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        resources = {row.id: row for row in await session.scalars(select(MealEventOffering)
            .where(MealEventOffering.id.in_(ids)).options(selectinload(MealEventOffering.meal))
            .order_by(MealEventOffering.id).with_for_update().execution_options(populate_existing=True))}
    else:
        resources = {row.id: row for row in await session.scalars(select(Product).where(Product.id.in_(ids))
            .order_by(Product.id).with_for_update().execution_options(populate_existing=True))}
    requested_by_source = defaultdict(int)
    old_by_source = defaultdict(int)
    for item in order.items:
        source_id = item.source_meal_offering_id or item.source_product_id
        requested_by_source[source_id] += quantities[item.id]
        old_by_source[source_id] += item.quantity
    for source_id, requested in requested_by_source.items():
        if requested == 0:
            continue
        resource = resources.get(source_id)
        if resource is None:
            raise HTTPException(409, "原品項已不存在，請移除該品項或聯絡管理員")
        increasing = requested > old_by_source[source_id]
        enabled = resource.is_active and (resource.meal.is_active if isinstance(resource, MealEventOffering) else resource.daily_sales_enabled)
        if increasing and not enabled:
            raise HTTPException(409, "已停用品項只能減少數量")
        remaining = (resource.capacity - resource.paid_quantity - resource.reserved_quantity
                     if isinstance(resource, MealEventOffering) else resource.stock_quantity)
        if requested > remaining + owned[source_id]:
            raise HTTPException(409, "品項剩餘數量不足，請減少數量後重新確認")
    if order.payment_method == PaymentMethod.CASH_ON_PICKUP:
        actual = defaultdict(int)
        for row in active:
            if row.payment_attempt_id is not None or aware(row.expires_at) <= now:
                raise HTTPException(409, "現場付款保留紀錄已過期或不一致")
            actual[row.source_meal_offering_id] += row.quantity
        if dict(actual) != dict(old_by_source):
            raise HTTPException(409, "現場付款保留數量不完整，請由管理員查核")


async def _promotion_quote(session, order, subtotal, now):
    if not order.promotion_id:
        return None, 0
    await lock_promotions(session)
    old = order.promotion_snapshot or {}
    rule = await session.get(Promotion, order.promotion_id)
    if rule is None:
        raise HTTPException(409, "原優惠資料不完整，請由管理員查核")
    # New snapshots retain the original offer; legacy orders explicitly reprice the current rule.
    pricing = old.get("pricing")
    eligible = old.get("eligible", True)
    if pricing is None:
        pricing = {"discount_kind": rule.discount_kind, "discount_value": rule.discount_value,
                   "minimum_amount": rule.minimum_amount}
        eligible = rule.enabled and aware(rule.starts_at) <= now < aware(rule.ends_at)
    discount = 0
    if eligible and subtotal >= pricing["minimum_amount"]:
        discount = min(subtotal - 1, pricing["discount_value"] if pricing["discount_kind"] == "fixed"
                       else subtotal * pricing["discount_value"] // 100)
    return {**old, "id": rule.id, "name": old.get("name", rule.name), "code": old.get("code", rule.code),
            "pricing": pricing, "eligible": eligible, "discount": max(0, discount), "original_amount": subtotal}, max(0, discount)


async def _quote(session, order, quantities, now):
    subtotal = sum(item.unit_price * quantities[item.id] for item in order.items)
    promotion, discount = await _promotion_quote(session, order, subtotal, now)
    lines = []
    remaining = discount
    retained = [item for item in order.items if quantities[item.id]]
    last_id = retained[-1].id
    for item in order.items:
        line_amount = item.unit_price * quantities[item.id]
        reduction = remaining if item.id == last_id else (discount * line_amount // subtotal if subtotal else 0)
        remaining -= reduction
        lines.append(OrderQuantityAmendmentQuoteLine(order_item_id=item.id, product_name=item.product_name,
            unit_label=item.unit_label, previous_quantity=item.quantity, quantity=quantities[item.id],
            unit_price=item.unit_price, subtotal=line_amount - reduction))
    from .routers.logistics import quote_quantity_amendment_shipping
    shipping = await quote_quantity_amendment_shipping(session, order, subtotal - discount, {item.id for item in retained})
    if subtotal - discount + shipping <= 0:
        raise HTTPException(422, "修改後訂單金額必須大於零，請由管理員處理零元訂單")
    tax_by_id = {item.id: item.tax_type for item in order.items}
    tax = sum(included_tax_amount(line.subtotal, tax_by_id[line.order_item_id]) for line in lines)
    tax += included_tax_amount(shipping, order_shipping_tax_type(order))
    return OrderQuantityAmendmentQuote(order_id=order.id, expected_revision=order.revision,
        previous_total=order.amount_total, amount_total=subtotal - discount + shipping, tax_amount=tax,
        shipping_fee=shipping, discount_amount=discount, items=lines, promotion=promotion)


async def _adjust_cash_reservations(session, order, quantities, now):
    requested = defaultdict(int)
    for item in order.items:
        requested[item.source_meal_offering_id] += quantities[item.id]
    for row in order.reservations:
        if row.status != ReservationStatus.ACTIVE:
            continue
        offering = await session.get(MealEventOffering, row.source_meal_offering_id)
        new_quantity = requested.pop(row.source_meal_offering_id, 0)
        offering.reserved_quantity += new_quantity - row.quantity
        if new_quantity:
            row.quantity = new_quantity
        else:
            row.status = ReservationStatus.RELEASED
            row.released_at = now


async def prepare_quantity_amendment(session, order_id, body, user, settings, *, confirm=False):
    existing_owner = await session.scalar(select(Order.user_id).where(Order.id == order_id))
    if existing_owner is None or (existing_owner != user.id and user.user_role != UserRole.ADMIN):
        raise HTTPException(404, "找不到訂單")
    # New-order and amendment lock order both take the platform setting before the parent.
    existing_quantities = dict((await session.execute(select(OrderItem.id, OrderItem.quantity).where(OrderItem.order_id == order_id))).all())
    increasing = any(item.quantity > existing_quantities.get(item.order_item_id, 0) for item in body.items)
    platform = await platform_settings(session, lock=True) if increasing else None
    order = await _load_locked_order(session, order_id, user)
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    if confirm:
        existing = next((row for row in order.amendments if row.client_request_id == str(body.client_request_id)), None)
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise HTTPException(409, "此修改識別碼已使用，請重新確認")
            return order, None
    if platform is not None and not platform.orders_open:
        raise HTTPException(409, platform.closed_message)
    if order.revision != body.expected_revision:
        raise HTTPException(409, "訂單已更新，請重新載入並確認數量")
    now = datetime.now(timezone.utc)
    reason = order_amendment_blocked_reason(order, now)
    if reason:
        raise HTTPException(409, reason)
    await _check_recorded_payment(session, order)
    quantities = _quantities(order, body)
    if any(quantities[item.id] > item.quantity for item in order.items):
        from .sales_scope import require_sales_scope_allows
        require_sales_scope_allows(settings, sales_channel=order.sales_channel,
            fulfillment_method=order.fulfillment_method, meal_event_id=order.meal_event_id)
    if order.payment_method != PaymentMethod.CASH_ON_PICKUP and any(
        row.status == ReservationStatus.ACTIVE and row.payment_attempt_id is None for row in order.reservations
    ):
        raise HTTPException(409, "訂單庫存保留缺少付款紀錄，請由管理員查核")
    await _check_resources(session, order, quantities, now)
    quote = await _quote(session, order, quantities, now)
    if not confirm:
        return order, quote
    if body.expected_total != quote.amount_total:
        raise HTTPException(409, detail={"message": "優惠或運費已變動，請重新確認總額", "quote": quote.model_dump(mode="json")})
    before = order_snapshot(order)
    if order.payment_method == PaymentMethod.CASH_ON_PICKUP:
        await _adjust_cash_reservations(session, order, quantities, now)
    else:
        for attempt in order.payment_attempts:
            if attempt.superseded_at is None:
                await release_attempt_reservations(session, attempt)
                attempt.superseded_at = now
                if attempt.status == PaymentStatus.PENDING:
                    attempt.status = PaymentStatus.EXPIRED
    by_id = {line.order_item_id: line for line in quote.items}
    for item in list(order.items):
        line = by_id[item.id]
        if line.quantity == 0:
            order.items.remove(item)
        else:
            item.quantity = line.quantity
            item.subtotal = line.subtotal
    order.revision += 1
    order.amount_total = quote.amount_total
    order.tax_amount = quote.tax_amount
    order.promotion_snapshot = quote.promotion
    if order.fulfillment and order.fulfillment.shipment:
        order.fulfillment.shipment.shipping_fee = quote.shipping_fee
    after = order_snapshot(order)
    amendment = OrderAmendment(order=order, actor_id=user.id, client_request_id=str(body.client_request_id),
        request_fingerprint=fingerprint, previous_revision=order.revision - 1, revision=order.revision,
        before_snapshot=before, after_snapshot=after, created_at=now)
    session.add(amendment)
    session.add(AdminAudit(actor_id=user.id, action="order.quantity_amendment", aggregate_type="order",
        aggregate_id=order.id, reason="未付款訂單修改數量", data={"previous_revision": order.revision - 1,
        "revision": order.revision, "before": before, "after": after}))
    from .printing import enqueue_amendment_print
    await enqueue_amendment_print(session, order, before, settings)
    await session.flush()
    return order, quote
