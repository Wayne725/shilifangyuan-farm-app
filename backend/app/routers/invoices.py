from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
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
from ..models import AdminAudit, Invoice, InvoiceStatus, User
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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrationConfigurationError as exc:
        await session.rollback()
        session.add(
            AdminAudit(
                actor_id=admin.id,
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
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        session.add(
            AdminAudit(
                actor_id=admin.id,
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
            actor_id=admin.id,
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
