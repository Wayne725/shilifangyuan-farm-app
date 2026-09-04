from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import Settings, get_settings
from ..database import get_session
from ..integrations.common import IntegrationError
from ..integrations.ecpay import (
    CheckoutForm,
    callback_event_key,
)
from ..integrations.payment_service import (
    PaymentApplicationError,
    SQLAlchemyPaymentCallbackRepository,
    create_membership_payment_attempt,
    create_payment_attempt,
    ensure_payment_runtime_enabled,
    payment_adapter_from_settings,
    payment_attempt_read,
    raygate_payment_adapter_from_settings,
)
from ..integrations.raygate import (
    RAYGATE_PAYMENT_PROVIDER,
    canonical_event_key as raygate_event_key,
)
from ..models import (
    MembershipCharge,
    Order,
    PaymentAttempt,
    PaymentStatus,
    User,
)
from ..rate_limit import PAYMENT_REFRESH_RULE, enforce


payments_router = APIRouter(tags=["payments"])
logger = logging.getLogger(__name__)


class PaymentAttemptResponse(BaseModel):
    id: str
    attempt_id: str
    payment_url: str
    status: str
    expires_at: datetime


async def _owned_payment_attempt(
    session: AsyncSession,
    attempt_id: str,
    user: User,
) -> PaymentAttempt:
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
    return attempt


def _payment_attempt_status(attempt: PaymentAttempt) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "status": attempt.status.value,
        "expires_at": attempt.expires_at,
        "order_id": attempt.order_id,
        "membership_charge_id": attempt.membership_charge_id,
    }


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _refresh_raygate_attempt(
    session: AsyncSession,
    settings: Settings,
    attempt: PaymentAttempt,
) -> dict[str, str]:
    adapter = raygate_payment_adapter_from_settings(settings)
    query_result = await adapter.query_order(attempt.merchant_trade_no)
    repository = SQLAlchemyPaymentCallbackRepository(session)
    await repository.apply_query_result(attempt, query_result)
    return query_result


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
            detail="線上付款目前尚未開啟；訂單已保留，可稍後從訂單中心續辦",
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
            detail="線上付款目前尚未開啟；繳款紀錄已保留，可稍後續辦",
        ) from exc


