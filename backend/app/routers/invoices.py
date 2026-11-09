from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin_explanations import AdminExplanation
from ..auth import get_current_user, require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..domain import member_sale_is_tax_exempt
from ..integrations.common import (
    IntegrationConfigurationError,
    IntegrationError,
)
from ..integrations.invoice_service import (
    InvoiceApplicationError,
    enqueue_invoice_issue,
    invoice_adapter_from_settings,
    reconcile_order_invoice,
)
from ..integrations.invoice_void import InvoiceVoidError, void_refunded_order_invoice
from ..models import AdminAudit, Invoice, InvoiceStatus, PaymentStatus, SalesChannel, User
from ..invoice_timing import invoice_handling, pickup_invoice_due, cancel_unissued_invoice_jobs
from ..schemas import InvoicePreferenceInput
from ..rate_limit import INVOICE_QUERY_RULE, client_key, enforce


invoices_router = APIRouter(tags=["invoices"])


class AdminInvoiceIssueRequest(BaseModel):
    handling: Literal["on_payment", "pickup_day"]
    reason: AdminExplanation = Field(default="", max_length=500)
    confirm_not_invoiced_elsewhere: bool
    preferences: Optional[InvoicePreferenceInput] = None


@invoices_router.post("/v1/admin/orders/{order_id}/invoice/issue")
async def arrange_order_invoice(
    order_id: UUID,
    body: AdminInvoiceIssueRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    from .orders import load_order
    order = await load_order(session, str(order_id), lock=True, refresh=True)
    if not body.confirm_not_invoiced_elsewhere:
        raise HTTPException(422, "請確認此筆未由其他系統開立發票")
    if order.payment_status != PaymentStatus.PAID or order.cancelled_at is not None:
        raise HTTPException(409, "只有已收款且未取消的訂單可以安排開票")
    if order.invoice is not None or order.invoice_status in {InvoiceStatus.ISSUED, InvoiceStatus.VOID_PENDING, InvoiceStatus.VOIDED}:
        raise HTTPException(409, "已有開票紀錄，請查詢原發票，不可重新開立")
    due = None
    if body.handling == "pickup_day":
        if (order.sales_channel != SalesChannel.MEAL_PREORDER and getattr(order, "customer_kind", "account") != "walk_in") or order.fulfillment is None or order.fulfillment.pickup_at is None:
            raise HTTPException(422, "取餐日開票需要便當或現場交易的取餐時間")
        due = pickup_invoice_due(order.fulfillment.pickup_at)
    changed = invoice_handling(order) != body.handling
    preference_data = {name: getattr(order, name) for name in InvoicePreferenceInput.model_fields}
    if body.preferences is not None:
        if member_sale_is_tax_exempt(order.membership_type_snapshot, body.preferences.invoice_buyer_type) != order.member_tax_exempt_snapshot:
            raise HTTPException(409, "此變更會改變訂單原有稅別，請先更正訂單再安排開票")
        preference_data.update(body.preferences.model_dump())
    notification_email = preference_data["invoice_buyer_email"] or order.contact_email
    if not notification_email:
        raise HTTPException(422, "請先補齊客人的有效發票 Email，再安排系統開票")
    try:
        InvoicePreferenceInput.model_validate({**preference_data, "invoice_buyer_email": notification_email})
    except ValidationError as exc:
        raise HTTPException(422, "請先補齊有效的發票 Email、統編與載具資料，再安排開票") from exc
    if body.preferences is not None:
        for name, value in body.preferences.model_dump().items():
            if getattr(order, name) != value:
                changed = True
                setattr(order, name, value)
    if changed or order.invoice_status != InvoiceStatus.PENDING:
        await cancel_unissued_invoice_jobs(session, order)
        order.invoice_handling = body.handling
        order.invoice_issue_after = due
        order.invoice_status = InvoiceStatus.NOT_ELIGIBLE
        enqueue_invoice_issue(session, order, trigger="admin_requested")
        session.add(AdminAudit(actor_id=admin.id, action="invoice.arranged", aggregate_type="order",
            aggregate_id=order.id, reason=body.reason,
            data={"handling": body.handling, "issue_after": due.isoformat() if due else None}))
        await session.commit()
    return {"order_id": order.id, "invoice_status": order.invoice_status.value,
        "invoice_handling": invoice_handling(order), "invoice_issue_after": order.invoice_issue_after}


class MobileBarcodeValidationRequest(BaseModel):
    barcode: str = Field(min_length=1, max_length=32)


class MobileBarcodeValidationResponse(BaseModel):
    barcode: str
    valid: bool
    provider_checked: bool
    message: str = ""


class AdminInvoiceQueryRequest(BaseModel):
    reason: AdminExplanation = Field(default="", max_length=1000)


class AdminInvoiceQueryResponse(BaseModel):
    order_id: str
    found: bool
    provider: str
    status: InvoiceStatus
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    message: str


class AdminInvoiceVoidRequest(AdminInvoiceQueryRequest):
    reason: AdminExplanation = Field(default="", max_length=20)


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
    try:
        invoice = await void_refunded_order_invoice(
            session,
            str(order_id),
            invoice_adapter_from_settings(settings),
            reason=body.reason,
            actor_id=actor_id,
        )
    except InvoiceVoidError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except (IntegrationError, ValueError, TimeoutError) as exc:
        await session.rollback()
        session.add(AdminAudit(
            actor_id=actor_id, action="invoice.void_query_failed",
            aggregate_type="order", aggregate_id=str(order_id),
            reason=body.reason, data={"error_type": type(exc).__name__},
        ))
        await session.commit()
        raise HTTPException(502, "作廢前查詢失敗，未送出作廢；請檢查供應商設定") from exc
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
