"""Cash mutations share the caller's transaction; collection locks event then order."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .models import (
    AdminAudit, FulfillmentState, FulfillmentStatus, InventoryReservation,
    MealEvent, MealEventOffering, MealEventStatus, Order, PaymentAttempt,
    PaymentMethod, PaymentStatus, Refund, RefundStatus,
    ReservationStatus, SalesChannel, PointAccount, PointTransaction, PointSourceType, MembershipType, InvoiceStatus, new_id,
)
from .integrations.invoice_service import (
    enqueue_invoice_adjustment_after_refund, enqueue_invoice_issue,
)
from .integrations.payment_service import (
    PaymentApplicationError, award_order_purchase_points,
    reverse_order_purchase_points,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def reserve_cash_order(session: AsyncSession, order: Order) -> None:
    if order.sales_channel != SalesChannel.MEAL_PREORDER or order.meal_event is None:
        raise PaymentApplicationError("現場付款目前僅支援便當")
    expires_at = order.meal_event.pickup_ends_at
    for item in sorted(order.items, key=lambda line: line.source_meal_offering_id or line.source_product_id or ""):
        offering = await session.scalar(select(MealEventOffering).where(
            MealEventOffering.id == item.source_meal_offering_id,
            MealEventOffering.meal_event_id == order.meal_event_id,
        ).with_for_update().execution_options(populate_existing=True))
        if offering is None or not offering.is_active or offering.capacity - offering.reserved_quantity - offering.paid_quantity < item.quantity:
            raise PaymentApplicationError("便當剩餘數量不足")
        offering.reserved_quantity += item.quantity
        session.add(InventoryReservation(
            order=order, source_meal_offering_id=item.source_meal_offering_id,
            source_product_id=item.source_product_id, quantity=item.quantity,
            status=ReservationStatus.ACTIVE, expires_at=expires_at,
        ))


async def confirm_cash_payment(
    session: AsyncSession, order: Order, *, amount: int, reason: str,
    actor_id: str, settings: Settings, now: datetime, collection_method: str = "cash", reference: str = "",
) -> None:
    if order.payment_method != PaymentMethod.CASH_ON_PICKUP or order.sales_channel != SalesChannel.MEAL_PREORDER:
        raise PaymentApplicationError("此訂單不是現場付款")
    if amount != order.amount_total or amount <= 0:
        raise PaymentApplicationError("實收金額必須等於訂單總額")
    attempts = list(await session.scalars(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id).with_for_update()))
    if order.payment_status == PaymentStatus.PAID and any(attempt.provider == "onsite" and attempt.status == PaymentStatus.PAID for attempt in attempts):
        return
    if any(not attempt.provider_response.get("payment_correction") for attempt in attempts):
        raise PaymentApplicationError("此訂單已有付款交易，不可再次收現金")
    if order.payment_status != PaymentStatus.PENDING or order.cancelled_at is not None or order.fulfillment_status == FulfillmentStatus.CANCELLED:
        raise PaymentApplicationError("此訂單目前不可收款")
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        event = await session.scalar(select(MealEvent).where(MealEvent.id == order.meal_event_id).with_for_update().execution_options(populate_existing=True))
        if event is None or event.status in {MealEventStatus.CANCELLED, MealEventStatus.COMPLETED, MealEventStatus.DRAFT} or now >= _aware(event.pickup_ends_at):
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
    attempt = PaymentAttempt(
        order=order, provider="onsite", merchant_trade_no=f"SITE{new_id().replace('-', '')}",
        provider_trade_no=new_id(), amount=amount, status=PaymentStatus.PAID,
        expires_at=now, paid_at=now,
        provider_response={"confirmed_by_id": actor_id, "reason": reason, "confirmed_at": now.isoformat(), "collection_method": collection_method, "reference": reference},
    )
    session.add(attempt)
    for reservation in sorted(active, key=lambda row: row.source_meal_offering_id or row.source_product_id or ""):
        if reservation.source_meal_offering_id:
            offering = await session.scalar(select(MealEventOffering).where(MealEventOffering.id == reservation.source_meal_offering_id).with_for_update().execution_options(populate_existing=True))
            if offering is None or offering.reserved_quantity < reservation.quantity:
                raise PaymentApplicationError("便當保留數量不一致")
            offering.reserved_quantity -= reservation.quantity
            offering.paid_quantity += reservation.quantity
        reservation.status = ReservationStatus.CONSUMED
        reservation.payment_attempt = attempt
    order.payment_status = PaymentStatus.PAID
    order.paid_at = now
    order.invoice_status = InvoiceStatus.EXTERNAL
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
    await reverse_order_purchase_points(session, order, refund.id)
    enqueue_invoice_adjustment_after_refund(session, order, refund)
    session.add(AdminAudit(actor_id=actor_id, action="order.cash_refund", aggregate_type="order", aggregate_id=order.id, reason=reason, data={"amount": amount, "refund_id": refund.id}))


async def expire_cash_order(session: AsyncSession, order: Order, now: datetime) -> None:
    if order.payment_method != PaymentMethod.CASH_ON_PICKUP or order.payment_status != PaymentStatus.PENDING:
        return
    for reservation in sorted(order.reservations, key=lambda row: row.source_meal_offering_id or row.source_product_id or ""):
        if reservation.status != ReservationStatus.ACTIVE:
            continue
        if reservation.source_meal_offering_id:
            offering = await session.scalar(select(MealEventOffering).where(MealEventOffering.id == reservation.source_meal_offering_id).with_for_update().execution_options(populate_existing=True))
            if offering:
                offering.reserved_quantity = max(0, offering.reserved_quantity - reservation.quantity)
        reservation.status = ReservationStatus.RELEASED
        reservation.released_at = now
    order.payment_status = PaymentStatus.EXPIRED
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
    if "mark_cash_unpaid" not in order_available_actions(order, True, now) or amount != order.amount_total:
        raise PaymentApplicationError("此訂單目前無法更正為未付款")
    if order.meal_event is None or now >= _aware(order.meal_event.pickup_ends_at):
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
    for reservation in sorted(active, key=lambda row: row.source_meal_offering_id or ""):
        offering = await session.scalar(select(MealEventOffering).where(MealEventOffering.id == reservation.source_meal_offering_id).with_for_update().execution_options(populate_existing=True))
        if offering is None or offering.paid_quantity < reservation.quantity:
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
    await _adjust_collection_points(session, order, -1, audit_id)
    session.add(AdminAudit(id=audit_id, actor_id=actor_id, action="order.cash_payment_unpaid",
        aggregate_type="order", aggregate_id=order.id, reason=reason,
        data={"amount": amount, "payment_attempt_id": attempt.id}))
