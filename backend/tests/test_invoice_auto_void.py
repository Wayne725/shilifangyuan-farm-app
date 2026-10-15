import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.jobs as jobs
from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_service import enqueue_invoice_adjustment_after_refund
from app.models import (
    AdminAudit,
    Invoice,
    InvoiceAllowance,
    InvoiceAllowanceStatus,
    InvoiceBuyerType,
    InvoiceStatus,
    Notification,
    Order,
    OutboxEvent,
    OutboxStatus,
    PaymentStatus,
    Refund,
    RefundStatus,
    UserRole,
)
from tests.support import fanyu_test_context, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order

pytestmark = pytest.mark.asyncio


async def _make_adjustment(
    session,
    *,
    provider="fanyu",
    amount=200,
    buyer_type=InvoiceBuyerType.PERSONAL,
    missing_number=False,
):
    admin, _product, order = await make_regular_order(
        session, invoice_context=fanyu_test_context()
    )
    admin.user_role = UserRole.ADMIN
    order.payment_status = PaymentStatus.REFUNDED
    order.invoice_status = InvoiceStatus.ISSUED
    invoice = Invoice(
        order=order,
        relate_number="AUTOVOIDTEST",
        provider=provider,
        status=InvoiceStatus.PENDING if missing_number else InvoiceStatus.ISSUED,
        invoice_number=None if missing_number else "AB12345678",
        invoice_date=None if missing_number else datetime(2026, 9, 5, tzinfo=timezone.utc),
        total_amount=200,
        buyer_type=buyer_type,
        buyer_tax_id="24536806" if buyer_type == InvoiceBuyerType.COMPANY else None,
        provider_request={"sellerID": "15989995"},
        provider_context=fanyu_test_context(),
    )
    refund = Refund(
        order_id=order.id,
        amount=amount,
        status=RefundStatus.COMPLETED,
        reason="取消訂單全額退款",
        requested_by_id=admin.id,
    )
    session.add_all([invoice, refund])
    await session.flush()
    assert enqueue_invoice_adjustment_after_refund(session, order, refund)
    await session.commit()
    event = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "invoice.adjustment_required"
        )
    )
    return admin, order, invoice, refund, event


def _provider_response(data):
    return HTTPResponse(
        200, json.dumps({"statusCode": "0", "respData": data}), {}
    )


async def _process(session):
    return await jobs._process_outbox(
        session,
        make_test_settings(invoice_provider="fanyu"),
        datetime.now(timezone.utc),
        limit=10,
    )


async def _retry_from_database(session, event_id):
    session.expunge_all()
    event = await session.get(OutboxEvent, event_id)
    event.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    session.expunge_all()
    return await _process(session)


@pytest.mark.parametrize(
    ("provider", "amount", "expected_auto_void"),
    [("fanyu", 200, True), ("fanyu", 100, False), ("ecpay", 200, False)],
)
async def test_only_new_full_fanyu_refunds_request_auto_void(
    database_session, provider, amount, expected_auto_void
):
    _admin, _order, _invoice, _refund, event = await _make_adjustment(
        database_session, provider=provider, amount=amount
    )
    assert bool(event.payload.get("auto_void")) is expected_auto_void
    assert event.payload["adjustment"] == ("void" if amount == 200 else "allowance")


@pytest.mark.parametrize("already_voided", [False, True])
@pytest.mark.parametrize("missing_number", [False, True])
@pytest.mark.parametrize(
    "buyer_type", [InvoiceBuyerType.PERSONAL, InvoiceBuyerType.COMPANY]
)
async def test_auto_void_queries_submits_and_confirms_refunded_invoice(
    database_session, monkeypatch, missing_number, buyer_type, already_voided
):
    admin, order, invoice, refund, event = await _make_adjustment(
        database_session, buyer_type=buyer_type, missing_number=missing_number
    )
    order_id, invoice_id, event_id = order.id, invoice.id, event.id
    admin_id, refund_id = admin.id, refund.id
    calls = []
    cancelled = already_voided

    async def transport(url, envelope, _headers, _timeout):
        nonlocal cancelled
        path = url.rsplit("/", 1)[-1]
        calls.append((path, envelope["reqData"]))
        if path == "cancelInvoice":
            assert calls[0][0] == "queryInvoice"
            cancelled = True
            return _provider_response({"invNo": "AB12345678"})
        assert path == "queryInvoice"
        return _provider_response(
            {"invNo": "AB12345678", "invDate": "20260905", "status": "1" if cancelled else "0"}
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: adapter)
    database_session.expunge_all()

    assert await _process(database_session) == (1, 0)
    database_session.expunge_all()
    saved_invoice = await database_session.get(Invoice, invoice_id)
    saved_order = await database_session.get(Order, order_id)
    saved_event = await database_session.get(OutboxEvent, event_id)
    assert saved_invoice.status == saved_order.invoice_status == InvoiceStatus.VOIDED
    assert saved_invoice.provider_status == "1"
    assert saved_invoice.invoice_number == "AB12345678"
    assert saved_invoice.voided_at is not None
    assert saved_event.status == OutboxStatus.COMPLETED
    audit = await database_session.scalar(
        select(AdminAudit).where(
            AdminAudit.aggregate_id == order_id,
            AdminAudit.action == "invoice.void_checked",
        )
    )
    assert audit is not None
    assert audit.actor_id == admin_id
    assert audit.data["trigger"] == "automatic_refund"
    assert audit.data["refund_id"] == refund_id
    if already_voided:
        assert [path for path, _ in calls] == ["queryInvoice"]
    else:
        assert [path for path, _ in calls] == ["queryInvoice", "cancelInvoice", "queryInvoice"]
        cancel_request = calls[1][1]
        assert cancel_request["orderID"] == "AUTOVOIDTEST"
        assert cancel_request["process_type"] == ("B" if buyer_type == InvoiceBuyerType.COMPANY else "C")
        assert cancel_request["buyerID"] == ("24536806" if buyer_type == InvoiceBuyerType.COMPANY else "00000000")
        assert cancel_request["invNo"] == "AB12345678"
        assert cancel_request["invDate"] == "20260905"