@payments_router.get("/v1/payment-attempts/{attempt_id}")
async def get_payment_attempt_status(
    attempt_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    attempt = await _owned_payment_attempt(session, attempt_id, user)
    return _payment_attempt_status(attempt)


@payments_router.post("/v1/payment-attempts/{attempt_id}/refresh")
async def refresh_payment_attempt_status(
    attempt_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    attempt = await _owned_payment_attempt(session, attempt_id, user)
    enforce(f"payment-refresh:attempt:{attempt_id}", PAYMENT_REFRESH_RULE)
    if (
        attempt.provider == RAYGATE_PAYMENT_PROVIDER
        and attempt.status
        in {
            PaymentStatus.PENDING,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }
    ):
        merchant_trade_no = attempt.merchant_trade_no
        try:
            await _refresh_raygate_attempt(session, settings, attempt)
        except (IntegrationError, PaymentApplicationError, ValueError) as exc:
            await session.rollback()
            logger.warning(
                "RayGate user refresh query unavailable "
                "merchant_trade_no=%s: %s",
                merchant_trade_no,
                exc,
            )
        attempt = await _owned_payment_attempt(session, attempt_id, user)
    response = _payment_attempt_status(attempt)
    retry_until = _aware(attempt.expires_at) + timedelta(
        hours=settings.raygate_payment_reconcile_hours
    )
    if (
        attempt.provider == RAYGATE_PAYMENT_PROVIDER
        and attempt.status
        in {
            PaymentStatus.PENDING,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }
        and datetime.now(timezone.utc) <= retry_until
    ):
        response["status"] = "confirming"
    return response


@payments_router.get(
    "/payments/{attempt_id}/checkout",
    response_class=HTMLResponse,
)
async def payment_checkout(
    attempt_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        ensure_payment_runtime_enabled(settings)
    except PaymentApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    if attempt.provider == RAYGATE_PAYMENT_PROVIDER:
        redirect_url = str(attempt.checkout_payload.get("redirect_url", ""))
        expected_prefix = "{}/calc/pay_encrypt/".format(
            settings.raygate_payment_base_url.rstrip("/")
        )
        if not redirect_url.startswith(expected_prefix):
            raise HTTPException(status_code=409, detail="雷門付款頁資料不完整")
        return RedirectResponse(
            redirect_url,
            status_code=status.HTTP_303_SEE_OTHER,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )
    if attempt.provider != "ecpay":
        raise HTTPException(status_code=409, detail="不支援的付款服務")
    fields = {
        key: str(value) for key, value in attempt.checkout_payload.items()
    }
    form = CheckoutForm(
        action_url=settings.ecpay_payment_aio_url,
        fields=fields,
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
        adapter = payment_adapter_from_settings(settings, "ecpay")
        repository = SQLAlchemyPaymentCallbackRepository(session)
        acknowledgement = await adapter.process_callback(payload, repository)
        return PlainTextResponse(
            acknowledgement,
            headers={"Cache-Control": "no-store"},
        )
    except (IntegrationError, PaymentApplicationError, ValueError) as exc:
        await session.rollback()
        logger.exception(
            "ECPay payment callback rejected merchant_trade_no=%s: %s",
            payload.get("MerchantTradeNo", "missing"),
            exc,
        )
        return PlainTextResponse(
            "0|ERROR",
            status_code=status.HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
        )


@payments_router.post(
    "/webhooks/raygate/payment",
    response_class=PlainTextResponse,
)
async def raygate_payment_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    content_length = request.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) > 4096:
        return PlainTextResponse(
            "ERROR",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            headers={"Cache-Control": "no-store"},
        )
    content_type = request.headers.get("Content-Type", "").split(";", 1)[0]
    if content_type.strip().lower() != "application/json":
        return PlainTextResponse(
            "ERROR",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            headers={"Cache-Control": "no-store"},
        )
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                return PlainTextResponse(
                    "ERROR",
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    headers={"Cache-Control": "no-store"},
                )
        envelope = json.loads(body)
        if not isinstance(envelope, dict):
            raise ValueError("RayGate callback body must be an object")
        adapter = raygate_payment_adapter_from_settings(settings)
        callback_result = adapter.verify_callback(
            envelope,
            request.headers.get("X-ePay-Identifier", ""),
        )
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        logger.warning("RayGate payment callback envelope rejected: %s", exc)
        return PlainTextResponse(
            "ERROR",
            status_code=status.HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
        )

    callback_payload = callback_result.to_canonical_payload()
    try:
        confirmed_payload = await adapter.query_order(
            callback_result.pos_order_number
        )
    except IntegrationError as exc:
        await session.rollback()
        logger.warning(
            "RayGate payment query unavailable pos_order_number=%s: %s",
            callback_result.pos_order_number,
            exc,
        )
        return PlainTextResponse(
            "RETRY",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Cache-Control": "no-store", "Retry-After": "30"},
        )

    comparison_keys = (
        "MerchantTradeNo",
        "TradeAmt",
        "TradeNo",
        "PaymentType",
        "RayGateStoreCode",
    )
    if any(
        callback_payload.get(key) != confirmed_payload.get(key)
        for key in comparison_keys
    ):
        await session.rollback()
        logger.error(
            "RayGate callback/query mismatch pos_order_number=%s",
            callback_result.pos_order_number,
        )
        return PlainTextResponse(
            "ERROR",
            status_code=status.HTTP_409_CONFLICT,
            headers={"Cache-Control": "no-store"},
        )
    try:
        repository = SQLAlchemyPaymentCallbackRepository(session)
        await repository.apply_raygate_payment_callback(
            raygate_event_key(confirmed_payload),
            confirmed_payload,
        )
    except (IntegrationError, PaymentApplicationError, ValueError) as exc:
        await session.rollback()
        logger.exception(
            "RayGate payment callback rejected pos_order_number=%s: %s",
            callback_result.pos_order_number,
            exc,
        )
        return PlainTextResponse(
            "ERROR",
            status_code=status.HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
        )
    return PlainTextResponse(
        "OK",
        headers={"Cache-Control": "no-store"},
    )


@payments_router.get(
    "/payments/raygate/result",
    response_class=RedirectResponse,
)
async def raygate_payment_result(
    attempt_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    attempt = await session.get(PaymentAttempt, attempt_id)
    if attempt is None or attempt.provider != RAYGATE_PAYMENT_PROVIDER:
        raise HTTPException(status_code=404, detail="找不到付款資料")
    enforce(f"payment-refresh:attempt:{attempt_id}", PAYMENT_REFRESH_RULE)
    merchant_trade_no = attempt.merchant_trade_no
    order_id = attempt.order_id
    membership_charge_id = attempt.membership_charge_id
    payment_state = attempt.status.value
    if attempt.status in {
        PaymentStatus.PENDING,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    }:
        try:
            query_result = await _refresh_raygate_attempt(
                session,
                settings,
                attempt,
            )
            await session.refresh(attempt)
            payment_state = attempt.status.value
        except (IntegrationError, PaymentApplicationError, ValueError) as exc:
            await session.rollback()
            logger.warning(
                "RayGate browser return query unavailable "
                "merchant_trade_no=%s: %s",
                merchant_trade_no,
                exc,
            )
            payment_state = "confirming"
        else:
            if query_result.get("PaymentDisposition") not in {"paid", "refunded"}:
                payment_state = "confirming"
    return RedirectResponse(
        _payment_result_location(
            settings,
            payment_state,
            order_id=order_id,
            membership_charge_id=membership_charge_id,
            attempt_id=attempt_id,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
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
        adapter = payment_adapter_from_settings(settings, "ecpay")
        verified = adapter.verify_callback(payload)
    except (IntegrationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="付款結果驗證失敗") from exc

    attempt = await session.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.merchant_trade_no == verified["MerchantTradeNo"]
        )
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="找不到付款資料")
    order_id = attempt.order_id
    membership_charge_id = attempt.membership_charge_id
    try:
        repository = SQLAlchemyPaymentCallbackRepository(session)
        await repository.apply_ecpay_payment_callback(
            callback_event_key(verified),
            verified,
        )
        await session.refresh(attempt)
        payment_state = attempt.status.value
    except (IntegrationError, PaymentApplicationError, ValueError) as exc:
        await session.rollback()
        logger.exception(
            "ECPay browser result could not be applied merchant_trade_no=%s: %s",
            verified.get("MerchantTradeNo", "missing"),
            exc,
        )
        payment_state = "confirming"
    return RedirectResponse(
        _payment_result_location(
            settings,
            payment_state,
            order_id=order_id,
            membership_charge_id=membership_charge_id,
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _payment_result_location(
    settings: Settings,
    payment_state: str,
    *,
    order_id: str | None,
    membership_charge_id: str | None,
    attempt_id: str | None = None,
) -> str:
    if order_id is not None:
        query = urlencode(
            {
                "order_id": order_id,
                "payment": payment_state,
                **({"attempt_id": attempt_id} if attempt_id else {}),
            }
        )
        return f"{settings.web_base_url.rstrip('/')}/orders?{query}"
    query = urlencode(
        {
            "membership_charge_id": membership_charge_id,
            "payment": payment_state,
            **({"attempt_id": attempt_id} if attempt_id else {}),
        }
    )
    return f"{settings.web_base_url.rstrip('/')}/account?{query}"


router = payments_router
