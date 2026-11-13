from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import false, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

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
from ..account_profiles import account_phone, set_account_phone
from ..audited_changes import audit_changes
from ..contact_phone import decrypt_contact_phone
from ..order_numbers import allocate_order_number
from ..order_history import OrderHistoryQuery, read_order_history
from ..printing import enqueue_order_print, stop_cancelled_order_prints
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
    meal_calendar_blocked_reason,
    meal_new_orders_blocked_reason,
    meal_order_deadline,
    meal_period,
    meal_pickup_slots,
    meal_schedule_has_pickup_slots,
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
    AdminMealEventRead,
    MealAdminOrderPage,
    MealCreate,
    MealCancellationQueueRead,
    AdminMealCancellationRequest,
    MealCancellationRequestCreate,
    MealCancellationRequestRead,
    MealCancellationReview,
    MealEventActionRequest,
    MealEventArchiveUpdate,
    MealEventCalendarRead,
    MealEventCancelRequest,
    MealDisasterAnnouncement,
    MealDisasterCancel,
    MealDisasterPreview,
    MealDisasterResult,
    MealDisasterSelection,
    MealEventCreate,
    MealEventDraftCreate,
    MealEventRead,
    MealOptionGroupRead,
    MealOptionRead,
    MealOfferingCapacityUpdate,
    MealOfferingRead,
    MealOrderCreate,
    MealOrderingPauseUpdate,
    MealOrderQuoteRead,
    MealOrderRead,
    MealContactRead,
    MealOrderSearchFilters,
    MealOrderSubmissionRead,
    MealOperationsSummary,
    MealPickupCredentialRead,
    MealPickupRedemptionRead,
    MealPickupVerify,
    MealPaymentOptionsUpdate,
    MealRead,
    MealScheduleDisable,
    MealScheduleDisabled,
    MealScheduleDisablePreview,
    MealScheduleDisablePreviewEvent,
    MealScheduleInput,
    MealScheduleOrderingStatus,
    MealScheduleRead,
    MealScheduleUpdate,
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


def _remaining_meal_ordering_start(event: MealEvent, now: datetime) -> datetime | None:
    start, _ = meal_booking_window(event, business=event.business_only)
    if any(
        max(start, now) < meal_order_deadline(event, slot, business=event.business_only)
        for slot in meal_pickup_slots(event)
    ):
        return start
    return None


def _meal_ordering_adjustment_blocked_reason(event: MealEvent, now: datetime) -> str | None:
    if event.status not in {MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN}:
        return "此場次目前無法調整接單狀態"
    if reason := meal_calendar_blocked_reason(event):
        return reason
    if _remaining_meal_ordering_start(event, now) is None:
        return "本場次已無可預訂的取餐時段"
    return None


def _require_meal_ordering_adjustable(event: MealEvent, now: datetime) -> None:
    if reason := _meal_ordering_adjustment_blocked_reason(event, now):
        raise HTTPException(409, reason)


def _meal_event_booking_fields(event: MealEvent) -> dict[str, Any]:
    booking_start, booking_end = meal_booking_window(event)
    return {
        "id": event.id,
        "booking_policy": event.booking_policy,
        "same_day_preparation_minutes": event.same_day_preparation_minutes,
        "business_only": event.business_only,
        "ordering_paused": event.ordering_paused_at is not None,
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


def _meal_event_fields(event: MealEvent, *, can_edit: bool) -> dict[str, Any]:
    return dict(
        **_meal_event_booking_fields(event),
        ordering_pause_version=event.ordering_pause_version,
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
                is_active=offering.is_active and offering.meal.is_active,
                option_groups=_meal_option_groups_read(offering.meal),
            )
            for offering in sorted(
                event.offerings,
                key=lambda item: item.position,
            )
            if offering.is_active
        ],
    )


def _meal_event_read(event: MealEvent, *, can_edit: bool = False) -> MealEventRead:
    return MealEventRead(**_meal_event_fields(event, can_edit=can_edit))


def _admin_meal_event_read(event: MealEvent, *, can_edit: bool = False) -> AdminMealEventRead:
    return AdminMealEventRead(
        **_meal_event_fields(event, can_edit=can_edit),
        archived_at=_aware(event.archived_at) if event.archived_at is not None else None,
    )