async def test_preflight_query_timeout_can_recover_and_submit_void_once(
    database_session, monkeypatch
):
    _admin, _order, invoice, _refund, event = await _make_adjustment(database_session)
    invoice_id, event_id = invoice.id, event.id
    calls = []
    query_available = False
    cancelled = False

    async def transport(url, _envelope, _headers, _timeout):
        nonlocal cancelled
        path = url.rsplit("/", 1)[-1]
        calls.append(path)
        if path == "cancelInvoice":
            cancelled = True
            return _provider_response({"invNo": "AB12345678"})
        assert path == "queryInvoice"
        if not query_available:
            raise TimeoutError("isolated preflight query timeout")
        return _provider_response(
            {"invNo": "AB12345678", "invDate": "20260905", "status": "1" if cancelled else "0"}
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: adapter)
    assert await _process(database_session) == (0, 1)
    database_session.expunge_all()
    saved_invoice = await database_session.get(Invoice, invoice_id)
    saved_event = await database_session.get(OutboxEvent, event_id)
    assert saved_invoice.status == InvoiceStatus.VOID_PENDING
    assert saved_invoice.void_source != "api_submission_started"
    assert saved_event.status == OutboxStatus.PENDING
    assert calls == ["queryInvoice"]

    query_available = True
    assert await _retry_from_database(database_session, event_id) == (1, 0)
    database_session.expunge_all()
    saved_invoice = await database_session.get(Invoice, invoice_id)
    saved_event = await database_session.get(OutboxEvent, event_id)
    assert saved_invoice.status == InvoiceStatus.VOIDED
    assert saved_event.status == OutboxStatus.COMPLETED
    assert calls == ["queryInvoice", "queryInvoice", "cancelInvoice", "queryInvoice"]


@pytest.mark.parametrize("timeout", [False, True])
async def test_unconfirmed_auto_void_persists_query_only_across_retries(
    database_session, monkeypatch, timeout
):
    _admin, order, invoice, _refund, event = await _make_adjustment(database_session)
    order_id, invoice_id, event_id = order.id, invoice.id, event.id
    cancel_calls = []
    confirmed = False

    async def transport(url, envelope, _headers, _timeout):
        if url.endswith("/cancelInvoice"):
            cancel_calls.append(envelope["reqData"])
            if timeout:
                raise TimeoutError("isolated provider timeout")
            return _provider_response({"invNo": "AB12345678"})
        assert url.endswith("/queryInvoice")
        return _provider_response(
            {"invNo": "AB12345678", "invDate": "20260905", "status": "1" if confirmed else "0"}
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: adapter)
    assert await _process(database_session) == (0, 1)
    database_session.expunge_all()
    saved_invoice = await database_session.get(Invoice, invoice_id)
    saved_order = await database_session.get(Order, order_id)
    saved_event = await database_session.get(OutboxEvent, event_id)
    assert saved_invoice.status == saved_order.invoice_status == InvoiceStatus.VOID_PENDING
    assert saved_event.status == OutboxStatus.PENDING
    assert saved_event.attempts == 1
    assert saved_event.last_error
    assert saved_event.available_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    assert len(cancel_calls) == 1

    assert await _retry_from_database(database_session, event_id) == (0, 1)
    assert len(cancel_calls) == 1

    confirmed = True
    assert await _retry_from_database(database_session, event_id) == (1, 0)
    database_session.expunge_all()
    saved_invoice = await database_session.get(Invoice, invoice_id)
    saved_event = await database_session.get(OutboxEvent, event_id)
    assert saved_invoice.status == InvoiceStatus.VOIDED
    assert saved_event.status == OutboxStatus.COMPLETED
    assert saved_event.attempts == 3
    assert len(cancel_calls) == 1


