from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import (
    get_current_user,
    membership_type_for_user,
    require_admin,
)
from ..config import Settings, get_settings
from ..database import get_session
from ..domain import DomainError, included_tax_amount, order_available_actions, order_fulfillment_is_irreversible
from ..integrations.notifications import (
    NotificationCommand,
    NotificationService,
    SQLAlchemyNotificationRepository,
)
from ..integrations.invoice_service import (
    invoice_context_from_settings,
    enqueue_invoice_adjustment_after_refund,
    enqueue_invoice_issue,
)
from ..integrations.payment_service import (
    PaymentApplicationError,
    allow_local_refund_without_payment_attempt,
    create_provider_aware_refund,
    reverse_order_purchase_points,
)
from ..meal_pricing import price_meal_line
from ..meal_schedules import TAIPEI, generate_scheduled_meal_events
from ..sales_scope import DEMO_MEAL_EVENT_IDS
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
    MealOption,
    MealOptionGroup,
    MealScheduleTemplate,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderItemOption,
    OrderKind,
    PaymentStatus,
    ReservationStatus,
    SalesChannel,
    TaxType,
    User,
    new_id,
)
from ..schemas import (
    ActivityReview,
    MealCreate,
    MealEventActionRequest,
    MealEventCancelRequest,
    MealEventCreate,
    MealEventRead,
    MealOptionGroupRead,
    MealOptionRead,
    MealOfferingRead,
    MealOrderCreate,
    MealOrderQuoteRead,
    MealOrderRead,
    MealPickupCredentialRead,
    MealPickupRedemptionRead,
    MealPickupVerify,
    MealRead,
    MealScheduleInput,
    MealScheduleRead,
)
from ..v2_domain import (
    can_cancel_meal_order,
    meal_available_quantity,
    validate_meal_preorder,
)


meals_router = APIRouter(tags=["meals"])


def _require_live_meal_event(event_id: str, settings: Settings) -> None:
    if (
        settings.environment == "production"
        and settings.sales_scope == "meals_only"
        and event_id in DEMO_MEAL_EVENT_IDS
    ):
        raise HTTPException(status_code=409, detail="展示場次不可正式下單，請等待正式場次公告")


