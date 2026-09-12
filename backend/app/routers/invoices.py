from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..integrations.common import (
    IntegrationConfigurationError,
    IntegrationError,
)
from ..integrations.invoice_service import (
    InvoiceApplicationError,
    invoice_adapter_from_settings,
    reconcile_order_invoice,
)
from ..integrations.fanyu_invoice import FanyuInvoiceAdapter
from ..models import AdminAudit, Invoice, InvoiceAllowance, InvoiceAllowanceStatus, InvoiceStatus, Order, PaymentStatus, Refund, RefundStatus, User
from ..rate_limit import INVOICE_QUERY_RULE, client_key, enforce


invoices_router = APIRouter(tags=["invoices"])


class MobileBarcodeValidationRequest(BaseModel):
    barcode: str = Field(min_length=1, max_length=32)


class MobileBarcodeValidationResponse(BaseModel):
    barcode: str
    valid: bool
    provider_checked: bool
    message: str = ""


class AdminInvoiceQueryRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        reason = value.strip()
        if not reason:
            raise ValueError("請填寫查詢原因")
        return reason


class AdminInvoiceQueryResponse(BaseModel):
    order_id: str
    found: bool
    provider: str
    status: InvoiceStatus
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    message: str


class AdminInvoiceVoidRequest(AdminInvoiceQueryRequest):
    reason: str = Field(min_length=1, max_length=20)


