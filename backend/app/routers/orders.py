from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, require_admin
from ..database import get_session
from ..domain import (
    DomainError,
    ensure_self_cancel_allowed,
    order_available_actions,
    price_for_membership,
    remove_paid_quantity,
)
from ..models import (
    AdminAudit,
    FulfillmentStatus,
    GroupDecisionStatus,
    InventoryReservation,
    InvoiceStatus,
    Notification,
    Order,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentStatus,
    Product,
    Refund,
    RefundStatus,
    ReservationStatus,
    User,
    UserRole,
)
from ..schemas import (
    CancelRequest,
    FulfillmentUpdate,
    OrderCreate,
    OrderQuoteLine,
    OrderQuoteRead,
    OrderQuoteRequest,
    OrderRead,
    RefundRequest,
)


orders_router = APIRouter(prefix="/v1/orders", tags=["orders"])


def make_order_number() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    return f"SLF{timestamp}{secrets.token_hex(2).upper()}"


def order_read(order: Order, viewer_is_admin: bool) -> OrderRead:
    return OrderRead(
        id=order.id,
        order_number=order.order_number,
        order_kind=order.order_kind,
        group_campaign_id=order.group_campaign_id,
        membership_type_snapshot=order.membership_type_snapshot,
        amount_total=order.amount_total,
        contact_email=order.contact_email,
        invoice_carrier_type=order.invoice_carrier_type,
        fulfillment_status=order.fulfillment_status,
        payment_status=order.payment_status,
        invoice_status=order.invoice_status,
        paid_at=order.paid_at,
        cancelled_at=order.cancelled_at,
        created_at=order.created_at,
        available_actions=order_available_actions(order, viewer_is_admin),
        items=order.items,
    )


def consolidate_lines(lines: List[object]) -> Dict[str, int]:
    quantities: Dict[str, int] = {}
    for line in lines:
        quantities[line.product_id] = (
            quantities.get(line.product_id, 0) + line.quantity
        )
    return quantities


async def quote_products(
    session: AsyncSession,
    quantities: Dict[str, int],
    user: User,
    lock: bool = False,
) -> OrderQuoteRead:
    query = select(Product).where(
        Product.id.in_(quantities.keys()), Product.is_active.is_(True)
    )
    if lock:
        query = query.with_for_update()
    products = list(await session.scalars(query))
    if len(products) != len(quantities):
        raise HTTPException(status_code=422, detail="購物車包含不存在的商品")
    lines: List[OrderQuoteLine] = []
    for product in sorted(products, key=lambda item: item.name):
        quantity = quantities[product.id]
        if product.stock_quantity < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"{product.name} 庫存不足，目前剩餘 {product.stock_quantity}",
            )
        unit_price = price_for_membership(
            product.member_price,
            product.nonmember_price,
            user.membership_type,
        )
        lines.append(
            OrderQuoteLine(
                product_id=product.id,
                product_name=product.name,
                unit_label=product.unit,
                quantity=quantity,
                unit_price=unit_price,
                subtotal=unit_price * quantity,
                tax_type=product.tax_type,
            )
        )
    return OrderQuoteRead(
        membership_type=user.membership_type,
        amount_total=sum(line.subtotal for line in lines),
        items=lines,
    )


async def load_order(
    session: AsyncSession, order_id: str, lock: bool = False
) -> Order:
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.group_campaign),
            selectinload(Order.reservations),
        )
    )
    if lock:
        query = query.with_for_update()
    order = await session.scalar(query)
    if order is None:
        raise HTTPException(status_code=404, detail="找不到訂單")
    return order


