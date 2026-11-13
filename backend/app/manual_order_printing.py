from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import Settings
from .business_meals import is_business_meal
from .domain import aware
from .integrations.common import IntegrationError
from .integrations.feie import FeieError, configured_region, validate_live_config
from .integrations.pii_crypto import pii_cipher_from_settings
from .models import FulfillmentState, FulfillmentStatus, MemberProfile, Order, OrderItem, PaymentMethod, PaymentStatus, PrintJob, SalesChannel
from .contact_phone import decrypt_contact_phone
from .printing import (confirmed_print_payment, order_print_snapshot,
                       payment_snapshot_from_session, printer_fingerprint, render_receipt_snapshot)


async def load_manual_print_order(session: AsyncSession, order_id: str, *, lock: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id).options(
        selectinload(Order.items).selectinload(OrderItem.selected_options),
        selectinload(Order.fulfillment), selectinload(Order.user),
    ).execution_options(populate_existing=True)
    order = await session.scalar(query.with_for_update() if lock else query)
    if order is None:
        raise HTTPException(404, "找不到訂單")
    if order.sales_channel != SalesChannel.MEAL_PREORDER:
        raise HTTPException(409, "此出單入口僅適用便當訂單")
    return order


def current_fulfillment_status(order: Order) -> str:
    if order.cancelled_at is not None:
        return "cancelled"
    return (order.fulfillment.status if order.fulfillment else order.fulfillment_status).value


def manual_print_kind(order: Order) -> str:
    finished = (current_fulfillment_status(order) in {"cancelled", "picked_up", "delivered", "no_show"}
                or order.fulfillment_status in {FulfillmentStatus.CANCELLED, FulfillmentStatus.PICKED_UP})
    if (is_business_meal(order) and order.payment_method == PaymentMethod.CASH_ON_PICKUP and order.fulfillment
        and order.fulfillment.pickup_ends_at and datetime.now(timezone.utc) >= aware(order.fulfillment.pickup_ends_at)):
        finished = True
    return "order" if confirmed_print_payment(order) and not finished else "reference"


def manual_print_status_error(order: Order) -> str | None:
    if (order.cancelled_at is not None or order.fulfillment_status == FulfillmentStatus.CANCELLED
            or (order.fulfillment and order.fulfillment.status == FulfillmentState.CANCELLED)):
        return "order_cancelled_before_send"
    if order.payment_status in {PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED}:
        return "order_payment_reversed_before_send"
    if not confirmed_print_payment(order):
        return "order_payment_unconfirmed_before_send"
    return None


def manual_print_order_blocked_reason(order: Order) -> str | None:
    return {
        "order_cancelled_before_send": "訂單已取消，無法再次出單",
        "order_payment_reversed_before_send": "訂單已退款或正在處理退款，無法再次出單",
        "order_payment_unconfirmed_before_send": "訂單尚未確認付款成功，無法再次出單",
    }.get(manual_print_status_error(order))


async def current_manual_snapshot(session: AsyncSession, order: Order, settings: Settings,
                                  *, reference: bool = False) -> dict:
    fulfillment = order.fulfillment
    phone = decrypt_contact_phone(fulfillment.recipient_phone_encrypted,
        f"order-fulfillment:{fulfillment.id}:recipient_phone", settings) if fulfillment else None
    if phone is None:
        encrypted = await session.scalar(select(MemberProfile.phone_encrypted).where(MemberProfile.user_id == order.user_id))
        phone = decrypt_contact_phone(encrypted, f"member-profile:{order.user_id}", settings)
    field, value = ("phone", phone) if phone else ("email", order.contact_email)
    try:
        contact = {f"customer_{field}_encrypted": pii_cipher_from_settings(settings).encrypt_text(
            value, associated_data=f"receipt-contact:{order.id}:{field}")}
    except IntegrationError:
        raise FeieError("receipt_contact_decryption_failed") from None
    snapshot = order_print_snapshot(order, order.user.display_name if order.user else order.customer_name_snapshot or "現場客人", contact)
    status = current_fulfillment_status(order)
    labels = {"pending_confirmation": "待確認", "preparing": "製作中", "ready_for_pickup": "待取餐",
              "picked_up": "已取餐", "awaiting_shipment": "待出貨", "shipped": "已出貨",
              "delivered": "已送達", "no_show": "未取餐", "cancelled": "已取消"}
    snapshot.update(kind="reference" if reference else manual_print_kind(order), manual=True,
        printer_region=configured_region(settings), fulfillment_status_label=labels[status],
        **await payment_snapshot_from_session(session, order))
    if snapshot["kind"] == "reference":
        snapshot.pop("pickup_code", None)
    if fulfillment is None:
        snapshot.pop("needs_utensils", None)
    return snapshot


async def order_print_jobs(session: AsyncSession, order_id: str) -> list[PrintJob]:
    return list(await session.scalars(select(PrintJob).where(PrintJob.order_id == order_id)
        .order_by(PrintJob.created_at.desc(), PrintJob.id.desc())))


def manual_print_blocked_reason(order: Order, jobs: list[PrintJob], settings: Settings) -> str | None:
    if blocked := manual_print_order_blocked_reason(order):
        return blocked
    if not settings.printer_enabled:
        return "出單功能尚未啟用"
    if not settings.printer_dry_run:
        try:
            validate_live_config(settings)
        except FeieError:
            return "出單設備設定尚未完成，請先確認設備與雲端設定"
    for job in jobs:
        if job.status == "sending":
            return "這筆訂單正在送印，請先查詢出單結果"
        if job.status == "pending":
            return "這筆訂單已有等待發送的工作，請先確認出單結果"
    return None


def order_print_preview_token(snapshot: dict, order: Order, settings: Settings) -> str:
    payload = {"snapshot": render_receipt_snapshot(snapshot, order.id, settings),
        "payment_status": order.payment_status.value, "paid_at": str(order.paid_at),
        "fulfillment_status": current_fulfillment_status(order), "cancelled_at": str(order.cancelled_at),
        "printer": printer_fingerprint(settings), "region": configured_region(settings),
        "width": settings.printer_line_width, "mode": "dry_run" if settings.printer_dry_run else "live"}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