async def _load_event(
    session: AsyncSession,
    event_id: str,
    *,
    for_update: bool = False,
    refresh_existing: bool = False,
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
    if not for_update:
        return await session.scalar(query)
    query = query.with_for_update()
    if refresh_existing:
        return await session.scalar(query.execution_options(populate_existing=True))
    row = (await session.execute(query.add_columns(
        MealEvent.ordering_paused_at, MealEvent.ordering_pause_version,
    ))).one_or_none()
    if row is None:
        return None
    event, paused_at, version = row
    # Refresh the locked pause state without expiring other objects loaded by the caller.
    set_committed_value(event, "ordering_paused_at", paused_at)
    set_committed_value(event, "ordering_pause_version", version)
    return event


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
    settings: Settings | None = None,
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
    contact_phone = None
    if viewer_is_admin:
        contact_phone = decrypt_contact_phone(
            fulfillment.recipient_phone_encrypted, f"order-fulfillment:{fulfillment.id}:recipient_phone",
            settings or get_settings(),
        )
    return MealOrderRead(
        **business_payment_fields(order),
        customer_name=order.user.display_name if viewer_is_admin and order.user else None,
        customer_email=order.contact_email if viewer_is_admin else None,
        contact_phone=contact_phone,
        invoice_buyer_tax_id=order.invoice_buyer_tax_id if order.meal_booking_type == "business" else None,
        invoice_buyer_name=order.invoice_buyer_name if order.meal_booking_type == "business" else None,
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
    email = order.contact_email or await session.scalar(select(User.email).where(User.id == order.user_id))
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    await service.publish(
        NotificationCommand(
            user_id=order.user_id,
            event_type=event_type,
            title=title,
            body=body,
            data=data,
            email=email,
            dedupe_key=dedupe_key,
        )
    )


def _meal_order_query():
    return (
        select(Order)
        .where(Order.sales_channel == SalesChannel.MEAL_PREORDER)
        .options(
            selectinload(Order.user),
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


@meals_router.get("/v1/meal-checkout/contact", response_model=MealContactRead)
async def get_meal_checkout_contact(
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealContactRead:
    response.headers["Cache-Control"] = "private, no-store"
    return MealContactRead(phone=await account_phone(session, user, settings))


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
    settings: Settings = Depends(get_settings),
) -> list[MealOrderRead]:
    if await session.get(MealEvent, event_id) is None:
        raise HTTPException(status_code=404, detail="找不到便當場次")
    orders = list(await session.scalars(
        _meal_order_query().where(Order.meal_event_id == event_id)
        .outerjoin(Order.fulfillment)
        .order_by(func.coalesce(OrderFulfillment.pickup_at, OrderFulfillment.pickup_starts_at).nulls_last(), Order.created_at.desc(), Order.id.desc())
    ))
    return [await _meal_order_read(session, order, True, settings=settings) for order in orders]


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


PUBLIC_MEAL_EVENT_STATUSES = (
    MealEventStatus.PUBLISHED, MealEventStatus.ORDERING_CLOSED, MealEventStatus.PICKUP_OPEN,
)


def _public_meal_event_query(settings: Settings, *columns):
    query = select(MealEvent, *columns).where(
        MealEvent.status.in_(PUBLIC_MEAL_EVENT_STATUSES),
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
    active = (MealEventOffering.is_active != false()) & MealEventOffering.meal.has(Meal.is_active.is_(True))
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
    same_day = event.same_day_preparation_minutes is not None
    business = body.meal_booking_type == "business"
    if event.business_only and not business:
        raise HTTPException(422, "此場次僅開放會餐")
    if body.payment_method == PaymentMethod.CASH_ON_PICKUP and not business and (same_day or not event.allow_cash_payment):
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
    quantities: dict[str, int] = {}
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for requested in body.items:
        key = (requested.offering_id, tuple(sorted(requested.option_ids)) if business else ())
        if key in seen:
            raise HTTPException(status_code=422, detail="相同便當和選項請合併份數")
        seen.add(key)
        quantities[requested.offering_id] = quantities.get(requested.offering_id, 0) + requested.quantity
        if quantities[requested.offering_id] > 99:
            raise HTTPException(422, "每種便當最多可訂 99 份")
        offering = by_id.get(requested.offering_id)
        if offering is None:
            raise HTTPException(status_code=404, detail="找不到便當品項")
        if not offering.meal.is_active:
            raise HTTPException(409, "此便當已停售")
        try:
            validate_meal_preorder(
                event,
                offering,
                quantities[requested.offering_id],
                now,
            )
        except DomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            validate_meal_booking_day(event, now, business=business)
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
    pickup_at = body.pickup_at
    if same_day and not business and pickup_at is None:
        pickup_at = next((slot for slot in pickup_slots if now < meal_order_deadline(event, slot)), None)
        if pickup_at is None:
            raise HTTPException(409, "本場次已無可預訂的取餐時段")
    elif pickup_at is None and pickup_slots:
        pickup_at = pickup_slots[0]
    if pickup_at not in pickup_slots:
        raise HTTPException(status_code=422, detail="請選擇本場次午餐 11:00–13:00 或晚餐 16:30–18:00 的半小時取餐時段")
    if same_day and not business and now >= meal_order_deadline(event, pickup_at):
        raise HTTPException(409, "此取餐時段已截止，請重新選擇")
    pickup_at = pickup_at.astimezone(timezone.utc)
    from ..promotions import price_promotion
    promotion, discount = await price_promotion(session, total, body.coupon_code, user.id if user else None, now)
    if event.business_only and total - discount <= 0:
        raise HTTPException(422, "會餐金額必須大於零")
    if same_day and total - discount <= 0:
        raise HTTPException(422, "開放當日訂餐的場次須有大於零的應付金額")
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


@meals_router.get(
    "/v1/meal-events/{event_id}/order-submissions/{client_request_id}",
    response_model=MealOrderSubmissionRead,
)
async def read_meal_order_submission(
    event_id: str,
    client_request_id: UUID,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MealOrderSubmissionRead:
    response.headers["Cache-Control"] = "no-store"
    order_id = await session.scalar(select(Order.id).where(
        Order.user_id == user.id,
        Order.client_request_id == str(client_request_id),
        Order.meal_event_id == event_id,
        Order.sales_channel == SalesChannel.MEAL_PREORDER,
        Order.meal_booking_type == "standard",
    ))
    return MealOrderSubmissionRead(order_id=order_id)


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
    fingerprint_body = body.model_dump(mode="json", exclude={"client_request_id", "contact_phone"})
    if body.meal_booking_type == "standard":
        fingerprint_body.pop("meal_booking_type")
    # Default preferences must preserve fingerprints created before these fields existed.
    legacy_invoice_handling = "external" if body.payment_method == PaymentMethod.CASH_ON_PICKUP else "on_payment"
    if body.invoice_handling in {None, legacy_invoice_handling}:
        fingerprint_body.pop("invoice_handling")
    if body.pickup_location_id is None:
        fingerprint_body.pop("pickup_location_id")
    if not body.needs_utensils:
        fingerprint_body.pop("needs_utensils")

    def submission_fingerprint(payload: dict) -> str:
        serialized = json.dumps({"event_id": event_id, "body": payload}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode()).hexdigest()

    fingerprint = submission_fingerprint(fingerprint_body)
    legacy_fingerprint = submission_fingerprint({**fingerprint_body, "contact_phone": body.contact_phone}) if body.contact_phone else None
    submitting_user_id = user.id

    async def existing_submission():
        if body.client_request_id is None:
            return None
        existing = await session.scalar(_meal_order_query().where(Order.user_id == submitting_user_id, Order.client_request_id == str(body.client_request_id)))
        if existing is None:
            return None
        if legacy_fingerprint and existing.request_fingerprint == legacy_fingerprint:
            return await _meal_order_read(session, existing)
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, "此送單識別碼已使用，請重新確認訂單")
        if body.contact_phone:
            fulfillment = existing.fulfillment
            saved_phone = decrypt_contact_phone(
                fulfillment.recipient_phone_encrypted, f"order-fulfillment:{fulfillment.id}:recipient_phone", settings,
            ) if fulfillment else None
            if saved_phone != body.contact_phone:
                raise HTTPException(409, "此送單識別碼已使用，請重新確認訂單")
        return await _meal_order_read(session, existing)

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
    if body.meal_booking_type == "standard" and body.invoice_handling == "pickup_day":
        raise HTTPException(422, "取餐日開票適用會餐。請重新整理頁面後確認訂單。")
    from ..promotions import lock_promotions
    await lock_promotions(session)
    quote = await quote_meal_order(event_id, body, session, settings, user)
    if body.expected_total is not None and body.expected_total != quote.amount_total:
        raise HTTPException(status_code=409, detail="價格或優惠已變動，請重新確認訂單明細")
    contact_phone = body.contact_phone or await account_phone(session, user, settings)
    if contact_phone is None:
        raise HTTPException(422, "請填寫聯絡電話")
    by_id = {offering.id: offering for offering in event.offerings}
    now = datetime.now(timezone.utc)
    order_id = new_id()
    order_number = await allocate_order_number(session, "M", now)
    pickup_code = await _unique_pickup_code(session)
    deadline = meal_order_deadline(event, quote.pickup_at, business=body.meal_booking_type == "business")
    if (event.same_day_preparation_minutes is not None or event.business_only) and datetime.now(timezone.utc) >= deadline:
        raise HTTPException(409, "此取餐時段已截止，請重新選擇")
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
                unit_price=quoted.unit_price,
                subtotal=quoted.subtotal,
                tax_type=quoted.tax_type,
                selected_options=[
                    OrderItemOption(
                        source_meal_option_id=selection.option_id,
                        group_name=selection.group_name,
                        option_name=selection.option_name,
                        price_delta=selection.price_delta,
                        position=position,
                    )
                    for position, selection in enumerate(
                        quoted.selections
                    )
                ],
            )
            for requested, quoted in zip(body.items, quote.items, strict=True)
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
            meal_ordering_ends_at=deadline,
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
    try:
        cipher = pii_cipher_from_settings(settings)
        order.fulfillment.recipient_phone_encrypted = cipher.encrypt_text(
            contact_phone, associated_data=f"order-fulfillment:{order.fulfillment.id}:recipient_phone",
        )
        order.fulfillment.encryption_key_version = cipher.current_version
        if body.contact_phone:
            set_account_phone(user, contact_phone, settings)
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
        status=request.status, source=request.source, requested_at=_aware(request.requested_at),
        reviewed_by_id=request.reviewed_by_id,
        reviewed_by_name=request.reviewed_by_name,
        reviewed_at=_aware(request.reviewed_at) if request.reviewed_at else None,
        review_reason=request.review_reason, initiated_by_admin=request.initiated_by_admin,
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
    _user: User = Depends(get_current_user),
) -> MealCancellationRequestRead:
    raise HTTPException(status_code=403, detail="退單請致電客服，由管理員協助辦理")


@meals_router.get("/v1/admin/meal-cancellation-requests", response_model=MealCancellationQueueRead)
async def list_meal_cancellation_requests(
    starts_on: date | None = None,
    ends_on: date | None = None,
    review_status: Literal["pending", "approved", "rejected", "all"] = "pending",
    search: str = Query(default="", max_length=100),
    order_id: str | None = Query(default=None, min_length=1, max_length=36),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealCancellationQueueRead:
    query = select(MealCancellationRequest).join(Order).where(Order.sales_channel == SalesChannel.MEAL_PREORDER)
    from .meal_cancellation_reports import cancellation_filters
    query = cancellation_filters(query, starts_on, ends_on, search, order_id=order_id)
    if review_status != "all":
        query = query.where(MealCancellationRequest.status == review_status)
    if review_status == "pending":
        query = query.where(Order.payment_status.not_in({PaymentStatus.EXPIRED, PaymentStatus.FAILED}))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    requests = list(await session.scalars(query.options(
        selectinload(MealCancellationRequest.order).selectinload(Order.user),
        selectinload(MealCancellationRequest.order).selectinload(Order.items).selectinload(OrderItem.selected_options),
        selectinload(MealCancellationRequest.order).selectinload(Order.meal_event),
        selectinload(MealCancellationRequest.order).selectinload(Order.fulfillment),
        selectinload(MealCancellationRequest.order).selectinload(Order.invoice),
        selectinload(MealCancellationRequest.order).selectinload(Order.cancellation_requests),
        selectinload(MealCancellationRequest.order).selectinload(Order.amendments),
        selectinload(MealCancellationRequest.order).selectinload(Order.payment_attempts),
    ).order_by(MealCancellationRequest.requested_at.desc(), MealCancellationRequest.id).offset(offset).limit(limit)))
    return MealCancellationQueueRead(
        items=[{
            "request": _cancellation_request_read(request),
            "order": await _meal_order_read(session, request.order, True, settings=settings),
            "customer_name": request.order.user.display_name,
            "customer_email": request.order.contact_email,
            "can_approve": admin_meal_cancellation_allowed(request.order),
        } for request in requests], total=total or 0, limit=limit, offset=offset,
    )


@meals_router.get("/v1/admin/meal-orders", response_model=MealAdminOrderPage)
async def admin_search_meal_orders(
    filters: Annotated[MealOrderSearchFilters, Query()],
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealAdminOrderPage:
    orders, total = await search_meal_orders(session, filters)
    return MealAdminOrderPage(items=[{
        "order": await _meal_order_read(session, order, True, settings=settings),
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
        cash = order.payment_method == PaymentMethod.CASH_ON_PICKUP
        if cash:
            refund_title = "現金退款待現場退還"
            refund_body = f"訂單 {order.order_number} 已取消，待人員實際退還現金 NT${order.amount_total}。"
        elif refund_queued:
            refund_title = "便當退款申請已送出"
            refund_body = f"訂單 {order.order_number} 已送出 NT${order.amount_total} 退款申請，待金流確認完成後會再通知。"
        else:
            refund_title = "便當退款紀錄已建立"
            refund_body = f"訂單 {order.order_number} 已建立 NT${order.amount_total} 的 Sandbox 退款紀錄。"
        await _publish_meal_refund_notification(
            session, order,
            title=refund_title, body=refund_body,
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
    await stop_cancelled_order_prints(session, order)


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
        return await _meal_order_read(session, order, True, settings=settings)
    pending = next((request for request in order.cancellation_requests if request.status == "pending"), None)
    if pending is not None:
        await review_meal_cancellation(pending.id, MealCancellationReview(decision="approve", reason=body.reason), admin, session, settings)
        return await _meal_order_read(session, order, True, settings=settings)
    now = datetime.now(timezone.utc)
    request = MealCancellationRequest(order_id=order.id, requested_by_id=admin.id, reason=body.reason,
        status="approved", source="admin", requested_at=now, reviewed_by_id=admin.id, reviewed_by=admin, reviewed_at=now, review_reason=body.reason)
    await _cancel_approved_meal_order(session, order, request, admin, settings, now)
    order.cancellation_requests.append(request)
    session.add(request)
    await session.flush()
    session.add(AdminAudit(actor_id=admin.id, action="meal_cancellation.approved", aggregate_type="order", aggregate_id=order.id,
        reason=body.reason, data={"request_id": request.id, "source": "phone"}))
    await _publish_meal_refund_notification(
        session, order, title="便當退單已辦理",
        body=f"訂單 {order.order_number} 已取消。"
             + ("款項與發票處理進度請查看訂單。" if order.paid_at is not None else ""),
        data={"order_id": order.id, "request_id": request.id},
        dedupe_key=f"meal-cancellation-review:{request.id}", event_type="meal_cancellation_approved",
    )
    await session.commit()
    wake_printer()
    return await _meal_order_read(session, order, True, settings=settings)


async def _validate_schedule_meals(session: AsyncSession, body: MealScheduleInput) -> None:
    meal_ids = {offering.meal_id for offering in body.offerings}
    query = select(Meal.id).where(Meal.id.in_(meal_ids))
    if body.enabled:
        query = query.where(Meal.is_active.is_(True))
    valid_ids = set(await session.scalars(query))
    if valid_ids != meal_ids:
        raise HTTPException(status_code=422, detail="每日菜單包含無效或停用餐點")


def _validate_schedule_pickup_slots(body: MealScheduleInput, existing: MealScheduleTemplate | None = None) -> None:
    if meal_schedule_has_pickup_slots(body.meal_period, body.pickup_start_time, body.pickup_end_time):
        return
    # An old schedule with an unusable time range can still be disabled.
    if existing is not None and not body.enabled and (
        body.meal_period == existing.meal_period
        and body.pickup_start_time == time.fromisoformat(existing.pickup_start_time)
        and body.pickup_end_time == time.fromisoformat(existing.pickup_end_time)
    ):
        return
    raise HTTPException(422, "取餐時間須包含至少一個該餐別的半小時取餐時段")


def _schedule_values(body: MealScheduleInput) -> dict[str, Any]:
    values = body.model_dump(mode="json", exclude={"expected_updated_at"})
    for name in ("pickup_start_time", "pickup_end_time", "cutoff_time"):
        values[name] = getattr(body, name).strftime("%H:%M")
    return values


def _advance_meal_schedule_version(template: MealScheduleTemplate, now: datetime) -> None:
    template.updated_at = max(now, _aware(template.updated_at) + timedelta(microseconds=1))


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
    _validate_schedule_pickup_slots(body)
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
    body: MealScheduleUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealScheduleRead:
    template = await session.scalar(select(MealScheduleTemplate).where(MealScheduleTemplate.id == template_id)
        .with_for_update().execution_options(populate_existing=True))
    if template is None:
        raise HTTPException(status_code=404, detail="找不到每日供餐設定")
    if body.expected_updated_at is not None and _aware(template.updated_at) != body.expected_updated_at:
        raise HTTPException(409, "每日供餐設定已更新，請重新開啟編輯後再試")
    _validate_schedule_pickup_slots(body, template)
    await _validate_schedule_meals(session, body)
    before = MealScheduleRead.model_validate(template).model_dump(mode="json")
    for name, value in _schedule_values(body).items():
        setattr(template, name, value)
    _advance_meal_schedule_version(template, datetime.now(timezone.utc))
    await session.flush()
    session.add(AdminAudit(
        actor_id=admin.id, action="meal_schedule.update", aggregate_type="meal_schedule",
        aggregate_id=template.id, data={"before": before, "after": body.model_dump(mode="json", exclude={"expected_updated_at"})},
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
    schedule = MealScheduleRead.model_validate(template)
    _validate_schedule_pickup_slots(schedule)
    await _validate_schedule_meals(session, schedule)
    conversion = {}
    created_ids = await generate_scheduled_meal_events(session, datetime.now(timezone.utc), template_id=template.id, report=conversion)
    return {"created_count": len(created_ids), "created_event_ids": created_ids, "conversion": conversion}


def _schedule_disable_ordering_status(event: MealEvent, now: datetime) -> MealScheduleOrderingStatus:
    if event.status == MealEventStatus.DRAFT:
        return "draft"
    if event.status not in {MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN} or meal_calendar_blocked_reason(event):
        return "closed"
    start = _remaining_meal_ordering_start(event, now)
    if start is None:
        return "closed"
    if event.ordering_paused_at is not None:
        return "paused"
    return "open" if start <= now else "scheduled"


async def _enabled_meal_schedule(session: AsyncSession, template_id: str, *, lock: bool) -> MealScheduleTemplate:
    query = select(MealScheduleTemplate).where(MealScheduleTemplate.id == template_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    template = await session.scalar(query)
    if template is None:
        raise HTTPException(404, "找不到每日供餐設定")
    if not template.enabled:
        raise HTTPException(409, "此排程已停用")
    return template


async def _meal_schedule_disable_preview(
    session: AsyncSession, template: MealScheduleTemplate, *, lock: bool,
) -> tuple[MealScheduleDisablePreview, dict[str, MealEvent], datetime]:
    query = select(MealEvent).where(
        MealEvent.schedule_template_id == template.id,
        MealEvent.status.not_in({MealEventStatus.CANCELLED, MealEventStatus.COMPLETED}),
        MealEvent.pickup_ends_at > datetime.now(timezone.utc),
    ).order_by(MealEvent.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    events = list(await session.scalars(query))
    counts = await session.execute(select(Order.meal_event_id, func.count(Order.id)).where(
        Order.meal_event_id.in_([event.id for event in events]),
        Order.sales_channel == SalesChannel.MEAL_PREORDER,
        Order.cancelled_at.is_(None),
        Order.fulfillment_status != FulfillmentStatus.CANCELLED,
    ).group_by(Order.meal_event_id))
    count_by_event = dict(counts.all())
    now = datetime.now(timezone.utc)
    current_events = {event.id: event for event in events if _aware(event.pickup_ends_at) > now}
    rows = []
    event_versions = []
    for event in current_events.values():
        row = MealScheduleDisablePreviewEvent(
            id=event.id, title=event.title, service_date=meal_service_date(event),
            order_count=count_by_event.get(event.id, 0), ordering_status=_schedule_disable_ordering_status(event, now),
        )
        rows.append(row)
        event_versions.append({
            **row.model_dump(mode="json"),
            "status": event.status.value,
            "updated_at": _aware(event.updated_at).isoformat(),
            "booking_policy": event.booking_policy,
            "business_only": event.business_only,
            "same_day_preparation_minutes": event.same_day_preparation_minutes,
            "ordering_pause_version": event.ordering_pause_version,
            "windows": [_aware(value).isoformat() if value is not None else None for value in (
                event.ordering_starts_at, event.ordering_ends_at, event.pickup_starts_at,
                event.pickup_ends_at, event.pickup_slot_ends_at,
            )],
        })
    snapshot = {
        "template": [template.id, _aware(template.updated_at).isoformat()],
        "events": event_versions,
    }
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    rows.sort(key=lambda row: (row.service_date, row.id))
    return MealScheduleDisablePreview(preview_version=version, events=rows), current_events, now


@meals_router.get("/v1/admin/meal-schedules/{template_id}/disable-preview", response_model=MealScheduleDisablePreview)
async def admin_preview_meal_schedule_disable(
    template_id: str, response: Response, _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealScheduleDisablePreview:
    response.headers["Cache-Control"] = "no-store"
    template = await _enabled_meal_schedule(session, template_id, lock=False)
    preview, _, _ = await _meal_schedule_disable_preview(session, template, lock=False)
    return preview


@meals_router.post("/v1/admin/meal-schedules/{template_id}/disable", response_model=MealScheduleDisabled)
async def admin_disable_meal_schedule(
    template_id: str, body: MealScheduleDisable, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealScheduleDisabled:
    template = await _enabled_meal_schedule(session, template_id, lock=True)
    preview, events, now = await _meal_schedule_disable_preview(session, template, lock=True)
    if preview.preview_version != body.preview_version:
        raise HTTPException(409, "場次或訂單已變動，請重新預覽後再試")

    paused_events = []
    if body.scope == "pause_generated":
        paused_events = [events[row.id] for row in preview.events if row.ordering_status in {"open", "scheduled"}]
        if not paused_events:
            raise HTTPException(409, "目前沒有可暫停接單的場次")
        for event in paused_events:
            await _apply_meal_ordering_pause(session, event, MealOrderingPauseUpdate(
                paused=True, expected_version=event.ordering_pause_version,
            ), admin, now)

    template.enabled = False
    _advance_meal_schedule_version(template, now)
    session.add(AdminAudit(
        actor_id=admin.id, action="meal_schedule.disable", aggregate_type="meal_schedule", aggregate_id=template.id,
        data={"scope": body.scope, "paused_event_ids": [event.id for event in paused_events]},
    ))
    await session.commit()
    return MealScheduleDisabled(paused_event_count=len(paused_events))


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


class MealDetailsUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=5000)
    price: int = Field(ge=0, le=1_000_000)
    tax_type: TaxType
    is_active: bool
    expected_updated_at: datetime
    confirmed_event_ids: set[str] = Field(default_factory=set)


async def meal_edit_events(session: AsyncSession, meal_id: str, *, lock: bool = False) -> list[MealEvent]:
    query = select(MealEvent).where(
        MealEvent.pickup_ends_at > datetime.now(timezone.utc),
        MealEvent.status.in_([MealEventStatus.DRAFT, MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN]),
        MealEvent.offerings.any((MealEventOffering.meal_id == meal_id) & MealEventOffering.is_active.is_(True)),
    ).order_by(MealEvent.id)
    return list(await session.scalars(query.with_for_update() if lock else query))


@meals_router.get("/v1/admin/meals/{meal_id}/edit-impact")
async def preview_meal_edit(
    meal_id: str, _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> list[dict[str, str]]:
    if await session.get(Meal, meal_id) is None:
        raise HTTPException(404, "找不到餐點")
    return [{"id": event.id, "title": event.title} for event in await meal_edit_events(session, meal_id)
            if event.status != MealEventStatus.DRAFT]


@meals_router.patch("/v1/admin/meals/{meal_id}", response_model=MealRead)
async def edit_meal(
    meal_id: str, body: MealDetailsUpdate, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealRead:
    current = (await session.execute(
        select(Meal.price, Meal.tax_type, Meal.is_active, Meal.updated_at).where(Meal.id == meal_id)
    )).one_or_none()
    if current is None:
        raise HTTPException(404, "找不到餐點")
    if _aware(current.updated_at) != body.expected_updated_at:
        raise HTTPException(409, "餐點資料已更新，請重新讀取後再修改")
    price_changed = current.price != body.price
    requires_impact = price_changed or current.tax_type != body.tax_type or (current.is_active and not body.is_active)
    templates = []
    if price_changed:
        templates = list(await session.scalars(select(MealScheduleTemplate).order_by(MealScheduleTemplate.id).with_for_update()))
    events = await meal_edit_events(session, meal_id, lock=True) if requires_impact else []
    meal = await session.scalar(select(Meal).where(Meal.id == meal_id)
        .options(selectinload(Meal.option_groups).selectinload(MealOptionGroup.options))
        .with_for_update().execution_options(populate_existing=True))
    if meal is None:
        raise HTTPException(404, "找不到餐點")
    if _aware(meal.updated_at) != body.expected_updated_at:
        raise HTTPException(409, "餐點資料已更新，請重新讀取後再修改")
    updates = body.model_dump(exclude={"expected_updated_at", "confirmed_event_ids"})
    updates["name"] = body.name.strip()
    if not updates["name"]:
        raise HTTPException(422, "請填寫餐點名稱")
    before = {key: getattr(meal, key) for key in updates}
    if before == updates:
        return MealRead.model_validate(meal)
    if {event.id for event in events if event.status != MealEventStatus.DRAFT} - body.confirmed_event_ids:
        raise HTTPException(409, "請先確認會受影響的已發布場次")
    for key, value in updates.items():
        setattr(meal, key, value)
    audit_changes(session, admin.id, "meal", meal.id, "meal.details_update", before, updates)
    if body.price != before["price"] and events:
        offerings = await session.scalars(select(MealEventOffering).where(
            MealEventOffering.meal_event_id.in_([event.id for event in events]),
            MealEventOffering.meal_id == meal_id, MealEventOffering.is_active.is_(True),
            MealEventOffering.price == before["price"],
        ).order_by(MealEventOffering.id).with_for_update())
        for offering in offerings:
            audit_changes(session, admin.id, "meal_event_offering", offering.id, "meal_offering.price_update",
                          {"price": offering.price}, {"price": body.price})
            offering.price = body.price
    for template in templates:
        updated_offerings = [
            {**item, "price": body.price} if item["meal_id"] == meal_id and item["price"] == before["price"] else item
            for item in template.offerings
        ]
        if updated_offerings != template.offerings:
            audit_changes(session, admin.id, "meal_schedule_template", template.id, "meal_schedule.price_update",
                          {"offerings": template.offerings}, {"offerings": updated_offerings})
            template.offerings = updated_offerings
    await session.commit()
    return MealRead.model_validate(meal)


@meals_router.get(
    "/v1/admin/meal-events",
    response_model=list[AdminMealEventRead],
)
async def admin_list_meal_events(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminMealEventRead]:
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
    return [_admin_meal_event_read(
        event,
        can_edit=_meal_event_allows_edit(event, now) and event.id not in ordered_event_ids,
    ) for event in events]


@meals_router.patch("/v1/admin/meal-events/{event_id}/archive", response_model=AdminMealEventRead)
async def archive_meal_event(
    event_id: str,
    body: MealEventArchiveUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminMealEventRead:
    event = await _load_event(session, event_id, for_update=True, refresh_existing=True)
    if event is None:
        raise HTTPException(404, "找不到便當場次")
    if event.status not in {MealEventStatus.COMPLETED, MealEventStatus.CANCELLED}:
        raise HTTPException(409, "只有已完成或已取消的場次可以封存")
    previous = event.archived_at
    if (previous is not None) == body.archived:
        return _admin_meal_event_read(event)

    archived_at = datetime.now(timezone.utc) if body.archived else None
    updated_id = await session.scalar(
        update(MealEvent).where(MealEvent.id == event.id, MealEvent.archived_at == previous)
        .values(archived_at=archived_at).returning(MealEvent.id)
        .execution_options(synchronize_session="fetch"),
    )
    if updated_id is None:
        raise HTTPException(409, "封存狀態已更新，請重新整理後再試")
    audit_changes(session, admin.id, "meal_event", event.id, "meal_event.archive_update",
                  {"archived_at": _aware(previous) if previous is not None else None}, {"archived_at": archived_at})
    await session.commit()
    return _admin_meal_event_read(event)


def _configured_event_values(
    body: MealEventCreate, *, existing: MealEvent | None = None, pickup_source: MealEvent | None = None,
) -> dict:
    values = body.model_dump(exclude={"offerings", "replaces_event_id"})
    values["pickup_starts_at"] = _aware(body.pickup_starts_at).astimezone(timezone.utc)
    values["pickup_ends_at"] = _aware(body.pickup_ends_at).astimezone(timezone.utc)
    pickup_config = existing or pickup_source
    if pickup_config is not None:
        keep_slots = (existing is not None and existing.schedule_template_id is not None) or (
            values["pickup_starts_at"] == _aware(pickup_config.pickup_starts_at)
            and values["pickup_ends_at"] == _aware(pickup_config.pickup_ends_at)
        )
        values["meal_period"] = pickup_config.meal_period if keep_slots else None
        values["pickup_slot_ends_at"] = pickup_config.pickup_slot_ends_at if keep_slots else None
    policy = body.booking_policy
    if existing is not None and "booking_policy" not in body.model_fields_set:
        policy = existing.booking_policy
    values["booking_policy"] = policy
    if existing is not None:
        for name in ("same_day_preparation_minutes", "business_only"):
            if name not in body.model_fields_set:
                values[name] = getattr(existing, name)
    same_day = values["same_day_preparation_minutes"] is not None
    business_only = values["business_only"]
    if business_only and same_day:
        raise HTTPException(422, "僅限會餐的場次須於取餐前預訂")
    if business_only and body.allow_cash_payment:
        raise HTTPException(422, "會餐現金付款不需另外啟用")
    if policy == "legacy":
        if business_only:
            raise HTTPException(422, "請選擇工作日或特殊日期規則以開放僅限會餐的場次")
        if same_day:
            raise HTTPException(422, "請選擇工作日或特殊日期規則以開放當日訂餐")
        if existing is None or existing.booking_policy != "legacy":
            raise HTTPException(422, "新場次請使用工作日或特殊日期接單規則")
        return values
    if same_day and body.allow_cash_payment:
        raise HTTPException(422, "開放當日訂餐的場次僅支援線上付款")
    service_date = values["pickup_starts_at"].astimezone(TAIPEI).date()
    try:
        values["ordering_starts_at"], values["ordering_ends_at"] = configured_booking_window(
            service_date, policy, body.ordering_starts_at, body.ordering_ends_at, same_day=same_day,
        )
    except CalendarUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if same_day or business_only:
        candidate = MealEvent(**values, service_date=service_date)
        slots = meal_pickup_slots(candidate)
        if business_only and not slots:
            raise HTTPException(422, "此場次沒有可預訂的取餐時段")
        if same_day:
            same_day_start = max(
                values["ordering_starts_at"], datetime.combine(service_date, time.min, TAIPEI),
            )
            if not any(same_day_start < meal_order_deadline(candidate, slot) for slot in slots):
                raise HTTPException(422, "當日沒有可預訂的取餐時段，請調整截止或最短備餐時間")
    return values


def _meal_service_coverage(event: MealEvent) -> tuple[date, str, str]:
    return meal_service_date(event), meal_period(event), event.location.strip().casefold()


def _meal_event_replacements():
    source_id = AdminAudit.data["replaces_event_id"].as_string()
    return select(source_id, MealEvent).join(
        MealEvent, MealEvent.id == AdminAudit.aggregate_id,
    ).where(
        AdminAudit.action == "meal_event.create",
        AdminAudit.aggregate_type == "meal_event",
        source_id.is_not(None),
    )


@meals_router.post(
    "/v1/admin/meal-events",
    response_model=MealEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_meal_event(
    body: MealEventDraftCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    source = None
    if body.replaces_event_id is not None:
        source = await session.scalar(select(MealEvent).where(MealEvent.id == body.replaces_event_id).with_for_update())
        if source is None:
            raise HTTPException(404, "找不到原便當場次")
        if source.status != MealEventStatus.CANCELLED:
            raise HTTPException(409, "僅已取消的場次可另建替代場次")
    return await _create_meal_event(body, admin, session, source=source)


async def _create_meal_event(
    body: MealEventCreate, admin: User, session: AsyncSession, *, source: MealEvent | None = None,
) -> MealEventRead:
    values = _configured_event_values(body, pickup_source=source)
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
    if source is not None:
        if _remaining_meal_ordering_start(event, datetime.now(timezone.utc)) is None:
            raise HTTPException(422, "補開場次已無可預訂的時段，請調整供餐日期或時間")
        coverage = _meal_service_coverage(event)
        replacements = await session.execute(_meal_event_replacements().where(
            AdminAudit.data["replaces_event_id"].as_string() == source.id,
            MealEvent.status != MealEventStatus.CANCELLED,
        ))
        if any(_meal_service_coverage(replacement) == coverage for _, replacement in replacements):
            raise HTTPException(409, "相同日期、餐別與取餐地點已有替代場次")
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
                "same_day_preparation_minutes": event.same_day_preparation_minutes,
                "business_only": event.business_only,
                "service_date": _aware(event.pickup_starts_at).astimezone(TAIPEI).date().isoformat(),
                "ordering_starts_at": _aware(event.ordering_starts_at).isoformat(),
                "ordering_ends_at": _aware(event.ordering_ends_at).isoformat(),
                **({"replaces_event_id": source.id} if source is not None else {}),
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
    if event.status == MealEventStatus.PUBLISHED:
        if _aware(values["ordering_ends_at"]) <= datetime.now(timezone.utc):
            raise HTTPException(422, "已發布場次的訂購截止時間不得設為過去")
        candidate = MealEvent(**values, service_date=event.service_date)
        if not meal_pickup_slots(candidate):
            raise HTTPException(422, "此場次沒有可預訂的取餐時段")
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
            "same_day_preparation_minutes": event.same_day_preparation_minutes,
            "business_only": event.business_only,
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
    return await _create_meal_event(body, admin, session)


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
    now = datetime.now(timezone.utc)
    start, cutoff = meal_booking_window(event, business=True) if event.business_only else (
        _aware(event.ordering_starts_at), _aware(event.ordering_ends_at)
    )
    if cutoff <= max(start, now):
        raise HTTPException(status_code=409, detail="訂購截止時間已過")
    slots = meal_pickup_slots(event)
    if not slots:
        raise HTTPException(409, "此場次沒有可預訂的取餐時段")
    if event.same_day_preparation_minutes is not None and not any(
        now < meal_order_deadline(event, slot) for slot in slots
    ):
        raise HTTPException(409, "本場次已無可預訂的取餐時段")
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
    if body.allow_cash_payment:
        if event.business_only:
            raise HTTPException(422, "會餐現金付款不需另外啟用")
        if event.same_day_preparation_minutes is not None:
            raise HTTPException(422, "開放當日訂餐的場次僅支援線上付款")
    event.allow_cash_payment = body.allow_cash_payment
    session.add(AdminAudit(actor_id=admin.id, action="meal_event.payment_options", aggregate_type="meal_event", aggregate_id=event.id, data={"allow_cash_payment": body.allow_cash_payment}))
    await session.commit()
    return _meal_event_read(event)


@meals_router.patch("/v1/admin/meal-events/{event_id}/ordering-pause", response_model=MealEventRead)
async def update_meal_ordering_pause(
    event_id: str,
    body: MealOrderingPauseUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True, refresh_existing=True)
    if event is None:
        raise HTTPException(404, "找不到便當場次")
    if event.ordering_pause_version != body.expected_version:
        raise HTTPException(409, "接單狀態已更新，請重新整理後再試")
    now = datetime.now(timezone.utc)
    _require_meal_ordering_adjustable(event, now)

    can_edit = _meal_event_allows_edit(event, now) and not await session.scalar(
        select(Order.id).where(Order.meal_event_id == event.id).limit(1),
    )
    if (event.ordering_paused_at is not None) != body.paused:
        await _apply_meal_ordering_pause(session, event, body, admin, now)
        await session.commit()
    return _meal_event_read(event, can_edit=can_edit)


async def _apply_meal_ordering_pause(
    session: AsyncSession, event: MealEvent, body: MealOrderingPauseUpdate, admin: User, now: datetime,
) -> None:
    paused = event.ordering_paused_at is not None
    next_version = body.expected_version + 1
    updated_id = await session.scalar(
        update(MealEvent)
        .where(MealEvent.id == event.id, MealEvent.ordering_pause_version == body.expected_version)
        .values(ordering_paused_at=now if body.paused else None, ordering_pause_version=next_version)
        .returning(MealEvent.id)
        .execution_options(synchronize_session="fetch"),
    )
    if updated_id is None:
        raise HTTPException(409, "接單狀態已更新，請重新整理後再試")
    session.add(AdminAudit(
        actor_id=admin.id,
        action="meal_event.ordering_pause",
        aggregate_type="meal_event",
        aggregate_id=event.id,
        reason=body.reason,
        data={
            "before": {"paused": paused, "version": body.expected_version},
            "after": {"paused": body.paused, "version": next_version},
        },
    ))


@meals_router.patch(
    "/v1/admin/meal-events/{event_id}/offerings/{offering_id}/capacity",
    response_model=MealEventRead,
)
async def increase_meal_offering_capacity(
    event_id: str,
    offering_id: str,
    body: MealOfferingCapacityUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealEventRead:
    event = await _load_event(session, event_id, for_update=True, refresh_existing=True)
    if event is None:
        raise HTTPException(404, "找不到便當場次")
    offering = next((item for item in event.offerings if item.id == offering_id), None)
    if offering is None:
        raise HTTPException(404, "找不到可追加份數的餐點")
    await session.refresh(
        offering, ["capacity", "reserved_quantity", "paid_quantity", "is_active"], with_for_update=True,
    )
    if not offering.is_active:
        raise HTTPException(404, "找不到可追加份數的餐點")
    has_orders = event.status == MealEventStatus.PUBLISHED and await session.scalar(
        select(Order.id).where(Order.meal_event_id == event.id).limit(1),
    )
    _require_meal_ordering_adjustable(event, datetime.now(timezone.utc))
    if event.status == MealEventStatus.PUBLISHED and not has_orders:
        raise HTTPException(409, "此場次尚無訂單，請編輯場次調整供應份數")

    stale = "供應份數已更新，請重新整理後再試"
    previous_capacity = offering.capacity
    if previous_capacity != body.expected_capacity:
        raise HTTPException(409, stale)
    if body.capacity <= previous_capacity:
        raise HTTPException(422, "新供應份數必須大於目前份數")
    updated_id = await session.scalar(
        update(MealEventOffering)
        .where(MealEventOffering.id == offering.id, MealEventOffering.capacity == body.expected_capacity)
        .values(capacity=body.capacity)
        .returning(MealEventOffering.id)
        .execution_options(synchronize_session="fetch"),
    )
    if updated_id is None:
        raise HTTPException(409, stale)
    session.add(AdminAudit(
        actor_id=admin.id,
        action="meal_event.offering_capacity_increased",
        aggregate_type="meal_event",
        aggregate_id=event.id,
        reason=body.reason,
        data={
            "offering_id": offering.id,
            "before": {"capacity": previous_capacity},
            "after": {"capacity": body.capacity},
        },
    ))
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
    ready_candidates = select(Order).where(
        Order.meal_event_id == event.id,
        Order.payment_status == PaymentStatus.PAID,
    )
    if event.same_day_preparation_minutes is not None:
        ready_candidates = ready_candidates.where(Order.meal_booking_type == "business")
    orders = (
        await session.scalars(
            ready_candidates.options(selectinload(Order.fulfillment)).with_for_update()
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
    orders = await _event_orders_for_cancellation(session, [event.id], lock=True)
    await _cancel_meal_event_orders(session, event, orders, admin, settings, body.reason)
    await session.commit()
    wake_printer()
    return _meal_event_read(event)


async def _event_orders_for_cancellation(session: AsyncSession, event_ids: list[str], *, lock: bool) -> list[Order]:
    query = (
        select(Order).where(Order.meal_event_id.in_(event_ids))
        .options(selectinload(Order.items), selectinload(Order.fulfillment),
                 selectinload(Order.reservations), selectinload(Order.invoice))
        .order_by(Order.id)
    )
    return list(await session.scalars(query.with_for_update() if lock else query))


def _was_picked_up(order: Order) -> bool:
    return order.fulfillment_status == FulfillmentStatus.PICKED_UP or (
        order.fulfillment is not None and order.fulfillment.status == FulfillmentState.PICKED_UP
    )


def _event_cancellation_active_orders(orders: list[Order]) -> list[Order]:
    return [order for order in orders if order.cancelled_at is None
            and order.payment_status not in {PaymentStatus.EXPIRED, PaymentStatus.FAILED}]


async def _cancel_meal_event_orders(
    session: AsyncSession, event: MealEvent, orders: list[Order], admin: User, settings: Settings, reason: str,
) -> None:
    if any(_was_picked_up(order) for order in orders):
        raise HTTPException(409, "已有訂單完成取餐，不可取消整場")

    now = datetime.now(timezone.utc)
    event.status = MealEventStatus.CANCELLED
    event.cancelled_at = now
    event.cancellation_reason = reason
    for order in _event_cancellation_active_orders(orders):
        await _release_active_meal_reservations(session, order, now)
        was_paid = order.payment_status == PaymentStatus.PAID
        if was_paid:
            await _release_paid_meal_capacity(session, order, now)
        elif order.payment_status == PaymentStatus.PENDING:
            order.payment_status = PaymentStatus.EXPIRED
        order.cancelled_at = now
        order.cancellation_reason = reason
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
                    reason=f"便當場次取消：{reason}" if reason else "",
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
                    else "便當場次取消，已全額退款"
                ),
                body=(
                    (f"「{event.title}」已取消，訂單 {order.order_number} 待人員實際退還現金 NT${order.amount_total}。"
                     if order.payment_method == PaymentMethod.CASH_ON_PICKUP else
                     f"「{event.title}」已取消，訂單 {order.order_number} 已送出 NT${order.amount_total} 退款申請，待金流確認完成後會再通知。")
                    if refund_queued
                    else (
                        f"「{event.title}」已取消，訂單 {order.order_number} "
                        f"已全額退款 NT${order.amount_total}。"
                    )
                ),
                data={"meal_event_id": event.id, "order_id": order.id},
                dedupe_key=f"meal-event-refund:{event.id}:{order.id}",
                event_type=(
                    "meal_event_refund_requested" if refund_queued else "refund_completed"
                ),
            )
        else:
            await _publish_meal_refund_notification(
                session, order, title="便當訂單已取消",
                body=f"「{event.title}」停止供餐，訂單 {order.order_number} 已取消。",
                data={"meal_event_id": event.id, "order_id": order.id},
                dedupe_key=f"meal-event-cancelled:{event.id}:{order.id}", event_type="meal_event_cancelled",
            )
        await stop_cancelled_order_prints(session, order)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="meal_event.cancel",
            aggregate_type="meal_event",
            aggregate_id=event.id,
            reason=reason,
        )
    )


async def _meal_disaster_plan(
    session: AsyncSession, body: MealDisasterSelection, *, lock: bool = False,
) -> tuple[MealDisasterPreview, list[tuple[MealEvent, list[Order]]]]:
    start = datetime.combine(body.service_date, time.min, TAIPEI).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    query = (
        select(MealEvent).where(
            MealEvent.pickup_starts_at >= start,
            MealEvent.pickup_starts_at < end,
            MealEvent.status.notin_([MealEventStatus.CANCELLED, MealEventStatus.COMPLETED]),
        ).order_by(MealEvent.id)
    )
    events = [
        event for event in await session.scalars(query.with_for_update() if lock else query)
        if meal_period(event) in body.periods
    ]
    by_event: dict[str, list[Order]] = {event.id: [] for event in events}
    if events:
        for order in await _event_orders_for_cancellation(session, list(by_event), lock=lock):
            by_event[order.meal_event_id].append(order)
    event_summaries = []
    fingerprint = []
    now = datetime.now(timezone.utc)
    for event in events:
        orders = by_event[event.id]
        active = _event_cancellation_active_orders(orders)
        paid = [order for order in active if order.payment_status == PaymentStatus.PAID]
        paid_cash = [order for order in paid if order.payment_method == PaymentMethod.CASH_ON_PICKUP]
        blocked = any(_was_picked_up(order) for order in orders)
        event_summaries.append({
            "id": event.id, "title": event.title, "period": meal_period(event),
            "order_count": len(active), "paid_count": len(paid),
            "paid_total": sum(order.amount_total for order in paid), "blocked": blocked,
            "cash_paid_count": len(paid_cash), "cash_paid_total": sum(order.amount_total for order in paid_cash),
            "ordering_paused": event.ordering_paused_at is not None,
            "ordering_pause_version": event.ordering_pause_version,
            "ordering_pause_blocked_reason": _meal_ordering_adjustment_blocked_reason(event, now),
        })
        fingerprint.append([event.id, event.title, event.status.value, blocked, [
            [order.id, order.revision, order.payment_status.value, order.payment_method.value, order.amount_total] for order in active
        ]])
    token = hashlib.sha256(json.dumps(
        [body.service_date.isoformat(), sorted(body.periods), fingerprint], ensure_ascii=False,
    ).encode()).hexdigest()
    return MealDisasterPreview(events=event_summaries, preview_token=token), [
        (event, by_event[event.id]) for event in events
    ]


@meals_router.post("/v1/admin/meal-disaster/preview", response_model=MealDisasterPreview)
async def preview_meal_disaster(
    body: MealDisasterSelection,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MealDisasterPreview:
    preview, _ = await _meal_disaster_plan(session, body)
    return preview


@meals_router.post("/v1/admin/meal-disaster/cancel", response_model=MealDisasterResult)
async def cancel_meal_disaster(
    body: MealDisasterCancel,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MealDisasterResult:
    preview, plan = await _meal_disaster_plan(session, body, lock=True)
    if preview.preview_token != body.preview_token or not plan:
        raise HTTPException(409, "場次或訂單已變動，請重新預覽")
    if any(event.blocked for event in preview.events):
        raise HTTPException(409, "有場次已完成取餐，請重新選擇餐別")
    public_event_ids = [event.id for event, _ in plan if event.status in PUBLIC_MEAL_EVENT_STATUSES]
    for event, orders in plan:
        await _cancel_meal_event_orders(session, event, orders, admin, settings, body.reason)
    session.add(AdminAudit(
        actor_id=admin.id, action="meal_disaster.cancel", aggregate_type="meal_event", aggregate_id=plan[0][0].id,
        reason=body.reason, data={
            "service_date": body.service_date.isoformat(), "periods": sorted(body.periods),
            "announcement": body.announcement.strip(), "event_ids": [event.id for event, _ in plan],
            "public_event_ids": public_event_ids,
        },
    ))
    await session.commit()
    wake_printer()
    return MealDisasterResult(
        cancelled_event_count=len(plan), order_count=sum(event.order_count for event in preview.events),
        paid_count=sum(event.paid_count for event in preview.events), paid_total=sum(event.paid_total for event in preview.events),
        cash_paid_count=sum(event.cash_paid_count for event in preview.events),
        cash_paid_total=sum(event.cash_paid_total for event in preview.events),
    )


@meals_router.get("/v1/meal-announcements", response_model=list[MealDisasterAnnouncement])
async def list_meal_announcements(
    response: Response, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings),
) -> list[MealDisasterAnnouncement]:
    response.headers["Cache-Control"] = "no-store"
    today = datetime.now(TAIPEI).date()
    service_date = AdminAudit.data["service_date"].as_string()
    rows = list(await session.scalars(select(AdminAudit.data).where(
        AdminAudit.action == "meal_disaster.cancel",
        service_date >= today.isoformat(),
    ).order_by(service_date, AdminAudit.created_at.desc(), AdminAudit.id.desc())))
    cancelled_ids = {event_id for row in rows for event_id in row.get("event_ids", [])}
    if not cancelled_ids:
        return []

    cancelled_query = select(MealEvent).where(
        MealEvent.id.in_(cancelled_ids), MealEvent.status == MealEventStatus.CANCELLED,
    )
    if settings.environment.strip().lower() == "production":
        cancelled_query = cancelled_query.where(MealEvent.id.not_in(DEMO_MEAL_EVENT_IDS))
    cancelled = {event.id: event for event in await session.scalars(cancelled_query)}
    if not cancelled:
        return []

    restored = await _restored_meal_events(session, cancelled, today)
    affected_periods: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for row in rows:
        day = row["service_date"]
        message = row.get("announcement", "").strip() or "颱風暫停供餐"
        public_ids = set(row.get("public_event_ids", []))
        for event_id in row.get("event_ids", []):
            event = cancelled.get(event_id)
            if event is None or event.id in restored or meal_service_date(event).isoformat() != day:
                continue
            period = meal_period(event)
            _, event_ids = affected_periods.setdefault((day, period), (message, set()))
            if event_id in public_ids:
                event_ids.add(event_id)

    grouped: dict[tuple[str, str], tuple[list[str], set[str]]] = {}
    for (day, period), (message, event_ids) in affected_periods.items():
        periods, all_event_ids = grouped.setdefault((day, message), ([], set()))
        periods.append(period)
        all_event_ids.update(event_ids)

    return [
        MealDisasterAnnouncement(
            service_date=day, periods=sorted(periods), message=message, cancelled_event_ids=sorted(event_ids),
        )
        for (day, message), (periods, event_ids) in list(grouped.items())[:10]
    ]


async def _restored_meal_events(
    session: AsyncSession, cancelled: dict[str, MealEvent], today: date,
) -> set[str]:
    start = datetime.combine(today, time.min, TAIPEI).astimezone(timezone.utc)
    replacement_rows = list(await session.execute(_meal_event_replacements().where(
        MealEvent.pickup_starts_at >= start,
        MealEvent.status != MealEventStatus.DRAFT,
    )))
    events = {**cancelled, **{event.id: event for _, event in replacement_rows}}
    sources = {event.id: source_id for source_id, event in replacement_rows}
    restored = set()
    for source_id, replacement in replacement_rows:
        if replacement.status == MealEventStatus.CANCELLED:
            continue
        coverage = _meal_service_coverage(replacement)
        while source_id in events and source_id not in restored and _meal_service_coverage(events[source_id]) == coverage:
            restored.add(source_id)
            source_id = sources.get(source_id)
    return restored


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
    if event is None or event.status not in {MealEventStatus.PUBLISHED, MealEventStatus.ORDERING_CLOSED, MealEventStatus.PICKUP_OPEN}:
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
    from ..cash_payments import expire_cash_orders
    pending_cash = list(await session.scalars(select(Order).where(
        Order.meal_event_id == event.id, Order.payment_method == PaymentMethod.CASH_ON_PICKUP,
        Order.payment_status == PaymentStatus.PENDING, Order.meal_booking_type != "business",
    ).options(selectinload(Order.reservations), selectinload(Order.fulfillment)).with_for_update()))
    await expire_cash_orders(session, pending_cash, now)
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
