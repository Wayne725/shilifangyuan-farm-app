from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..auth import (
    get_current_user,
    get_optional_user,
    membership_type_for_user,
    require_admin,
)
from ..config import Settings, get_settings
from ..integrations.pii_crypto import pii_cipher_from_settings
from ..integrations.common import IntegrationError
from ..invoice_tax_summary import invoice_tax_summary
from ..database import get_session
from ..order_numbers import allocate_order_number
from ..order_history import OrderHistoryQuery, read_order_history
from ..printing import enqueue_order_print, enqueue_cancellation_print
from ..printer_worker import wake_printer
from ..platform_settings import require_accepting_orders
from ..domain import (
    DomainError,
    invoice_estimated_tax_amount,
    member_sale_is_tax_exempt,
    admin_meal_cancellation_allowed,
    order_available_actions,
    order_amendment_blocked_reason,
    sale_tax_type,
)
from ..integrations.notifications import (
    NotificationCommand,
    NotificationService,
    SQLAlchemyNotificationRepository,
)
from ..invoice_timing import invoice_handling, pickup_invoice_due, cancel_unissued_invoice_jobs
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
from ..meal_photos import router as meal_photos_router
from ..meal_options_admin import router as meal_options_router
from ..meal_booking import (
    configured_booking_window,
    meal_booking_window,
    meal_new_orders_blocked_reason,
    meal_period,
    meal_pickup_slots,
    meal_service_date,
    validate_meal_booking_day,
)
from ..meal_schedules import TAIPEI, generate_scheduled_meal_events
from ..workday_calendar import CalendarUnavailable, calendar_status, is_workday, next_workday, previous_workday
from ..meal_operations import meal_operations_summary, search_meal_orders
from ..sales_scope import DEMO_MEAL_EVENT_IDS
from ..models import (
    AdminAudit,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    InvoiceStatus,
    Meal,
    MealCancellationRequest,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    MealOption,
    MealOptionGroup,
    MealScheduleTemplate,
    MembershipType,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderItemOption,
    OrderKind,
    PaymentStatus,
    PaymentAttempt,
    PaymentMethod,
    PickupLocation,
    ReservationStatus,
    SalesChannel,
    TaxType,
    User,
    new_id,
)
from ..schemas import (
    ActivityReview,
    MealAdminOrderPage,
    MealCreate,
    MealCancellationQueueRead,
    AdminMealCancellationRequest,
    MealCancellationRequestCreate,
    MealCancellationRequestRead,
    MealCancellationReview,
    MealEventActionRequest,
    MealEventCalendarRead,
    MealEventCancelRequest,
    MealEventCreate,
    MealEventRead,
    MealOptionGroupRead,
    MealOptionRead,
    MealOfferingRead,
    MealOrderCreate,
    MealOrderQuoteRead,
    MealOrderRead,
    MealOrderSearchFilters,
    MealOperationsSummary,
    MealPickupCredentialRead,
    MealPickupRedemptionRead,
    MealPickupVerify,
    MealPaymentOptionsUpdate,
    MealRead,
    MealScheduleInput,
    MealScheduleRead,
)
from ..v2_domain import (
    meal_available_quantity,
    validate_meal_preorder,
)


meals_router = APIRouter(tags=["meals"])
meals_router.include_router(meal_photos_router)
meals_router.include_router(meal_options_router)


def _require_live_meal_event(event_id: str, settings: Settings) -> None:
    if (
        settings.environment == "production"
        and settings.sales_scope in {"meals_only", "pickup"}
        and event_id in DEMO_MEAL_EVENT_IDS
    ):
        raise HTTPException(status_code=409, detail="展示場次不可正式下單，請等待正式場次公告")


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


def _meal_event_booking_fields(event: MealEvent) -> dict[str, Any]:
    booking_start, booking_end = meal_booking_window(event)
    return {
        "id": event.id,
        "booking_policy": event.booking_policy,
        "new_orders_blocked_reason": meal_new_orders_blocked_reason(event),
        "title": event.title,
        "location": event.location,
        "ordering_starts_at": _aware(event.ordering_starts_at),
        "ordering_ends_at": booking_end,
        "pickup_starts_at": _aware(event.pickup_starts_at),
        "pickup_ends_at": _aware(event.pickup_ends_at),
        "status": event.status,
        "service_date": meal_service_date(event),
        "meal_period": meal_period(event),
        "pickup_slot_ends_at": _aware(event.pickup_slot_ends_at) if event.pickup_slot_ends_at else None,
        "pickup_slots": meal_pickup_slots(event),
        "booking_starts_at": booking_start,
        "booking_ends_at": booking_end,
    }


def _meal_event_read(event: MealEvent, *, can_edit: bool = False) -> MealEventRead:
    return MealEventRead(
        **_meal_event_booking_fields(event),
        allow_cash_payment=event.allow_cash_payment,
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
    FulfillmentState.PREPARING: "preparing",
    FulfillmentState.READY_FOR_PICKUP: "ready",
    FulfillmentState.PICKED_UP: "picked_up",
    FulfillmentState.NO_SHOW: "no_show",
    FulfillmentState.CANCELLED: "cancelled",
}


