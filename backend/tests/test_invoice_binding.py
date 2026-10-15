import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_service import (
    InvoiceApplicationError,
    issue_paid_order_invoice,
    reconcile_order_invoice,
)
from app.models import AdminAudit, Invoice, InvoiceStatus, OutboxEvent, PaymentStatus, Refund, RefundStatus, UserRole
from app.routers.invoices import invoices_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order


def account_context(settings):
    return {
        "version": 1,
        "provider": "fanyu",
        "base_url": settings.base_url.rstrip("/"),
        "company_id": settings.company_id,
        "seller_id": settings.seller_id,
        "user_id": settings.user_id,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', [issue_paid_order_invoice, reconcile_order_invoice])
@pytest.mark.parametrize('has_invoice', [False, True])
async def test_api01_cannot_replay_web2_order_or_invoice(database_session, operation, has_invoice):
    _, _, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    original = account_context(fanyu_settings(base_url='https://web2.einvoice.com.tw/einv'))
    order.invoice_provider_context = original
    if has_invoice:
        database_session.add(Invoice(
            order=order, provider='fanyu', relate_number='WEB2BEFOREAPI01',
            status=InvoiceStatus.PENDING, provider_context=original,
            provider_request={'sellerID': '15989995'},
        ))
    await database_session.commit()

    async def no_transport(*_args):
        pytest.fail('舊 web2 工作不得改送 api01')

    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url='https://api01.einvoice.com.tw/einv'), no_transport)
    with pytest.raises(InvoiceApplicationError, match='環境.*帳戶'):
        await operation(database_session, order.id, adapter)
    assert order.invoice_provider_context == original


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [issue_paid_order_invoice, reconcile_order_invoice])
@pytest.mark.parametrize("has_invoice", [False, True])
async def test_legacy_unbound_records_never_contact_current_fanyu(
    database_session, operation, has_invoice,
):
    _, _, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.invoice_provider_context = None
    if has_invoice:
        database_session.add(Invoice(
            order=order, provider="fanyu", relate_number="LEGACYUNBOUND",
            status=InvoiceStatus.PENDING,
            provider_request={"sellerID": "15989995"},
        ))
    await database_session.commit()
    calls = []

    async def transport(url, *_args):
        calls.append(url)
        return HTTPResponse(200, json.dumps({
            "statusCode": "0", "respData": {
                "invNo": "AB12345678", "invDate": "20260909", "status": "0",
            },
        }), {})

    with pytest.raises(InvoiceApplicationError, match="環境.*帳戶"):
        await operation(database_session, order.id, FanyuInvoiceAdapter(fanyu_settings(), transport))
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["query", "void"])
@pytest.mark.parametrize("case", ["legacy", "wrong_host", "wrong_user"])
async def test_admin_blocked_query_and_void_keep_reason_without_contacting_provider(
    database_session, monkeypatch, action, case,
):
    import app.routers.invoices as routes

    admin, _, order = await make_regular_order(database_session)
    admin.user_role = UserRole.ADMIN
    order.payment_status = PaymentStatus.REFUNDED
    context = account_context(fanyu_settings())
    if case == "wrong_host":
        context["base_url"] = "https://web2.einvoice.com.tw/einv"
    if case == "wrong_user":
        context["user_id"] = "OTHERADMIN"
    order.invoice_provider_context = None if case == "legacy" else context
    database_session.add_all([
        Invoice(
            order=order, provider="fanyu", relate_number="ADMINBINDING",
            provider_context=None if case == "legacy" else context,
            provider_request={"sellerID": "15989995"},
            status=InvoiceStatus.VOID_PENDING, invoice_number="AB12345678",
            invoice_date=datetime(2026, 9, 9, tzinfo=timezone.utc),
        ),
        Refund(order_id=order.id, amount=200, status=RefundStatus.COMPLETED, reason="測試退款", requested_by_id=admin.id),
    ])
    await database_session.commit()
    order_id = order.id

    async def transport(*_args):
        pytest.fail("管理端不得用新帳戶查詢或作廢舊發票")

    monkeypatch.setattr(routes, "invoice_adapter_from_settings", lambda _: FanyuInvoiceAdapter(fanyu_settings(), transport))
    async with api_test_context(database_session, [invoices_router], settings=make_test_settings(invoice_provider="fanyu")) as client:
        response = await client.post(
            f"/v1/admin/orders/{order_id}/invoice/{action}",
            headers=auth_headers(admin), json={"reason": "核對舊帳戶"},
        )
    assert response.status_code == 409, response.text
    audit = await database_session.scalar(select(AdminAudit).where(
        AdminAudit.action == f"invoice.{action}_blocked", AdminAudit.aggregate_id == order_id,
    ))
    assert audit is not None
    assert audit.reason == "核對舊帳戶"
    assert audit.data == {"error_type": "InvoiceApplicationError"}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [issue_paid_order_invoice, reconcile_order_invoice])
