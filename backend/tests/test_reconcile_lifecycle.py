import asyncio
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
import app.main as main_module
from app.integrations.common import HTTPResponse
from app.integrations.email_sender import EmailSendResult
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_service import enqueue_invoice_issue
from app.models import PaymentStatus
from tests.support import make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order


@pytest.mark.asyncio
async def test_lifespan_finishes_invoice_and_email_without_browser_traffic(database_session, monkeypatch):
    _user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.contact_email = "invoice-audit@example.com"
    enqueue_invoice_issue(database_session, order, trigger="verified_payment")
    await database_session.commit()
    delivered = asyncio.Event()
    issued = False

    async def transport(url, body, headers, timeout):
        nonlocal issued
        path = url.rsplit("/", 1)[-1]
        if path == "queryInvoice" and not issued:
            result = {"statusCode": "3", "respData": {}}
        else:
            assert path == "openInvoice"
            assert body["reqData"]["notifyEmail"] == "invoice-audit@example.com"
            issued = True
            result = {"statusCode": "0", "respData": {"invNo": "AB12345678", "invDate": "20260905"}}
        return HTTPResponse(200, json.dumps(result), {})

    class Sender:
        async def send(self, message):
            assert "AB12345678" in message.text_content
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
        await asyncio.wait_for(delivered.wait(), timeout=1)
    assert jobs.should_reconcile_now(minimum_interval_seconds=0)
