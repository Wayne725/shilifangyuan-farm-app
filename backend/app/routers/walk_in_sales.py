from datetime import date, datetime, time, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..domain import aware, included_tax_amount
from ..integrations.invoice_service import enqueue_invoice_issue, invoice_context_from_settings
from ..invoice_timing import TAIPEI, pickup_invoice_due
from ..models import (
    AdminAudit, FulfillmentMethod, FulfillmentState, FulfillmentStatus, InvoiceStatus,
    MembershipType, Order, OrderFulfillment, OrderItem, OrderKind, PaymentAttempt,
    PaymentMethod, PaymentStatus, Refund, RefundStatus, SalesChannel, TaxType, User, WalkInSale, new_id,
)
from ..order_numbers import allocate_order_number
from ..schemas import InvoicePreferenceInput


walk_in_sales_router = APIRouter(prefix="/v1/admin/walk-in-sales", tags=["walk-in-sales"])


class WalkInSaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID
    product_name: str = Field(min_length=1, max_length=120)
    unit_price: int = Field(ge=1, le=1_000_000, strict=True)
    quantity: int = Field(default=1, ge=1, le=999, strict=True)
    tax_type: TaxType = TaxType.TAXABLE
    customer_name: str | None = Field(default=None, max_length=120)
    contact_email: EmailStr | None = Field(default=None, max_length=80)
    invoice_handling: Literal["on_payment", "pickup_day", "external"] = "on_payment"
    pickup_date: date | None = None
    preferences: InvoicePreferenceInput = Field(default_factory=InvoicePreferenceInput)
    reason: str = Field(min_length=1, max_length=500)
    confirm_received: bool
    confirm_not_invoiced_elsewhere: bool = False

    @field_validator("product_name", "reason", "customer_name")
    @classmethod
    def trimmed_text(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("文字欄位不可只有空白")
        return value.strip()

    @model_validator(mode="after")
    def validate_collection(self):
        if not self.confirm_received:
            raise ValueError("請確認已收到此筆款項")
        if self.invoice_handling != "external":
            if not self.confirm_not_invoiced_elsewhere:
                raise ValueError("請確認此筆未由其他系統開立發票")
            if not (self.contact_email or self.preferences.invoice_buyer_email):
                raise ValueError("目前電子開票須提供客人 Email；紙本發票列印尚未啟用")
        if self.invoice_handling == "pickup_day" and self.pickup_date is None:
            raise ValueError("取餐日開票必須填寫取餐日期")
        if self.invoice_handling != "pickup_day" and self.pickup_date is not None:
            raise ValueError("取餐日期僅供取餐日開票使用")
        email = self.preferences.invoice_buyer_email or self.contact_email
        self.preferences = InvoicePreferenceInput.model_validate({
            **self.preferences.model_dump(), "invoice_buyer_email": email,
            "invoice_carrier_value": self.preferences.invoice_carrier_value.strip().upper() if self.preferences.invoice_carrier_value else None,
        })
        return self


def walk_in_sale_read(order: Order) -> dict:
    return {
        "order_id": order.id, "order_number": order.order_number,
        "payment_status": order.payment_status.value, "invoice_status": order.invoice_status.value,
        "invoice_handling": order.invoice_handling, "invoice_issue_after": aware(order.invoice_issue_after) if order.invoice_issue_after else None,
        "amount_total": order.amount_total, "tax_amount": order.tax_amount,
        "customer_name": order.customer_name_snapshot or "現場客人", "contact_email": order.contact_email or None,
        "receipt_print_status": "not_requested", "invoice_print_status": "not_supported",
    }


async def _existing(session: AsyncSession, request_id: str, fingerprint: str | None = None) -> Order | None:
    row = (await session.execute(select(WalkInSale, Order).join(Order, Order.id == WalkInSale.order_id)
        .where(WalkInSale.client_request_id == request_id))).first()
    if row is None:
        return None
    sale, order = row
    if fingerprint is not None and sale.request_fingerprint != fingerprint:
        raise HTTPException(409, "這筆操作已登錄不同內容，請查核原交易；不可用同一編號更改收款")
    return order


@walk_in_sales_router.get("/request/{client_request_id}")
async def recover_walk_in_sale(
    client_request_id: UUID, response: Response,
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    response.headers["Cache-Control"] = "no-store"
    order = await _existing(session, str(client_request_id))
    if order is None:
        raise HTTPException(404, "尚未找到此筆現場交易")
    return walk_in_sale_read(order)


class WalkInRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=500)
    confirm_returned: Literal[True]

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("請填寫現場退款原因")
        return value.strip()


@walk_in_sales_router.post("/{order_id}/refund")
async def refund_walk_in_sale(
    order_id: UUID, body: WalkInRefundRequest, response: Response,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    from ..cash_payments import confirm_cash_refund, request_cash_refund
    from ..integrations.payment_service import PaymentApplicationError
    from .orders import load_order

    response.headers["Cache-Control"] = "no-store"
    if await session.get(WalkInSale, str(order_id)) is None:
        raise HTTPException(404, "找不到現場交易")
    order = await load_order(session, str(order_id), lock=True, refresh=True)
    if order.customer_kind != "walk_in" or body.amount != order.amount_total:
        raise HTTPException(409, "現場退款必須等於原交易全額")
    refund = await session.scalar(select(Refund).where(Refund.order_id == order.id, Refund.provider == "onsite"))
    if order.payment_status == PaymentStatus.REFUNDED and refund is not None and refund.status == RefundStatus.COMPLETED:
        return walk_in_sale_read(order)
    if order.payment_status not in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING}:
        raise HTTPException(409, "此現場交易目前不可退款")
    now = datetime.now(timezone.utc)
    try:
        if order.payment_status == PaymentStatus.PAID:
            attempt = next((row for row in order.payment_attempts if row.provider == "onsite" and row.status == PaymentStatus.PAID), None)
            await request_cash_refund(session, order, attempt, amount=body.amount, reason=body.reason, actor_id=admin.id)
            order.payment_status = PaymentStatus.REFUND_PENDING
        order.cancelled_at = order.cancelled_at or now
        order.cancellation_reason = body.reason
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        if order.fulfillment is not None:
            order.fulfillment.status = FulfillmentState.CANCELLED
        await confirm_cash_refund(session, order, amount=body.amount, reason=body.reason, actor_id=admin.id, now=now)
        await session.commit()
    except PaymentApplicationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return walk_in_sale_read(order)


@walk_in_sales_router.get("/{order_id}")
async def get_walk_in_sale(
    order_id: UUID, response: Response,
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    response.headers["Cache-Control"] = "no-store"
    order = await session.scalar(select(Order).join(WalkInSale, WalkInSale.order_id == Order.id).where(Order.id == str(order_id)))
    if order is None:
        raise HTTPException(404, "找不到現場交易")
    return walk_in_sale_read(order)


@walk_in_sales_router.post("", status_code=201)
async def create_walk_in_sale(
    body: WalkInSaleCreate, response: Response,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    request_id = str(body.client_request_id)
    fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = await _existing(session, request_id, fingerprint)
    if existing is not None:
        response.status_code = 200
        return walk_in_sale_read(existing)
    now = datetime.now(timezone.utc)
    if body.pickup_date is not None and body.pickup_date < now.astimezone(TAIPEI).date():
        raise HTTPException(422, "取餐日期不可早於今天；發票不能倒填日期")
    pickup_at = datetime.combine(body.pickup_date, time(12), TAIPEI).astimezone(timezone.utc) if body.pickup_date else None
    completed = pickup_at is None
    amount = body.unit_price * body.quantity
    actor_id = admin.id
    order = Order(
        id=new_id(), user_id=None, customer_kind="walk_in", customer_name_snapshot=body.customer_name or "現場客人",
        order_number=await allocate_order_number(session, "O", now),
        order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        membership_type_snapshot=MembershipType.NONMEMBER, member_tax_exempt_snapshot=False,
        contact_email=str(body.contact_email or body.preferences.invoice_buyer_email or ""),
        amount_total=amount, tax_amount=included_tax_amount(amount, body.tax_type),
        payment_method=PaymentMethod.CASH_ON_PICKUP, payment_status=PaymentStatus.PAID, paid_at=now,
        invoice_handling=body.invoice_handling,
        invoice_issue_after=pickup_invoice_due(pickup_at) if pickup_at else None,
        invoice_status=InvoiceStatus.EXTERNAL if body.invoice_handling == "external" else InvoiceStatus.NOT_ELIGIBLE,
        invoice_provider_context=invoice_context_from_settings(settings),
        **body.preferences.model_dump(),
        fulfillment_status=FulfillmentStatus.PICKED_UP if completed else FulfillmentStatus.PENDING_CONFIRMATION,
        items=[OrderItem(product_name=body.product_name, unit_label="份", quantity=body.quantity,
            unit_price=body.unit_price, subtotal=amount, tax_type=body.tax_type)],
        fulfillment=OrderFulfillment(method=FulfillmentMethod.COOPERATIVE_PICKUP,
            status=FulfillmentState.PICKED_UP if completed else FulfillmentState.PENDING_CONFIRMATION,
            pickup_at=pickup_at, fulfilled_at=now if completed else None),
    )
    attempt = PaymentAttempt(order=order, provider="onsite", merchant_trade_no=f"SITE{new_id().replace('-', '')}",
        provider_trade_no=new_id(), amount=amount, status=PaymentStatus.PAID, expires_at=now, paid_at=now,
        provider_response={"confirmed_by_id": actor_id, "confirmed_at": now.isoformat(), "reason": body.reason, "collection_method": "cash", "walk_in": True})
    session.add_all([order, attempt])
    try:
        await session.flush()
        session.add(WalkInSale(order_id=order.id, client_request_id=request_id, request_fingerprint=fingerprint, operator_id=actor_id))
        await session.flush()
        enqueue_invoice_issue(session, order, trigger="walk_in_payment_confirmed")
        session.add(AdminAudit(actor_id=actor_id, action="walk_in_sale.collected", aggregate_type="order", aggregate_id=order.id,
            reason=body.reason, data={"amount": amount, "tax_type": body.tax_type.value, "invoice_handling": body.invoice_handling}))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _existing(session, request_id, fingerprint)
        if existing is None:
            raise
        response.status_code = 200
        return walk_in_sale_read(existing)
    return walk_in_sale_read(order)
