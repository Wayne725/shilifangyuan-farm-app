from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, require_admin
from ..config import Settings, get_settings
from ..sales_scope import require_sales_scope_allows
from ..database import get_session
from ..domain import DomainError
from ..integrations.common import IntegrationError
from ..integrations.ecpay_logistics import (
    LogisticsSelectionRequest as ECPaySelectionRequest,
    UpdateTempLogisticsRequest,
    ecpay_logistics_adapter_from_settings,
)
from ..integrations.invoice_service import enqueue_invoice_issue
from ..integrations.pii_crypto import (
    VersionedPIICipher,
    pii_cipher_from_settings,
)
from ..models import (
    AdminAudit,
    ExternalEvent,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    GroupDecisionStatus,
    Order,
    OrderFulfillment,
    OrderKind,
    PaymentAttempt,
    PaymentStatus,
    Product,
    SalesChannel,
    Shipment,
    ShipmentStatus,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
    User,
    UserRole,
)
from ..schemas import (
    LogisticsSelectionRequest,
    ShipmentRead,
    ShippingRateInput,
    ShippingRateRead,
)
from ..v2_domain import (
    shipping_fee_for_rate,
    validate_shipping_selection,
)


logistics_router = APIRouter(tags=["logistics"])
logger = logging.getLogger(__name__)

PROVIDER_SAFE_FIELDS = {
    "TempLogisticsID",
    "LogisticsType",
    "LogisticsSubType",
    "ReceiverStoreID",
    "ReceiverStoreName",
    "ScheduledDeliveryDate",
    "ScheduledDeliveryTime",
    "MerchantTradeNo",
    "LogisticsID",
    "LogisticsStatus",
    "LogisticsStatusName",
    "UpdateStatusDate",
    "CVSPaymentNo",
    "CVSValidationNo",
    "BookingNote",
    "ShipmentNo",
    "RtnCode",
    "RtnMsg",
}
IN_TRANSIT_CODES = {
    "2030",
    "2063",
    "2073",
    "3018",
    "3024",
}
DELIVERED_CODES = {"2067", "3022"}
EXCEPTION_CODES = {"2074", "3020"}
ECPAY_SELECTION_CHANNELS = {
    ("HOME", "TCAT"): ShippingChannel.HOME_DELIVERY,
    ("CVS", "UNIMART"): ShippingChannel.SEVEN_ELEVEN,
    ("CVS", "UNIMARTC2C"): ShippingChannel.SEVEN_ELEVEN,
    ("CVS", "UNIMARTFREEZE"): ShippingChannel.SEVEN_ELEVEN,
    ("CVS", "FAMI"): ShippingChannel.FAMILY_MART,
    ("CVS", "FAMIC2C"): ShippingChannel.FAMILY_MART,
    ("CVS", "HILIFE"): ShippingChannel.HILIFE,
    ("CVS", "HILIFEC2C"): ShippingChannel.HILIFE,
}


class ShipmentOperationRead(BaseModel):
    shipment: ShipmentRead
    provider: Dict[str, Any] = Field(default_factory=dict)


class LogisticsSelectionRead(BaseModel):
    shipment: ShipmentRead
    #: Open this in a browser to reach ECPay's store picker.
    selection_url: str
    shipping_fee: int
    product_subtotal: int
    amount_total: int
    expires_in_seconds: int


class SandboxShipmentStatusUpdate(BaseModel):
    status: ShipmentStatus
    reason: str = Field(min_length=1, max_length=1000)


class ShippingRatePatch(BaseModel):
    fee: Optional[int] = Field(default=None, ge=0)
    free_shipping_threshold: Optional[int] = Field(default=None, ge=0)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def validate_effective_range(self) -> "ShippingRatePatch":
        if (
            self.effective_from
            and self.effective_to
            and self.effective_to < self.effective_from
        ):
            raise ValueError("運費結束日不可早於生效日")
        return self


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_provider_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key in PROVIDER_SAFE_FIELDS
    }


def _pii_context(fulfillment_id: str, field: str) -> str:
    return f"order-fulfillment:{fulfillment_id}:{field}"


def _encrypt_recipient(
    fulfillment: OrderFulfillment,
    cipher: VersionedPIICipher,
    *,
    name: str,
    phone: str,
    address: str,
) -> None:
    fulfillment.recipient_name_encrypted = cipher.encrypt_text(
        name,
        associated_data=_pii_context(fulfillment.id, "recipient_name"),
    )
    fulfillment.recipient_phone_encrypted = cipher.encrypt_text(
        phone,
        associated_data=_pii_context(fulfillment.id, "recipient_phone"),
    )
    fulfillment.shipping_address_encrypted = cipher.encrypt_text(
        address,
        associated_data=_pii_context(fulfillment.id, "shipping_address"),
    )
    fulfillment.encryption_key_version = cipher.current_version


def _shipment_read(shipment: Shipment) -> ShipmentRead:
    return ShipmentRead.model_validate(shipment)