@pytest.mark.parametrize(
    "case",
    ["not_refunded", "refund_pending", "partial_refund", "wrong_seller", "wrong_context", "allowance", "not_found", "wrong_number", "missing_date"],
)
async def test_auto_void_never_submits_when_refund_or_invoice_is_unsafe(
    database_session, monkeypatch, case
):
    _admin, order, invoice, refund, _event = await _make_adjustment(database_session)
    invoice_id = invoice.id
    if case == "not_refunded":
        order.payment_status = PaymentStatus.PAID
    elif case == "refund_pending":
        refund.status = RefundStatus.PENDING
    elif case == "partial_refund":
        refund.amount = 100
    elif case == "wrong_seller":
        invoice.provider_request = {"sellerID": "wrong"}
    elif case == "wrong_context":
        invoice.provider_context = {**fanyu_test_context(), "seller_id": "wrong"}
    elif case == "allowance":
        database_session.add(
            InvoiceAllowance(
                invoice_id=invoice_id,
                sales_return_number="ALLOWANCE-TEST",
                status=InvoiceAllowanceStatus.ISSUED,
                sales_amount=100,
                total_amount=100,
                reason="已折讓",
            )
        )
    await database_session.commit()
    calls = []

    async def transport(url, _envelope, _headers, _timeout):
        calls.append(url)
        assert url.endswith("/queryInvoice"), "Unsafe request attempted to cancel invoice"
        return _provider_response(
            {} if case == "not_found" else {
                "invNo": "XY87654321" if case == "wrong_number" else "AB12345678",
                "invDate": "" if case == "missing_date" else "20260905",
                "status": "0",
            }
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: adapter)
    database_session.expunge_all()
    assert await _process(database_session) == (0, 1)
    database_session.expunge_all()
    saved_invoice = await database_session.get(Invoice, invoice_id)
    assert saved_invoice.status == InvoiceStatus.VOID_PENDING
    assert saved_invoice.invoice_number == "AB12345678"
    assert all(url.endswith("/queryInvoice") for url in calls)


@pytest.mark.parametrize("case", ["legacy", "partial_refund", "other_provider"])
async def test_non_automatic_adjustments_keep_admin_notification_only(
    database_session, monkeypatch, case
):
    admin, _order, _invoice, _refund, event = await _make_adjustment(
        database_session,
        amount=100 if case == "partial_refund" else 200,
        provider="ecpay" if case == "other_provider" else "fanyu",
    )
    admin_id, event_id = admin.id, event.id
    if case == "legacy":
        event.payload = {key: value for key, value in event.payload.items() if key != "auto_void"}
        await database_session.commit()

    def unexpected_adapter(_settings):
        pytest.fail("Historical or manual adjustment must not call invoice provider")

    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", unexpected_adapter)
    assert await _process(database_session) == (1, 0)
    database_session.expunge_all()
    saved_event = await database_session.get(OutboxEvent, event_id)
    notification = await database_session.scalar(
        select(Notification).where(
            Notification.user_id == admin_id,
            Notification.event_type == "invoice.adjustment_required",
        )
    )
    assert saved_event.status == OutboxStatus.COMPLETED
    assert notification is not None


async def test_exhausted_auto_void_notifies_admin_without_resubmitting(
    database_session, monkeypatch
):
    admin, _order, invoice, _refund, event = await _make_adjustment(database_session)
    admin_id, invoice_id, event_id = admin.id, invoice.id, event.id
    invoice.void_source = "api_submission_started"
    event.attempts = 7
    event.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await database_session.commit()
    calls = []

    async def transport(url, _envelope, _headers, _timeout):
        calls.append(url)
        assert url.endswith("/queryInvoice"), "Previously submitted void must not be resent"
        return _provider_response(
            {"invNo": "AB12345678", "invDate": "20260905", "status": "0"}
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: adapter)
    database_session.expunge_all()
    assert await _process(database_session) == (0, 1)
    database_session.expunge_all()
    saved_event = await database_session.get(OutboxEvent, event_id)
    saved_invoice = await database_session.get(Invoice, invoice_id)
    notification = await database_session.scalar(
        select(Notification).where(
            Notification.user_id == admin_id,
            Notification.event_type == "invoice_void_manual_review_required",
        )
    )
    assert saved_event.status == OutboxStatus.FAILED
    assert saved_event.attempts == 8
    assert saved_invoice.status == InvoiceStatus.VOID_PENDING
    assert notification is not None
    assert notification.data["invoice_id"] == invoice_id
    assert notification.data["outbox_event_id"] == event_id
    assert calls
    assert all(url.endswith("/queryInvoice") for url in calls)
