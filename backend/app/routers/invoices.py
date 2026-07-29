from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..config import Settings, get_settings
from ..integrations.common import IntegrationError
from ..integrations.invoice_service import invoice_adapter_from_settings
from ..models import User


invoices_router = APIRouter(tags=["invoices"])


class MobileBarcodeValidationRequest(BaseModel):
    barcode: str = Field(min_length=1, max_length=32)


class MobileBarcodeValidationResponse(BaseModel):
    barcode: str
    valid: bool
    provider_checked: bool
    message: str = ""


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


router = invoices_router
