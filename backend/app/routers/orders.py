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
from ..platform_settings import require_accepting_orders
from ..config import Settings, get_settings
from ..controlled_product_payments import is_meals_test_product_order, meals_test_product_enabled, meals_test_product_pairs
from ..sales_scope import require_sales_scope_allows
from ..domain import (
    DomainError,
    aware,
    ensure_admin_cancel_allowed,
    included_tax_amount,
    member_sale_is_tax_exempt,
    order_available_actions,
    order_fulfillment_is_irreversible,
    price_for_membership,
    remove_paid_quantity,
    sale_tax_type,
)
from ..integrations.invoice_service import enqueue_invoice_issue, invoice_context_from_settings
from ..integrations.payment_service import (
    PaymentApplicationError,
    create_provider_aware_refund,
    lock_order_payment_subject,
)
from ..models import (
    AdminAudit,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    GroupCampaign,
    GroupDecisionStatus,
    InventoryReservation,
    MealEvent,
    InvoiceBuyerType,
    MealEventOffering,
    MealCancellationRequest,
    Notification,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentStatus,
    PaymentMethod,
    PickupLocation,
    Product,
    ReservationStatus,
    SalesChannel,
    ShipmentStatus,
    User,
    UserRole,
)
from ..schemas import (
    CancelRequest,
    CashConfirmationRequest,
    MealCancellationReview,
    MealCancellationRequestCreate,
    MealCancellationRequestRead,
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
    requests = order.__dict__.get("cancellation_requests", [])
    latest = max(requests, key=lambda request: (aware(request.requested_at), request.id), default=None)
    actions = order_available_actions(order, viewer_is_admin)
    if latest and latest.status == "pending":
        actions = [action for action in actions if action != "refund"]
    return OrderRead(
        id=order.id,
        order_number=order.order_number,
        order_kind=order.order_kind,
        sales_channel=order.sales_channel,
        fulfillment_method=order.fulfillment_method,
        group_campaign_id=order.group_campaign_id,
        meal_event_id=order.meal_event_id,
        membership_type_snapshot=order.membership_type_snapshot,
        member_tax_exempt_snapshot=order.member_tax_exempt_snapshot,
        amount_total=order.amount_total,
        tax_amount=order.tax_amount,
        contact_email=order.contact_email,
        invoice_buyer_type=order.invoice_buyer_type,
        invoice_buyer_tax_id=order.invoice_buyer_tax_id,
        invoice_buyer_name=order.invoice_buyer_name,
        invoice_buyer_email=order.invoice_buyer_email,
        invoice_carrier_type=order.invoice_carrier_type,
        fulfillment_status=order.fulfillment_status,
        payment_status=order.payment_status,
        payment_method=order.payment_method,
        invoice_status=order.invoice_status,
        paid_at=aware(order.paid_at) if order.paid_at is not None else None,
        cancelled_at=order.cancelled_at,
        created_at=order.created_at,
        available_actions=actions,
        cancellation_request=(MealCancellationRequestRead.model_validate(latest).model_copy(update={
            "requested_at": aware(latest.requested_at),
            "reviewed_at": aware(latest.reviewed_at) if latest.reviewed_at else None,
        }) if latest else None),
        items=order.items,
        fulfillment=fulfillment,
        shipment=shipment,
        meal_event=order.meal_event,
        invoice=order.__dict__.get("invoice"),
    )


def consolidate_lines(lines: List[object]) -> Dict[str, int]:
    quantities: Dict[str, int] = {}
    for line in lines:
        quantities[line.product_id] = (
            quantities.get(line.product_id, 0) + line.quantity
        )
        if quantities[line.product_id] > 999:
            raise HTTPException(status_code=422, detail="同一商品合計數量不可超過 999")
    return quantities


async def quote_products(
    session: AsyncSession,
    quantities: Dict[str, int],
    user: User,
    lock: bool = False,
    invoice_buyer_type: InvoiceBuyerType = InvoiceBuyerType.PERSONAL,
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
    membership_type = membership_type_for_user(user)
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
                tax_type=sale_tax_type(
                    product.tax_type, membership_type, invoice_buyer_type
                ),
            )
        )
    return OrderQuoteRead(
        membership_type=membership_type,
        member_tax_exempt=member_sale_is_tax_exempt(
            membership_type, invoice_buyer_type
        ),
        tax_amount=sum(
            included_tax_amount(line.subtotal, line.tax_type)
            for line in lines
        ),
        amount_total=sum(line.subtotal for line in lines),
        items=lines,
    )