async def _load_order(
    session: AsyncSession,
    order_id: str,
    *,
    lock: bool = False,
) -> Order:
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.group_campaign),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
    )
    if lock:
        query = query.with_for_update()
    order = await session.scalar(query)
    if order is None:
        raise HTTPException(status_code=404, detail="找不到訂單")
    return order


def _ensure_order_viewer(order: Order, user: User) -> None:
    if order.user_id != user.id and user.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=404, detail="找不到訂單")


async def _ensure_no_active_payment_attempt(
    session: AsyncSession,
    order_id: str,
) -> None:
    active_attempt_id = await session.scalar(
        select(PaymentAttempt.id)
        .where(
            PaymentAttempt.order_id == order_id,
            PaymentAttempt.status == PaymentStatus.PENDING,
            PaymentAttempt.expires_at > _now(),
        )
        .limit(1)
    )
    if active_attempt_id is not None:
        raise HTTPException(
            status_code=409,
            detail="已有待確認付款，無法重新選擇物流",
        )


async def _shipping_profile(
    session: AsyncSession,
    order: Order,
    channel: ShippingChannel,
) -> ShippingTemperature:
    if (
        order.sales_channel == SalesChannel.MEAL_PREORDER
        or order.meal_event_id is not None
    ):
        raise HTTPException(status_code=409, detail="便當預購不支援物流")
    if (
        order.sales_channel == SalesChannel.GROUP
        or order.order_kind == OrderKind.GROUP
    ):
        campaign = order.group_campaign
        if (
            campaign is None
            or not campaign.can_ship
            or campaign.shipping_temperature is None
        ):
            raise HTTPException(status_code=409, detail="此團購不支援物流")
        temperatures = [campaign.shipping_temperature]
        channel_sets = [campaign.allowed_shipping_channels]
    else:
        product_ids = {
            item.source_product_id
            for item in order.items
            if item.source_product_id is not None
        }
        products = list(
            await session.scalars(
                select(Product).where(Product.id.in_(product_ids))
            )
        )
        if len(products) != len(product_ids) or len(products) != len(order.items):
            raise HTTPException(
                status_code=409,
                detail="訂單含有無法配送的非商品品項",
            )
        if any(
            not product.can_ship or product.shipping_temperature is None
            for product in products
        ):
            raise HTTPException(status_code=409, detail="訂單含有不可配送商品")
        temperatures = [
            product.shipping_temperature for product in products
        ]
        channel_sets = [
            product.allowed_shipping_channels for product in products
        ]
    try:
        return validate_shipping_selection(
            temperatures,
            channel_sets,
            channel,
        )
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _effective_rate(
    session: AsyncSession,
    channel: ShippingChannel,
    temperature: ShippingTemperature,
    *,
    on_date: Optional[date] = None,
) -> ShippingRate:
    effective_date = on_date or date.today()
    rate = await session.scalar(
        select(ShippingRate)
        .where(
            ShippingRate.channel == channel,
            ShippingRate.temperature == temperature,
            ShippingRate.is_active.is_(True),
            ShippingRate.effective_from <= effective_date,
            or_(
                ShippingRate.effective_to.is_(None),
                ShippingRate.effective_to >= effective_date,
            ),
        )
        .order_by(ShippingRate.effective_from.desc())
        .limit(1)
    )
    if rate is None:
        raise HTTPException(
            status_code=422,
            detail="找不到適用的物流費率",
        )
    return rate


def _product_subtotal(
    order: Order,
    shipment: Optional[Shipment] = None,
) -> int:
    if shipment is None:
        fulfillment = order.__dict__.get("fulfillment")
        shipment = (
            fulfillment.__dict__.get("shipment")
            if fulfillment is not None
            else None
        )
    existing_fee = shipment.shipping_fee if shipment is not None else 0
    return max(0, order.amount_total - existing_fee)


SELECTION_TOKEN_TTL = timedelta(minutes=30)


def _selection_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, digest


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verify_selection_token(shipment: Shipment, token: str) -> None:
    expected = shipment.selection_token_hash or ""
    if not expected or not hmac.compare_digest(expected, _token_digest(token)):
        raise HTTPException(status_code=400, detail="物流選擇結果驗證失敗")
    expires_at = shipment.selection_token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is None or expires_at <= _now():
        raise HTTPException(status_code=410, detail="物流選擇連結已失效")


async def _shipment_for_token(
    session: AsyncSession,
    token: str,
) -> Shipment:
    shipment = await session.scalar(
        select(Shipment)
        .where(Shipment.selection_token_hash == _token_digest(token))
        .options(
            selectinload(Shipment.fulfillment).selectinload(
                OrderFulfillment.order
            )
        )
        .with_for_update()
    )
    if shipment is None:
        raise HTTPException(status_code=404, detail="找不到物流選擇資料")
    expires_at = shipment.selection_token_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is None or expires_at <= _now():
        raise HTTPException(status_code=410, detail="物流選擇連結已失效")
    return shipment


