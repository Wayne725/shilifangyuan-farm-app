"""Run only against the disposable local PostgreSQL CI database."""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.jobs as jobs
from app.database import Base
from app.integrations.common import HTTPResponse
from app.integrations.email_sender import EmailSendResult
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository,
    create_payment_attempt,
    payment_adapter_from_settings,
)
from app.models import PaymentAttempt, PaymentStatus
from tests.support import make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order, payment_settings, signed_payment_callback


@pytest.fixture
async def postgres_sessions():
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    if url.host not in {"localhost", "127.0.0.1"} or url.database != "shilifangyuan_test":
        pytest.fail("Only the disposable local shilifangyuan_test database is allowed")
    schema = f"acceptance_{uuid4().hex}"
    admin_engine = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_workers_do_not_repeat_payment_query_invoice_or_email(postgres_sessions, monkeypatch):
    async with postgres_sessions() as session:
        buyer, _product, order = await make_regular_order(session)
        order.contact_email = "isolated-ci@example.com"
        now = datetime.now(timezone.utc)
        attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=now)
        callback = signed_payment_callback(attempt, now + timedelta(seconds=1))
        await payment_adapter_from_settings(payment_settings()).process_callback(
            callback, SQLAlchemyPaymentCallbackRepository(session),
        )
        session.add(PaymentAttempt(
            order_id=order.id, provider="raygate", merchant_trade_no="CI-POLL-ONCE",
            amount=order.amount_total, status=PaymentStatus.EXPIRED,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        ))
        await session.commit()

    payment_queries = []
    invoice_requests = []
    emails = []
    issued = False

    class PaymentProvider:
        async def query_order(self, trade_no):
            payment_queries.append(trade_no)
            await asyncio.sleep(0.05)
            return {"TradeStatus": "0", "PaymentDisposition": "pending"}

    async def invoice_transport(url, body, _headers, _timeout):
        nonlocal issued
        path = url.rsplit("/", 1)[-1]
        invoice_requests.append(path)
        await asyncio.sleep(0.05)
        if path == "queryInvoice" and not issued:
            result = {"statusCode": "3", "respData": {}}
        else:
            assert path in {"openInvoice", "queryInvoice"}
            if path == "openInvoice":
                assert not issued, "The same invoice must not be issued twice"
                assert body["reqData"]["notifyEmail"] == "isolated-ci@example.com"
                issued = True
            result = {"statusCode": "0", "respData": {"invNo": "AB12345678", "invDate": "20260905", "status": "0"}}
        return HTTPResponse(200, json.dumps(result), {})

    class EmailProvider:
        async def send(self, message):
            emails.append(message)
            await asyncio.sleep(0.05)
            return EmailSendResult(True, "isolated-ci", 200, "mock")

    settings = make_test_settings(payment_provider="raygate", invoice_provider="fanyu")
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *args: PaymentProvider())
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: FanyuInvoiceAdapter(fanyu_settings(), transport=invoice_transport))
    monkeypatch.setattr(jobs, "email_sender_from_settings", lambda _: EmailProvider())

    async def run_worker():
        async with postgres_sessions() as session:
            return await jobs.reconcile_once(session, settings)

    reports = await asyncio.gather(*(run_worker() for _ in range(5)))
    reports.extend(await asyncio.gather(*(run_worker() for _ in range(5))))
    assert all(report.outbox_failed == 0 for report in reports)
    assert payment_queries == ["CI-POLL-ONCE"]
    assert invoice_requests == ["queryInvoice", "openInvoice"]
    assert len(emails) == 1
    assert emails[0].subject == "付款成功通知"
    assert emails[0].to_email == "isolated-ci@example.com"
    assert "SLFTEST0001" in emails[0].text_content
    assert "AB12345678" not in emails[0].text_content
