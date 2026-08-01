from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any

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
from ..domain import DomainError
from ..models import (
    AdminAudit,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    InvoiceStatus,
    Meal,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    Notification,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentStatus,
    Refund,
    RefundStatus,
    SalesChannel,
    TaxType,
    User,
)
from ..schemas import (
    ActivityReview,
    MealCreate,
    MealEventCreate,
    MealEventRead,
    MealOfferingRead,
    MealOrderCreate,
    MealPickupVerify,
    MealRead,
)
from ..v2_domain import (
    can_cancel_meal_order,
    validate_meal_preorder,
)


meals_router = APIRouter(tags=["meals"])


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _meal_event_read(event: MealEvent) -> MealEventRead:
    return MealEventRead(
        id=event.id,
        title=event.title,
        location=event.location,
        ordering_starts_at=event.ordering_starts_at,
        ordering_ends_at=event.ordering_ends_at,
        pickup_starts_at=event.pickup_starts_at,
        pickup_ends_at=event.pickup_ends_at,
        status=event.status,
        offerings=[
            MealOfferingRead.model_validate(offering)
            for offering in sorted(
                event.offerings,
                key=lambda item: item.position,
            )
            if offering.is_active
        ],
    )


async def _load_event(
    session: AsyncSession,
    event_id: str,
    *,
    for_update: bool = False,
) -> MealEvent | None:
    query = (
        select(MealEvent)
        .where(MealEvent.id == event_id)
        .options(
            selectinload(MealEvent.offerings).selectinload(
                MealEventOffering.meal
            )
        )
    )
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


_MEAL_FULFILLMENT_LABELS = {
    FulfillmentState.PENDING_CONFIRMATION: "pending",
    FulfillmentState.PREPARING: "pending",
    FulfillmentState.READY_FOR_PICKUP: "ready",
    FulfillmentState.PICKED_UP: "picked_up",
    FulfillmentState.NO_SHOW: "no_show",
    FulfillmentState.CANCELLED: "cancelled",
}


async def _meal_order_read(
    session: AsyncSession,
    order: Order,
) -> dict[str, Any]:
    """Shape a meal pre-order for the App; never exposes the pickup QR token."""
    offering_ids = {
        item.source_meal_offering_id
        for item in order.items
        if item.source_meal_offering_id is not None
    }
    meal_ids: dict[str, str] = {}
    if offering_ids:
        offerings = await session.scalars(
            select(MealEventOffering).where(
                MealEventOffering.id.in_(offering_ids)
            )
        )
        meal_ids = {
            offering.id: offering.meal_id for offering in offerings
        }
    fulfillment = order.fulfillment
    event = order.meal_event
    return {
        "id": order.id,
        "order_number": order.order_number,
        "meal_event_id": order.meal_event_id,
        "meal_event_title": event.title if event is not None else "",
        "venue_name": event.location if event is not None else "",
        "pickup_start": event.pickup_starts_at if event is not None else None,
        "pickup_end": event.pickup_ends_at if event is not None else None,
        "pickup_code": (
            fulfillment.pickup_code if fulfillment is not None else None
        ),
        "payment_status": order.payment_status.value,
        "fulfillment_status": _MEAL_FULFILLMENT_LABELS.get(
            fulfillment.status if fulfillment is not None else None,
            "pending",
        ),
        "amount_total": order.amount_total,
        "created_at": order.created_at,
        "items": [
            {
                "meal_id": meal_ids.get(item.source_meal_offering_id or "", ""),
                "meal_name": item.product_name,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "subtotal": item.subtotal,
            }
            for item in order.items
        ],
    }


def _meal_order_query():
    return (
        select(Order)
        .where(Order.sales_channel == SalesChannel.MEAL_PREORDER)
        .options(
            selectinload(Order.items),
            selectinload(Order.meal_event),
            selectinload(Order.fulfillment),
        )
    )


