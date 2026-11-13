"""Cash mutations share the caller's transaction; collection locks event then order."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .business_meals import business_payment_blocked_reason, business_payment_window, is_business_meal
from .config import Settings
from .invoice_timing import invoice_handling, pickup_invoice_due, cancel_unissued_invoice_jobs
from .models import (
    AdminAudit, FulfillmentState, FulfillmentStatus, InventoryReservation,
    MealEvent, MealEventOffering, MealEventStatus, Order, PaymentAttempt,
    PaymentMethod, PaymentStatus, Refund, RefundStatus,
    ReservationStatus, SalesChannel, PointAccount, PointTransaction, PointSourceType, MembershipType, InvoiceStatus, User, new_id,
)
from .integrations.invoice_service import (
    enqueue_invoice_adjustment_after_refund, enqueue_invoice_issue,
)
from .integrations.notifications import NotificationCommand, NotificationService, SQLAlchemyNotificationRepository
from .integrations.payment_service import (
    PaymentApplicationError, award_order_purchase_points,
    reverse_order_purchase_points,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def _lock_meal_offerings(
    session: AsyncSession, offering_ids: Iterable[str | None],
) -> dict[str, MealEventOffering]:
    ids = {offering_id for offering_id in offering_ids if offering_id is not None}
    if not ids:
        return {}
    offerings = await session.scalars(
        select(MealEventOffering).where(MealEventOffering.id.in_(ids))
        .order_by(MealEventOffering.id).with_for_update().execution_options(populate_existing=True)
    )
    return {offering.id: offering for offering in offerings}


async def reserve_cash_order(session: AsyncSession, order: Order) -> None:
    if order.sales_channel != SalesChannel.MEAL_PREORDER or order.meal_event is None:
        raise PaymentApplicationError("現場付款目前僅支援便當")
    expires_at = order.meal_event.pickup_ends_at
    if order.payment_method == PaymentMethod.CASH_ON_PICKUP and is_business_meal(order):
        window = business_payment_window(order)
        if window is None:
            raise PaymentApplicationError("會餐取餐日期未提供")
        expires_at = window[1]
    quantities: dict[str, int] = defaultdict(int)
    for item in order.items:
        if item.source_meal_offering_id is None:
            raise PaymentApplicationError("便當剩餘數量不足")
        quantities[item.source_meal_offering_id] += item.quantity
    offerings = await _lock_meal_offerings(session, quantities)
    for offering_id, quantity in quantities.items():
        offering = offerings.get(offering_id)
        if (offering is None or offering.meal_event_id != order.meal_event_id or not offering.is_active
            or offering.capacity - offering.reserved_quantity - offering.paid_quantity < quantity):
            raise PaymentApplicationError("便當剩餘數量不足")
        offering.reserved_quantity += quantity
        session.add(InventoryReservation(
            order=order, source_meal_offering_id=offering_id, quantity=quantity,
            status=ReservationStatus.ACTIVE, expires_at=expires_at,
        ))


async def confirm_cash_payment(
    session: AsyncSession, order: Order, *, amount: int, reason: str,
    actor_id: str, settings: Settings, now: datetime, collection_method: str = "cash", reference: str = "", invoice_policy: str | None = None,
) -> None:
    if order.payment_method != PaymentMethod.CASH_ON_PICKUP or order.sales_channel != SalesChannel.MEAL_PREORDER:
        raise PaymentApplicationError("此訂單不是現場付款")
    if amount != order.amount_total or amount <= 0:
        raise PaymentApplicationError("實收金額必須等於訂單總額")
    attempts = list(await session.scalars(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id).order_by(PaymentAttempt.id).with_for_update()))
    if order.payment_status == PaymentStatus.PAID and any(attempt.provider == "onsite" and attempt.status == PaymentStatus.PAID for attempt in attempts):
        return
    if any(not attempt.provider_response.get("payment_correction") for attempt in attempts):
        raise PaymentApplicationError("此訂單已有付款交易，不可再次收現金")
    if order.payment_status != PaymentStatus.PENDING or order.cancelled_at is not None or order.fulfillment_status == FulfillmentStatus.CANCELLED:
        raise PaymentApplicationError("此訂單目前不可收款")
    event = await session.scalar(select(MealEvent).where(MealEvent.id == order.meal_event_id).with_for_update().execution_options(populate_existing=True))
    business = is_business_meal(order)
    if business:
        if blocked := business_payment_blocked_reason(order, now):
            raise PaymentApplicationError(blocked)
        if invoice_policy not in {None, "pickup_day"}:
            raise PaymentApplicationError("會餐發票由系統於取餐日開立")
        if collection_method != "cash":
            raise PaymentApplicationError("會餐現場付款只收現金")
    elif event is None or event.status in {MealEventStatus.CANCELLED, MealEventStatus.COMPLETED, MealEventStatus.DRAFT} or now >= _aware(event.pickup_ends_at):
        raise PaymentApplicationError("場次已結束或取消，不可收款")
    active = [reservation for reservation in order.reservations if reservation.status == ReservationStatus.ACTIVE]
    expected: dict[tuple[str | None, str | None], int] = {}
    reserved: dict[tuple[str | None, str | None], int] = {}
    for item in order.items:
        key = (item.source_meal_offering_id, item.source_product_id)
        expected[key] = expected.get(key, 0) + item.quantity
    for reservation in active:
        key = (reservation.source_meal_offering_id, reservation.source_product_id)
        reserved[key] = reserved.get(key, 0) + reservation.quantity
        if reservation.payment_attempt_id is not None or now >= _aware(reservation.expires_at):
            raise PaymentApplicationError("訂單保留已過期，不可收款")
    if not active or reserved != expected:
        raise PaymentApplicationError("訂單保留數量不完整，不可收款")
    if business:
        order.invoice_handling = "pickup_day"
        order.invoice_issue_after = business_payment_window(order)[0]
    elif invoice_policy is not None:
        if invoice_policy not in {"external", "on_payment", "pickup_day"}:
            raise PaymentApplicationError("開票方式不正確")
        if order.__dict__.get("invoice") is not None:
            raise PaymentApplicationError("已有開票紀錄，不可變更開票方式")
        order.invoice_handling = invoice_policy
        order.invoice_issue_after = pickup_invoice_due(order.fulfillment.pickup_at or order.meal_event.pickup_starts_at) if invoice_policy == "pickup_day" else None
    attempt = PaymentAttempt(
        order=order, order_revision=order.revision, provider="onsite", merchant_trade_no=f"SITE{new_id().replace('-', '')}",
        provider_trade_no=new_id(), amount=amount, status=PaymentStatus.PAID,
        expires_at=now, paid_at=now,
        provider_response={"confirmed_by_id": actor_id, "reason": reason, "confirmed_at": now.isoformat(), "collection_method": collection_method, "reference": reference},
    )
    session.add(attempt)
    offerings = await _lock_meal_offerings(session, (row.source_meal_offering_id for row in active))
    for reservation in active:
        offering = offerings.get(reservation.source_meal_offering_id)
        if offering is None or offering.meal_event_id != order.meal_event_id or offering.reserved_quantity < reservation.quantity:
            raise PaymentApplicationError("便當保留數量不一致")
        offering.reserved_quantity -= reservation.quantity
        offering.paid_quantity += reservation.quantity
        reservation.status = ReservationStatus.CONSUMED
        reservation.payment_attempt = attempt
    order.payment_status = PaymentStatus.PAID
    order.paid_at = now
    if invoice_handling(order) == "external":
        order.invoice_status = InvoiceStatus.EXTERNAL
    else:
        enqueue_invoice_issue(session, order, trigger="onsite_payment_confirmed")
    await award_order_purchase_points(session, order)
    if attempts:
        await _adjust_collection_points(session, order, 1, new_id())
    session.add(AdminAudit(actor_id=actor_id, action="order.cash_payment", aggregate_type="order", aggregate_id=order.id, reason=reason, data={"amount": amount, "payment_method": order.payment_method.value, "collection_method": collection_method}))


async def request_cash_refund(
    session: AsyncSession, order: Order, attempt: PaymentAttempt | None,
    *, amount: int, reason: str, actor_id: str,
) -> tuple[Refund, bool]:
    if attempt is None or attempt.provider != "onsite" or attempt.status != PaymentStatus.PAID:
        raise PaymentApplicationError("找不到可退還的現金收款")
    if amount != order.amount_total or amount != attempt.amount:
        raise PaymentApplicationError("現金退款必須為原訂單全額")
    if await session.scalar(select(Refund.id).where(Refund.payment_attempt_id == attempt.id)):
        raise PaymentApplicationError("此筆付款已有退款紀錄")
    refund = Refund(order=order, payment_attempt=attempt, provider="onsite", amount=amount, reason=reason, requested_by_id=actor_id, status=RefundStatus.PENDING)
    session.add(refund)
    attempt.status = PaymentStatus.REFUND_PENDING
    await session.flush()
    return refund, True


async def confirm_cash_refund(
    session: AsyncSession, order: Order, *, amount: int, reason: str,
    actor_id: str, now: datetime,
) -> None:
    if order.payment_method != PaymentMethod.CASH_ON_PICKUP or amount != order.amount_total:
        raise PaymentApplicationError("現金退款方式或金額不符")
    refund = await session.scalar(select(Refund).where(Refund.order_id == order.id, Refund.provider == "onsite").with_for_update().execution_options(populate_existing=True))
    if refund is not None and refund.status == RefundStatus.COMPLETED and order.payment_status == PaymentStatus.REFUNDED:
        return
    if refund is None or refund.status != RefundStatus.PENDING or order.payment_status != PaymentStatus.REFUND_PENDING:
        raise PaymentApplicationError("請先取消訂單或建立現金退款申請")
    attempt = await session.get(PaymentAttempt, refund.payment_attempt_id)
    if attempt is None or attempt.provider != "onsite" or attempt.status != PaymentStatus.REFUND_PENDING:
        raise PaymentApplicationError("現金退款交易狀態不一致")
    refund.status = RefundStatus.COMPLETED
    refund.completed_at = now
    refund.provider_response = {"confirmed_by_id": actor_id, "reason": reason, "confirmed_at": now.isoformat()}
    attempt.status = PaymentStatus.REFUNDED
    order.payment_status = PaymentStatus.REFUNDED
    await cancel_unissued_invoice_jobs(session, order)
    await reverse_order_purchase_points(session, order, refund.id)
    enqueue_invoice_adjustment_after_refund(session, order, refund)
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        email = order.contact_email or await session.scalar(select(User.email).where(User.id == order.user_id))
        await NotificationService(SQLAlchemyNotificationRepository(session)).publish(NotificationCommand(
            user_id=order.user_id, event_type="refund_completed", title="現金退款完成",
            body=f"訂單 {order.order_number} 已全額退款 NT${amount}。",
            data={"order_id": order.id}, email=email, dedupe_key=f"refund:{refund.id}",
        ))
    session.add(AdminAudit(actor_id=actor_id, action="order.cash_refund", aggregate_type="order", aggregate_id=order.id, reason=reason, data={"amount": amount, "refund_id": refund.id}))


async def expire_cash_orders(session: AsyncSession, orders: Iterable[Order], now: datetime) -> None:
    pending = [order for order in orders if (
        order.payment_method == PaymentMethod.CASH_ON_PICKUP and order.payment_status == PaymentStatus.PENDING
    )]
    reservations = [row for order in pending for row in order.reservations if row.status == ReservationStatus.ACTIVE]
    offerings = await _lock_meal_offerings(session, (row.source_meal_offering_id for row in reservations))
    for reservation in reservations:
        offering = offerings.get(reservation.source_meal_offering_id)
        if offering is not None:
            offering.reserved_quantity = max(0, offering.reserved_quantity - reservation.quantity)
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now
    for order in pending:
        order.payment_status = PaymentStatus.EXPIRED
        if is_business_meal(order):
            continue
        order.cancelled_at = now
        order.cancellation_reason = "現場付款逾期未完成"
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        if order.fulfillment:
            order.fulfillment.status = FulfillmentState.NO_SHOW
            order.fulfillment.fulfilled_at = now


async def _adjust_collection_points(session: AsyncSession, order: Order, direction: int, reference: str) -> None:
    if order.membership_type_snapshot != MembershipType.MEMBER:
        return
    account = await session.scalar(select(PointAccount).where(PointAccount.user_id == order.user_id))
    amount = order.amount_total // 100
    if account is not None and amount:
        session.add(PointTransaction(account_id=account.id, amount=direction * amount,
            source_type=PointSourceType.ADMIN_ADJUSTMENT, reference_id=reference,
            note=f"訂單 {order.order_number} 現場收款更正"))


async def mark_cash_unpaid(session: AsyncSession, order: Order, *, amount: int, reason: str, actor_id: str, now: datetime) -> None:
    from .domain import order_available_actions
    if order.__dict__.get("invoice") is not None:
        raise PaymentApplicationError("此訂單已有開票紀錄，請使用退款與發票作廢流程")
    if "mark_cash_unpaid" not in order_available_actions(order, True, now) or amount != order.amount_total:
        raise PaymentApplicationError("此訂單目前無法更正為未付款")
    if not is_business_meal(order) and (order.meal_event is None or now >= _aware(order.meal_event.pickup_ends_at)):
        raise PaymentApplicationError("場次已結束，無法更正收款")
    attempt = await session.scalar(select(PaymentAttempt).where(
        PaymentAttempt.order_id == order.id, PaymentAttempt.provider == "onsite",
        PaymentAttempt.status == PaymentStatus.PAID,
    ).with_for_update())
    if attempt is None or await session.scalar(select(Refund.id).where(Refund.payment_attempt_id == attempt.id)):
        raise PaymentApplicationError("找不到可更正的收款紀錄，或已進入退款流程")
    active = [row for row in order.reservations if row.status == ReservationStatus.CONSUMED and row.payment_attempt_id == attempt.id]
    if not active:
        raise PaymentApplicationError("訂單保留紀錄不完整，無法更正")
    offerings = await _lock_meal_offerings(session, (row.source_meal_offering_id for row in active))
    for reservation in active:
        offering = offerings.get(reservation.source_meal_offering_id)
        if offering is None or offering.meal_event_id != order.meal_event_id or offering.paid_quantity < reservation.quantity:
            raise PaymentApplicationError("便當付款數量不一致")
        offering.paid_quantity -= reservation.quantity
        offering.reserved_quantity += reservation.quantity
        reservation.status = ReservationStatus.ACTIVE
        reservation.payment_attempt_id = None
        reservation.payment_attempt = None
    audit_id = new_id()
    attempt.provider_response = {**attempt.provider_response, "payment_correction": {
        "previous_paid_at": attempt.paid_at.isoformat(), "actor_id": actor_id,
        "reason": reason, "corrected_at": now.isoformat(),
    }}
    attempt.status = PaymentStatus.FAILED
    attempt.paid_at = None
    order.payment_status = PaymentStatus.PENDING
    order.paid_at = None
    await cancel_unissued_invoice_jobs(session, order)
    await _adjust_collection_points(session, order, -1, audit_id)
    session.add(AdminAudit(id=audit_id, actor_id=actor_id, action="order.cash_payment_unpaid",
        aggregate_type="order", aggregate_id=order.id, reason=reason,
        data={"amount": amount, "payment_attempt_id": attempt.id}))