@orders_router.post("/quote", response_model=OrderQuoteRead)
async def quote_order(
    body: OrderQuoteRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderQuoteRead:
    return await quote_products(
        session, consolidate_lines(body.items), user, lock=False
    )


@orders_router.post(
    "",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    body: OrderCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    quote = await quote_products(
        session, consolidate_lines(body.items), user, lock=True
    )
    order = Order(
        order_number=make_order_number(),
        order_kind=OrderKind.REGULAR,
        user_id=user.id,
        membership_type_snapshot=user.membership_type,
        amount_total=quote.amount_total,
        contact_email=body.contact_email.lower(),
        invoice_carrier_type=body.invoice_carrier_type,
        invoice_carrier_value=body.invoice_carrier_value,
        items=[
            OrderItem(
                source_product_id=line.product_id,
                product_name=line.product_name,
                unit_label=line.unit_label,
                quantity=line.quantity,
                unit_price=line.unit_price,
                subtotal=line.subtotal,
                tax_type=line.tax_type,
            )
            for line in quote.items
        ],
    )
    session.add(order)
    await session.commit()
    order = await load_order(session, order.id)
    return order_read(order, False)


@orders_router.get("", response_model=List[OrderRead])
async def list_orders(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> List[OrderRead]:
    query = select(Order).options(
        selectinload(Order.items),
        selectinload(Order.group_campaign),
    )
    if user.user_role != UserRole.ADMIN:
        query = query.where(Order.user_id == user.id)
    orders = list(
        await session.scalars(query.order_by(Order.created_at.desc()))
    )
    return [
        order_read(order, user.user_role == UserRole.ADMIN) for order in orders
    ]


@orders_router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await load_order(session, order_id)
    if order.user_id != user.id and user.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=404, detail="找不到訂單")
    return order_read(order, user.user_role == UserRole.ADMIN)


async def release_active_reservations(
    session: AsyncSession, order: Order
) -> None:
    now = datetime.now(timezone.utc)
    for reservation in order.reservations:
        if reservation.status != ReservationStatus.ACTIVE:
            continue
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now
        if (
            order.group_campaign is not None
            and reservation.group_campaign_id == order.group_campaign_id
        ):
            order.group_campaign.reserved_quantity = max(
                0,
                order.group_campaign.reserved_quantity - reservation.quantity,
            )
        elif reservation.source_product_id is not None:
            product = await session.get(Product, reservation.source_product_id)
            if product is not None:
                product.stock_quantity += reservation.quantity


async def request_order_refund(
    session: AsyncSession,
    order: Order,
    actor: User,
    reason: str,
) -> None:
    if order.fulfillment_status == FulfillmentStatus.PICKED_UP:
        raise HTTPException(status_code=409, detail="完成取貨後不可退款")
    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=409, detail="只有已付款訂單可退款")
    now = datetime.now(timezone.utc)
    group_release_quantity = 0
    for reservation in order.reservations:
        if reservation.status == ReservationStatus.ACTIVE:
            await release_active_reservations(session, order)
            break
    for reservation in order.reservations:
        if reservation.status != ReservationStatus.CONSUMED:
            continue
        if (
            order.order_kind == OrderKind.GROUP
            and reservation.group_campaign_id == order.group_campaign_id
        ):
            group_release_quantity += reservation.quantity
        elif reservation.source_product_id is not None:
            product = await session.get(Product, reservation.source_product_id)
            if product is not None:
                product.stock_quantity += reservation.quantity
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now
    if (
        group_release_quantity
        and order.group_campaign is not None
    ):
        remove_paid_quantity(
            order.group_campaign, group_release_quantity, now
        )
    order.payment_status = PaymentStatus.REFUND_PENDING
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    order.cancelled_at = now
    order.cancellation_reason = reason
    refund = Refund(
        order=order,
        amount=order.amount_total,
        status=RefundStatus.PENDING,
        reason=reason,
        requested_by_id=actor.id,
    )
    session.add(refund)
    await session.flush()
    session.add(
        OutboxEvent(
            event_type="refund.requested",
            aggregate_type="order",
            aggregate_id=order.id,
            payload={"refund_id": refund.id, "amount": refund.amount},
        )
    )


@orders_router.post("/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(
    order_id: str,
    body: CancelRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await load_order(session, order_id, lock=True)
    if order.user_id != user.id:
        raise HTTPException(status_code=404, detail="找不到訂單")
    try:
        ensure_self_cancel_allowed(order, datetime.now(timezone.utc))
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if order.payment_status == PaymentStatus.PAID:
        await request_order_refund(session, order, user, body.reason)
    else:
        await release_active_reservations(session, order)
        order.payment_status = PaymentStatus.EXPIRED
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = body.reason
    session.add(
        OutboxEvent(
            event_type="order.cancelled",
            aggregate_type="order",
            aggregate_id=order.id,
            payload={"reason": body.reason},
        )
    )
    await session.commit()
    return order_read(order, False)


@orders_router.post("/{order_id}/admin/refund", response_model=OrderRead)
async def refund_order(
    order_id: str,
    body: RefundRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await load_order(session, order_id, lock=True)
    await request_order_refund(session, order, admin, body.reason)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="order.refund",
            aggregate_type="order",
            aggregate_id=order.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return order_read(order, True)


@orders_router.patch(
    "/{order_id}/admin/fulfillment", response_model=OrderRead
)
@orders_router.post("/{order_id}/fulfillment", response_model=OrderRead)
async def update_fulfillment(
    order_id: str,
    body: FulfillmentUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await load_order(session, order_id, lock=True)
    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=409, detail="未付款訂單無法出貨")
    if (
        order.order_kind == OrderKind.GROUP
        and order.group_campaign is not None
        and order.group_campaign.decision_status != GroupDecisionStatus.CONFIRMED
    ):
        raise HTTPException(status_code=409, detail="團購尚未確認成團")
    allowed = {
        FulfillmentStatus.PENDING_CONFIRMATION: FulfillmentStatus.PREPARING,
        FulfillmentStatus.PREPARING: FulfillmentStatus.READY_FOR_PICKUP,
        FulfillmentStatus.READY_FOR_PICKUP: FulfillmentStatus.PICKED_UP,
    }
    if allowed.get(order.fulfillment_status) != body.status:
        raise HTTPException(status_code=409, detail="訂單履約狀態不可跳級")
    order.fulfillment_status = body.status
    if body.status == FulfillmentStatus.PICKED_UP:
        order.invoice_status = InvoiceStatus.PENDING
        session.add(
            OutboxEvent(
                event_type="invoice.issue",
                aggregate_type="order",
                aggregate_id=order.id,
                payload={"order_id": order.id},
            )
        )
    session.add_all(
        [
            AdminAudit(
                actor_id=admin.id,
                action=f"order.fulfillment.{body.status.value}",
                aggregate_type="order",
                aggregate_id=order.id,
            ),
            Notification(
                user_id=order.user_id,
                event_type=f"order.{body.status.value}",
                title="訂單狀態已更新",
                body=f"訂單 {order.order_number}：{body.status.value}",
                data={"order_id": order.id},
            ),
        ]
    )
    await session.commit()
    return order_read(order, True)


router = orders_router