@invoices_router.post("/v1/admin/orders/{order_id}/invoice/void")
async def void_refunded_invoice(
    order_id: UUID,
    body: AdminInvoiceVoidRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    actor_id = admin.id
    enforce(client_key(request, "invoice-void", admin.id), INVOICE_QUERY_RULE)
    order = await session.scalar(select(Order).where(Order.id == str(order_id)).options(selectinload(Order.invoice)).with_for_update())
    if order is None or order.invoice is None:
        raise HTTPException(404, "找不到訂單發票")
    invoice = order.invoice
    refunded = await session.scalar(select(func.coalesce(func.sum(Refund.amount), 0)).where(Refund.order_id == order.id, Refund.status == RefundStatus.COMPLETED))
    if order.payment_status != PaymentStatus.REFUNDED or refunded < order.amount_total:
        raise HTTPException(409, "此操作僅供已完成全額退款的訂單")
    if invoice.provider != "fanyu" or settings.invoice_provider != "fanyu":
        raise HTTPException(409, "請至原發票供應商處理，不可切換供應商作廢")
    allowance = await session.scalar(select(InvoiceAllowance.id).where(InvoiceAllowance.invoice_id == invoice.id, InvoiceAllowance.status != InvoiceAllowanceStatus.VOIDED).limit(1))
    if allowance:
        raise HTTPException(409, "已有折讓紀錄，請先由會計核對，不可直接作廢")
    if not invoice.invoice_number or not invoice.invoice_date:
        raise HTTPException(409, "請先查回原發票號碼與日期")
    original_number = invoice.invoice_number
    try:
        adapter = invoice_adapter_from_settings(settings)
        if not isinstance(adapter, FanyuInvoiceAdapter) or invoice.provider_request.get("sellerID") != adapter.settings.seller_id:
            raise InvoiceApplicationError("目前賣方設定不符合原發票快照，請人工核對")
        found = await reconcile_order_invoice(session, order.id, adapter, commit=False)
        if found is None:
            raise InvoiceApplicationError("汎宇查無原發票，停止作廢，請先人工核對")
        if invoice.invoice_number != original_number:
            raise InvoiceApplicationError("查回票號與原發票不同，停止作廢")
        if invoice.invoice_date is None:
            raise InvoiceApplicationError("查回發票日期不完整，停止作廢")
        if invoice.provider_status not in {"0", "1"}:
            raise InvoiceApplicationError("汎宇尚未回傳可確認的原發票狀態")
        if invoice.status == InvoiceStatus.VOIDED and invoice.provider_status != "1":
            raise InvoiceApplicationError("汎宇狀態與本機作廢紀錄不一致，請人工核對")
    except InvoiceApplicationError as exc:
        await session.rollback()
        session.add(AdminAudit(actor_id=actor_id, action="invoice.void_blocked", aggregate_type="order", aggregate_id=str(order_id),
                               reason=body.reason, data={"error_type": type(exc).__name__}))
        await session.commit()
        raise HTTPException(409, str(exc)) from exc
    except (IntegrationError, ValueError, TimeoutError) as exc:
        await session.rollback()
        session.add(AdminAudit(actor_id=actor_id, action="invoice.void_query_failed", aggregate_type="order", aggregate_id=str(order_id),
                               reason=body.reason, data={"error_type": type(exc).__name__}))
        await session.commit()
        raise HTTPException(502, "作廢前查詢失敗，未送出作廢；請檢查供應商設定") from exc

    if invoice.status != InvoiceStatus.VOIDED and invoice.void_source != "api_submission_started":
        invoice.status = order.invoice_status = InvoiceStatus.VOID_PENDING
        invoice.void_reason = body.reason
        invoice.void_source = "api_submission_started"
        session.add(AdminAudit(actor_id=actor_id, action="invoice.void_requested", aggregate_type="order", aggregate_id=order.id,
                               reason=body.reason, data={"invoice_number": original_number, "provider": "fanyu"}))
        # Durable submission marker must survive a provider timeout or process restart.
        await session.commit()
        try:
            response = await adapter.cancel_invoice(
                order_id=invoice.relate_number, buyer_type=invoice.buyer_type.value,
                buyer_tax_id=invoice.buyer_tax_id or "", invoice_number=original_number,
                invoice_date=invoice.invoice_date.replace(tzinfo=invoice.invoice_date.tzinfo or timezone.utc).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d"),
                reason=body.reason,
            )
            if str(response.get("invNo", "")) != original_number:
                raise ValueError("作廢回傳票號不符")
            found = await reconcile_order_invoice(session, order.id, adapter, commit=False)
            if found is None or invoice.invoice_number != original_number:
                raise ValueError("作廢後查回票號不符")
        except (IntegrationError, ValueError, TimeoutError):
            await session.rollback()
            invoice = await session.scalar(select(Invoice).where(Invoice.order_id == str(order_id)))
            invoice.error_message = "作廢送出結果未確認；請重新查詢，勿重送作廢"
    session.add(AdminAudit(actor_id=actor_id, action="invoice.void_checked", aggregate_type="order", aggregate_id=str(order_id),
                           reason=body.reason, data={"status": invoice.status.value, "provider": "fanyu"}))
    await session.commit()
    done = invoice.status == InvoiceStatus.VOIDED
    return JSONResponse(status_code=200 if done else 202, content={"order_id": str(order_id), "status": invoice.status.value,
                        "message": "汎宇已確認作廢" if done else "作廢結果仍待確認，請重新查詢；系統不會重複送出"})


@invoices_router.post(
    "/v1/invoice-carriers/mobile-barcode/validate",
    response_model=MobileBarcodeValidationResponse,
)
async def validate_mobile_barcode(
    body: MobileBarcodeValidationRequest,
    _user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> MobileBarcodeValidationResponse:
    try:
        result = await invoice_adapter_from_settings(
            settings
        ).validate_mobile_barcode(body.barcode)
    except (IntegrationError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="手機條碼驗證服務目前無法使用",
        ) from exc
    return MobileBarcodeValidationResponse(
        barcode=result.barcode,
        valid=result.valid,
        provider_checked=result.provider_checked,
        message=result.message,
    )


@invoices_router.post(
    "/v1/admin/orders/{order_id}/invoice/query",
    response_model=AdminInvoiceQueryResponse,
)
async def query_order_invoice(
    order_id: UUID,
    body: AdminInvoiceQueryRequest,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AdminInvoiceQueryResponse:
    actor_id = admin.id
    order_id_value = str(order_id)
    enforce(
        client_key(request, "invoice-query", admin.id),
        INVOICE_QUERY_RULE,
    )
    try:
        await reconcile_order_invoice(
            session,
            order_id_value,
            invoice_adapter_from_settings(settings),
            commit=False,
        )
        invoice = await session.scalar(
            select(Invoice).where(Invoice.order_id == order_id_value)
        )
        if invoice is None:
            raise InvoiceApplicationError("找不到發票資料")
    except InvoiceApplicationError as exc:
        await session.rollback()
        session.add(AdminAudit(
            actor_id=actor_id, action="invoice.query_blocked",
            aggregate_type="order", aggregate_id=order_id_value,
            reason=body.reason, data={"error_type": type(exc).__name__},
        ))
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrationConfigurationError as exc:
        await session.rollback()
        session.add(
            AdminAudit(
                actor_id=actor_id,
                action="invoice.provider_query_failed",
                aggregate_type="order",
                aggregate_id=order_id_value,
                reason=body.reason,
                data={"error_type": type(exc).__name__},
            )
        )
        await session.commit()
        raise HTTPException(
            status_code=503,
            detail="電子發票服務尚未完成安全設定",
        ) from exc
    except (IntegrationError, ValueError, TimeoutError) as exc:
        await session.rollback()
        session.add(
            AdminAudit(
                actor_id=actor_id,
                action="invoice.provider_query_failed",
                aggregate_type="order",
                aggregate_id=order_id_value,
                reason=body.reason,
                data={"error_type": type(exc).__name__},
            )
        )
        await session.commit()
        raise HTTPException(
            status_code=502,
            detail="電子發票查詢暫時失敗",
        ) from exc

    found = bool(invoice.invoice_number)
    session.add(
        AdminAudit(
            actor_id=actor_id,
            action="invoice.provider_queried",
            aggregate_type="order",
            aggregate_id=order_id_value,
            reason=body.reason,
            data={
                "found": found,
                "provider": invoice.provider,
                "invoice_status": invoice.status.value,
            },
        )
    )
    await session.commit()
    return AdminInvoiceQueryResponse(
        order_id=order_id_value,
        found=found,
        provider=invoice.provider,
        status=invoice.status,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        message=(
            "已同步電子發票資料"
            if found
            else "加值中心目前查無此銷貨單的電子發票"
        ),
    )


router = invoices_router