async def _meal_ids_for_offerings(session: AsyncSession, offering_ids: set[str]) -> dict[str, str]:
    if not offering_ids:
        return {}
    rows = await session.execute(select(MealEventOffering.id, MealEventOffering.meal_id).where(
        MealEventOffering.id.in_(offering_ids)))
    return dict(rows.all())


async def _meal_order_read(
    session: AsyncSession,
    order: Order,
    viewer_is_admin: bool = False,
    *,
    offering_meal_ids: Optional[dict[str, str]] = None,
) -> MealOrderRead:
    offering_ids = {
        item.source_meal_offering_id
        for item in order.items
        if item.source_meal_offering_id is not None
    }
    meal_ids = (offering_meal_ids if offering_meal_ids is not None
        else await _meal_ids_for_offerings(session, offering_ids))
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
    onsite_payment = None
    if viewer_is_admin and order.payment_method == PaymentMethod.CASH_ON_PICKUP and order.payment_status == PaymentStatus.PAID:
        attempt = await session.scalar(select(PaymentAttempt).where(
            PaymentAttempt.order_id == order.id, PaymentAttempt.provider == "onsite", PaymentAttempt.status == PaymentStatus.PAID,
        ).order_by(PaymentAttempt.paid_at.desc()).limit(1))
        if attempt is not None:
            onsite_payment = {"method": attempt.provider_response.get("collection_method", "cash"),
                "reference": attempt.provider_response.get("reference", ""), "paid_at": _aware(attempt.paid_at).isoformat()}
    from ..business_meals import business_payment_fields
    return MealOrderRead(
        **business_payment_fields(order),
        invoice_tax=invoice_tax_summary(order.__dict__.get("invoice")),
        kitchen_note=order.kitchen_note,
        onsite_payment=onsite_payment,
        id=order.id,
        revision=order.revision,
        amendment_blocked_reason=order_amendment_blocked_reason(order),
        amendments=sorted(order.__dict__.get("amendments", []), key=lambda item: item.revision, reverse=True),
        order_number=order.order_number,
        sales_channel=order.sales_channel,
        fulfillment_method=order.fulfillment_method,
        meal_event_id=event.id,
        meal_event_title=event.title,
        venue_name=fulfillment.pickup_location or event.location,
        pickup_location_id=fulfillment.pickup_location_id,
        pickup_address=fulfillment.pickup_address,
        pickup_instructions=fulfillment.pickup_instructions,
        needs_utensils=fulfillment.needs_utensils,
        pickup_start=_aware(fulfillment.pickup_starts_at or event.pickup_starts_at),
        pickup_end=_aware(fulfillment.pickup_ends_at or event.pickup_ends_at),
        pickup_at=_aware(fulfillment.pickup_at) if fulfillment.pickup_at else None,
        pickup_code=(
            fulfillment.pickup_code if credential_available else None
        ),
        pickup_qr_payload=qr_payload,
        payment_status=order.payment_status,
        payment_method=order.payment_method,
        invoice_status=order.invoice_status,
        invoice_handling=invoice_handling(order),
        invoice_issue_after=order.invoice_issue_after,
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
        promotion=order.promotion_snapshot,
        amount_total=order.amount_total,
        member_tax_exempt_snapshot=order.member_tax_exempt_snapshot,
        tax_amount=order.tax_amount,
        created_at=_aware(order.created_at),
        available_actions=order_available_actions(order, viewer_is_admin),
        items=[
            {
                "order_item_id": item.id,
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
                "tax_type": item.tax_type,
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
        cancellation_request=(
            _cancellation_request_read(max(order.cancellation_requests, key=lambda request: (_aware(request.requested_at), request.id)))
            if order.cancellation_requests else None
        ),
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
    for reservation in sorted(order.reservations, key=lambda row: row.source_meal_offering_id or ""):
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

    for offering_id, item_quantity in sorted(item_quantity_by_offering.items()):
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
            selectinload(Order.cancellation_requests),
            selectinload(Order.amendments),
            selectinload(Order.payment_attempts),
        )
    )


@meals_router.get(
    "/v1/meal-orders",
    response_model=list[MealOrderRead],
)
async def list_meal_orders(
    filters: Annotated[OrderHistoryQuery, Query()],
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MealOrderRead]:
    orders = await read_order_history(session, user, filters, meal=True)
    offering_ids = {item.source_meal_offering_id for order in orders for item in order.items
        if item.source_meal_offering_id is not None}
    meal_ids = await _meal_ids_for_offerings(session, offering_ids)
    return [await _meal_order_read(session, order, offering_meal_ids=meal_ids) for order in orders]


@meals_router.get("/v1/admin/meal-events/{event_id}/orders", response_model=list[MealOrderRead])
async def admin_list_meal_event_orders(
    event_id: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MealOrderRead]:
    if await session.get(MealEvent, event_id) is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    orders = list(await session.scalars(
        _meal_order_query().where(Order.meal_event_id == event_id).order_by(Order.created_at.desc(), Order.id.desc())
    ))
    return [await _meal_order_read(session, order, True) for order in orders]


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


def _public_meal_event_query(settings: Settings, *columns):
    query = select(MealEvent, *columns).where(
        MealEvent.status.in_([
            MealEventStatus.PUBLISHED,
            MealEventStatus.ORDERING_CLOSED,
            MealEventStatus.PICKUP_OPEN,
        ])
    )
    if settings.environment.strip().lower() == "production":
        query = query.where(MealEvent.id.not_in(DEMO_MEAL_EVENT_IDS))
    return query.order_by(MealEvent.pickup_starts_at)


@meals_router.get("/v1/meal-events", response_model=list[MealEventRead])
async def list_meal_events(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[MealEventRead]:
    events = (
        await session.scalars(
            _public_meal_event_query(settings).options(
                selectinload(MealEvent.offerings).selectinload(
                    MealEventOffering.meal
                ).selectinload(Meal.option_groups).selectinload(
                    MealOptionGroup.options
                )
            )
        )
    ).all()
    return [_meal_event_read(event) for event in events]


@meals_router.get("/v1/meal-events/calendar", response_model=list[MealEventCalendarRead])
async def list_meal_event_calendar(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[MealEventCalendarRead]:
    # SQLite can use the event ID index when this boolean predicate is an inequality.
    active = MealEventOffering.is_active != false()
    available = MealEventOffering.capacity > MealEventOffering.paid_quantity + MealEventOffering.reserved_quantity
    events = await session.execute(_public_meal_event_query(
        settings,
        MealEvent.offerings.any(active),
        MealEvent.offerings.any(active & available),
    ))
    return [
        MealEventCalendarRead(
            **_meal_event_booking_fields(event),
            has_active_offerings=has_active,
            has_available_offerings=has_available,
        )
        for event, has_active, has_available in events
    ]


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
    user: Optional[User] = Depends(get_optional_user),
) -> MealOrderQuoteRead:
    _require_live_meal_event(event_id, settings)
    event = await _load_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    if reason := meal_new_orders_blocked_reason(event):
        raise HTTPException(status_code=409, detail=reason)
    if body.payment_method == PaymentMethod.CASH_ON_DELIVERY:
        raise HTTPException(status_code=422, detail="便當僅支援線上付款或現場付款")
    if body.payment_method == PaymentMethod.CASH_ON_PICKUP and not event.allow_cash_payment:
        raise HTTPException(status_code=422, detail="此場次未開放現場付款")
    pickup_location = None
    if body.pickup_location_id is not None:
        pickup_location = await session.scalar(
            select(PickupLocation).where(
                PickupLocation.id == body.pickup_location_id,
                PickupLocation.is_active.is_(True),
            ).with_for_update(read=True)
        )
        if pickup_location is None:
            raise HTTPException(status_code=422, detail="找不到可用的取餐點，請重新選擇")
    by_id = {offering.id: offering for offering in event.offerings}
    now = datetime.now(timezone.utc)
    membership_type = (
        membership_type_for_user(user) if user is not None else MembershipType.NONMEMBER
    )
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
            validate_meal_booking_day(event, now)
        except ValueError as exc:
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
                "tax_type": sale_tax_type(
                    offering.meal.tax_type, membership_type, body.invoice_buyer_type
                ),
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
    if body.payment_method == PaymentMethod.CASH_ON_PICKUP and total <= 0:
        raise HTTPException(status_code=422, detail="現場付款金額必須大於零")
    pickup_slots = meal_pickup_slots(event)
    pickup_at = body.pickup_at or (pickup_slots[0] if pickup_slots else None)
    if pickup_at not in pickup_slots:
        raise HTTPException(status_code=422, detail="請選擇本場次午餐 11:00–13:00 或晚餐 16:30–18:00 的半小時取餐時段")
    pickup_at = pickup_at.astimezone(timezone.utc)
    from ..promotions import price_promotion
    promotion, discount = await price_promotion(session, total, body.coupon_code, user.id if user else None, now)
    original_total = total
    remaining = discount
    for index, line in enumerate(lines):
        reduction = remaining if index == len(lines) - 1 else discount * line["subtotal"] // original_total
        line["subtotal"] -= reduction
        remaining -= reduction
    total -= discount
    return MealOrderQuoteRead(
        promotion=promotion, original_amount=original_total, discount_amount=discount,
        sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        membership_type=membership_type,
        member_tax_exempt=member_sale_is_tax_exempt(
            membership_type, body.invoice_buyer_type
        ),
        tax_amount=invoice_estimated_tax_amount(
            ((line['subtotal'], line['tax_type']) for line in lines), body.invoice_buyer_type,
        ),
        items=lines,
        amount_total=total,
        pickup_at=pickup_at,
        needs_utensils=body.needs_utensils,
        pickup={
            "location": pickup_location.name if pickup_location else event.location,
            "location_id": pickup_location.id if pickup_location else None,
            "address": pickup_location.address if pickup_location else "",
            "instructions": pickup_location.instructions if pickup_location else "",
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
    fingerprint_body = body.model_dump(mode="json", exclude={"client_request_id"})
    if body.meal_booking_type == "standard":
        fingerprint_body.pop("meal_booking_type")
    # Default preferences must preserve fingerprints created before these fields existed.
    legacy_invoice_handling = "external" if body.payment_method == PaymentMethod.CASH_ON_PICKUP else "on_payment"
    if body.invoice_handling in {None, legacy_invoice_handling}:
        fingerprint_body.pop("invoice_handling")
    if body.contact_phone is None:
        fingerprint_body.pop("contact_phone")
    if body.pickup_location_id is None:
        fingerprint_body.pop("pickup_location_id")
    if not body.needs_utensils:
        fingerprint_body.pop("needs_utensils")
    fingerprint = hashlib.sha256(json.dumps({"event_id": event_id, "body": fingerprint_body}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    submitting_user_id = user.id

    async def existing_submission():
        if body.client_request_id is None:
            return None
        existing = await session.scalar(_meal_order_query().where(Order.user_id == submitting_user_id, Order.client_request_id == str(body.client_request_id)))
        if existing is not None and existing.request_fingerprint != fingerprint:
            raise HTTPException(409, "此送單識別碼已使用，請重新確認訂單")
        return await _meal_order_read(session, existing) if existing is not None else None

    existing = await existing_submission()
    if existing is not None:
        return existing
    await require_accepting_orders(session)
    _require_live_meal_event(event_id, settings)
    event = await _load_event(session, event_id, for_update=True)
    if event is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    existing = await existing_submission()
    if existing is not None:
        return existing
    from ..promotions import lock_promotions
    await lock_promotions(session)
    quote = await quote_meal_order(event_id, body, session, settings, user)
    if body.expected_total is not None and body.expected_total != quote.amount_total:
        raise HTTPException(status_code=409, detail="價格或優惠已變動，請重新確認訂單明細")
    by_id = {offering.id: offering for offering in event.offerings}
    quote_by_offering = {item.offering_id: item for item in quote.items}
    now = datetime.now(timezone.utc)
    order_id = new_id()
    order_number = await allocate_order_number(session, "M", now)
    pickup_code = await _unique_pickup_code(session)
    order = Order(
        id=order_id,
        client_request_id=str(body.client_request_id) if body.client_request_id else None,
        request_fingerprint=fingerprint if body.client_request_id else None,
        kitchen_note=body.kitchen_note.strip() if body.kitchen_note else None,
        order_number=order_number,
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER,
        invoice_provider_context=invoice_context_from_settings(settings),
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user_id=user.id,
        meal_event_id=event.id,
        membership_type_snapshot=quote.membership_type,
        member_tax_exempt_snapshot=quote.member_tax_exempt,
        promotion_id=quote.promotion["id"] if quote.promotion else None,
        promotion_snapshot=quote.promotion,
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
        payment_status=PaymentStatus.PENDING,
        payment_method=body.payment_method,
        meal_booking_type=body.meal_booking_type,
        invoice_handling=body.invoice_handling or ("external" if body.payment_method == PaymentMethod.CASH_ON_PICKUP else "on_payment"),
        invoice_issue_after=pickup_invoice_due(quote.pickup_at) if body.invoice_handling == "pickup_day" else None,
        invoice_status=InvoiceStatus.EXTERNAL if (body.invoice_handling == "external" or (body.invoice_handling is None and body.payment_method == PaymentMethod.CASH_ON_PICKUP)) else InvoiceStatus.NOT_ELIGIBLE,
        fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[
            OrderItem(
                source_meal_offering_id=requested.offering_id,
                product_name=by_id[requested.offering_id].meal.name,
                unit_label="份",
                quantity=requested.quantity,
                unit_price=quote_by_offering[requested.offering_id].unit_price,
                subtotal=quote_by_offering[requested.offering_id].subtotal,
                tax_type=quote_by_offering[requested.offering_id].tax_type,
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
            pickup_location_id=quote.pickup.location_id,
            pickup_location=quote.pickup.location,
            pickup_address=quote.pickup.address,
            pickup_instructions=quote.pickup.instructions,
            needs_utensils=quote.needs_utensils,
            pickup_starts_at=event.pickup_starts_at,
            pickup_ends_at=event.pickup_ends_at,
            pickup_at=quote.pickup_at,
            meal_ordering_ends_at=meal_booking_window(event)[1],
            pickup_code=pickup_code,
        ),
    )
    user.notification_email = order.contact_email
    session.add(order)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await existing_submission()
        if existing is not None:
            return existing
        raise
    if body.contact_phone:
        try:
            cipher = pii_cipher_from_settings(settings)
            order.fulfillment.recipient_phone_encrypted = cipher.encrypt_text(
                body.contact_phone,
                associated_data=f"order-fulfillment:{order.fulfillment.id}:recipient_phone",
            )
            order.fulfillment.encryption_key_version = cipher.current_version
        except IntegrationError as exc:
            raise HTTPException(status_code=503, detail="聯絡電話目前無法安全儲存，請稍後再試") from exc
    qr_token = _pickup_qr_payload(order)
    order.fulfillment.pickup_qr_token_hash = hashlib.sha256(
        qr_token.encode("utf-8")
    ).hexdigest()
    if body.payment_method == PaymentMethod.CASH_ON_PICKUP or body.meal_booking_type == "business":
        from ..cash_payments import reserve_cash_order
        order.meal_event = event
        try:
            await reserve_cash_order(session, order)
        except PaymentApplicationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    await enqueue_order_print(session, order, user, settings)
    await session.commit()
    wake_printer()
    stored_order = await session.scalar(
        _meal_order_query().where(Order.id == order.id)
    )
    assert stored_order is not None
    return await _meal_order_read(session, stored_order)


def _cancellation_request_read(request: MealCancellationRequest) -> MealCancellationRequestRead:
    return MealCancellationRequestRead(
        id=request.id, order_id=request.order_id, reason=request.reason,
        status=request.status, requested_at=_aware(request.requested_at),
        reviewed_by_id=request.reviewed_by_id,
        reviewed_by_name=request.reviewed_by_name,
        reviewed_at=_aware(request.reviewed_at) if request.reviewed_at else None,
        review_reason=request.review_reason,
    )


async def _locked_cancellation_order(session: AsyncSession, order_id: str) -> Order:
    event_id = await session.scalar(select(Order.meal_event_id).where(
        Order.id == order_id, Order.sales_channel == SalesChannel.MEAL_PREORDER,
    ))
    if event_id is not None:
        await session.scalar(select(MealEvent).where(MealEvent.id == event_id).with_for_update())
    order = await session.scalar(
        _meal_order_query().where(Order.id == order_id)
        .options(selectinload(Order.reservations), selectinload(Order.fulfillment).selectinload(OrderFulfillment.shipment))
        .with_for_update(of=Order).execution_options(populate_existing=True)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="找不到便當訂單")
    return order


@meals_router.post("/v1/meal-orders/{order_id}/cancel", response_model=MealCancellationRequestRead, status_code=201, deprecated=True)
@meals_router.post("/v1/meal-orders/{order_id}/cancellation-requests", response_model=MealCancellationRequestRead, status_code=201)
async def request_meal_cancellation(
    order_id: str,
    body: MealCancellationRequestCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MealCancellationRequestRead:
    raise HTTPException(status_code=403, detail="退單請致電客服，由管理員協助辦理")


@meals_router.get("/v1/admin/meal-cancellation-requests", response_model=MealCancellationQueueRead)
async def list_meal_cancellation_requests(
    starts_on: date | None = None,
    ends_on: date | None = None,
    review_status: Literal["pending", "approved", "rejected", "all"] = "pending",
    search: str = Query(default="", max_length=100),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealCancellationQueueRead:
    query = select(MealCancellationRequest).join(Order).where(Order.sales_channel == SalesChannel.MEAL_PREORDER)
    from .meal_cancellation_reports import cancellation_filters
    query = cancellation_filters(query, starts_on, ends_on, "")
    if review_status != "all":
        query = query.where(MealCancellationRequest.status == review_status)
    if search.strip():
        query = query.where(or_(Order.order_number.icontains(search.strip(), autoescape=True), Order.contact_email.icontains(search.strip(), autoescape=True)))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    requests = list(await session.scalars(query.options(
        selectinload(MealCancellationRequest.order).selectinload(Order.items).selectinload(OrderItem.selected_options),
        selectinload(MealCancellationRequest.order).selectinload(Order.meal_event),
        selectinload(MealCancellationRequest.order).selectinload(Order.fulfillment),
        selectinload(MealCancellationRequest.order).selectinload(Order.invoice),
        selectinload(MealCancellationRequest.order).selectinload(Order.cancellation_requests),
        selectinload(MealCancellationRequest.order).selectinload(Order.amendments),
        selectinload(MealCancellationRequest.order).selectinload(Order.payment_attempts),
        selectinload(MealCancellationRequest.requested_by),
    ).order_by(MealCancellationRequest.requested_at.desc(), MealCancellationRequest.id).offset(offset).limit(limit)))
    return MealCancellationQueueRead(
        items=[{
            "request": _cancellation_request_read(request),
            "order": await _meal_order_read(session, request.order, True),
            "customer_name": request.requested_by.display_name,
            "customer_email": request.order.contact_email,
            "can_approve": admin_meal_cancellation_allowed(request.order),
        } for request in requests], total=total or 0, limit=limit, offset=offset,
    )


@meals_router.get("/v1/admin/meal-orders", response_model=MealAdminOrderPage)
async def admin_search_meal_orders(
    filters: Annotated[MealOrderSearchFilters, Query()],
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealAdminOrderPage:
    orders, total = await search_meal_orders(session, filters)
    return MealAdminOrderPage(items=[{
        "order": await _meal_order_read(session, order, True),
        "customer_name": order.user.display_name,
        "customer_email": order.contact_email,
        "meal_period": order.meal_event.meal_period,
    } for order in orders], total=total, limit=filters.limit, offset=filters.offset)


@meals_router.get("/v1/admin/meal-operations-summary", response_model=MealOperationsSummary)
async def admin_meal_operations_summary(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealOperationsSummary:
    return await meal_operations_summary(session)


async def _cancel_approved_meal_order(
    session: AsyncSession, order: Order, request: MealCancellationRequest,
    admin: User, settings: Settings, now: datetime,
) -> None:
    if not admin_meal_cancellation_allowed(order, now):
        raise HTTPException(status_code=409, detail="訂單已取消、交付或付款狀態待處理，無法核准退單")
    redeemed = await session.scalar(select(AdminAudit.id).where(
        AdminAudit.aggregate_type == "order", AdminAudit.aggregate_id == order.id,
        AdminAudit.action.in_({"meal_order.redeem", "order.fulfillment.picked_up", "order.fulfillment.delivered", "order.fulfillment.shipped"}),
    ).limit(1))
    if redeemed is not None:
        raise HTTPException(status_code=409, detail="訂單已有交付紀錄，無法退單")
    was_paid = order.payment_status == PaymentStatus.PAID
    if was_paid:
        try:
            refund, refund_queued = await create_provider_aware_refund(
                session, order=order, amount=order.amount_total,
                reason=f"管理員核准退單：{request.reason}" if request.reason else "", requested_by_id=admin.id,
                allow_unbound_local_completion=allow_local_refund_without_payment_attempt(settings), now=now,
            )
        except PaymentApplicationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _release_active_meal_reservations(session, order, now)
    if was_paid:
        # Prepared meals remain consumed capacity; cancellation cannot sell them again.
        order.fulfillment.meal_capacity_retained = order.fulfillment.status in {
            FulfillmentState.PREPARING, FulfillmentState.READY_FOR_PICKUP, FulfillmentState.NO_SHOW,
        } or now >= _aware(order.meal_event.pickup_ends_at)
        if not order.fulfillment.meal_capacity_retained:
            await _release_paid_meal_capacity(session, order, now)
        order.payment_status = PaymentStatus.REFUND_PENDING if refund_queued else PaymentStatus.REFUNDED
        if not refund_queued:
            await reverse_order_purchase_points(session, order, refund.id)
            enqueue_invoice_adjustment_after_refund(session, order, refund)
        await _publish_meal_refund_notification(
            session, order,
            title="便當退款申請已送出" if refund_queued else "便當退款紀錄已建立",
            body=(f"訂單 {order.order_number} 已送出 NT${order.amount_total} 退款申請，待金流確認完成後會再通知。"
                  if refund_queued else f"訂單 {order.order_number} 已建立 NT${order.amount_total} 的 Sandbox 退款紀錄。"),
            data={"meal_event_id": order.meal_event.id, "order_id": order.id},
            dedupe_key=f"meal-order-refund:{order.id}",
            event_type="refund_requested" if refund_queued else "refund_completed",
        )
    else:
        order.payment_status = PaymentStatus.EXPIRED
    order.cancelled_at = now
    order.cancellation_reason = request.reason
    await cancel_unissued_invoice_jobs(session, order)
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    order.fulfillment.status = FulfillmentState.CANCELLED
    await enqueue_cancellation_print(session, order, settings)


@meals_router.post("/v1/admin/meal-cancellation-requests/{request_id}/review", response_model=MealCancellationRequestRead)
async def review_meal_cancellation(
    request_id: str,
    body: MealCancellationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealCancellationRequestRead:
    order_id = await session.scalar(select(MealCancellationRequest.order_id).where(MealCancellationRequest.id == request_id))
    if order_id is None:
        raise HTTPException(status_code=404, detail="找不到退單申請")
    # All reviewers take the order lock before the request lock, as pickup does.
    order = await _locked_cancellation_order(session, order_id)
    request = await session.scalar(select(MealCancellationRequest).where(MealCancellationRequest.id == request_id)
        .with_for_update().execution_options(populate_existing=True))
    result_status = "approved" if body.decision == "approve" else "rejected"
    if request.status != "pending":
        if request.status == result_status:
            return _cancellation_request_read(request)
        raise HTTPException(status_code=409, detail="此申請已完成審核，不能改成另一個結果")
    now = datetime.now(timezone.utc)
    if body.decision == "approve":
        await _cancel_approved_meal_order(session, order, request, admin, settings, now)
    request.status = result_status
    request.reviewed_by_id = admin.id
    request.reviewed_by = admin
    request.reviewed_at = now
    request.review_reason = body.reason
    session.add(AdminAudit(
        actor_id=admin.id, action=f"meal_cancellation.{result_status}", aggregate_type="order", aggregate_id=order.id,
        reason=body.reason, data={"request_id": request.id},
    ))
    await _publish_meal_refund_notification(
        session, order,
        title="便當退單申請已核准" if result_status == "approved" else "便當退單申請未核准",
        body=f"訂單 {order.order_number} 的退單申請{'已核准' if result_status == 'approved' else '未核准'}。"
        + (f"審核說明：{body.reason}。" if body.reason else "")
        + ("款項與發票處理進度請查看訂單。" if result_status == "approved" else "原訂單仍依目前狀態處理。"),
        data={"order_id": order.id, "request_id": request.id},
        dedupe_key=f"meal-cancellation-review:{request.id}", event_type=f"meal_cancellation_{result_status}",
    )
    await session.commit()
    wake_printer()
    return _cancellation_request_read(request)


@meals_router.post("/v1/admin/meal-orders/{order_id}/cancel", response_model=MealOrderRead)
async def admin_cancel_meal_order(
    order_id: str,
    body: AdminMealCancellationRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealOrderRead:
    order = await _locked_cancellation_order(session, order_id)
    if order.cancelled_at is not None:
        return await _meal_order_read(session, order, True)
    pending = next((request for request in order.cancellation_requests if request.status == "pending"), None)
    if pending is not None:
        await review_meal_cancellation(pending.id, MealCancellationReview(decision="approve", reason=body.reason), admin, session, settings)
        return await _meal_order_read(session, order, True)
    now = datetime.now(timezone.utc)
    request = MealCancellationRequest(order_id=order.id, requested_by_id=admin.id, reason=body.reason,
        status="approved", requested_at=now, reviewed_by_id=admin.id, reviewed_by=admin, reviewed_at=now, review_reason=body.reason)
    await _cancel_approved_meal_order(session, order, request, admin, settings, now)
    order.cancellation_requests.append(request)
    session.add(request)
    await session.flush()
    session.add(AdminAudit(actor_id=admin.id, action="meal_cancellation.approved", aggregate_type="order", aggregate_id=order.id,
        reason=body.reason, data={"request_id": request.id, "source": "phone"}))
    await _publish_meal_refund_notification(
        session, order, title="便當退單已辦理",
        body=f"訂單 {order.order_number} 已由管理員辦理退單；款項與發票處理進度請查看訂單。",
        data={"order_id": order.id, "request_id": request.id},
        dedupe_key=f"meal-cancellation-review:{request.id}", event_type="meal_cancellation_approved",
    )
    await session.commit()
    wake_printer()
    return await _meal_order_read(session, order, True)


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


@meals_router.get("/v1/admin/meal-schedules/calendar-status")
async def admin_meal_calendar_status(_admin: User = Depends(require_admin)) -> dict:
    return calendar_status()


@meals_router.get("/v1/admin/meal-calendar")
async def admin_meal_calendar(service_date: date | None = None,
                              _admin: User = Depends(require_admin)) -> dict:
    try:
        selected = service_date or next_workday(datetime.now(TAIPEI).date())
        previous = previous_workday(selected)
        working = is_workday(selected)
        try:
            following = next_workday(selected).isoformat()
        except CalendarUnavailable:
            following = None
        return {"service_date": selected.isoformat(), "is_workday": working,
                "previous_workday": previous.isoformat(), "next_workday": following,
                "ordering_starts_at": datetime.combine(previous, time.min, TAIPEI).astimezone(timezone.utc),
                "ordering_ends_at": datetime.combine(selected - timedelta(days=1), time(20), TAIPEI).astimezone(timezone.utc)}
    except CalendarUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


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
    conversion = {}
    created_ids = await generate_scheduled_meal_events(session, datetime.now(timezone.utc), template_id=template.id, report=conversion)
    return {"created_count": len(created_ids), "created_event_ids": created_ids, "conversion": conversion}


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
            .order_by(MealEvent.created_at.desc(), MealEvent.id.desc())
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


def _configured_event_values(body: MealEventCreate, *, existing: MealEvent | None = None) -> dict:
    values = body.model_dump(exclude={"offerings"})
    values["pickup_starts_at"] = _aware(body.pickup_starts_at).astimezone(timezone.utc)
    values["pickup_ends_at"] = _aware(body.pickup_ends_at).astimezone(timezone.utc)
    policy = body.booking_policy
    if existing is not None and "booking_policy" not in body.model_fields_set:
        policy = existing.booking_policy
    values["booking_policy"] = policy
    if policy == "legacy":
        if existing is None or existing.booking_policy != "legacy":
            raise HTTPException(422, "新場次請使用工作日或特殊日期接單規則")
        return values
    service_date = _aware(body.pickup_starts_at).astimezone(TAIPEI).date()
    try:
        values["ordering_starts_at"], values["ordering_ends_at"] = configured_booking_window(
            service_date, policy, body.ordering_starts_at, body.ordering_ends_at)
    except CalendarUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return values


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
    values = _configured_event_values(body)
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
        **values,
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
                "booking_policy": event.booking_policy,
                "service_date": _aware(event.pickup_starts_at).astimezone(TAIPEI).date().isoformat(),
                "ordering_starts_at": _aware(event.ordering_starts_at).isoformat(),
                "ordering_ends_at": _aware(event.ordering_ends_at).isoformat(),
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
    values = _configured_event_values(body, existing=event)
    service_date = _aware(body.pickup_starts_at).astimezone(TAIPEI).date()
    if event.schedule_template_id and service_date != event.service_date:
        raise HTTPException(status_code=422, detail="排程場次不可更換供餐日期；請另建手動場次")
    if event.status == MealEventStatus.PUBLISHED and _aware(values["ordering_ends_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="已發布場次的訂購截止時間不得設為過去")
    meal_ids = {offering.meal_id for offering in body.offerings}
    active_ids = set(await session.scalars(select(Meal.id).where(Meal.id.in_(meal_ids), Meal.is_active.is_(True))))
    if meal_ids != active_ids:
        raise HTTPException(status_code=422, detail="場次包含無效或停用餐點")
    before = _meal_event_read(event).model_dump(mode="json")
    event.offerings.clear()
    await session.flush()
    for name, value in values.items():
        setattr(event, name, value)
    event.offerings = [MealEventOffering(**offering.model_dump()) for offering in body.offerings]
    session.add(AdminAudit(
        actor_id=admin.id, action="meal_event.update", aggregate_type="meal_event", aggregate_id=event.id,
        data={"before": before, "after": {
            **body.model_dump(mode="json"),
            "booking_policy": event.booking_policy,
            "service_date": service_date.isoformat(),
            "ordering_starts_at": _aware(event.ordering_starts_at).isoformat(),
            "ordering_ends_at": _aware(event.ordering_ends_at).isoformat(),
        }},
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
    if event.booking_policy == "workday":
        try:
            if not is_workday(_aware(event.pickup_starts_at).astimezone(TAIPEI).date()):
                raise HTTPException(409, "此日期不是供餐工作日；請先編輯為特殊日期再發布")
        except CalendarUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
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


@meals_router.patch("/v1/admin/meal-events/{event_id}/payment-options", response_model=MealEventRead)
async def update_meal_payment_options(
    event_id: str, body: MealPaymentOptionsUpdate,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True)
    if event is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    if event.status in {MealEventStatus.CANCELLED, MealEventStatus.COMPLETED}:
        raise HTTPException(status_code=409, detail="場次已結束或取消")
    event.allow_cash_payment = body.allow_cash_payment
    session.add(AdminAudit(actor_id=admin.id, action="meal_event.payment_options", aggregate_type="meal_event", aggregate_id=event.id, data={"allow_cash_payment": body.allow_cash_payment}))
    await session.commit()
    return _meal_event_read(event)


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
        await cancel_unissued_invoice_jobs(session, order)
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        if order.fulfillment is not None:
            order.fulfillment.status = FulfillmentState.CANCELLED
        if was_paid:
            try:
                refund, refund_queued = await create_provider_aware_refund(
                    session,
                    order=order,
                    amount=order.amount_total,
                    reason=f"便當場次取消：{body.reason}" if body.reason else "",
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
                    ("現金退款待現場退還" if order.payment_method == PaymentMethod.CASH_ON_PICKUP else "便當場次取消退款已送出")
                    if refund_queued
                    else "便當場次取消退款紀錄已建立"
                ),
                body=(
                    (f"「{event.title}」已取消，訂單 {order.order_number} 待人員實際退還現金 NT${order.amount_total}。"
                     if order.payment_method == PaymentMethod.CASH_ON_PICKUP else
                     f"「{event.title}」已取消，訂單 {order.order_number} 已送出 NT${order.amount_total} 退款申請，待金流確認完成後會再通知。")
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
        await enqueue_cancellation_print(session, order, settings)
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
    wake_printer()
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
    from ..cash_payments import expire_cash_order
    pending_cash = list(await session.scalars(select(Order).where(
        Order.meal_event_id == event.id, Order.payment_method == PaymentMethod.CASH_ON_PICKUP,
        Order.payment_status == PaymentStatus.PENDING,
    ).options(selectinload(Order.reservations), selectinload(Order.fulfillment)).with_for_update()))
    for order in pending_cash:
        await expire_cash_order(session, order, now)
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
        .with_for_update(of=Order)
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
