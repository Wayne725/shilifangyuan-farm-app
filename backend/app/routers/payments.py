from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import Settings, get_settings
from ..database import get_session
from ..integrations.common import IntegrationError
from ..integrations.ecpay import CheckoutForm
from ..integrations.payment_service import (
    PaymentApplicationError,
    SQLAlchemyPaymentCallbackRepository,
    create_membership_payment_attempt,
    create_payment_attempt,
    payment_adapter_from_settings,
    payment_attempt_read,
)
from ..models import (
    MembershipCharge,
    Order,
    PaymentAttempt,
    PaymentStatus,
    User,
)


payments_router = APIRouter(tags=["payments"])


class PaymentAttemptResponse(BaseModel):
    id: str
    attempt_id: str
    payment_url: str
    status: str
    expires_at: datetime


@payments_router.post(
    "/v1/orders/{order_id}/payment-attempts",
    response_model=PaymentAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_payment(
    order_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    try:
        attempt = await create_payment_attempt(
            session=session,
            order_id=order_id,
            user=user,
            settings=settings,
        )
        return payment_attempt_read(attempt, settings)
    except PaymentApplicationError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=503,
            detail="測試金流目前無法建立付款頁，請確認後端金流設定",
        ) from exc


@payments_router.post(
    "/v1/membership/charges/{charge_id}/payment-attempts",
    response_model=PaymentAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_membership_payment(
    charge_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    try:
        attempt = await create_membership_payment_attempt(
            session=session,
            charge_id=charge_id,
            user=user,
            settings=settings,
        )
        return payment_attempt_read(attempt, settings)
    except PaymentApplicationError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=503,
            detail="測試金流目前無法建立付款頁，請確認後端金流設定",
        ) from exc


@payments_router.get("/v1/payment-attempts/{attempt_id}")
async def get_payment_attempt_status(
    attempt_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    attempt = await session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.id == attempt_id)
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="找不到付款資料")
    subject_owner_id = None
    if attempt.order_id is not None:
        subject_owner_id = await session.scalar(
            select(Order.user_id).where(Order.id == attempt.order_id)
        )
    elif attempt.membership_charge_id is not None:
        subject_owner_id = await session.scalar(
            select(MembershipCharge.user_id).where(
                MembershipCharge.id == attempt.membership_charge_id
            )
        )
    if subject_owner_id != user.id:
        raise HTTPException(status_code=404, detail="找不到付款資料")
    return {
        "id": attempt.id,
        "status": attempt.status.value,
        "expires_at": attempt.expires_at,
        "order_id": attempt.order_id,
        "membership_charge_id": attempt.membership_charge_id,
    }


@payments_router.get(
    "/payments/{attempt_id}/checkout",
    response_class=HTMLResponse,
)
async def payment_checkout(
    attempt_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    attempt = await session.get(PaymentAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="找不到付款頁")
    expires_at = attempt.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        attempt.status != PaymentStatus.PENDING
        or expires_at <= datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=410, detail="付款頁已失效")
    if not attempt.checkout_payload:
        raise HTTPException(status_code=409, detail="付款頁尚未建立")
    form = CheckoutForm(
        action_url=settings.ecpay_payment_aio_url,
        fields={key: str(value) for key, value in attempt.checkout_payload.items()},
    )
    return HTMLResponse(
        content=form.to_html(),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@payments_router.post(
    "/webhooks/ecpay/payment",
    response_class=PlainTextResponse,
)
async def ecpay_payment_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    form = await request.form()
    payload = {str(key): str(value) for key, value in form.items()}
    try:
        adapter = payment_adapter_from_settings(settings)
        repository = SQLAlchemyPaymentCallbackRepository(session)
        acknowledgement = await adapter.process_callback(payload, repository)
        return PlainTextResponse(
            acknowledgement,
            headers={"Cache-Control": "no-store"},
        )
    except (IntegrationError, PaymentApplicationError, ValueError):
        await session.rollback()
        return PlainTextResponse(
            "0|ERROR",
            status_code=status.HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
        )


@payments_router.post("/payments/result", response_class=RedirectResponse)
async def ecpay_payment_result(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    form = await request.form()
    payload = {str(key): str(value) for key, value in form.items()}
    try:
        verified = payment_adapter_from_settings(settings).verify_callback(payload)
    except (IntegrationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="付款結果驗證失敗") from exc

    attempt = await session.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.merchant_trade_no == verified["MerchantTradeNo"]
        )
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="找不到付款資料")
    if attempt.order_id is not None:
        query = urlencode({"order_id": attempt.order_id, "payment": "return"})
        location = "{}/orders?{}".format(
            settings.web_base_url.rstrip("/"),
            query,
        )
    else:
        query = urlencode(
            {
                "membership_charge_id": attempt.membership_charge_id,
                "payment": "return",
            }
        )
        location = "{}/membership?{}".format(
            settings.web_base_url.rstrip("/"),
            query,
        )
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


router = payments_router
