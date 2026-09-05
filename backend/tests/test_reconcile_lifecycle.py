import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
import app.main as main_module
from app.integrations.common import HTTPResponse
from app.integrations.email_sender import EmailSendResult
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository,
    create_payment_attempt,
    payment_adapter_from_settings,
)
from tests.support import make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order, payment_settings, signed_payment_callback


@pytest.mark.asyncio
async def test_lifespan_finishes_invoice_and_email_without_browser_traffic(database_session, monkeypatch):
    user, _product, order = await make_regular_order(database_session)
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