@meals_router.get("/v1/meal-orders")
async def list_meal_orders(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    orders = list(
        await session.scalars(
            _meal_order_query()
            .where(Order.user_id == user.id)
            .order_by(Order.created_at.desc())
        )
    )
    return [await _meal_order_read(session, order) for order in orders]


@meals_router.get("/v1/meal-orders/{order_id}")
async def get_meal_order(
    order_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    order = await session.scalar(
        _meal_order_query().where(
            Order.id == order_id,
            Order.user_id == user.id,
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="找不到便當訂單")
    return await _meal_order_read(session, order)


async def _unique_pickup_code(session: AsyncSession) -> str:
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        existing = await session.scalar(
            select(OrderFulfillment.id).where(
                OrderFulfillment.pickup_code == code
            )
        )
        if existing is None:
            return code
    raise HTTPException(status_code=503, detail="暫時無法產生取餐碼")


@meals_router.get("/v1/meal-events", response_model=list[MealEventRead])
async def list_meal_events(
    session: AsyncSession = Depends(get_session),
) -> list[MealEventRead]:
    events = (
        await session.scalars(
            select(MealEvent)
            .where(
                MealEvent.status.in_(
                    [
                        MealEventStatus.PUBLISHED,
                        MealEventStatus.ORDERING_CLOSED,
                        MealEventStatus.PICKUP_OPEN,
                    ]
                )
            )
            .options(
                selectinload(MealEvent.offerings).selectinload(
                    MealEventOffering.meal
                )
            )
            .order_by(MealEvent.pickup_starts_at)
        )
    ).all()
    return [_meal_event_read(event) for event in events]


@meals_router.get(
    "/v1/meal-events/{event_id}",
    response_model=MealEventRead,
)
async def get_meal_event(
    event_id: str,
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id)
    if event is None or event.status == MealEventStatus.DRAFT:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    return _meal_event_read(event)


@meals_router.post("/v1/meal-events/{event_id}/quote")
async def quote_meal_order(
    event_id: str,
    body: MealOrderCreate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    event = await _load_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    by_id = {offering.id: offering for offering in event.offerings}
    now = datetime.now(timezone.utc)
    total = 0
    lines = []
    seen: set[str] = set()
    for requested in body.items:
        if requested.offering_id in seen:
            raise HTTPException(status_code=422, detail="同一便當不可重複")
        seen.add(requested.offering_id)
        offering = by_id.get(requested.offering_id)
        if offering is None:
            raise HTTPException(status_code=404, detail="找不到便當品項")
        try:
            validate_meal_preorder(
                event,
                offering,
                requested.quantity,
                now,
            )
        except DomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        subtotal = offering.price * requested.quantity
        total += subtotal
        lines.append(
            {
                "offering_id": offering.id,
                "name": offering.meal.name,
                "quantity": requested.quantity,
                "unit_price": offering.price,
                "subtotal": subtotal,
            }
        )
    return {
        "sales_channel": SalesChannel.MEAL_PREORDER.value,
        "fulfillment_method": FulfillmentMethod.EVENT_PICKUP.value,
        "items": lines,
        "amount_total": total,
        "pickup": {
            "location": event.location,
            "starts_at": event.pickup_starts_at,
            "ends_at": event.pickup_ends_at,
        },
    }


@meals_router.post(
    "/v1/meal-events/{event_id}/orders",
    status_code=status.HTTP_201_CREATED,
)
async def create_meal_order(
    event_id: str,
    body: MealOrderCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    event = await _load_event(session, event_id, for_update=True)
    if event is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    quote = await quote_meal_order(event_id, body, session)
    by_id = {offering.id: offering for offering in event.offerings}
    now = datetime.now(timezone.utc)
    order_number = f"M{now:%y%m%d%H%M%S}{secrets.randbelow(1000):03d}"
    pickup_code = await _unique_pickup_code(session)
    qr_token = secrets.token_urlsafe(32)
    order = Order(
        order_number=order_number,
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user_id=user.id,
        meal_event_id=event.id,
        membership_type_snapshot=membership_type_for_user(user),
        amount_total=quote["amount_total"],
        contact_email=body.contact_email.lower(),
        invoice_carrier_type=body.invoice_carrier_type,
        invoice_carrier_value=body.invoice_carrier_value,
        payment_status=PaymentStatus.PENDING,
        invoice_status=InvoiceStatus.NOT_ELIGIBLE,
        fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[
            OrderItem(
                source_meal_offering_id=requested.offering_id,
                product_name=by_id[requested.offering_id].meal.name,
                unit_label="份",
                quantity=requested.quantity,
                unit_price=by_id[requested.offering_id].price,
                subtotal=(
                    by_id[requested.offering_id].price * requested.quantity
                ),
                tax_type=by_id[requested.offering_id].meal.tax_type,
            )
            for requested in body.items
        ],
        fulfillment=OrderFulfillment(
            method=FulfillmentMethod.EVENT_PICKUP,
            status=FulfillmentState.PENDING_CONFIRMATION,
            pickup_location=event.location,
            pickup_starts_at=event.pickup_starts_at,
            pickup_ends_at=event.pickup_ends_at,
            pickup_code=pickup_code,
            pickup_qr_token_hash=hashlib.sha256(
                qr_token.encode("utf-8")
            ).hexdigest(),
        ),
    )
    session.add(order)
    await session.commit()
    return {
        "id": order.id,
        "order_number": order.order_number,
        "sales_channel": order.sales_channel.value,
        "fulfillment_method": order.fulfillment_method.value,
        "amount_total": order.amount_total,
        "payment_status": order.payment_status.value,
        "pickup_code": pickup_code,
        "pickup_qr_token": qr_token,
        "available_actions": ["pay", "cancel"],
    }


@meals_router.post("/v1/meal-orders/{order_id}/cancel")
async def cancel_meal_order(
    order_id: str,
    body: ActivityReview,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    order = await session.scalar(
        select(Order)
        .where(
            Order.id == order_id,
            Order.user_id == user.id,
            Order.sales_channel == SalesChannel.MEAL_PREORDER,
        )
        .options(
            selectinload(Order.meal_event),
            selectinload(Order.items),
            selectinload(Order.fulfillment),
        )
        .with_for_update()
    )
    if order is None or order.meal_event is None:
        raise HTTPException(status_code=404, detail="找不到便當訂單")
    if order.cancelled_at is not None:
        raise HTTPException(status_code=409, detail="訂單已取消")
    if not can_cancel_meal_order(
        order.paid_at,
        order.meal_event.ordering_ends_at,
    ):
        raise HTTPException(status_code=409, detail="已超過自行取消期限")
    now = datetime.now(timezone.utc)
    if order.payment_status == PaymentStatus.PAID:
        order.payment_status = PaymentStatus.REFUNDED
        session.add(
            Refund(
                order_id=order.id,
                amount=order.amount_total,
                status=RefundStatus.COMPLETED,
                reason=body.reason or "買家於期限內取消便當預購",
                requested_by_id=user.id,
                completed_at=now,
            )
        )
        for item in order.items:
            if item.source_meal_offering_id is None:
                continue
            offering = await session.scalar(
                select(MealEventOffering)
                .where(
                    MealEventOffering.id
                    == item.source_meal_offering_id
                )
                .with_for_update()
            )
            if offering is not None:
                offering.paid_quantity = max(
                    0,
                    offering.paid_quantity - item.quantity,
                )
    else:
        order.payment_status = PaymentStatus.EXPIRED
    order.cancelled_at = now
    order.cancellation_reason = body.reason or "買家取消"
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    if order.fulfillment is not None:
        order.fulfillment.status = FulfillmentState.CANCELLED
    await session.commit()
    return {
        "id": order.id,
        "payment_status": order.payment_status.value,
        "fulfillment_status": FulfillmentState.CANCELLED.value,
    }


@meals_router.get(
    "/v1/admin/meals",
    response_model=list[MealRead],
)
async def admin_list_meals(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MealRead]:
    meals = (
        await session.scalars(select(Meal).order_by(Meal.name))
    ).all()
    return [MealRead.model_validate(meal) for meal in meals]


@meals_router.post(
    "/v1/admin/meals",
    response_model=MealRead,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_meal(
    body: MealCreate,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealRead:
    slug = f"meal-{secrets.token_hex(6)}"
    meal = Meal(slug=slug, **body.model_dump())
    session.add(meal)
    await session.commit()
    return MealRead.model_validate(meal)


@meals_router.get(
    "/v1/admin/meal-events",
    response_model=list[MealEventRead],
)
async def admin_list_meal_events(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MealEventRead]:
    events = (
        await session.scalars(
            select(MealEvent)
            .options(
                selectinload(MealEvent.offerings).selectinload(
                    MealEventOffering.meal
                )
            )
            .order_by(MealEvent.created_at.desc())
        )
    ).all()
    return [_meal_event_read(event) for event in events]


@meals_router.post(
    "/v1/admin/meal-events",
    response_model=MealEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_meal_event(
    body: MealEventCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    meal_ids = {offering.meal_id for offering in body.offerings}
    meals = (
        await session.scalars(
            select(Meal).where(
                Meal.id.in_(meal_ids),
                Meal.is_active.is_(True),
            )
        )
    ).all()
    if len(meals) != len(meal_ids):
        raise HTTPException(status_code=422, detail="場次包含無效餐點")
    event = MealEvent(
        title=body.title,
        location=body.location,
        ordering_starts_at=body.ordering_starts_at,
        ordering_ends_at=body.ordering_ends_at,
        pickup_starts_at=body.pickup_starts_at,
        pickup_ends_at=body.pickup_ends_at,
        status=MealEventStatus.DRAFT,
        created_by_id=admin.id,
        offerings=[
            MealEventOffering(**offering.model_dump())
            for offering in body.offerings
        ],
    )
    session.add(event)
    await session.commit()
    event = await _load_event(session, event.id)
    assert event is not None
    return _meal_event_read(event)


@meals_router.post(
    "/v1/admin/meal-events/{event_id}/duplicate",
    response_model=MealEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_meal_event(
    event_id: str,
    body: MealEventCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    source = await _load_event(session, event_id)
    if source is None:
        raise HTTPException(status_code=404, detail="找不到原便當場次")
    source_meal_ids = {offering.meal_id for offering in source.offerings}
    if not body.offerings:
        raise HTTPException(status_code=422, detail="新場次需包含餐點")
    if not {
        offering.meal_id for offering in body.offerings
    }.issubset(source_meal_ids):
        raise HTTPException(status_code=422, detail="複製場次不可加入新餐點")
    return await admin_create_meal_event(body, admin, session)


@meals_router.post(
    "/v1/admin/meal-events/{event_id}/publish",
    response_model=MealEventRead,
)
async def publish_meal_event(
    event_id: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True)
    if event is None or event.status != MealEventStatus.DRAFT:
        raise HTTPException(status_code=409, detail="此場次目前不可發布")
    if _aware(event.ordering_ends_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="訂購截止時間已過")
    event.status = MealEventStatus.PUBLISHED
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.publish",
            aggregate_type="meal_event",
            aggregate_id=event.id,
        )
    )
    await session.commit()
    return _meal_event_read(event)


@meals_router.post(
    "/v1/admin/meal-events/{event_id}/open-pickup",
    response_model=MealEventRead,
)
async def open_meal_pickup(
    event_id: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True)
    if event is None or event.status not in {
        MealEventStatus.PUBLISHED,
        MealEventStatus.ORDERING_CLOSED,
    }:
        raise HTTPException(status_code=409, detail="此場次目前不可開放取餐")
    event.status = MealEventStatus.PICKUP_OPEN
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.open_pickup",
            aggregate_type="meal_event",
            aggregate_id=event.id,
        )
    )
    await session.commit()
    return _meal_event_read(event)


@meals_router.post(
    "/v1/admin/meal-events/{event_id}/cancel",
    response_model=MealEventRead,
)
async def cancel_meal_event(
    event_id: str,
    body: ActivityReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫取消原因")
    event = await _load_event(session, event_id, for_update=True)
    if event is None or event.status in {
        MealEventStatus.CANCELLED,
        MealEventStatus.COMPLETED,
    }:
        raise HTTPException(status_code=409, detail="此場次目前不可取消")
    now = datetime.now(timezone.utc)
    event.status = MealEventStatus.CANCELLED
    event.cancelled_at = now
    event.cancellation_reason = body.reason
    orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.meal_event_id == event.id,
                Order.payment_status == PaymentStatus.PAID,
            )
            .options(
                selectinload(Order.items),
                selectinload(Order.fulfillment),
            )
            .with_for_update()
        )
    ).all()
    offerings = {offering.id: offering for offering in event.offerings}
    for order in orders:
        order.payment_status = PaymentStatus.REFUNDED
        order.cancelled_at = now
        order.cancellation_reason = body.reason
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        if order.fulfillment is not None:
            order.fulfillment.status = FulfillmentState.CANCELLED
        session.add(
            Refund(
                order_id=order.id,
                amount=order.amount_total,
                status=RefundStatus.COMPLETED,
                reason=f"便當場次取消：{body.reason}",
                requested_by_id=admin.id,
                completed_at=now,
            )
        )
        session.add(
            Notification(
                user_id=order.user_id,
                event_type="meal_event.cancelled",
                title="便當場次已取消",
                body=f"「{event.title}」已取消，款項已進入 Sandbox 退款紀錄",
                data={"meal_event_id": event.id, "order_id": order.id},
            )
        )
        for item in order.items:
            offering = offerings.get(item.source_meal_offering_id)
            if offering is not None:
                offering.paid_quantity = max(
                    0,
                    offering.paid_quantity - item.quantity,
                )
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.cancel",
            aggregate_type="meal_event",
            aggregate_id=event.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return _meal_event_read(event)


@meals_router.post(
    "/v1/admin/meal-events/{event_id}/redeem"
)
async def redeem_meal_pickup(
    event_id: str,
    body: MealPickupVerify,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    event = await session.get(MealEvent, event_id)
    if event is None or event.status != MealEventStatus.PICKUP_OPEN:
        raise HTTPException(status_code=409, detail="此場次尚未開放取餐")
    order = await session.scalar(
        select(Order)
        .join(OrderFulfillment)
        .where(
            Order.meal_event_id == event.id,
            Order.payment_status == PaymentStatus.PAID,
            OrderFulfillment.pickup_code == body.pickup_code,
        )
        .options(selectinload(Order.fulfillment))
        .with_for_update()
    )
    if order is None or order.fulfillment is None:
        raise HTTPException(status_code=404, detail="找不到有效取餐碼")
    if order.fulfillment.status == FulfillmentState.PICKED_UP:
        raise HTTPException(status_code=409, detail="此取餐碼已核銷")
    now = datetime.now(timezone.utc)
    order.fulfillment.status = FulfillmentState.PICKED_UP
    order.fulfillment.fulfilled_at = now
    order.fulfillment_status = FulfillmentStatus.PICKED_UP
    order.invoice_status = InvoiceStatus.PENDING
    session.add_all(
        [
            OutboxEvent(
                event_type="invoice.issue_requested",
                aggregate_type="order",
                aggregate_id=order.id,
                payload={"order_id": order.id},
            ),
            AdminAudit(
                actor_id=admin.id,
                action="meal_order.redeem",
                aggregate_type="order",
                aggregate_id=order.id,
            ),
        ]
    )
    await session.commit()
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "pickup_code": body.pickup_code,
        "status": FulfillmentState.PICKED_UP.value,
        "redeemed_at": now,
    }


router = meals_router
