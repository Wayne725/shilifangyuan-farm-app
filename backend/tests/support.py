from __future__ import annotations

import base64
import json

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from decimal import Decimal
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, make_token_pair
from app.config import Settings, get_settings
from app.database import get_session
from app.integrations.invoice import (
    InvoiceIssueRequest,
    PreparedInvoice,
    PreparedInvoiceLine,
)
from app.models import User


def tomorrow_meal_timeline(now: datetime) -> dict[str, datetime]:
    taipei = ZoneInfo("Asia/Taipei")
    tomorrow = now.astimezone(taipei).date() + timedelta(days=1)
    return {
        "ordering_starts_at": datetime.combine(tomorrow - timedelta(days=1), time.min, taipei).astimezone(timezone.utc),
        "ordering_ends_at": datetime.combine(tomorrow, time.min, taipei).astimezone(timezone.utc),
        "pickup_starts_at": datetime.combine(tomorrow, time(11), taipei).astimezone(timezone.utc),
        "pickup_ends_at": datetime.combine(tomorrow, time(13, 30), taipei).astimezone(timezone.utc),
    }


def fanyu_test_context() -> dict:
    return {
        "version": 1,
        "provider": "fanyu",
        "base_url": "https://webtest.einvoice.com.tw/einv",
        "company_id": "15989995",
        "seller_id": "15989995",
        "user_id": "15989995ADMIN",
    }


def make_test_settings(**overrides: Any) -> Settings:
    values = {
        "environment": "test",
        "pii_encryption_keys_json": json.dumps({"v1": base64.b64encode(b"t" * 32).decode()}),
        "app_base_url": "https://api.example.test",
        "web_base_url": "https://app.example.test",
        **overrides,
    }
    return Settings(_env_file=None, **values)


def auth_headers(user: User) -> dict[str, str]:
    token = make_token_pair(user)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def fast_password_hash(password: str) -> str:
    return hash_password(password, iterations=1_000)


def prepare_test_invoice(
    request: InvoiceIssueRequest,
    *,
    provider: str = "ecpay",
) -> PreparedInvoice:
    request.validate()
    total = sum(Decimal(line.amount) for line in request.items)
    return PreparedInvoice(
        provider=provider,
        relate_number=request.relate_number,
        buyer_type=request.buyer_type,
        provider_request=(
            {"orderID": request.relate_number, "sellerID": "15989995"}
            if provider == "fanyu" else {"RelateNumber": request.relate_number}
        ),
        sales_amount=total,
        tax_amount=Decimal("0"),
        total_amount=total,
        items=tuple(
            PreparedInvoiceLine(
                name=line.name,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=Decimal(line.unit_price),
                amount=Decimal(line.amount),
                tax_type=line.tax_type,
                sequence_number=index,
                remark=line.remark,
            )
            for index, line in enumerate(request.items, start=1)
        ),
    )


@asynccontextmanager
async def api_test_context(
    session: AsyncSession,
    routers: Sequence[APIRouter],
    *,
    settings: Settings | None = None,
    follow_redirects: bool = False,
) -> AsyncIterator[AsyncClient]:
    application = FastAPI()
    for router in routers:
        application.include_router(router)

    async def override_get_session():
        yield session

    application.dependency_overrides[get_session] = override_get_session
    if settings is not None:
        application.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
        follow_redirects=follow_redirects,
    ) as client:
        yield client
