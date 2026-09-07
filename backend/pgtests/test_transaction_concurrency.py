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


@pytest.mark.asyncio
async def test_concurrent_admin_void_and_retry_submit_only_once(postgres_sessions, monkeypatch):
    import app.routers.invoices as invoices
    from app.models import Invoice, InvoiceStatus, OutboxEvent, OutboxStatus, Refund, RefundStatus, User, UserRole
    from app.routers.operations import operations_router
    from tests.support import api_test_context, auth_headers

    async with postgres_sessions() as session:
        admin, _product, order = await make_regular_order(session)
        admin.user_role = UserRole.ADMIN
        order.payment_status = PaymentStatus.REFUNDED
        session.add(Invoice(order=order, relate_number="CONCURRENTVOID", provider="fanyu", status=InvoiceStatus.VOID_PENDING,
                            invoice_number="AB12345678", invoice_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
                            total_amount=200, provider_request={"sellerID": "15989995"}))
        session.add(Refund(order_id=order.id, amount=200, status=RefundStatus.COMPLETED, reason="隔離併發", requested_by_id=admin.id))
        event = OutboxEvent(event_type="send_email", aggregate_type="user", aggregate_id=admin.id, status=OutboxStatus.FAILED,
                            payload={"event_type": "refund_completed", "data": {"order_id": order.id}})
        session.add(event)
        await session.commit()
        admin_id, order_id, event_id = admin.id, order.id, event.id

    submitted = []
    cancelled = False

    async def transport(url, body, _headers, _timeout):
        nonlocal cancelled
        await asyncio.sleep(0.05)
        if url.endswith("/cancelInvoice"):
            submitted.append(body["reqData"])
            cancelled = True
            data = {"invNo": "AB12345678", "cancelDate": "20260906", "cancelTime": "11:00:00"}
        else:
            assert url.endswith("/queryInvoice")
            data = {"invNo": "AB12345678", "invDate": "20260905", "status": "1" if cancelled else "0"}
        return HTTPResponse(200, json.dumps({"statusCode": "0", "respData": data}), {})

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(invoices, "invoice_adapter_from_settings", lambda _: adapter)

    async def perform(path):
        async with postgres_sessions() as session:
            admin = await session.get(User, admin_id)
            async with api_test_context(session, [invoices.invoices_router, operations_router], settings=make_test_settings(invoice_provider="fanyu")) as client:
                response = await client.post(path, headers=auth_headers(admin), json={"reason": "隔離併發驗證"})
                return response.status_code

    results = await asyncio.gather(*(perform(f"/v1/admin/orders/{order_id}/invoice/void") for _ in range(5)))
    assert set(results) <= {200, 202}
    assert len(submitted) == 1
    retries = await asyncio.gather(*(perform(f"/v1/admin/failed-jobs/{event_id}/retry") for _ in range(5)))
    assert retries.count(200) == 1
    assert retries.count(409) == 4