@pytest.mark.parametrize("has_invoice", [False, True])
@pytest.mark.parametrize("change", [
    {"base_url": "https://api01.einvoice.com.tw/einv"},
    {"base_url": "https://web2.einvoice.com.tw/einv"},
    {"base_url": "https://web.einvoice.com.tw/einv"},
    {"company_id": "12345675"},
    {"seller_id": "12345675"},
    {"user_id": "OTHERADMIN"},
])
async def test_switching_environment_or_account_cannot_reuse_old_intent(
    database_session, operation, has_invoice, change,
):
    _, _, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    original = account_context(fanyu_settings())
    order.invoice_provider_context = original
    if has_invoice:
        database_session.add(Invoice(
            order=order, provider="fanyu", relate_number="OLDBOUND",
            status=InvoiceStatus.PENDING, provider_context=original,
            provider_request={"sellerID": "15989995"},
        ))
    await database_session.commit()

    async def transport(*_args):
        pytest.fail("不同環境／帳戶不得接觸供應商")

    adapter = FanyuInvoiceAdapter(fanyu_settings(**change), transport)
    with pytest.raises(InvoiceApplicationError, match="環境.*帳戶"):
        await operation(database_session, order.id, adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", [
    "https://api01.einvoice.com.tw/einv",
    "https://webtest.einvoice.com.tw/einv",
    "https://web.einvoice.com.tw/einv",
    "https://web2.einvoice.com.tw/einv",
])
async def test_new_bound_invoice_survives_key_rotation_without_changing_provider_payload(
    database_session, base_url,
):
    _, _, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    settings = fanyu_settings(base_url=base_url)
    context = account_context(settings)
    order.invoice_provider_context = context
    await database_session.commit()
    calls = []
    issued = False

    async def transport(url, envelope, *_args):
        nonlocal issued
        calls.append(url.rsplit("/", 1)[-1])
        data = envelope["reqData"]
        assert "provider_context" not in data
        assert "base_url" not in data
        assert envelope["companyID"] == settings.company_id
        assert data["sellerID"] == settings.seller_id
        if url.endswith("/openInvoice"):
            assert order.invoice.provider_context == context
            assert data["carrierType"] == "EG0478"
            assert data["carrierID1"] == data["carrierID2"] == order.contact_email
            assert data["printMark"] == "N"
            assert data["Details"][0]["sequenceNumber"] == ("001" if any(host in base_url for host in ("api01.", "web2.")) else "0001")
            issued = True
        return HTTPResponse(200, json.dumps({
            "statusCode": "0" if issued else "3",
            "statusDesc": "" if issued else "查無資料",
            "respData": {"invNo": "AB12345678", "invDate": "20260909", "status": "0"} if issued else {},
        }), {})

    result = await issue_paid_order_invoice(database_session, order.id, FanyuInvoiceAdapter(settings, transport))
    assert result.invoice_number == "AB12345678"
    assert order.invoice.provider_context == context
    assert "test-api-key" not in json.dumps(context)
    rotated = replace(settings, auth_password="rotated-password", api_key="rotated-key", base_url=base_url.replace("/einv", ":443/einv/"))
    # 同帳戶換金鑰及等效網址不應被誤判為跨帳戶；仍只補查，不重開。
    await reconcile_order_invoice(database_session, order.id, FanyuInvoiceAdapter(rotated, transport))
    assert calls == ["queryInvoice", "openInvoice", "queryInvoice"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["invoice_unbound", "invoice_wrong_host", "seller_snapshot_mismatch", "order_unbound"])
@pytest.mark.parametrize("operation", [issue_paid_order_invoice, reconcile_order_invoice])
async def test_invoice_and_order_bindings_must_both_match(database_session, case, operation):
    _, _, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    context = account_context(fanyu_settings())
    order.invoice_provider_context = None if case == "order_unbound" else context
    invoice_context = dict(context)
    if case == "invoice_wrong_host":
        invoice_context["base_url"] = "https://web2.einvoice.com.tw/einv"
    database_session.add(Invoice(
        order=order, provider="fanyu", relate_number="MISMATCHEDINTENT",
        status=InvoiceStatus.PENDING,
        provider_context=None if case == "invoice_unbound" else invoice_context,
        provider_request={"sellerID": "12345675" if case == "seller_snapshot_mismatch" else "15989995"},
    ))
    await database_session.commit()

    async def transport(*_args):
        pytest.fail("不完整或不一致的綁定不得接觸供應商")

    with pytest.raises(InvoiceApplicationError, match="環境.*帳戶"):
        await operation(database_session, order.id, FanyuInvoiceAdapter(fanyu_settings(), transport))


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("current_base_url", ["https://web2.einvoice.com.tw/einv", "https://api01.einvoice.com.tw/einv"])
async def test_background_job_reports_binding_error_without_opening_or_notifying(
    database_session, monkeypatch, legacy, current_base_url,
):
    import app.jobs as jobs

    _, _, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.invoice_provider_context = None if legacy else account_context(fanyu_settings())
    event = OutboxEvent(
        event_type="invoice.issue_requested", aggregate_type="order",
        aggregate_id=order.id, payload={"order_id": order.id},
    )
    database_session.add(event)
    await database_session.commit()

    async def transport(*_args):
        pytest.fail("舊工作不可送至新正式環境")

    class NoEmail:
        async def send(self, _message):
            pytest.fail("被阻擋的開票不得發送成功通知")

    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: FanyuInvoiceAdapter(
        fanyu_settings(base_url=current_base_url), transport,
    ))
    monkeypatch.setattr(jobs, "email_sender_from_settings", lambda _: NoEmail())
    report = await jobs.reconcile_once(database_session, make_test_settings())
    await database_session.refresh(event)
    assert report.outbox_failed == 1
    assert "環境／帳戶" in event.last_error
    assert "test-api-key" not in event.last_error
    assert await database_session.scalar(select(Invoice).where(Invoice.order_id == event.aggregate_id)) is None