def make_meal_order_number(
    order_id: str,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(timezone.utc)
    compact_id = order_id.replace("-", "").upper()
    return f"M{current:%y%m%d%H%M%S}{compact_id[-12:]}"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _meal_option_groups_read(meal: Meal) -> list[MealOptionGroupRead]:
    return [
        MealOptionGroupRead(
            id=group.id,
            name=group.name,
            min_selections=group.min_selections,
            max_selections=group.max_selections,
            position=group.position,
            is_active=group.is_active,
            options=[
                MealOptionRead.model_validate(option)
                for option in sorted(
                    group.options,
                    key=lambda item: item.position,
                )
                if option.is_active
            ],
        )
        for group in sorted(meal.option_groups, key=lambda item: item.position)
        if group.is_active
    ]


def _meal_event_allows_edit(event: MealEvent, now: datetime) -> bool:
    return event.status == MealEventStatus.DRAFT or (
        event.status == MealEventStatus.PUBLISHED and _aware(event.ordering_ends_at) > now
    )


def _meal_event_read(event: MealEvent, *, can_edit: bool = False) -> MealEventRead:
    local_pickup = _aware(event.pickup_starts_at).astimezone(TAIPEI)
    return MealEventRead(
        id=event.id,
        title=event.title,
        location=event.location,
        ordering_starts_at=_aware(event.ordering_starts_at),
        ordering_ends_at=_aware(event.ordering_ends_at),
        pickup_starts_at=_aware(event.pickup_starts_at),
        pickup_ends_at=_aware(event.pickup_ends_at),
        status=event.status,
        service_date=event.service_date or local_pickup.date(),
        meal_period=event.meal_period or ("dinner" if local_pickup.hour >= 15 else "lunch"),
        schedule_template_id=event.schedule_template_id,
        can_edit=can_edit,
        offerings=[
            MealOfferingRead(
                id=offering.id,
                meal_id=offering.meal_id,
                meal_name=offering.meal.name,
                description=offering.meal.description,
                image_url=offering.meal.image_url,
                price=offering.price,
                capacity=offering.capacity,
                reserved_quantity=offering.reserved_quantity,
                paid_quantity=offering.paid_quantity,
                available_quantity=meal_available_quantity(offering),
                position=offering.position,
                is_active=offering.is_active,
                option_groups=_meal_option_groups_read(offering.meal),
            )
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
            ).selectinload(Meal.option_groups).selectinload(
                MealOptionGroup.options
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
) -> MealOrderRead:
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
    if event is None or fulfillment is None:
        raise HTTPException(status_code=500, detail="便當訂單履約資料不完整")
    credential_available = (
        order.payment_status == PaymentStatus.PAID
        and order.cancelled_at is None
        and event.status not in {
            MealEventStatus.CANCELLED,
            MealEventStatus.COMPLETED,
        }
        and fulfillment.status in {
            FulfillmentState.PENDING_CONFIRMATION,
            FulfillmentState.PREPARING,
            FulfillmentState.READY_FOR_PICKUP,
        }
    )
    qr_payload = _pickup_qr_payload(order)
    if (
        not credential_available
        or fulfillment.pickup_qr_token_hash
        != hashlib.sha256(qr_payload.encode("utf-8")).hexdigest()
    ):
        qr_payload = None
    return MealOrderRead(
        id=order.id,
        order_number=order.order_number,
        sales_channel=order.sales_channel,
        fulfillment_method=order.fulfillment_method,
        meal_event_id=event.id,
        meal_event_title=event.title,
        venue_name=event.location,
        pickup_start=_aware(event.pickup_starts_at),
        pickup_end=_aware(event.pickup_ends_at),
        pickup_at=_aware(fulfillment.pickup_at) if fulfillment.pickup_at else None,
        pickup_code=(
            fulfillment.pickup_code if credential_available else None
        ),
        pickup_qr_payload=qr_payload,
        payment_status=order.payment_status,
        invoice_status=order.invoice_status,
        invoice_number=(order.invoice.invoice_number if order.invoice else None),
        invoice_date=(
            _aware(order.invoice.invoice_date)
            if order.invoice and order.invoice.invoice_date
            else None
        ),
        fulfillment_status=_MEAL_FULFILLMENT_LABELS.get(
            fulfillment.status if fulfillment is not None else None,
            "pending",
        ),
        paid_at=_aware(order.paid_at) if order.paid_at is not None else None,
        cancelled_at=(
            _aware(order.cancelled_at)
            if order.cancelled_at is not None
            else None
        ),
        amount_total=order.amount_total,
        created_at=_aware(order.created_at),
        available_actions=order_available_actions(order),
        items=[
            {
                "offering_id": item.source_meal_offering_id or "",
                "meal_id": meal_ids.get(item.source_meal_offering_id or "", ""),
                "meal_name": item.product_name,
                "quantity": item.quantity,
                "base_price": item.unit_price
                - sum(option.price_delta for option in item.selected_options),
                "option_price": sum(
                    option.price_delta for option in item.selected_options
                ),
                "unit_price": item.unit_price,
                "subtotal": item.subtotal,
                "selections": [
                    {
                        "group_id": None,
                        "group_name": option.group_name,
                        "option_id": option.source_meal_option_id,
                        "option_name": option.option_name,
                        "price_delta": option.price_delta,
                    }
                    for option in item.selected_options
                ],
            }
            for item in order.items
        ],
    )


def _pickup_qr_payload(order: Order) -> str:
    fulfillment = order.fulfillment
    if fulfillment is None or fulfillment.pickup_code is None:
        return ""
    return f"slf-meal:{order.id}:{fulfillment.pickup_code}"


async def _release_active_meal_reservations(
    session: AsyncSession,
    order: Order,
    now: datetime,
) -> None:
    for reservation in order.reservations:
        if reservation.status != ReservationStatus.ACTIVE:
            continue
        if reservation.source_meal_offering_id is not None:
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
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now


async def _release_paid_meal_capacity(
    session: AsyncSession,
    order: Order,
    now: datetime,
) -> None:
    consumed_by_offering: dict[str, int] = {}
    for reservation in order.reservations:
        offering_id = reservation.source_meal_offering_id
        if (
            reservation.status != ReservationStatus.CONSUMED
            or offering_id is None
        ):
            continue
        consumed_by_offering[offering_id] = (
            consumed_by_offering.get(offering_id, 0) + reservation.quantity
        )
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now

    item_quantity_by_offering: dict[str, int] = {}
    for item in order.items:
        offering_id = item.source_meal_offering_id
        if offering_id is None:
            continue
        item_quantity_by_offering[offering_id] = (
            item_quantity_by_offering.get(offering_id, 0) + item.quantity
        )

    for offering_id, item_quantity in item_quantity_by_offering.items():
        offering = await session.scalar(
            select(MealEventOffering)
            .where(MealEventOffering.id == offering_id)
            .with_for_update()
        )
        if offering is None:
            continue
        offering.paid_quantity = max(
            0,
            offering.paid_quantity
            - consumed_by_offering.get(offering_id, item_quantity),
        )


async def _publish_meal_refund_notification(
    session: AsyncSession,
    order: Order,
    *,
    title: str,
    body: str,
    data: dict[str, str],
    dedupe_key: str,
    event_type: str = "refund_completed",
) -> None:
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    await service.publish(
        NotificationCommand(
            user_id=order.user_id,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            email=order.contact_email,
            dedupe_key=dedupe_key,
        )
    )


def _meal_order_query():
    return (
        select(Order)
        .where(Order.sales_channel == SalesChannel.MEAL_PREORDER)
        .options(
            selectinload(Order.items).selectinload(
                OrderItem.selected_options
            ),
            selectinload(Order.meal_event),
            selectinload(Order.fulfillment),
            selectinload(Order.invoice),
        )
    )


@meals_router.get(
    "/v1/meal-orders",
    response_model=list[MealOrderRead],
)
async def list_meal_orders(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MealOrderRead]:
    orders = list(
        await session.scalars(
            _meal_order_query()
            .where(Order.user_id == user.id)
            .order_by(Order.created_at.desc())
        )
    )
    return [await _meal_order_read(session, order) for order in orders]


@meals_router.get("/v1/admin/meal-events/{event_id}/orders", response_model=list[MealOrderRead])
async def admin_list_meal_event_orders(
    event_id: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MealOrderRead]:
    if await session.get(MealEvent, event_id) is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    orders = list(await session.scalars(
        _meal_order_query().where(Order.meal_event_id == event_id).order_by(Order.created_at)
    ))
    return [await _meal_order_read(session, order) for order in orders]


@meals_router.get(
    "/v1/meal-orders/{order_id}",
    response_model=MealOrderRead,
)
async def get_meal_order(
    order_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MealOrderRead:
    order = await session.scalar(
        _meal_order_query().where(
            Order.id == order_id,
            Order.user_id == user.id,
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="找不到便當訂單")
    return await _meal_order_read(session, order)


@meals_router.get(
    "/v1/meal-orders/{order_id}/pickup-credential",
    response_model=MealPickupCredentialRead,
)
async def get_meal_pickup_credential(
    order_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MealPickupCredentialRead:
    order = await session.scalar(
        _meal_order_query().where(
            Order.id == order_id,
            Order.user_id == user.id,
        )
    )
    if order is None:
        raise HTTPException(status_code=404, detail="找不到便當訂單")
    order_read = await _meal_order_read(session, order)
    if (
        order_read.pickup_code is None
        or order_read.pickup_qr_payload is None
    ):
        raise HTTPException(status_code=409, detail="取餐憑證尚未生效")
    return MealPickupCredentialRead(
        order_id=order.id,
        pickup_code=order_read.pickup_code,
        qr_token=order_read.pickup_qr_payload,
    )


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
    settings: Settings = Depends(get_settings),
) -> list[MealEventRead]:
    events = (
        await session.scalars(
            select(MealEvent)
            .where(
                MealEvent.id.notin_(DEMO_MEAL_EVENT_IDS)
                if settings.environment.strip().lower() == "production"
                else True,
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
                ).selectinload(Meal.option_groups).selectinload(
                    MealOptionGroup.options
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


@meals_router.post(
    "/v1/meal-events/{event_id}/quote",
    response_model=MealOrderQuoteRead,
)
async def quote_meal_order(
    event_id: str,
    body: MealOrderCreate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealOrderQuoteRead:
    _require_live_meal_event(event_id, settings)
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
        try:
            priced = price_meal_line(
                offering,
                requested.quantity,
                requested.option_ids,
            )
        except DomainError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        total += priced.subtotal
        lines.append(
            {
                "offering_id": offering.id,
                "meal_id": offering.meal_id,
                "meal_name": offering.meal.name,
                "quantity": requested.quantity,
                "base_price": priced.base_price,
                "option_price": priced.option_price,
                "unit_price": priced.unit_price,
                "subtotal": priced.subtotal,
                "tax_type": offering.meal.tax_type,
                "selections": [
                    {
                        "group_id": option.group_id,
                        "group_name": option.group_name,
                        "option_id": option.option_id,
                        "option_name": option.option_name,
                        "price_delta": option.price_delta,
                    }
                    for option in priced.selections
                ],
            }
        )
    pickup_at = body.pickup_at or max(
        _aware(event.pickup_starts_at),
        now.replace(second=0, microsecond=0) + timedelta(minutes=1),
    )
    if pickup_at <= now or not (_aware(event.pickup_starts_at) <= pickup_at < _aware(event.pickup_ends_at)):
        raise HTTPException(status_code=422, detail="請選擇場次取餐區間內、尚未過去的取餐時間")
    return MealOrderQuoteRead(
        sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        items=lines,
        amount_total=total,
        pickup_at=pickup_at,
        pickup={
            "location": event.location,
            "starts_at": event.pickup_starts_at,
            "ends_at": event.pickup_ends_at,
        },
    )


@meals_router.post(
    "/v1/meal-events/{event_id}/orders",
    response_model=MealOrderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_meal_order(
    event_id: str,
    body: MealOrderCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealOrderRead:
    _require_live_meal_event(event_id, settings)
    event = await _load_event(session, event_id, for_update=True)
    if event is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    quote = await quote_meal_order(event_id, body, session, settings)
    by_id = {offering.id: offering for offering in event.offerings}
    quote_by_offering = {item.offering_id: item for item in quote.items}
    now = datetime.now(timezone.utc)
    order_id = new_id()
    order_number = make_meal_order_number(order_id, now)
    pickup_code = await _unique_pickup_code(session)
    order = Order(
        id=order_id,
        order_number=order_number,
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER,
        invoice_provider_context=invoice_context_from_settings(settings),
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user_id=user.id,
        meal_event_id=event.id,
        membership_type_snapshot=membership_type_for_user(user),
        amount_total=quote.amount_total,
        tax_amount=sum(
            included_tax_amount(
                quote_by_offering[item.offering_id].subtotal,
                by_id[item.offering_id].meal.tax_type,
            )
            for item in body.items
        ),
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
        payment_status=PaymentStatus.PENDING,
        invoice_status=InvoiceStatus.NOT_ELIGIBLE,
        fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[
            OrderItem(
                source_meal_offering_id=requested.offering_id,
                product_name=by_id[requested.offering_id].meal.name,
                unit_label="份",
                quantity=requested.quantity,
                unit_price=quote_by_offering[requested.offering_id].unit_price,
                subtotal=quote_by_offering[requested.offering_id].subtotal,
                tax_type=by_id[requested.offering_id].meal.tax_type,
                selected_options=[
                    OrderItemOption(
                        source_meal_option_id=selection.option_id,
                        group_name=selection.group_name,
                        option_name=selection.option_name,
                        price_delta=selection.price_delta,
                        position=position,
                    )
                    for position, selection in enumerate(
                        quote_by_offering[
                            requested.offering_id
                        ].selections
                    )
                ],
            )
            for requested in body.items
        ],
        fulfillment=OrderFulfillment(
            method=FulfillmentMethod.EVENT_PICKUP,
            status=FulfillmentState.PENDING_CONFIRMATION,
            pickup_location=event.location,
            pickup_starts_at=event.pickup_starts_at,
            pickup_ends_at=event.pickup_ends_at,
            pickup_at=quote.pickup_at,
            pickup_code=pickup_code,
        ),
    )
    session.add(order)
    await session.flush()
    qr_token = _pickup_qr_payload(order)
    order.fulfillment.pickup_qr_token_hash = hashlib.sha256(
        qr_token.encode("utf-8")
    ).hexdigest()
    await session.commit()
    stored_order = await session.scalar(
        _meal_order_query().where(Order.id == order.id)
    )
    assert stored_order is not None
    return await _meal_order_read(session, stored_order)


@meals_router.post("/v1/meal-orders/{order_id}/cancel")
async def cancel_meal_order(
    order_id: str,
    body: ActivityReview,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
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
            selectinload(Order.reservations),
            selectinload(Order.invoice),
        )
        .with_for_update()
    )
    if order is None or order.meal_event is None:
        raise HTTPException(status_code=404, detail="找不到便當訂單")
    if order.cancelled_at is not None:
        raise HTTPException(status_code=409, detail="訂單已取消")
    if order_fulfillment_is_irreversible(order) or (
        order.fulfillment is not None
        and order.fulfillment.status in {FulfillmentState.NO_SHOW, FulfillmentState.CANCELLED}
    ):
        raise HTTPException(status_code=409, detail="此便當已取餐或結束履約，無法自行取消")
    if not can_cancel_meal_order(
        order.paid_at,
        order.meal_event.ordering_ends_at,
    ):
        raise HTTPException(status_code=409, detail="已超過自行取消期限")
    now = datetime.now(timezone.utc)
    await _release_active_meal_reservations(session, order, now)
    if order.payment_status == PaymentStatus.PAID:
        try:
            refund, refund_queued = await create_provider_aware_refund(
                session,
                order=order,
                amount=order.amount_total,
                reason=body.reason or "買家於期限內取消便當預購",
                requested_by_id=user.id,
                allow_unbound_local_completion=(
                    allow_local_refund_without_payment_attempt(settings)
                ),
                now=now,
            )
        except PaymentApplicationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        order.payment_status = (
            PaymentStatus.REFUND_PENDING
            if refund_queued
            else PaymentStatus.REFUNDED
        )
        await _release_paid_meal_capacity(session, order, now)
        if not refund_queued:
            await reverse_order_purchase_points(session, order, refund.id)
            enqueue_invoice_adjustment_after_refund(session, order, refund)
        await _publish_meal_refund_notification(
            session,
            order,
            title=(
                "便當退款申請已送出"
                if refund_queued
                else "便當退款紀錄已建立"
            ),
            body=(
                f"訂單 {order.order_number} 已送出 NT${order.amount_total} "
                "退款申請，待金流確認完成後會再通知。"
                if refund_queued
                else (
                    f"訂單 {order.order_number} 已建立 NT${order.amount_total} "
                    "的 Sandbox 退款紀錄。"
                )
            ),
            data={
                "meal_event_id": order.meal_event.id,
                "order_id": order.id,
            },
            dedupe_key=f"meal-order-refund:{order.id}",
            event_type=(
                "refund_requested" if refund_queued else "refund_completed"
            ),
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


async def _validate_schedule_meals(session: AsyncSession, body: MealScheduleInput) -> None:
    meal_ids = {offering.meal_id for offering in body.offerings}
    query = select(Meal.id).where(Meal.id.in_(meal_ids))
    if body.enabled:
        query = query.where(Meal.is_active.is_(True))
    valid_ids = set(await session.scalars(query))
    if valid_ids != meal_ids:
        raise HTTPException(status_code=422, detail="每日菜單包含無效或停用餐點")


def _schedule_values(body: MealScheduleInput) -> dict[str, Any]:
    values = body.model_dump(mode="json")
    for name in ("pickup_start_time", "pickup_end_time", "cutoff_time"):
        values[name] = getattr(body, name).strftime("%H:%M")
    return values


@meals_router.get("/v1/admin/meal-schedules", response_model=list[MealScheduleRead])
async def admin_list_meal_schedules(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MealScheduleRead]:
    templates = list(await session.scalars(select(MealScheduleTemplate).order_by(MealScheduleTemplate.created_at)))
    return [MealScheduleRead.model_validate(template) for template in templates]


@meals_router.post("/v1/admin/meal-schedules", response_model=MealScheduleRead, status_code=201)
async def admin_create_meal_schedule(
    body: MealScheduleInput,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealScheduleRead:
    await _validate_schedule_meals(session, body)
    template = MealScheduleTemplate(**_schedule_values(body), created_by_id=admin.id)
    session.add(template)
    await session.flush()
    session.add(AdminAudit(
        actor_id=admin.id, action="meal_schedule.create", aggregate_type="meal_schedule",
        aggregate_id=template.id, data=body.model_dump(mode="json"),
    ))
    await generate_scheduled_meal_events(session, datetime.now(timezone.utc), template_id=template.id)
    return MealScheduleRead.model_validate(template)


@meals_router.put("/v1/admin/meal-schedules/{template_id}", response_model=MealScheduleRead)
async def admin_update_meal_schedule(
    template_id: str,
    body: MealScheduleInput,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealScheduleRead:
    template = await session.scalar(select(MealScheduleTemplate).where(MealScheduleTemplate.id == template_id).with_for_update())
    if template is None:
        raise HTTPException(status_code=404, detail="找不到每日供餐設定")
    await _validate_schedule_meals(session, body)
    before = MealScheduleRead.model_validate(template).model_dump(mode="json")
    for name, value in _schedule_values(body).items():
        setattr(template, name, value)
    await session.flush()
    session.add(AdminAudit(
        actor_id=admin.id, action="meal_schedule.update", aggregate_type="meal_schedule",
        aggregate_id=template.id, data={"before": before, "after": body.model_dump(mode="json")},
    ))
    await generate_scheduled_meal_events(session, datetime.now(timezone.utc), template_id=template.id)
    return MealScheduleRead.model_validate(template)


@meals_router.post("/v1/admin/meal-schedules/{template_id}/generate")
async def admin_generate_meal_schedule(
    template_id: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    template = await session.get(MealScheduleTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="找不到每日供餐設定")
    if not template.enabled:
        raise HTTPException(status_code=409, detail="請先啟用每日供餐設定")
    await _validate_schedule_meals(session, MealScheduleRead.model_validate(template))
    created_ids = await generate_scheduled_meal_events(session, datetime.now(timezone.utc), template_id=template.id)
    return {"created_count": len(created_ids), "created_event_ids": created_ids}


@meals_router.get(
    "/v1/admin/meals",
    response_model=list[MealRead],
)
async def admin_list_meals(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MealRead]:
    meals = (
        await session.scalars(
            select(Meal)
            .options(
                selectinload(Meal.option_groups).selectinload(
                    MealOptionGroup.options
                )
            )
            .order_by(Meal.name)
        )
    ).all()
    return [MealRead.model_validate(meal) for meal in meals]


@meals_router.post(
    "/v1/admin/meals",
    response_model=MealRead,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_meal(
    body: MealCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealRead:
    slug = f"meal-{secrets.token_hex(6)}"
    values = body.model_dump(exclude={"option_groups"})
    meal = Meal(
        slug=slug,
        **values,
        option_groups=[
            MealOptionGroup(
                **group.model_dump(exclude={"options"}),
                options=[
                    MealOption(**option.model_dump())
                    for option in group.options
                ],
            )
            for group in body.option_groups
        ],
    )
    session.add(meal)
    await session.flush()
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal.create",
            aggregate_type="meal",
            aggregate_id=meal.id,
            data={"name": meal.name},
        )
    )
    await session.commit()
    meal = await session.scalar(
        select(Meal)
        .where(Meal.id == meal.id)
        .options(
            selectinload(Meal.option_groups).selectinload(
                MealOptionGroup.options
            )
        )
    )
    assert meal is not None
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
                ).selectinload(Meal.option_groups).selectinload(
                    MealOptionGroup.options
                )
            )
            .order_by(MealEvent.created_at.desc())
        )
    ).all()
    ordered_event_ids = set(await session.scalars(
        select(Order.meal_event_id).where(Order.meal_event_id.in_([event.id for event in events])).distinct()
    ))
    now = datetime.now(timezone.utc)
    return [_meal_event_read(
        event,
        can_edit=_meal_event_allows_edit(event, now) and event.id not in ordered_event_ids,
    ) for event in events]


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
    await session.flush()
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.create",
            aggregate_type="meal_event",
            aggregate_id=event.id,
            data={
                "title": event.title,
                "offering_count": len(body.offerings),
            },
        )
    )
    await session.commit()
    event = await _load_event(session, event.id)
    assert event is not None
    return _meal_event_read(event, can_edit=True)


@meals_router.put("/v1/admin/meal-events/{event_id}", response_model=MealEventRead)
async def admin_update_meal_event(
    event_id: str,
    body: MealEventCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True)
    if event is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    if not _meal_event_allows_edit(event, datetime.now(timezone.utc)):
        raise HTTPException(status_code=409, detail="僅草稿或尚未截單的開放訂購場次可編輯")
    if await session.scalar(select(Order.id).where(Order.meal_event_id == event_id).limit(1)):
        raise HTTPException(status_code=409, detail="此場次已有訂單，為保留訂購承諾不可修改；請另建場次")
    service_date = _aware(body.pickup_starts_at).astimezone(TAIPEI).date()
    if event.schedule_template_id and service_date != event.service_date:
        raise HTTPException(status_code=422, detail="排程場次不可更換供餐日期；請另建手動場次")
    if event.status == MealEventStatus.PUBLISHED and _aware(body.ordering_ends_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="已發布場次的訂購截止時間不得設為過去")
    meal_ids = {offering.meal_id for offering in body.offerings}
    active_ids = set(await session.scalars(select(Meal.id).where(Meal.id.in_(meal_ids), Meal.is_active.is_(True))))
    if meal_ids != active_ids:
        raise HTTPException(status_code=422, detail="場次包含無效或停用餐點")
    before = _meal_event_read(event).model_dump(mode="json")
    event.offerings.clear()
    await session.flush()
    for name, value in body.model_dump(exclude={"offerings"}).items():
        setattr(event, name, value)
    event.offerings = [MealEventOffering(**offering.model_dump()) for offering in body.offerings]
    session.add(AdminAudit(
        actor_id=admin.id, action="meal_event.update", aggregate_type="meal_event", aggregate_id=event.id,
        data={"before": before, "after": body.model_dump(mode="json")},
    ))
    await session.commit()
    updated = await _load_event(session, event.id)
    assert updated is not None
    return _meal_event_read(updated, can_edit=True)


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
    has_orders = await session.scalar(select(Order.id).where(Order.meal_event_id == event.id).limit(1))
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.publish",
            aggregate_type="meal_event",
            aggregate_id=event.id,
        )
    )
    await session.commit()
    return _meal_event_read(event, can_edit=has_orders is None)


@meals_router.post(
    "/v1/admin/meal-events/{event_id}/open-pickup",
    response_model=MealEventRead,
)
async def open_meal_pickup(
    event_id: str,
    body: Optional[MealEventActionRequest] = None,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True)
    if event is None or event.status not in {
        MealEventStatus.PUBLISHED,
        MealEventStatus.ORDERING_CLOSED,
    }:
        raise HTTPException(status_code=409, detail="此場次目前不可開放取餐")
    now = datetime.now(timezone.utc)
    if not (_aware(event.pickup_starts_at) <= now < _aware(event.pickup_ends_at)):
        raise HTTPException(status_code=409, detail="目前不在場次取餐時間內")
    event.status = MealEventStatus.PICKUP_OPEN
    orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.meal_event_id == event.id,
                Order.payment_status == PaymentStatus.PAID,
            )
            .options(selectinload(Order.fulfillment))
            .with_for_update()
        )
    ).all()
    for order in orders:
        if order.fulfillment is None or order.fulfillment.status not in {
            FulfillmentState.PENDING_CONFIRMATION,
            FulfillmentState.PREPARING,
        }:
            continue
        order.fulfillment.status = FulfillmentState.READY_FOR_PICKUP
        order.fulfillment_status = FulfillmentStatus.READY_FOR_PICKUP
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.open_pickup",
            aggregate_type="meal_event",
            aggregate_id=event.id,
            reason=body.reason if body is not None else None,
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
    body: MealEventCancelRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True)
    if event is None or event.status in {
        MealEventStatus.CANCELLED,
        MealEventStatus.COMPLETED,
    }:
        raise HTTPException(status_code=409, detail="此場次目前不可取消")
    orders = (
        await session.scalars(
            select(Order)
            .where(Order.meal_event_id == event.id)
            .options(
                selectinload(Order.items),
                selectinload(Order.fulfillment),
                selectinload(Order.reservations),
                selectinload(Order.invoice),
            )
            .with_for_update()
        )
    ).all()
    if any(
        order.fulfillment_status == FulfillmentStatus.PICKED_UP
        or (
            order.fulfillment is not None
            and order.fulfillment.status == FulfillmentState.PICKED_UP
        )
        for order in orders
    ):
        raise HTTPException(
            status_code=409,
            detail="已有訂單完成取餐，不可取消整場",
        )

    now = datetime.now(timezone.utc)
    event.status = MealEventStatus.CANCELLED
    event.cancelled_at = now
    event.cancellation_reason = body.reason
    for order in orders:
        if order.cancelled_at is not None:
            continue
        await _release_active_meal_reservations(session, order, now)
        was_paid = order.payment_status == PaymentStatus.PAID
        if was_paid:
            await _release_paid_meal_capacity(session, order, now)
        elif order.payment_status == PaymentStatus.PENDING:
            order.payment_status = PaymentStatus.EXPIRED
        order.cancelled_at = now
        order.cancellation_reason = body.reason
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        if order.fulfillment is not None:
            order.fulfillment.status = FulfillmentState.CANCELLED
        if was_paid:
            try:
                refund, refund_queued = await create_provider_aware_refund(
                    session,
                    order=order,
                    amount=order.amount_total,
                    reason=f"便當場次取消：{body.reason}",
                    requested_by_id=admin.id,
                    allow_unbound_local_completion=(
                        allow_local_refund_without_payment_attempt(settings)
                    ),
                    now=now,
                )
            except PaymentApplicationError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            order.payment_status = (
                PaymentStatus.REFUND_PENDING
                if refund_queued
                else PaymentStatus.REFUNDED
            )
            if not refund_queued:
                await reverse_order_purchase_points(session, order, refund.id)
                enqueue_invoice_adjustment_after_refund(session, order, refund)
            await _publish_meal_refund_notification(
                session,
                order,
                title=(
                    "便當場次取消退款已送出"
                    if refund_queued
                    else "便當場次取消退款紀錄已建立"
                ),
                body=(
                    f"「{event.title}」已取消，訂單 {order.order_number} "
                    f"已送出 NT${order.amount_total} 退款申請，"
                    "待金流確認完成後會再通知。"
                    if refund_queued
                    else (
                        f"「{event.title}」已取消，訂單 {order.order_number} "
                        f"已建立 NT${order.amount_total} 的 Sandbox 退款紀錄。"
                    )
                ),
                data={"meal_event_id": event.id, "order_id": order.id},
                dedupe_key=f"meal-event-refund:{event.id}:{order.id}",
                event_type=(
                    "refund_requested" if refund_queued else "refund_completed"
                ),
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
    "/v1/admin/meal-events/{event_id}/complete",
    response_model=MealEventRead,
)
async def complete_meal_event(
    event_id: str,
    body: Optional[MealEventActionRequest] = None,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True)
    if event is None or event.status != MealEventStatus.PICKUP_OPEN:
        raise HTTPException(status_code=409, detail="此場次目前不可結束")
    now = datetime.now(timezone.utc)
    if _aware(event.pickup_ends_at) > now:
        raise HTTPException(status_code=409, detail="尚未到取餐結束時間")
    orders = (
        await session.scalars(
            select(Order)
            .where(
                Order.meal_event_id == event.id,
                Order.payment_status == PaymentStatus.PAID,
            )
            .options(selectinload(Order.fulfillment))
            .with_for_update()
        )
    ).all()
    for order in orders:
        if order.fulfillment is None or order.fulfillment.status in {
            FulfillmentState.PICKED_UP,
            FulfillmentState.NO_SHOW,
            FulfillmentState.CANCELLED,
        }:
            continue
        order.fulfillment.status = FulfillmentState.NO_SHOW
        order.fulfillment.fulfilled_at = now
        order.fulfillment_status = FulfillmentStatus.PICKED_UP
        enqueue_invoice_issue(
            session,
            order,
            trigger="legacy_meal_no_show",
        )
    event.status = MealEventStatus.COMPLETED
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.complete",
            aggregate_type="meal_event",
            aggregate_id=event.id,
            reason=body.reason if body is not None else None,
        )
    )
    await session.commit()
    return _meal_event_read(event)


