import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, call

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
import app.main as main_module
from app.config import get_settings
from app.database import get_session
from app.integrations.common import HTTPResponse
from app.integrations.email_sender import EmailSendResult
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository,
    create_payment_attempt,
    payment_adapter_from_settings,
)
from tests.support import fanyu_test_context, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order, payment_settings, signed_payment_callback


@pytest.mark.asyncio
@pytest.mark.parametrize("request_reconciliation_enabled", [True, False])
async def test_request_reconciliation_can_be_disabled(
    database_session, monkeypatch, request_reconciliation_enabled,
):
    monkeypatch.delenv("RECONCILIATION_ON_REQUEST_ENABLED", raising=False)
    if not request_reconciliation_enabled:
        monkeypatch.setenv("RECONCILIATION_ON_REQUEST_ENABLED", "false")
    settings = make_test_settings(environment="development", reconciliation_enabled=False)
    assert settings.reconciliation_on_request_enabled is request_reconciliation_enabled

    should_reconcile = Mock(return_value=True)
    schedule = Mock()
    reconcile_once = AsyncMock(return_value=jobs.ReconcileReport())
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "should_reconcile_now", should_reconcile)
    monkeypatch.setattr(main_module, "schedule_background_reconcile", schedule)
    monkeypatch.setattr(jobs, "reconcile_once", reconcile_once)
    application = main_module.create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_session] = lambda: database_session

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        platform = await client.get("/v1/platform")
        assert platform.status_code == 200
        should_reconcile.return_value = False
        assert (await client.get("/v1/platform")).status_code == 200
        registration = await client.post("/v1/auth/register", json={
            "email": "isolated-reconcile@example.com",
            "display_name": "隔離驗收",
            "password": "synthetic-password-123",
        })
        assert registration.status_code == 201
        assert (await client.post("/internal/reconcile")).status_code == 401
        explicit = await client.post(
            "/internal/reconcile", headers={"X-Reconcile-Secret": settings.internal_reconcile_secret},
        )
        assert explicit.status_code == 200
    reconcile_once.assert_awaited_once_with(database_session, settings, source="internal")
    if request_reconciliation_enabled:
        assert should_reconcile.call_count == 2
        assert schedule.call_args_list == [call(settings), call(settings, minimum_interval_seconds=0)]
    else:
        should_reconcile.assert_not_called()
        schedule.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_finishes_invoice_and_email_without_browser_traffic(database_session, monkeypatch):
    user, _product, order = await make_regular_order(database_session, invoice_context=fanyu_test_context())
    order.contact_email = "invoice-audit@example.com"
    now = datetime.now(timezone.utc)
    attempt = await create_payment_attempt(database_session, order.id, user, payment_settings(), now=now)
    callback = signed_payment_callback(attempt, now + timedelta(seconds=1))
    for _ in range(2):
        await payment_adapter_from_settings(payment_settings()).process_callback(
            callback, SQLAlchemyPaymentCallbackRepository(database_session),
        )
    delivered = asyncio.Event()
    issued = asyncio.Event()
    emails = []

    async def transport(url, body, headers, timeout):
        path = url.rsplit("/", 1)[-1]
        if path == "queryInvoice" and not issued.is_set():
            result = {"statusCode": "3", "respData": {}}
        else:
            assert path == "openInvoice"
            assert body["reqData"]["notifyEmail"] == "invoice-audit@example.com"
            issued.set()
            result = {"statusCode": "0", "respData": {"invNo": "AB12345678", "invDate": "20260905"}}
        return HTTPResponse(200, json.dumps(result), {})

    class Sender:
        async def send(self, message):
            emails.append(message)
            delivered.set()
            return EmailSendResult(True, "isolated-email", 200, "mock")

    settings = make_test_settings().model_copy(update={
        "environment": "production", "reconciliation_enabled": True,
        "reconciliation_interval_seconds": 0.01,
    })
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "check_database_connection", AsyncMock(return_value=False))
    monkeypatch.setattr(jobs, "SessionLocal", async_sessionmaker(database_session.bind, expire_on_commit=False))
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: FanyuInvoiceAdapter(fanyu_settings(), transport=transport))
    monkeypatch.setattr(jobs, "email_sender_from_settings", lambda _: Sender())
    async with main_module.lifespan(None):
        await asyncio.wait_for(asyncio.gather(delivered.wait(), issued.wait()), timeout=2)
    assert len(emails) == 1
    assert emails[0].subject == "付款成功通知"
    assert emails[0].to_email == "invoice-audit@example.com"
    assert "SLFTEST0001" in emails[0].text_content
    assert "AB12345678" not in emails[0].text_content
    assert jobs.should_reconcile_now(minimum_interval_seconds=0)
