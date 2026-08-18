from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import (
    get_current_user,
    membership_type_for_user,
    require_admin,
)
from ..database import get_session
from ..domain import (
    DomainError,
    ensure_self_cancel_allowed,
    included_tax_amount,
    order_available_actions,
    price_for_membership,
    remove_paid_quantity,
)
from ..models import (
    AdminAudit,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    GroupDecisionStatus,
    InventoryReservation,
    InvoiceStatus,
    MealEventOffering,
    Notification,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentStatus,
    PickupLocation,
    Product,
    Refund,
    RefundStatus,
    ReservationStatus,
    SalesChannel,
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
    fulfillment = order.__dict__.get("fulfillment")
    shipment = (
        fulfillment.shipment
        if fulfillment is not None
        else None
    )
    return OrderRead(
        id=order.id,
        order_number=order.order_number,
        order_kind=order.order_kind,
        sales_channel=order.sales_channel,
        fulfillment_method=order.fulfillment_method,
        group_campaign_id=order.group_campaign_id,
        meal_event_id=order.meal_event_id,
        membership_type_snapshot=order.membership_type_snapshot,
        amount_total=order.amount_total,
        tax_amount=order.tax_amount,
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
        fulfillment=fulfillment,
        shipment=shipment,
        meal_event=order.meal_event,
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
        membership_type = membership_type_for_user(user)
        unit_price = price_for_membership(
            product.member_price,
            product.nonmember_price,
            membership_type,
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
        membership_type=membership_type_for_user(user),
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
            selectinload(Order.meal_event),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
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
    if body.fulfillment_method not in {
        FulfillmentMethod.COOPERATIVE_PICKUP,
        FulfillmentMethod.ECPAY_LOGISTICS,
    }:
        raise HTTPException(status_code=422, detail="一般訂單不支援此履約方式")
    quote = await quote_products(
        session, consolidate_lines(body.items), user, lock=True
    )
    pickup_location = None
    if body.pickup_location_id is not None:
        if body.fulfillment_method != FulfillmentMethod.COOPERATIVE_PICKUP:
            raise HTTPException(
                status_code=422,
                detail="只有合作社取貨可選擇領取地點",
            )
        pickup_location = await session.scalar(
            select(PickupLocation).where(
                PickupLocation.id == body.pickup_location_id,
                PickupLocation.is_active.is_(True),
            )
        )
        if pickup_location is None:
            raise HTTPException(status_code=422, detail="找不到可用的領取地點")
    order = Order(
        order_number=make_order_number(),
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=body.fulfillment_method,
        user_id=user.id,
        membership_type_snapshot=membership_type_for_user(user),
        amount_total=quote.amount_total,
        tax_amount=sum(
            included_tax_amount(line.subtotal, line.tax_type)
            for line in quote.items
        ),
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
        fulfillment=OrderFulfillment(
            method=body.fulfillment_method,
            status=FulfillmentState.PENDING_CONFIRMATION,
            pickup_location_id=(
                pickup_location.id if pickup_location is not None else None
            ),
            pickup_location=(
                pickup_location.name if pickup_location is not None else None
            ),
        ),
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
        selectinload(Order.meal_event),
        selectinload(Order.fulfillment).selectinload(
            OrderFulfillment.shipment
        ),
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
        elif reservation.source_meal_offering_id is not None:
            offering = await session.get(
                MealEventOffering,
                reservation.source_meal_offering_id,
            )
            if offering is not None:
                offering.reserved_quantity = max(
                    0,
                    offering.reserved_quantity - reservation.quantity,
                )


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
        elif reservation.source_meal_offering_id is not None:
            offering = await session.get(
                MealEventOffering,
                reservation.source_meal_offering_id,
            )
            if offering is not None:
                offering.paid_quantity = max(
                    0,
                    offering.paid_quantity - reservation.quantity,
                )
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
    fulfillment = order.__dict__.get("fulfillment")
    if fulfillment is not None:
        fulfillment.status = FulfillmentState.CANCELLED
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
        fulfillment = order.__dict__.get("fulfillment")
        if fulfillment is not None:
            fulfillment.status = FulfillmentState.CANCELLED
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
        order.fulfillment_method == FulfillmentMethod.ECPAY_LOGISTICS
        and body.status != FulfillmentStatus.PREPARING
    ):
        raise HTTPException(
            status_code=409,
            detail="物流訂單只能先由後台標記為備貨中，後續請由物流模組推進",
        )
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        raise HTTPException(
            status_code=409,
            detail="便當訂單請由便當場次取餐流程推進",
        )
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
    fulfillment = order.__dict__.get("fulfillment")
    if fulfillment is not None:
        state_map = {
            FulfillmentStatus.PENDING_CONFIRMATION: (
                FulfillmentState.PENDING_CONFIRMATION
            ),
            FulfillmentStatus.PREPARING: FulfillmentState.PREPARING,
            FulfillmentStatus.READY_FOR_PICKUP: (
                FulfillmentState.READY_FOR_PICKUP
            ),
            FulfillmentStatus.PICKED_UP: FulfillmentState.PICKED_UP,
            FulfillmentStatus.CANCELLED: FulfillmentState.CANCELLED,
        }
        fulfillment.status = state_map[body.status]
        if body.pickup_starts_at is not None:
            fulfillment.pickup_starts_at = body.pickup_starts_at
            fulfillment.pickup_ends_at = body.pickup_ends_at
        if body.status == FulfillmentStatus.PICKED_UP:
            fulfillment.fulfilled_at = datetime.now(timezone.utc)
    if body.status == FulfillmentStatus.PICKED_UP:
        order.invoice_status = InvoiceStatus.PENDING
        session.add(
            OutboxEvent(
                event_type="invoice.issue_requested",
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