@meals_router.post(
    "/v1/admin/meal-events/{event_id}/redeem",
    response_model=MealPickupRedemptionRead,
)
async def redeem_meal_pickup(
    event_id: str,
    body: MealPickupVerify,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealPickupRedemptionRead:
    event = await session.get(MealEvent, event_id)
    if event is None or event.status != MealEventStatus.PICKUP_OPEN:
        raise HTTPException(status_code=409, detail="此場次尚未開放取餐")
    now = datetime.now(timezone.utc)
    if not (_aware(event.pickup_starts_at) <= now < _aware(event.pickup_ends_at)):
        raise HTTPException(status_code=409, detail="目前不在場次取餐時間內")
    credential_filter = (
        OrderFulfillment.pickup_code == body.pickup_code
        if body.pickup_code is not None
        else OrderFulfillment.pickup_qr_token_hash
        == hashlib.sha256((body.qr_token or "").encode("utf-8")).hexdigest()
    )
    order = await session.scalar(
        select(Order)
        .join(OrderFulfillment)
        .where(
            Order.meal_event_id == event.id,
            Order.payment_status == PaymentStatus.PAID,
            credential_filter,
        )
        .options(selectinload(Order.fulfillment))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None or order.fulfillment is None:
        raise HTTPException(status_code=404, detail="找不到有效取餐碼")
    if order.fulfillment.status == FulfillmentState.PICKED_UP:
        raise HTTPException(status_code=409, detail="此取餐碼已核銷")
    if order.cancelled_at is not None or order.fulfillment.status not in {
        FulfillmentState.PENDING_CONFIRMATION,
        FulfillmentState.PREPARING,
        FulfillmentState.READY_FOR_PICKUP,
    }:
        raise HTTPException(status_code=409, detail="此取餐憑證已失效")
    now = datetime.now(timezone.utc)
    order.fulfillment.status = FulfillmentState.PICKED_UP
    order.fulfillment.fulfilled_at = now
    order.fulfillment_status = FulfillmentStatus.PICKED_UP
    enqueue_invoice_issue(
        session,
        order,
        trigger="legacy_meal_redeemed",
    )
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_order.redeem",
            aggregate_type="order",
            aggregate_id=order.id,
        )
    )
    await session.commit()
    return MealPickupRedemptionRead(
        order_id=order.id,
        order_number=order.order_number,
        pickup_code=order.fulfillment.pickup_code or "",
        status=FulfillmentState.PICKED_UP.value,
        redeemed_at=now,
    )


router = meals_router
