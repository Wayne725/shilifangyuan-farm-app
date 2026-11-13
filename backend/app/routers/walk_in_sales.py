from datetime import date, datetime, time, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, EmailStr, Field, PrivateAttr, SecretStr, field_validator, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin_explanations import AdminExplanation
from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..domain import aware, invoice_estimated_tax_amount, member_sale_is_tax_exempt
from ..integrations.common import IntegrationError
from ..integrations.fanyu_invoice import FanyuInvoiceAdapter
from ..integrations.invoice import is_mobile_barcode_format
from ..integrations.invoice_certificate import retrieve_fanyu_invoice_certificate
from ..integrations.invoice_service import (
    InvoiceApplicationError, enqueue_invoice_issue, invoice_adapter_from_settings, invoice_context_from_settings,
)
from ..integrations.payment_service import award_order_purchase_points
from ..integrations.pii_crypto import pii_cipher_from_settings
from ..invoice_timing import TAIPEI, pickup_invoice_due
from ..models import (
    AdminAudit, FulfillmentMethod, FulfillmentState, FulfillmentStatus, Invoice, InvoiceBuyerType, InvoiceCarrierType, InvoiceStatus,
    MembershipType, Order, OrderFulfillment, OrderItem, OrderKind, PaymentAttempt,
    PaymentMethod, PaymentStatus, Refund, RefundStatus, SalesChannel, TaxType, User, WalkInRequestGuard,
    WalkInSale, new_id,
)
from ..order_numbers import allocate_order_number
from ..phone_lookup import phone_lookup_digests
from ..rate_limit import INVOICE_QUERY_RULE, PAYMENT_REFRESH_RULE, client_key, enforce
from ..recovery_quarantine import RecoveryHoldActive
from ..schemas import is_taiwan_tax_id
from ..walk_in_member_pricing import (
    CounterPhoneLookup, CounterPhoneLookupInput, counter_member_tax_type, counter_phone_lookup, counter_phone_matches,
    CounterMemberIdentity, CounterMemberQuote, CounterMemberQuoteInput, counter_member_quote,
    normalize_counter_phone,
)


class WalkInRoute(APIRoute):
    def get_route_handler(self):
        route_handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await route_handler(request)
            except RequestValidationError as exc:
                details = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
                return JSONResponse({"detail": details}, status_code=422, headers={"Cache-Control": "no-store"})

        return handle


walk_in_sales_router = APIRouter(prefix="/v1/admin/walk-in-sales", tags=["walk-in-sales"], route_class=WalkInRoute)


class WalkInInvoicePreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_buyer_type: InvoiceBuyerType = InvoiceBuyerType.PERSONAL
    invoice_buyer_tax_id: str | None = Field(default=None, max_length=10)
    invoice_buyer_name: str | None = Field(default=None, max_length=60)
    invoice_buyer_email: EmailStr | None = Field(default=None, max_length=80)
    invoice_carrier_type: InvoiceCarrierType = InvoiceCarrierType.CLOUD
    invoice_carrier_value: str | None = Field(default=None, max_length=64)
    _submitted_tax_id: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def validate_preference(self):
        self._submitted_tax_id = self.invoice_buyer_tax_id
        if self.invoice_buyer_type == InvoiceBuyerType.COMPANY:
            self.invoice_buyer_tax_id = (self.invoice_buyer_tax_id or "").strip()
            if not is_taiwan_tax_id(self.invoice_buyer_tax_id):
                raise ValueError("公司發票統一編號格式不正確")
            if not (self.invoice_buyer_name or "").strip():
                self.invoice_buyer_name = None
        elif any(value and value.strip() for value in (self.invoice_buyer_tax_id, self.invoice_buyer_name)):
            raise ValueError("個人發票不可帶入公司統編資料")
        if self.invoice_carrier_type == InvoiceCarrierType.MOBILE_BARCODE:
            barcode = (self.invoice_carrier_value or "").strip().upper()
            if not is_mobile_barcode_format(barcode):
                raise ValueError("手機條碼格式不正確")
            self.invoice_carrier_value = barcode
        elif self.invoice_carrier_value:
            raise ValueError("雲端發票不需要載具號碼")
        if self.invoice_carrier_type == InvoiceCarrierType.CLOUD and len(str(self.invoice_buyer_email or "")) > 64:
            raise ValueError("Email 會員載具不可超過 64 字元")
        return self


class WalkInSaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID
    product_name: str = Field(min_length=1, max_length=120)
    sale_category: Literal["meal", "product"] | None = None
    unit_price: int = Field(ge=1, le=1_000_000, strict=True)
    quantity: int = Field(default=1, ge=1, le=999, strict=True)
    tax_type: TaxType = TaxType.TAXABLE
    customer_name: str | None = Field(default=None, max_length=120)
    contact_email: EmailStr | None = Field(default=None, max_length=80)
    invoice_handling: Literal["on_payment", "pickup_day", "external"] = "on_payment"
    pickup_date: date | None = None
    preferences: WalkInInvoicePreferenceInput = Field(default_factory=WalkInInvoicePreferenceInput)
    reason: AdminExplanation = Field(default="", max_length=500)
    confirm_received: bool
    confirm_not_invoiced_elsewhere: bool = False
    member: CounterMemberIdentity | None = None
    catalog_id: UUID | None = None
    phone: SecretStr | None = Field(default=None, min_length=8, max_length=40)
    phone_match_id: str | None = Field(default=None, max_length=48, pattern=r"^(membership|roster):[a-zA-Z0-9-]+$")

    @field_validator("phone")
    @classmethod
    def normalized_phone(cls, value: SecretStr | None) -> SecretStr | None:
        return normalize_counter_phone(value) if value is not None else None

    @field_validator("product_name", "customer_name")
    @classmethod
    def trimmed_text(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("文字欄位不可只有空白")
        return value.strip()

    @model_validator(mode="after")
    def validate_collection(self):
        if self.member is not None and (self.catalog_id is None or self.sale_category is None):
            raise ValueError("社員交易須選擇目錄品項")
        if self.member is None and self.catalog_id is not None:
            raise ValueError("社員核對須填寫社員編號及手機號碼")
        if self.phone_match_id is not None and self.phone is None:
            raise ValueError("請先輸入電話號碼核對社員")
        if self.member is not None and self.phone is not None:
            raise ValueError("請以單一方式核對社員")
        if not self.confirm_received:
            raise ValueError("請確認已收到此筆款項")
        if self.invoice_handling != "external":
            if not self.confirm_not_invoiced_elsewhere:
                raise ValueError("請確認此筆未由其他系統開立發票")
            if not (self.contact_email or self.preferences.invoice_buyer_email) and self.preferences.invoice_carrier_type != InvoiceCarrierType.MOBILE_BARCODE:
                raise ValueError("Email 會員載具須提供客人 Email；紙本發票列印尚未啟用")
        if self.invoice_handling == "pickup_day" and self.pickup_date is None:
            raise ValueError("取餐日開票必須填寫取餐日期")
        if self.invoice_handling != "pickup_day" and self.pickup_date is not None:
            raise ValueError("取餐日期僅供取餐日開票使用")
        email = self.preferences.invoice_buyer_email or self.contact_email
        submitted_tax_id = self.preferences._submitted_tax_id
        self.preferences = WalkInInvoicePreferenceInput.model_validate({
            **self.preferences.model_dump(), "invoice_buyer_email": email,
            "invoice_carrier_value": self.preferences.invoice_carrier_value.strip().upper() if self.preferences.invoice_carrier_value else None,
        })
        self.preferences._submitted_tax_id = submitted_tax_id
        return self


async def walk_in_sale_read(session: AsyncSession, order: Order) -> dict:
    sale_category, invoice_number = (await session.execute(
        select(WalkInSale.sale_category, Invoice.invoice_number)
        .select_from(WalkInSale)
        .outerjoin(Invoice, Invoice.order_id == WalkInSale.order_id)
        .where(WalkInSale.order_id == order.id),
    )).one()
    return {
        "order_id": order.id, "order_number": order.order_number,
        "sale_category": sale_category, "invoice_number": invoice_number,
        "payment_status": order.payment_status.value, "invoice_status": order.invoice_status.value,
        "invoice_handling": order.invoice_handling, "invoice_issue_after": aware(order.invoice_issue_after) if order.invoice_issue_after else None,
        "amount_total": order.amount_total, "tax_amount": order.tax_amount,
        "customer_name": order.customer_name_snapshot or "現場客人", "contact_email": order.contact_email or None,
        "receipt_print_status": "not_requested", "invoice_print_status": "not_supported",
    }


async def _existing(session: AsyncSession, request_id: str, fingerprints: tuple[str, ...] = ()) -> Order | None:
    row = (await session.execute(select(WalkInSale, Order).join(Order, Order.id == WalkInSale.order_id)
        .where(WalkInSale.client_request_id == request_id))).first()
    if row is None:
        return None
    sale, order = row
    if fingerprints and sale.request_fingerprint not in fingerprints:
        raise HTTPException(409, "這筆操作已登錄不同內容，請查核原交易；不可用同一編號更改收款")
    return order


async def _lock_request(session: AsyncSession, request_id: str) -> bool:
    insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(WalkInRequestGuard).values(request_id=request_id)
    # A conflicting write waits until the preceding attempt commits or rolls back.
    locked = await session.execute(statement.on_conflict_do_update(
        index_elements=[WalkInRequestGuard.request_id], set_={"request_id": statement.excluded.request_id},
    ).returning(WalkInRequestGuard.retired))
    return locked.scalar_one()


def _stale_quote(detail: str) -> HTTPException:
    return HTTPException(409, detail, headers={
        "X-Error-Code": "walk_in_member_quote_stale", "Cache-Control": "no-store",
    })


async def _retire_request(session: AsyncSession, request_id: str) -> None:
    await session.execute(update(WalkInRequestGuard).where(
        WalkInRequestGuard.request_id == request_id,
    ).values(retired=True))
    await session.commit()


@walk_in_sales_router.post("/member-phone", response_model=CounterPhoneLookup)
async def find_walk_in_member_phone(
    body: CounterPhoneLookupInput, request: Request, response: Response,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CounterPhoneLookup:
    response.headers["Cache-Control"] = "private, no-store"
    enforce(client_key(request, "counter-member-phone", admin.id), PAYMENT_REFRESH_RULE)
    return await counter_phone_lookup(session, settings, body.phone.get_secret_value())


@walk_in_sales_router.post("/member-quote", response_model=CounterMemberQuote)
async def quote_walk_in_member_sale(
    body: CounterMemberQuoteInput, request: Request, response: Response,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CounterMemberQuote:
    response.headers["Cache-Control"] = "no-store"
    enforce(client_key(request, "counter-member-quote", admin.id), PAYMENT_REFRESH_RULE)
    _, quote = await counter_member_quote(session, settings, body)
    return quote


@walk_in_sales_router.get("/request/{client_request_id}")
async def recover_walk_in_sale(
    client_request_id: UUID, response: Response,
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    response.headers["Cache-Control"] = "no-store"
    order = await _existing(session, str(client_request_id))
    if order is None:
        raise HTTPException(404, "尚未找到此筆現場交易")
    return await walk_in_sale_read(session, order)


class WalkInRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: int = Field(ge=1, strict=True)
    reason: AdminExplanation = Field(default="", max_length=500)
    confirm_returned: Literal[True]


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
        return await walk_in_sale_read(session, order)
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
    return await walk_in_sale_read(session, order)


@walk_in_sales_router.get("/{order_id}")
async def get_walk_in_sale(
    order_id: UUID, response: Response,
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    response.headers["Cache-Control"] = "no-store"
    order = await session.scalar(select(Order).join(WalkInSale, WalkInSale.order_id == Order.id).where(Order.id == str(order_id)))
    if order is None:
        raise HTTPException(404, "找不到現場交易")
    return await walk_in_sale_read(session, order)


@walk_in_sales_router.get("/{order_id}/invoice-certificate", response_class=Response)
async def get_walk_in_invoice_certificate(
    order_id: UUID, request: Request, output: Literal["pdf", "codes"] = "pdf",
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    headers = {"Cache-Control": "private, no-store"}
    enforce(client_key(request, "walk-in-invoice-certificate", admin.id), INVOICE_QUERY_RULE)
    if await session.get(WalkInSale, str(order_id)) is None:
        raise HTTPException(404, "找不到現場交易", headers=headers)
    if settings.invoice_provider != "fanyu":
        raise HTTPException(409, "請使用原汎宇帳戶取得證明聯", headers=headers)
    try:
        adapter = invoice_adapter_from_settings(settings)
        if not isinstance(adapter, FanyuInvoiceAdapter):
            raise InvoiceApplicationError("請使用原汎宇帳戶取得證明聯")
        certificate = await retrieve_fanyu_invoice_certificate(
            session, str(order_id), adapter, include_thermal_pdf=output == "pdf",
        )
    except RecoveryHoldActive:
        raise
    except InvoiceApplicationError as exc:
        raise HTTPException(409, str(exc), headers=headers) from exc
    except (IntegrationError, TimeoutError, ValueError) as exc:
        raise HTTPException(502, "無法取得原發票的列印資料", headers=headers) from exc

    codes = certificate.codes
    if output == "codes":
        return JSONResponse({
            "invoice_number": codes.invoice_number, "barcode": codes.barcode,
            "left_qrcode": codes.left_qrcode, "right_qrcode": codes.right_qrcode,
        }, headers=headers)
    if certificate.thermal_pdf is None:
        raise HTTPException(409, "此原票未提供熱感 PDF", headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="{codes.invoice_number}.pdf"'
    return Response(certificate.thermal_pdf, media_type="application/pdf", headers=headers)


@walk_in_sales_router.post("", status_code=201)
async def create_walk_in_sale(
    body: WalkInSaleCreate, request: Request, response: Response,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    request_id = str(body.client_request_id)
    fingerprint_data = body.model_dump(mode="json", exclude={"member", "catalog_id", "phone", "phone_match_id"})
    if body.member is not None:
        fingerprint_data["member_number"] = body.member.member_number
        fingerprint_data["catalog_id"] = str(body.catalog_id)
    if "sale_category" not in body.model_fields_set:
        fingerprint_data.pop("sale_category")
        # Older clients hashed the submitted tax ID before whitespace normalization.
        fingerprint_data["preferences"]["invoice_buyer_tax_id"] = body.preferences._submitted_tax_id
    def fingerprint(data: dict) -> str:
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    phone = body.phone.get_secret_value() if body.phone is not None else None
    cipher = None
    if phone is not None:
        try:
            cipher = pii_cipher_from_settings(settings)
        except IntegrationError as exc:
            raise HTTPException(503, "電話資料暫時無法安全儲存，請稍後再試") from exc
        digests = phone_lookup_digests(phone, cipher, purpose="counter-sale-request")
        fingerprint_data["phone_match_id"] = body.phone_match_id
        current_digest = digests.pop(cipher.current_version)
        fingerprints = tuple(
            fingerprint({**fingerprint_data, "phone_digest": digest}) for digest in (current_digest, *digests.values())
        )
    else:
        fingerprints = (fingerprint(fingerprint_data),)
    existing = await _existing(session, request_id, fingerprints)
    if existing is not None:
        response.status_code = 200
        return await walk_in_sale_read(session, existing)
    if body.invoice_handling != "external" and not (body.contact_email or body.preferences.invoice_buyer_email) and settings.invoice_provider != "fanyu":
        raise HTTPException(422, "目前開票供應商須提供客人 Email")
    now = datetime.now(timezone.utc)
    if body.pickup_date is not None and body.pickup_date < now.astimezone(TAIPEI).date():
        raise HTTPException(422, "取餐日期不可早於今天；發票不能倒填日期")
    verified_membership_id = None
    verified_roster_id = None
    membership_type = MembershipType.NONMEMBER
    member_tax_exempt = False
    customer_name = body.customer_name or "現場客人"
    tax_type = body.tax_type
    if body.member is not None or phone is not None:
        enforce(client_key(request, "counter-member-collection", admin.id), PAYMENT_REFRESH_RULE)
    retired = await _lock_request(session, request_id)
    try:
        existing = await _existing(session, request_id, fingerprints)
    except HTTPException:
        await session.rollback()
        raise
    if existing is not None:
        result = await walk_in_sale_read(session, existing)
        await session.rollback()
        response.status_code = 200
        return result
    if retired:
        await session.rollback()
        raise _stale_quote("原操作編號已結束，請重新核對交易")
    if body.member is not None:
        try:
            membership, quote = await counter_member_quote(session, settings, CounterMemberQuoteInput(
                member=body.member, sale_category=body.sale_category, catalog_id=body.catalog_id,
                invoice_buyer_type=body.preferences.invoice_buyer_type,
            ), lock=True)
            if (body.product_name, body.unit_price, body.tax_type) != (quote.product_name, quote.unit_price, quote.tax_type):
                raise HTTPException(409, "品項、售價或稅別已變更，請重新核對")
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            # Retire the ID before signaling that another request cannot collect it.
            await _retire_request(session, request_id)
            raise _stale_quote(exc.detail) from exc
        verified_membership_id = membership.id
        membership_type, member_tax_exempt = quote.membership_type, quote.member_tax_exempt
    if phone is not None:
        matches, _ = await counter_phone_matches(session, settings, phone, lock=True)
        selected = next(((match, name) for match, name in matches if match.id == body.phone_match_id), None)
        if selected is None and (matches or body.phone_match_id):
            await _retire_request(session, request_id)
            raise _stale_quote("社員核對結果已更新，請重新核對電話並確認身分")
        if selected is not None:
            match, customer_name = selected
            source, identifier = match.id.split(":", 1)
            if source == "membership":
                verified_membership_id = identifier
            else:
                verified_roster_id = identifier
            membership_type = match.membership_type
            member_tax_exempt = member_sale_is_tax_exempt(membership_type, body.preferences.invoice_buyer_type)
            tax_type = counter_member_tax_type(membership_type, body.preferences.invoice_buyer_type)
    pickup_at = datetime.combine(body.pickup_date, time(12), TAIPEI).astimezone(timezone.utc) if body.pickup_date else None
    completed = pickup_at is None
    amount = body.unit_price * body.quantity
    actor_id = admin.id
    fulfillment_id = new_id()
    preferences = body.preferences.model_dump()
    if settings.invoice_provider != "fanyu" and body.preferences.invoice_buyer_type == InvoiceBuyerType.COMPANY:
        preferences["invoice_buyer_name"] = body.preferences.invoice_buyer_name or body.preferences.invoice_buyer_tax_id
    order = Order(
        id=new_id(), user_id=None, customer_kind="walk_in", customer_name_snapshot=customer_name,
        order_number=await allocate_order_number(session, "O", now),
        order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        verified_counter_membership_id=verified_membership_id,
        verified_counter_roster_entry_id=verified_roster_id,
        membership_type_snapshot=membership_type, member_tax_exempt_snapshot=member_tax_exempt,
        contact_email=str(body.contact_email or body.preferences.invoice_buyer_email or ""),
        amount_total=amount, tax_amount=invoice_estimated_tax_amount(
            [(amount, tax_type)], body.preferences.invoice_buyer_type,
        ),
        payment_method=PaymentMethod.CASH_ON_PICKUP, payment_status=PaymentStatus.PAID, paid_at=now,
        invoice_handling=body.invoice_handling,
        invoice_issue_after=pickup_invoice_due(pickup_at) if pickup_at else None,
        invoice_status=InvoiceStatus.EXTERNAL if body.invoice_handling == "external" else InvoiceStatus.NOT_ELIGIBLE,
        invoice_provider_context=invoice_context_from_settings(settings),
        **preferences,
        fulfillment_status=FulfillmentStatus.PICKED_UP if completed else FulfillmentStatus.PENDING_CONFIRMATION,
        items=[OrderItem(product_name=body.product_name, unit_label="份", quantity=body.quantity,
            unit_price=body.unit_price, subtotal=amount, tax_type=tax_type)],
        fulfillment=OrderFulfillment(id=fulfillment_id, method=FulfillmentMethod.COOPERATIVE_PICKUP,
            status=FulfillmentState.PICKED_UP if completed else FulfillmentState.PENDING_CONFIRMATION,
            pickup_at=pickup_at, fulfilled_at=now if completed else None),
    )
    if phone is not None and cipher is not None:
        order.fulfillment.recipient_phone_encrypted = cipher.encrypt_text(
            phone, associated_data=f"order-fulfillment:{fulfillment_id}:recipient_phone",
        )
        order.fulfillment.encryption_key_version = cipher.current_version
    attempt = PaymentAttempt(order=order, provider="onsite", merchant_trade_no=f"SITE{new_id().replace('-', '')}",
        provider_trade_no=new_id(), amount=amount, status=PaymentStatus.PAID, expires_at=now, paid_at=now,
        provider_response={"confirmed_by_id": actor_id, "confirmed_at": now.isoformat(), "reason": body.reason, "collection_method": "cash", "walk_in": True})
    session.add_all([order, attempt])
    try:
        await session.flush()
        session.add(WalkInSale(order_id=order.id, client_request_id=request_id, request_fingerprint=fingerprints[0], operator_id=actor_id, sale_category=body.sale_category))
        await session.flush()
        enqueue_invoice_issue(session, order, trigger="walk_in_payment_confirmed")
        if verified_membership_id is not None:
            await award_order_purchase_points(session, order)
        audit_data = {"amount": amount, "tax_type": tax_type.value, "invoice_handling": body.invoice_handling, "sale_category": body.sale_category}
        if verified_membership_id is not None:
            audit_data["verified_membership_id"] = verified_membership_id
        if body.catalog_id is not None:
            audit_data["catalog_id"] = str(body.catalog_id)
        if verified_roster_id is not None:
            audit_data["verified_roster_entry_id"] = verified_roster_id
        session.add(AdminAudit(actor_id=actor_id, action="walk_in_sale.collected", aggregate_type="order", aggregate_id=order.id,
            reason=body.reason, data=audit_data))
        await session.execute(delete(WalkInRequestGuard).where(WalkInRequestGuard.request_id == request_id))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _existing(session, request_id, fingerprints)
        if existing is None:
            raise
        response.status_code = 200
        return await walk_in_sale_read(session, existing)
    return await walk_in_sale_read(session, order)