async def _shipment_for_provider_callback(
    session: AsyncSession,
    logistics_id: str,
) -> Optional[Shipment]:
    identity = (
        await session.execute(
            select(Shipment.id, OrderFulfillment.order_id)
            .join(OrderFulfillment)
            .where(Shipment.ecpay_logistics_id == logistics_id)
        )
    ).one_or_none()
    if identity is None:
        return None
    shipment_id, order_id = identity
    await session.execute(
        select(Order.id).where(Order.id == order_id).with_for_update()
    )
    return await session.scalar(
        select(Shipment)
        .where(Shipment.id == shipment_id)
        .options(
            selectinload(Shipment.fulfillment).selectinload(
                OrderFulfillment.order
            )
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )


async def _request_json_envelope(request: Request) -> Mapping[str, Any]:
    content_type = request.headers.get("content-type", "").lower()
    try:
        if "application/json" in content_type:
            payload = await request.json()
        else:
            form = await request.form()
            raw = form.get("ResultData")
            if raw is None:
                payload = dict(form)
            else:
                payload = json.loads(str(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="物流回傳格式錯誤") from exc
    if not isinstance(payload, Mapping):
        raise HTTPException(status_code=400, detail="物流回傳格式錯誤")
    return payload


def _status_from_provider(
    payload: Mapping[str, Any],
) -> Optional[ShipmentStatus]:
    code = str(
        payload.get("LogisticsStatus")
        or payload.get("RtnCode")
        or ""
    )
    if code in DELIVERED_CODES:
        return ShipmentStatus.DELIVERED
    if code in EXCEPTION_CODES:
        return ShipmentStatus.EXCEPTION
    if code in IN_TRANSIT_CODES:
        return ShipmentStatus.IN_TRANSIT
    return None


def _provider_status_time(
    payload: Mapping[str, Any],
) -> Optional[datetime]:
    raw = str(payload.get("UpdateStatusDate", "")).strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Taipei")
        )
    except ValueError:
        return None


def _should_apply_provider_update(
    shipment: Shipment,
    payload: Mapping[str, Any],
    target: Optional[ShipmentStatus],
) -> bool:
    if (
        shipment.status == ShipmentStatus.DELIVERED
        and target != ShipmentStatus.DELIVERED
    ):
        return False
    current_time = _provider_status_time(shipment.provider_payload or {})
    incoming_time = _provider_status_time(payload)
    if current_time is not None and (
        incoming_time is None or incoming_time <= current_time
    ):
        return False
    return True


def _channel_from_selection_result(
    payload: Mapping[str, Any],
) -> ShippingChannel:
    logistics_type = str(payload.get("LogisticsType", "")).upper()
    logistics_sub_type = str(payload.get("LogisticsSubType", "")).upper()
    channel = ECPAY_SELECTION_CHANNELS.get(
        (logistics_type, logistics_sub_type)
    )
    if channel is None:
        raise HTTPException(
            status_code=422,
            detail="綠界回傳的物流通路不受支援，請重新選擇物流",
        )
    return channel


async def _apply_shipment_status(
    session: AsyncSession,
    shipment: Shipment,
    target: ShipmentStatus,
) -> None:
    fulfillment = shipment.fulfillment
    order = fulfillment.order
    shipment.status = target
    if target == ShipmentStatus.CREATED:
        fulfillment.status = FulfillmentState.AWAITING_SHIPMENT
        order.fulfillment_status = FulfillmentStatus.PREPARING
    elif target == ShipmentStatus.IN_TRANSIT:
        fulfillment.status = FulfillmentState.SHIPPED
        order.fulfillment_status = FulfillmentStatus.PREPARING
    elif target == ShipmentStatus.DELIVERED:
        fulfillment.status = FulfillmentState.DELIVERED
        fulfillment.fulfilled_at = _now()
        order.fulfillment_status = FulfillmentStatus.PICKED_UP
        enqueue_invoice_issue(
            session,
            order,
            trigger="legacy_delivery_completed",
        )


def _update_shipment_provider_data(
    shipment: Shipment,
    payload: Mapping[str, Any],
) -> None:
    shipment.provider_payload = {
        **(shipment.provider_payload or {}),
        **_safe_provider_payload(payload),
    }
    tracking = payload.get("ShipmentNo") or payload.get("CVSPaymentNo")
    if tracking:
        shipment.tracking_number = str(tracking)
    booking_note = payload.get("BookingNote")
    if booking_note:
        shipment.ecpay_booking_note = str(booking_note)


@logistics_router.post(
    "/v1/orders/{order_id}/logistics/selection",
    response_model=LogisticsSelectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_logistics_selection(
    order_id: str,
    body: LogisticsSelectionRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LogisticsSelectionRead:
    require_sales_scope_allows(settings)
    order = await _load_order(session, order_id, lock=True)
    _ensure_order_viewer(order, user)
    if order.payment_status != PaymentStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail="物流必須在付款前選擇",
        )
    await _ensure_no_active_payment_attempt(session, order.id)
    actual_temperature = await _shipping_profile(
        session,
        order,
        body.channel,
    )
    if actual_temperature != body.temperature:
        raise HTTPException(status_code=422, detail="物流溫層與商品不符")
    rate = await _effective_rate(
        session,
        body.channel,
        actual_temperature,
    )
    subtotal = _product_subtotal(order)
    if not 1 <= subtotal <= 20000:
        raise HTTPException(
            status_code=422,
            detail="綠界物流商品金額必須介於 1 至 20,000 元",
        )
    shipping_fee = shipping_fee_for_rate(subtotal, rate)
    fulfillment = order.fulfillment
    if fulfillment is None:
        fulfillment = OrderFulfillment(
            order=order,
            method=FulfillmentMethod.ECPAY_LOGISTICS,
            status=FulfillmentState.PENDING_CONFIRMATION,
        )
        session.add(fulfillment)
        await session.flush()
        shipment = None
    else:
        shipment = fulfillment.shipment
    fulfillment.method = FulfillmentMethod.ECPAY_LOGISTICS
    order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    if shipment is None:
        shipment = Shipment(
            fulfillment=fulfillment,
            channel=body.channel,
            temperature=actual_temperature,
            status=ShipmentStatus.DRAFT,
            shipping_fee=shipping_fee,
        )
        session.add(shipment)
    elif shipment.status not in {
        ShipmentStatus.DRAFT,
        ShipmentStatus.SELECTION_PENDING,
        ShipmentStatus.READY_TO_CREATE,
    }:
        raise HTTPException(status_code=409, detail="物流單已不可重新選擇")
    shipment.channel = body.channel
    shipment.temperature = actual_temperature
    shipment.shipping_fee = shipping_fee
    await session.flush()
    cipher = pii_cipher_from_settings(settings)
    _encrypt_recipient(
        fulfillment,
        cipher,
        name=body.recipient_name,
        phone=body.recipient_phone,
        address=body.shipping_address,
    )
    token, token_hash = _selection_token()
    shipment.selection_token_hash = token_hash
    shipment.selection_token_expires_at = _now() + SELECTION_TOKEN_TTL
    shipment.status = ShipmentStatus.SELECTION_PENDING
    # The buyer pays subtotal + shipping; keep the order total authoritative.
    order.amount_total = subtotal + shipping_fee
    await session.commit()
    return LogisticsSelectionRead(
        shipment=_shipment_read(shipment),
        selection_url="{}/logistics/{}/select".format(
            settings.app_base_url.rstrip("/"), token
        ),
        shipping_fee=shipping_fee,
        product_subtotal=subtotal,
        amount_total=order.amount_total,
        expires_in_seconds=int(SELECTION_TOKEN_TTL.total_seconds()),
    )


@logistics_router.post(
    "/v1/orders/{order_id}/logistics/selection-link",
    response_model=LogisticsSelectionRead,
)
async def reissue_logistics_selection_link(
    order_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LogisticsSelectionRead:
    """Re-opens the picker for a buyer who abandoned it.

    The recipient details are already stored (encrypted), so this only mints a
    fresh token; without it an abandoned selection would strand the order,
    because payment is blocked until the store/address is fixed.
    """
    require_sales_scope_allows(settings)
    order = await _load_order(session, order_id, lock=True)
    _ensure_order_viewer(order, user)
    if order.payment_status != PaymentStatus.PENDING:
        raise HTTPException(status_code=409, detail="物流必須在付款前選擇")
    await _ensure_no_active_payment_attempt(session, order.id)
    if order.fulfillment is None or order.fulfillment.shipment is None:
        raise HTTPException(status_code=404, detail="訂單尚未選擇物流")
    shipment = order.fulfillment.shipment
    if shipment.status not in {
        ShipmentStatus.DRAFT,
        ShipmentStatus.SELECTION_PENDING,
        ShipmentStatus.READY_TO_CREATE,
    }:
        raise HTTPException(status_code=409, detail="物流單已不可重新選擇")
    token, token_hash = _selection_token()
    shipment.selection_token_hash = token_hash
    shipment.selection_token_expires_at = _now() + SELECTION_TOKEN_TTL
    shipment.status = ShipmentStatus.SELECTION_PENDING
    await session.commit()
    return LogisticsSelectionRead(
        shipment=_shipment_read(shipment),
        selection_url="{}/logistics/{}/select".format(
            settings.app_base_url.rstrip("/"), token
        ),
        shipping_fee=shipment.shipping_fee,
        product_subtotal=_product_subtotal(order),
        amount_total=order.amount_total,
        expires_in_seconds=int(SELECTION_TOKEN_TTL.total_seconds()),
    )


@logistics_router.get(
    "/logistics/{token}/select",
    response_class=HTMLResponse,
)
async def open_logistics_selection_page(
    token: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Unauthenticated by design.

    ECPay's store picker has to be reached by a top-level browser navigation,
    which cannot carry a Bearer token, so the one-time token in the path is the
    credential — exactly like `/payments/{attempt_id}/checkout`.
    """
    require_sales_scope_allows(settings)
    shipment = await _shipment_for_token(session, token)
    if shipment.status not in {
        ShipmentStatus.SELECTION_PENDING,
        ShipmentStatus.READY_TO_CREATE,
    }:
        raise HTTPException(status_code=409, detail="此物流單已不可重新選擇")
    fulfillment = shipment.fulfillment
    order = fulfillment.order
    if order.payment_status != PaymentStatus.PENDING:
        raise HTTPException(status_code=409, detail="物流必須在付款前選擇")

    cipher = pii_cipher_from_settings(settings)
    try:
        recipient_name = cipher.decrypt_text(
            fulfillment.recipient_name_encrypted or "",
            associated_data=_pii_context(fulfillment.id, "recipient_name"),
        )
        recipient_phone = cipher.decrypt_text(
            fulfillment.recipient_phone_encrypted or "",
            associated_data=_pii_context(fulfillment.id, "recipient_phone"),
        )
        shipping_address = cipher.decrypt_text(
            fulfillment.shipping_address_encrypted or "",
            associated_data=_pii_context(fulfillment.id, "shipping_address"),
        )
    except IntegrationError as exc:
        raise HTTPException(
            status_code=409,
            detail="收件資料無法讀取，請重新選擇物流",
        ) from exc

    is_mobile = (
        len(recipient_phone) == 10
        and recipient_phone.startswith("09")
        and recipient_phone.isdigit()
    )
    subtotal = _product_subtotal(order, shipment)
    callback_params = {"order_id": order.id, "token": token}
    query = urlencode(callback_params)
    try:
        adapter = ecpay_logistics_adapter_from_settings(settings)
        page = await adapter.create_selection_page(
            ECPaySelectionRequest(
                goods_amount=max(1, subtotal),
                goods_name="十里方圓農產",
                sender_name=settings.ecpay_logistics_sender_name,
                sender_zip_code=settings.ecpay_logistics_sender_zip_code,
                sender_address=settings.ecpay_logistics_sender_address,
                server_reply_url=(
                    f"{settings.app_base_url.rstrip('/')}"
                    "/webhooks/ecpay/logistics"
                ),
                client_reply_url=(
                    f"{settings.app_base_url.rstrip('/')}"
                    f"/logistics/selection-result?{query}"
                ),
                temperature={
                    ShippingTemperature.AMBIENT: "0001",
                    ShippingTemperature.CHILLED: "0002",
                    ShippingTemperature.FROZEN: "0003",
                }[shipment.temperature],
                receiver_address=shipping_address,
                receiver_cell_phone=recipient_phone if is_mobile else "",
                receiver_phone="" if is_mobile else recipient_phone,
                receiver_name=recipient_name,
                eshop_member_id=order.user_id.replace("-", "")[:24],
            )
        )
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=503,
            detail="物流選擇服務目前尚未開啟；訂單已保留，可稍後續辦",
        ) from exc
    await session.commit()
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@logistics_router.post(
    "/logistics/selection-result",
    response_class=RedirectResponse,
)
async def receive_logistics_selection_result(
    request: Request,
    order_id: str,
    token: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    order = await _load_order(session, order_id, lock=True)
    if order.fulfillment is None or order.fulfillment.shipment is None:
        raise HTTPException(status_code=404, detail="找不到物流選擇資料")
    shipment = order.fulfillment.shipment
    _verify_selection_token(shipment, token)
    await _ensure_no_active_payment_attempt(session, order.id)
    try:
        envelope = await _request_json_envelope(request)
        data = ecpay_logistics_adapter_from_settings(
            settings
        ).decode_selection_result(envelope)
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        logger.exception(
            "ECPay logistics selection result rejected order_id=%s: %s",
            order_id,
            exc,
        )
        raise HTTPException(status_code=400, detail="物流選擇結果驗證失敗") from exc
    actual_channel = _channel_from_selection_result(data)
    actual_temperature = await _shipping_profile(
        session,
        order,
        actual_channel,
    )
    if actual_temperature != shipment.temperature:
        raise HTTPException(status_code=422, detail="綠界回傳的物流溫層與商品不符")
    rate = await _effective_rate(
        session,
        actual_channel,
        actual_temperature,
    )
    subtotal = _product_subtotal(order, shipment)
    shipping_fee = shipping_fee_for_rate(subtotal, rate)
    shipment.channel = actual_channel
    shipment.temperature = actual_temperature
    shipment.shipping_fee = shipping_fee
    order.amount_total = subtotal + shipping_fee
    _update_shipment_provider_data(shipment, data)
    shipment.status = ShipmentStatus.READY_TO_CREATE
    # One-time use: the picker has done its job for this shipment.
    shipment.selection_token_hash = None
    shipment.selection_token_expires_at = None
    cipher = pii_cipher_from_settings(settings)
    _encrypt_recipient(
        order.fulfillment,
        cipher,
        name=str(data.get("ReceiverName") or ""),
        phone=str(
            data.get("ReceiverCellPhone")
            or data.get("ReceiverCellphone")
            or data.get("ReceiverPhone")
            or ""
        ),
        address=str(data.get("ReceiverAddress") or ""),
    )
    await session.commit()
    redirect_query = urlencode(
        {"order_id": order.id, "logistics": "selected"}
    )
    location = f"{settings.web_base_url.rstrip('/')}/orders?{redirect_query}"
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


@logistics_router.post(
    "/v1/admin/orders/{order_id}/logistics/create",
    response_model=ShipmentOperationRead,
)
async def create_formal_logistics_order(
    order_id: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ShipmentOperationRead:
    order = await _load_order(session, order_id, lock=True)
    if order.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=409, detail="訂單付款後才能建立物流單")
    if (
        order.order_kind == OrderKind.GROUP
        and (
            order.group_campaign is None
            or order.group_campaign.decision_status
            != GroupDecisionStatus.CONFIRMED
        )
    ):
        raise HTTPException(status_code=409, detail="團購確認成團後才能出貨")
    if order.fulfillment is None or order.fulfillment.shipment is None:
        raise HTTPException(status_code=409, detail="訂單尚未選擇物流")
    if (
        order.fulfillment_method != FulfillmentMethod.ECPAY_LOGISTICS
        or order.fulfillment.method != FulfillmentMethod.ECPAY_LOGISTICS
    ):
        raise HTTPException(status_code=409, detail="訂單履約方式不是物流配送")
    shipment = order.fulfillment.shipment
    if shipment.ecpay_logistics_id:
        return ShipmentOperationRead(
            shipment=_shipment_read(shipment),
            provider=_safe_provider_payload(shipment.provider_payload or {}),
        )
    require_sales_scope_allows(settings)
    if (
        order.fulfillment_status
        in {FulfillmentStatus.PICKED_UP, FulfillmentStatus.CANCELLED}
        or order.fulfillment.status
        in {
            FulfillmentState.PICKED_UP,
            FulfillmentState.DELIVERED,
            FulfillmentState.NO_SHOW,
            FulfillmentState.CANCELLED,
        }
    ):
        raise HTTPException(status_code=409, detail="訂單目前不可建立物流單")
    if (
        order.fulfillment_status != FulfillmentStatus.PREPARING
        or order.fulfillment.status != FulfillmentState.PREPARING
    ):
        raise HTTPException(status_code=409, detail="訂單進入備貨中後才能建立物流單")
    if shipment.status != ShipmentStatus.READY_TO_CREATE:
        raise HTTPException(status_code=409, detail="暫存物流單尚未完成")
    temp_id = str(
        (shipment.provider_payload or {}).get("TempLogisticsID", "")
    )
    if not temp_id:
        raise HTTPException(status_code=409, detail="缺少綠界暫存物流單編號")
    order_id_snapshot = order.id
    order_number = order.order_number
    admin_id = admin.id
    adapter = ecpay_logistics_adapter_from_settings(settings)
    try:
        await adapter.update_temp_order(
            UpdateTempLogisticsRequest(
                temp_logistics_id=temp_id,
                goods_amount=max(1, _product_subtotal(order)),
                goods_name="十里方圓農產",
                sender_name=settings.ecpay_logistics_sender_name,
                sender_zip_code=settings.ecpay_logistics_sender_zip_code,
                sender_address=settings.ecpay_logistics_sender_address,
                server_reply_url=(
                    f"{settings.app_base_url.rstrip('/')}"
                    "/webhooks/ecpay/logistics"
                ),
            )
        )
        result = await adapter.create_order(
            temp_logistics_id=temp_id,
            merchant_trade_no=order_number,
        )
    except (IntegrationError, ValueError):
        await session.rollback()
        try:
            result = await adapter.query_order(
                merchant_trade_no=order_number,
            )
            if str(result.get("MerchantTradeNo", "")) != order_number:
                raise ValueError("綠界物流訂單交易編號不符")
            if not str(result.get("LogisticsID", "")).strip():
                raise ValueError("綠界物流訂單缺少物流編號")
        except (IntegrationError, ValueError) as query_exc:
            await session.rollback()
            raise HTTPException(
                status_code=503,
                detail="綠界正式物流單建立失敗",
            ) from query_exc
        order = await _load_order(session, order_id_snapshot, lock=True)
        if order.fulfillment is None or order.fulfillment.shipment is None:
            raise HTTPException(status_code=409, detail="訂單尚未選擇物流")
        shipment = order.fulfillment.shipment
        if shipment.ecpay_logistics_id:
            return ShipmentOperationRead(
                shipment=_shipment_read(shipment),
                provider=_safe_provider_payload(
                    shipment.provider_payload or {}
                ),
            )
    logistics_id = str(result.get("LogisticsID", "")).strip()
    if not logistics_id:
        await session.rollback()
        raise HTTPException(status_code=503, detail="綠界正式物流單建立失敗")
    shipment.ecpay_logistics_id = logistics_id
    _update_shipment_provider_data(shipment, result)
    await _apply_shipment_status(session, shipment, ShipmentStatus.CREATED)
    session.add(
        AdminAudit(
            actor_id=admin_id,
            action="shipment.create",
            aggregate_type="shipment",
            aggregate_id=shipment.id,
            data={"ecpay_logistics_id": shipment.ecpay_logistics_id},
        )
    )
    await session.commit()
    return ShipmentOperationRead(
        shipment=_shipment_read(shipment),
        provider=_safe_provider_payload(shipment.provider_payload or {}),
    )


@logistics_router.get(
    "/v1/orders/{order_id}/logistics",
    response_model=ShipmentOperationRead,
)
async def query_logistics_order(
    order_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ShipmentOperationRead:
    order = await _load_order(session, order_id, lock=True)
    _ensure_order_viewer(order, user)
    if order.fulfillment is None or order.fulfillment.shipment is None:
        raise HTTPException(status_code=404, detail="找不到物流單")
    shipment = order.fulfillment.shipment
    if not shipment.ecpay_logistics_id:
        return ShipmentOperationRead(
            shipment=_shipment_read(shipment),
            provider=_safe_provider_payload(shipment.provider_payload or {}),
        )
    try:
        result = await ecpay_logistics_adapter_from_settings(
            settings
        ).query_order(logistics_id=shipment.ecpay_logistics_id)
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail="綠界物流查詢失敗") from exc
    target = _status_from_provider(result)
    if _should_apply_provider_update(shipment, result, target):
        _update_shipment_provider_data(shipment, result)
        if target is not None:
            await _apply_shipment_status(session, shipment, target)
    await session.commit()
    return ShipmentOperationRead(
        shipment=_shipment_read(shipment),
        provider=_safe_provider_payload(result),
    )


@logistics_router.get(
    "/v1/admin/orders/{order_id}/logistics/print",
    response_class=HTMLResponse,
)
async def print_logistics_document(
    order_id: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    order = await _load_order(session, order_id)
    if order.fulfillment is None or order.fulfillment.shipment is None:
        raise HTTPException(status_code=404, detail="找不到物流單")
    shipment = order.fulfillment.shipment
    sub_type = str(
        (shipment.provider_payload or {}).get("LogisticsSubType", "")
    )
    if not shipment.ecpay_logistics_id or not sub_type:
        raise HTTPException(status_code=409, detail="正式物流單尚未完成")
    try:
        page = await ecpay_logistics_adapter_from_settings(
            settings
        ).create_print_document_page(
            logistics_ids=[shipment.ecpay_logistics_id],
            logistics_sub_type=sub_type,
        )
    except (IntegrationError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="託運單產生失敗") from exc
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@logistics_router.post("/webhooks/ecpay/logistics")
async def logistics_status_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    try:
        adapter = ecpay_logistics_adapter_from_settings(settings)
    except (IntegrationError, ValueError):
        return JSONResponse(
            {"RtnCode": 0},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    try:
        envelope = await _request_json_envelope(request)
        data = adapter.verify_callback(envelope)
    except (HTTPException, IntegrationError, ValueError):
        await session.rollback()
        return JSONResponse(
            adapter.callback_acknowledgement(success=False),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    event_key = hashlib.sha256(
        "|".join(
            (
                str(data.get("MerchantTradeNo", "")),
                str(data.get("LogisticsID", "")),
                str(data.get("LogisticsStatus", "")),
                str(data.get("UpdateStatusDate", "")),
            )
        ).encode("utf-8")
    ).hexdigest()
    existing = await session.scalar(
        select(ExternalEvent).where(
            ExternalEvent.provider == "ecpay_logistics",
            ExternalEvent.external_event_key == event_key,
        )
    )
    if existing is not None:
        return JSONResponse(adapter.callback_acknowledgement())
    shipment = await _shipment_for_provider_callback(
        session,
        str(data.get("LogisticsID", "")),
    )
    if shipment is None:
        await session.rollback()
        return JSONResponse(
            adapter.callback_acknowledgement(success=False),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    target = _status_from_provider(data)
    if _should_apply_provider_update(shipment, data, target):
        _update_shipment_provider_data(shipment, data)
        if target is not None:
            await _apply_shipment_status(session, shipment, target)
    session.add(
        ExternalEvent(
            provider="ecpay_logistics",
            external_event_key=event_key,
            event_type="logistics.status",
            payload=_safe_provider_payload(data),
            processed=True,
            processed_at=_now(),
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
    return JSONResponse(adapter.callback_acknowledgement())


@logistics_router.post(
    "/v1/admin/orders/{order_id}/logistics/sandbox-status",
    response_model=ShipmentRead,
)
async def advance_sandbox_shipment_status(
    order_id: str,
    body: SandboxShipmentStatusUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ShipmentRead:
    if settings.environment.strip().lower() not in {
        "development",
        "sandbox",
        "test",
    }:
        raise HTTPException(status_code=404, detail="找不到此功能")
    if body.status not in {
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.DELIVERED,
        ShipmentStatus.EXCEPTION,
    }:
        raise HTTPException(
            status_code=422,
            detail="Sandbox 只允許推進配送中、已送達或異常",
        )
    order = await _load_order(session, order_id, lock=True)
    if order.fulfillment is None or order.fulfillment.shipment is None:
        raise HTTPException(status_code=404, detail="找不到物流單")
    shipment = order.fulfillment.shipment
    allowed: Dict[ShipmentStatus, Sequence[ShipmentStatus]] = {
        ShipmentStatus.CREATED: (
            ShipmentStatus.IN_TRANSIT,
            ShipmentStatus.EXCEPTION,
        ),
        ShipmentStatus.IN_TRANSIT: (
            ShipmentStatus.DELIVERED,
            ShipmentStatus.EXCEPTION,
        ),
        ShipmentStatus.EXCEPTION: (ShipmentStatus.IN_TRANSIT,),
    }
    if body.status not in allowed.get(shipment.status, ()):
        raise HTTPException(status_code=409, detail="Sandbox 貨態不可跳級")
    previous = shipment.status
    await _apply_shipment_status(session, shipment, body.status)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action=f"shipment.sandbox_status.{body.status.value}",
            aggregate_type="shipment",
            aggregate_id=shipment.id,
            reason=body.reason,
            data={
                "from": previous.value,
                "to": body.status.value,
                "sandbox": True,
            },
        )
    )
    await session.commit()
    return _shipment_read(shipment)


@logistics_router.get(
    "/v1/shipping-rates",
    response_model=List[ShippingRateRead],
)
async def list_effective_shipping_rates(
    channel: Optional[ShippingChannel] = None,
    temperature: Optional[ShippingTemperature] = None,
    effective_on: Optional[date] = None,
    session: AsyncSession = Depends(get_session),
) -> List[ShippingRate]:
    target_date = effective_on or date.today()
    query = select(ShippingRate).where(
        ShippingRate.is_active.is_(True),
        ShippingRate.effective_from <= target_date,
        or_(
            ShippingRate.effective_to.is_(None),
            ShippingRate.effective_to >= target_date,
        ),
    )
    if channel is not None:
        query = query.where(ShippingRate.channel == channel)
    if temperature is not None:
        query = query.where(ShippingRate.temperature == temperature)
    return list(
        await session.scalars(
            query.order_by(
                ShippingRate.channel,
                ShippingRate.temperature,
                ShippingRate.effective_from.desc(),
            )
        )
    )


@logistics_router.get(
    "/v1/admin/shipping-rates",
    response_model=List[ShippingRateRead],
)
async def list_admin_shipping_rates(
    include_inactive: bool = True,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> List[ShippingRate]:
    query = select(ShippingRate)
    if not include_inactive:
        query = query.where(ShippingRate.is_active.is_(True))
    return list(
        await session.scalars(
            query.order_by(
                ShippingRate.channel,
                ShippingRate.temperature,
                ShippingRate.effective_from.desc(),
            )
        )
    )


@logistics_router.post(
    "/v1/admin/shipping-rates",
    response_model=ShippingRateRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_shipping_rate(
    body: ShippingRateInput,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ShippingRate:
    rate = ShippingRate(
        **body.model_dump(),
        is_active=True,
    )
    try:
        session.add(rate)
        await session.flush()
        session.add(
            AdminAudit(
                actor_id=admin.id,
                action="shipping_rate.create",
                aggregate_type="shipping_rate",
                aggregate_id=rate.id,
                data=body.model_dump(mode="json"),
            )
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="相同通路、溫層與生效日的費率已存在",
        ) from exc
    return rate


@logistics_router.patch(
    "/v1/admin/shipping-rates/{rate_id}",
    response_model=ShippingRateRead,
)
async def update_shipping_rate(
    rate_id: str,
    body: ShippingRatePatch,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ShippingRate:
    rate = await session.get(ShippingRate, rate_id, with_for_update=True)
    if rate is None:
        raise HTTPException(status_code=404, detail="找不到物流費率")
    changes = body.model_dump(exclude_unset=True)
    effective_from = changes.get("effective_from", rate.effective_from)
    effective_to = changes.get("effective_to", rate.effective_to)
    if effective_to is not None and effective_to < effective_from:
        raise HTTPException(status_code=422, detail="運費生效區間錯誤")
    for key, value in changes.items():
        setattr(rate, key, value)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="shipping_rate.update",
            aggregate_type="shipping_rate",
            aggregate_id=rate.id,
            data=body.model_dump(exclude_unset=True, mode="json"),
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="相同通路、溫層與生效日的費率已存在",
        ) from exc
    return rate


@logistics_router.delete(
    "/v1/admin/shipping-rates/{rate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_shipping_rate(
    rate_id: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    rate = await session.get(ShippingRate, rate_id, with_for_update=True)
    if rate is None:
        raise HTTPException(status_code=404, detail="找不到物流費率")
    rate.is_active = False
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="shipping_rate.deactivate",
            aggregate_type="shipping_rate",
            aggregate_id=rate.id,
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router = logistics_router
