import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.jobs as jobs
from app.integrations.common import HTTPResponse
from app.integrations.email_sender import EmailSendResult
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.models import Invoice, InvoiceStatus, PaymentStatus, User
from app.routers.notifications import notifications_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_raygate import FakeRayGateRefundAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("method,target", [
    ("GET", "order"), ("GET", "payment"), ("POST", "refresh"), ("POST", "cancel"),
])
async def test_other_customer_cannot_read_or_mutate_order_payment(
    raygate_callback_context, method, target
):
    _client, session, order, attempt, _adapter, _result = raygate_callback_context
    other = User(email="other-audit@example.com", display_name="Other", password_hash="unused")
    session.add(other)
    await session.commit()
    paths = {
        "order": f"/v1/orders/{order.id}",
        "payment": f"/v1/payment-attempts/{attempt.id}",
        "refresh": f"/v1/payment-attempts/{attempt.id}/refresh",
        "cancel": f"/v1/orders/{order.id}/cancel",
    }
    async with api_test_context(
        session, [orders_router, payments_router], settings=make_test_settings()
    ) as client:
        response = await client.request(
            method, paths[target], headers=auth_headers(other), json={"reason": "audit"}
        )
    assert response.status_code == 404, response.text
    await session.refresh(order)
    assert order.payment_status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_verified_payment_invoice_mail_duplicate_and_refund_pipeline(
    raygate_callback_context, monkeypatch
):
    client, session, order, attempt, payment_adapter, _raw_result = raygate_callback_context
    order.contact_email = "audit-buyer@example.com"
    order.user.email = order.contact_email
    await session.commit()
    settings = make_test_settings(payment_provider="raygate", invoice_provider="fanyu")
    invoice_calls, mail_calls = [], []
    issued = False

    async def invoice_transport(url, envelope, _headers, _timeout):
        nonlocal issued
        path = url.rsplit("/", 1)[-1]
        invoice_calls.append(path)
        if path == "queryInvoice" and not issued:
            data = {"statusCode": "3", "statusDesc": "無資料", "respData": {}}
        else:
            assert path in {"queryInvoice", "openInvoice"}
            if path == "openInvoice":
                assert envelope["reqData"]["notifyEmail"] == order.contact_email
                assert envelope["reqData"]["carrierType"] == "EG0478"
                assert int(envelope["reqData"]["totalAmount"]) == order.amount_total
                issued = True
            data = {"statusCode": "0", "respData": {
                "invNo": "AB12345678", "invDate": "20260905",
                "invTime": "12:00:00", "randomNumber": "1234", "status": "0",
            }}
        return HTTPResponse(200, json.dumps(data), {})

    class EmailSender:
        async def send(self, message):
            mail_calls.append(message)
            return EmailSendResult(True, f"audit-mail-{len(mail_calls)}", 200, "mock")

    invoice_adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=invoice_transport)
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *args: payment_adapter)
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda *args: invoice_adapter)
    monkeypatch.setattr(jobs, "email_sender_from_settings", lambda *args: EmailSender())
    now = datetime.now(timezone.utc)
    report = await jobs.reconcile_once(session, settings, now=now)
    assert report.payment_attempts == 1
    assert report.outbox_failed == 0
    await session.refresh(order)
    assert order.payment_status == PaymentStatus.PAID

    assert (await jobs.reconcile_once(session, settings, now=now + timedelta(minutes=1))).outbox_failed == 0
    invoice = await session.scalar(select(Invoice).where(Invoice.order_id == order.id))
    assert invoice.status == InvoiceStatus.ISSUED
    assert invoice.invoice_number == "AB12345678"
    assert invoice_calls == ["queryInvoice", "openInvoice"]
    async with api_test_context(session, [notifications_router], settings=settings) as notification_client:
        notifications = await notification_client.get("/v1/notifications", headers=auth_headers(order.user))
    assert notifications.status_code == 200
    assert any(item["event_type"] == "invoice_issued" and "AB12345678" in item["body"] for item in notifications.json())
    await jobs.reconcile_once(session, settings, now=now + timedelta(minutes=2))
    assert [message.subject for message in mail_calls] == ["付款成功通知"]
    assert all("AB12345678" not in message.text_content for message in mail_calls)

    repeated = await client.post(
        f"/v1/payment-attempts/{attempt.id}/refresh", headers=auth_headers(order.user)
    )
    assert repeated.status_code == 200
    await jobs.reconcile_once(session, settings, now=now + timedelta(minutes=3))
    assert invoice_calls.count("openInvoice") == 1
    assert [message.subject for message in mail_calls] == ["付款成功通知"]

    refund_adapter = FakeRayGateRefundAdapter(await payment_adapter.query_order(attempt.merchant_trade_no))
    monkeypatch.setattr(jobs, "refund_adapter_from_settings", lambda *args: refund_adapter)
    async with api_test_context(session, [orders_router], settings=settings) as order_client:
        cancelled = await order_client.post(
            f"/v1/orders/{order.id}/cancel", json={"reason": "Isolated acceptance test"},
            headers=auth_headers(order.user),
        )
    assert cancelled.status_code == 200, cancelled.text
    await jobs.reconcile_once(session, settings, now=now + timedelta(minutes=4))
    await session.refresh(order)
    await session.refresh(invoice)
    assert order.payment_status == PaymentStatus.REFUNDED
    assert invoice.status == InvoiceStatus.VOID_PENDING
    assert len([call for call in refund_adapter.calls if call[0] == "refund"]) == 1
    # Automatic invoice voiding is deliberately not claimed by this test.