async def load_order(
    session: AsyncSession, order_id: str, lock: bool = False, refresh: bool = False
) -> Order:
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.group_campaign),
            selectinload(Order.reservations),
            selectinload(Order.cancellation_requests),
            selectinload(Order.invoice),
            selectinload(Order.meal_event),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
    )
    if lock:
        await lock_order_payment_subject(session, order_id)
        query = query.with_for_update().execution_options(populate_existing=True)
    if refresh:
        query = query.execution_options(populate_existing=True)
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
        session, consolidate_lines(body.items), user, lock=False,
        invoice_buyer_type=body.invoice_buyer_type,
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
    settings: Settings = Depends(get_settings),
) -> OrderRead:
    controlled_product = meals_test_product_enabled(settings)
    if not controlled_product:
        require_sales_scope_allows(
            settings, sales_channel=SalesChannel.REGULAR,
            fulfillment_method=body.fulfillment_method,
        )
    elif (
        len(body.items) != 1
        or body.items[0].product_id not in meals_test_product_pairs(settings)
        or body.items[0].quantity != 1
        or body.fulfillment_method != FulfillmentMethod.COOPERATIVE_PICKUP
    ):
        require_sales_scope_allows(settings)
    await require_accepting_orders(session)
    if body.payment_method != PaymentMethod.ONLINE:
        raise HTTPException(status_code=422, detail="一般商品貨到付款尚未啟用，現場付款目前僅限便當")
    if body.fulfillment_method not in {
        FulfillmentMethod.COOPERATIVE_PICKUP,
        FulfillmentMethod.ECPAY_LOGISTICS,
    }:
        raise HTTPException(status_code=422, detail="一般訂單不支援此履約方式")
    quote = await quote_products(
        session, consolidate_lines(body.items), user, lock=True,
        invoice_buyer_type=body.invoice_buyer_type,
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
        payment_method=body.payment_method,
        invoice_provider_context=invoice_context_from_settings(settings),
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=body.fulfillment_method,
        user_id=user.id,
        membership_type_snapshot=quote.membership_type,
        member_tax_exempt_snapshot=quote.member_tax_exempt,
        amount_total=quote.amount_total,
        tax_amount=quote.tax_amount,
        contact_email=body.contact_email.lower(),
        invoice_buyer_type=body.invoice_buyer_type,
        invoice_buyer_tax_id=(
            body.invoice_buyer_tax_id.strip()
            if body.invoice_buyer_tax_id
            else None
        ),
        invoice_buyer_name=(
            body.invoice_buyer_name.strip()
            if body.invoice_buyer_name
            else None
        ),
        invoice_buyer_email=(
            str(body.invoice_buyer_email).lower()
            if body.invoice_buyer_email
            else None
        ),
        invoice_carrier_type=body.invoice_carrier_type,
        invoice_carrier_value=(
            body.invoice_carrier_value.strip().upper()
            if body.invoice_carrier_value
            else None
        ),
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
    if controlled_product and not await is_meals_test_product_order(session, settings, order):
        require_sales_scope_allows(settings)
    user.notification_email = order.contact_email
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
        selectinload(Order.cancellation_requests),
        selectinload(Order.items),
        selectinload(Order.group_campaign),
        selectinload(Order.meal_event),
        selectinload(Order.invoice),
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
    for reservation in sorted(order.reservations, key=lambda row: row.source_meal_offering_id or row.source_product_id or row.group_campaign_id or ""):
        if reservation.status != ReservationStatus.ACTIVE:
            continue
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now
        if (
            reservation.group_campaign_id is not None
            and reservation.group_campaign_id == order.group_campaign_id
        ):
            campaign = await session.scalar(
                select(GroupCampaign)
                .where(GroupCampaign.id == reservation.group_campaign_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if campaign is None:
                continue
            campaign.reserved_quantity = max(
                0,
                campaign.reserved_quantity - reservation.quantity,
            )
        elif reservation.source_product_id is not None:
            product = await session.scalar(
                select(Product)
                .where(Product.id == reservation.source_product_id)
                .with_for_update()
            )
            if product is not None:
                product.stock_quantity += reservation.quantity
        elif reservation.source_meal_offering_id is not None:
            offering = await session.scalar(
                select(MealEventOffering)
                .where(
                    MealEventOffering.id
                    == reservation.source_meal_offering_id
                )
                .with_for_update()
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
    if order_fulfillment_is_irreversible(order):
        raise HTTPException(
            status_code=409,
            detail="物流已建立或商品已交付，請先完成人工攔截／退回再退款",
        )
    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=409, detail="只有已付款訂單可退款")
    now = datetime.now(timezone.utc)
    try:
        refund, _ = await create_provider_aware_refund(
            session,
            order=order,
            amount=order.amount_total,
            reason=reason,
            requested_by_id=actor.id,
            defer_completion=True,
            now=now,
        )
    except PaymentApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    group_release_quantity = 0
    for reservation in sorted(order.reservations, key=lambda row: row.source_meal_offering_id or row.source_product_id or row.group_campaign_id or ""):
        if reservation.status == ReservationStatus.ACTIVE:
            await release_active_reservations(session, order)
            break
    for reservation in sorted(order.reservations, key=lambda row: row.source_meal_offering_id or row.source_product_id or row.group_campaign_id or ""):
        if reservation.status != ReservationStatus.CONSUMED:
            continue
        if (
            order.order_kind == OrderKind.GROUP
            and reservation.group_campaign_id == order.group_campaign_id
        ):
            group_release_quantity += reservation.quantity
        elif reservation.source_product_id is not None:
            product = await session.scalar(
                select(Product)
                .where(Product.id == reservation.source_product_id)
                .with_for_update()
            )
            if product is not None:
                product.stock_quantity += reservation.quantity
        elif reservation.source_meal_offering_id is not None:
            offering = await session.scalar(
                select(MealEventOffering)
                .where(
                    MealEventOffering.id
                    == reservation.source_meal_offering_id
                )
                .with_for_update()
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
        and order.group_campaign_id is not None
    ):
        campaign = await session.scalar(
            select(GroupCampaign)
            .where(GroupCampaign.id == order.group_campaign_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if campaign is None:
            raise HTTPException(status_code=409, detail="找不到團購資料")
        remove_paid_quantity(
            campaign, group_release_quantity, now
        )
    order.payment_status = PaymentStatus.REFUND_PENDING
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    fulfillment = order.__dict__.get("fulfillment")
    if fulfillment is not None:
        fulfillment.status = FulfillmentState.CANCELLED
        shipment = fulfillment.__dict__.get("shipment")
        if shipment is not None and shipment.status in {
            ShipmentStatus.DRAFT,
            ShipmentStatus.SELECTION_PENDING,
            ShipmentStatus.READY_TO_CREATE,
        }:
            shipment.status = ShipmentStatus.CANCELLED
    order.cancelled_at = now
    order.cancellation_reason = reason


@orders_router.post("/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(
    order_id: str,
    body: CancelRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    raise HTTPException(status_code=403, detail="退單請致電客服，由管理員協助辦理")


async def _cancel_approved_order(session: AsyncSession, order: Order, admin: User, reason: str) -> None:
    try:
        ensure_admin_cancel_allowed(order, datetime.now(timezone.utc))
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if order.payment_status == PaymentStatus.PAID:
        await request_order_refund(session, order, admin, reason)
    else:
        await release_active_reservations(session, order)
        order.payment_status = PaymentStatus.EXPIRED
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        if order.fulfillment is not None:
            order.fulfillment.status = FulfillmentState.CANCELLED
            shipment = order.fulfillment.__dict__.get("shipment")
            if shipment is not None:
                shipment.status = ShipmentStatus.CANCELLED
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = reason
    session.add(OutboxEvent(event_type="order.cancelled", aggregate_type="order", aggregate_id=order.id, payload={"reason": reason}))


@orders_router.post("/{order_id}/admin/cancellation-requests/{request_id}/review", response_model=OrderRead)
async def review_order_cancellation(
    order_id: str,
    request_id: str,
    body: MealCancellationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await load_order(session, order_id, lock=True)
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        raise HTTPException(status_code=409, detail="便當退單請由便當退單審核流程處理")
    request = await session.scalar(select(MealCancellationRequest).where(
        MealCancellationRequest.id == request_id, MealCancellationRequest.order_id == order_id,
    ).with_for_update().execution_options(populate_existing=True))
    if request is None:
        raise HTTPException(status_code=404, detail="找不到退單申請")
    result = "approved" if body.decision == "approve" else "rejected"
    if request.status != "pending":
        if request.status == result:
            return order_read(order, True)
        raise HTTPException(status_code=409, detail="此申請已完成審核，不能改成另一個結果")
    if body.decision == "approve":
        await _cancel_approved_order(session, order, admin, request.reason)
    request.status = result
    request.reviewed_by_id = admin.id
    request.reviewed_at = datetime.now(timezone.utc)
    request.review_reason = body.reason
    session.add(AdminAudit(actor_id=admin.id, action=f"order_cancellation.{result}",
        aggregate_type="order", aggregate_id=order.id, reason=body.reason, data={"request_id": request.id}))
    await session.commit()
    return order_read(order, True)


@orders_router.post("/{order_id}/admin/cancel", response_model=OrderRead)
async def admin_cancel_order(
    order_id: str,
    body: MealCancellationRequestCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await load_order(session, order_id, lock=True)
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        raise HTTPException(status_code=409, detail="便當退單請由便當管理員退單流程處理")
    if order.cancelled_at is not None:
        return order_read(order, True)
    pending = next((request for request in order.cancellation_requests if request.status == "pending"), None)
    if pending is not None:
        return await review_order_cancellation(order_id, pending.id, MealCancellationReview(decision="approve", reason=body.reason), admin, session)
    await _cancel_approved_order(session, order, admin, body.reason)
    now = datetime.now(timezone.utc)
    request = MealCancellationRequest(order=order, requested_by_id=admin.id, reason=body.reason,
        status="approved", requested_at=now, reviewed_by_id=admin.id, reviewed_at=now, review_reason=body.reason)
    session.add(request)
    await session.flush()
    session.add(AdminAudit(actor_id=admin.id, action="order_cancellation.approved", aggregate_type="order", aggregate_id=order.id,
        reason=body.reason, data={"request_id": request.id, "source": "phone"}))
    await session.commit()
    return order_read(order, True)


@orders_router.post("/{order_id}/admin/refund", response_model=OrderRead)
async def refund_order(
    order_id: str,
    body: RefundRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await load_order(session, order_id, lock=True)
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        raise HTTPException(status_code=409, detail="便當退款請由便當退單審核流程處理")
    if any(request.status == "pending" for request in order.cancellation_requests):
        raise HTTPException(status_code=409, detail="此訂單有退單待審申請，請先完成退單審核")
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


@orders_router.post("/{order_id}/admin/cash-payment", response_model=OrderRead)
async def receive_cash_payment(
    order_id: str, body: CashConfirmationRequest,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> OrderRead:
    from ..cash_payments import confirm_cash_payment
    event_id = await session.scalar(select(Order.meal_event_id).where(Order.id == order_id))
    if event_id is not None:
        await session.scalar(select(MealEvent).where(MealEvent.id == event_id).with_for_update())
    order = await load_order(session, order_id, lock=True, refresh=True)
    try:
        await confirm_cash_payment(session, order, amount=body.amount, reason=body.reason,
            actor_id=admin.id, settings=settings, now=datetime.now(timezone.utc), collection_method=body.collection_method, reference=body.reference)
    except PaymentApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return order_read(order, True)


@orders_router.post("/{order_id}/admin/cash-unpaid", response_model=OrderRead)
async def correct_cash_payment(
    order_id: str, body: CashConfirmationRequest,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> OrderRead:
    from ..cash_payments import mark_cash_unpaid
    event_id = await session.scalar(select(Order.meal_event_id).where(Order.id == order_id))
    if event_id is not None:
        await session.scalar(select(MealEvent).where(MealEvent.id == event_id).with_for_update())
    order = await load_order(session, order_id, lock=True, refresh=True)
    try:
        await mark_cash_unpaid(session, order, amount=body.amount, reason=body.reason,
            actor_id=admin.id, now=datetime.now(timezone.utc))
    except PaymentApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return order_read(order, True)


@orders_router.post("/{order_id}/admin/cash-refund", response_model=OrderRead)
async def return_cash_payment(
    order_id: str, body: CashConfirmationRequest,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> OrderRead:
    from ..cash_payments import confirm_cash_refund
    order = await load_order(session, order_id, lock=True, refresh=True)
    try:
        await confirm_cash_refund(session, order, amount=body.amount, reason=body.reason,
            actor_id=admin.id, now=datetime.now(timezone.utc))
    except PaymentApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        enqueue_invoice_issue(
            session,
            order,
            trigger="legacy_fulfillment_completed",
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
