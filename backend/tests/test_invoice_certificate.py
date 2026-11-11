import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.integrations.common import HTTPResponse, IntegrationResponseError
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_certificate import retrieve_fanyu_invoice_certificate
from app.integrations.invoice_service import InvoiceApplicationError
from app.models import (
    Invoice, InvoiceBuyerType, InvoiceStatus, MembershipType, OutboxEvent, PaymentMethod, PaymentStatus,
    PrintJob, User, WalkInSale,
)
from app.recovery_quarantine import RecoveryHoldActive, begin_cutover
from app.routers import walk_in_sales
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order
from tests.test_recovery_quarantine import admin_user


NUMBER = "AB12345678"
OTHER_NUMBER = "XY87654321"
DAY = "20260910"
SELLER = "15989995"
BARCODE = f"11510{NUMBER}1234"
QR_LEFT = f"{NUMBER}11509101234000000000000006400000000{SELLER}{'A' * 24}:**********:1:1:1:餐盒:1:100"
QR_RIGHT = "**"
PDF_FIXTURE = b"%PDF-1.7\n%%EOF\n"


async def issued_invoice(session, adapter, buyer_type=InvoiceBuyerType.PERSONAL):
    _, _, order = await make_regular_order(session)
    order.invoice_status = InvoiceStatus.ISSUED
    order.invoice_provider_context = adapter.binding_context
    order.invoice_buyer_type = buyer_type
    invoice = Invoice(
        order=order, provider="fanyu", relate_number="ORIGINALTICKET",
        invoice_number=NUMBER, invoice_date=datetime(2026, 9, 9, 16, 8, tzinfo=timezone.utc),
        random_number="1234", status=InvoiceStatus.ISSUED, buyer_type=buyer_type,
        provider_context=adapter.binding_context, provider_request={"sellerID": SELLER},
        provider_response={"keep": True},
    )
    session.add(invoice)
    await session.commit()
    return order, invoice


def provider_transport(calls, *, override=None):
    responses = {
        "queryInvoice": {"invNo": NUMBER, "invDate": DAY, "randomNumber": "1234", "status": "0"},
        "getInvoicePrintCode": {
            "invNo": NUMBER, "barcode": f"*{BARCODE}*", "leftQrcode": QR_LEFT, "rightQrcode": QR_RIGHT,
        },
        "getInvoiceB2CPDF": {"b2c_PDF": base64.b64encode(PDF_FIXTURE).decode()},
    }
    if override:
        method, fields = override
        responses[method].update(fields)

    async def transport(url, envelope, _headers, _timeout):
        method = url.rsplit("/", 1)[-1]
        calls.append((method, envelope["reqData"]))
        return HTTPResponse(200, json.dumps({"statusCode": "0", "respData": responses[method]}), {})

    return transport


@pytest.mark.parametrize("buyer_type", list(InvoiceBuyerType))
async def test_fetches_original_codes_and_supported_document_without_changing_records(database_session, buyer_type):
    calls = []
    adapter = FanyuInvoiceAdapter(fanyu_settings(recovery_mode=True), provider_transport(calls))
    order, invoice = await issued_invoice(database_session, adapter, buyer_type)

    document = await retrieve_fanyu_invoice_certificate(database_session, order.id, adapter)

    process_type = "C" if buyer_type == InvoiceBuyerType.PERSONAL else "B"
    assert calls[:2] == [
        ("queryInvoice", {"orderID": "ORIGINALTICKET", "sellerID": SELLER, "process_type": process_type}),
        ("getInvoicePrintCode", {"invNo": NUMBER, "invDate": DAY, "sellerID": SELLER, "processType": process_type}),
    ]
    assert document.codes.barcode == BARCODE
    assert document.codes.left_qrcode == QR_LEFT and document.codes.right_qrcode == QR_RIGHT
    if buyer_type == InvoiceBuyerType.PERSONAL:
        assert calls[2:] == [("getInvoiceB2CPDF", {"invNo": NUMBER, "invDate": DAY, "sellerID": SELLER, "ranNo": "1234"})]
        assert document.thermal_pdf == PDF_FIXTURE
    else:
        assert len(calls) == 2
        assert document.thermal_pdf is None
    assert invoice.provider_response == {"keep": True}
    assert invoice.status == InvoiceStatus.ISSUED
    assert not database_session.dirty and not database_session.new
    assert not list(await database_session.scalars(select(OutboxEvent)))
    assert not list(await database_session.scalars(select(PrintJob)))


@pytest.mark.parametrize("output", ["pdf", "codes"])
async def test_admin_can_read_original_walk_in_certificate_without_enqueuing_or_sending(
    database_session, monkeypatch, output,
):
    calls = []
    adapter = FanyuInvoiceAdapter(fanyu_settings(), provider_transport(calls))
    order, _invoice = await issued_invoice(database_session, adapter)
    admin = await admin_user(database_session)
    buyer = await database_session.get(User, order.user_id)
    monkeypatch.setattr(walk_in_sales, "invoice_adapter_from_settings", lambda _: adapter)
    path = f"/v1/admin/walk-in-sales/{order.id}/invoice-certificate?output={output}"

    async with api_test_context(database_session, [walk_in_sales.walk_in_sales_router],
                                settings=make_test_settings(invoice_provider="fanyu")) as client:
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers=auth_headers(buyer))).status_code == 403
        assert (await client.get(path, headers=auth_headers(admin))).status_code == 404
        assert calls == []

        order.customer_kind, order.user_id = "walk_in", None
        order.payment_method = PaymentMethod.CASH_ON_PICKUP
        order.payment_status = PaymentStatus.PAID
        order.membership_type_snapshot = MembershipType.NONMEMBER
        database_session.add(WalkInSale(order_id=order.id, operator_id=admin.id,
                                       client_request_id=str(uuid4()), request_fingerprint="a" * 64))
        await database_session.commit()
        response = await client.get(path, headers=auth_headers(admin))

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    expected_calls = ["queryInvoice", "getInvoicePrintCode"]
    if output == "pdf":
        assert response.content == PDF_FIXTURE
        assert response.headers["Content-Type"] == "application/pdf"
        assert response.headers["Content-Disposition"] == f'attachment; filename="{NUMBER}.pdf"'
        expected_calls.append("getInvoiceB2CPDF")
    else:
        assert response.json() == {"invoice_number": NUMBER, "barcode": BARCODE,
                                   "left_qrcode": QR_LEFT, "right_qrcode": QR_RIGHT}
    assert [name for name, _ in calls] == expected_calls
    assert not database_session.dirty and not database_session.new
    assert not list(await database_session.scalars(select(OutboxEvent)))
    assert not list(await database_session.scalars(select(PrintJob)))


@pytest.mark.parametrize("case", ["missing", "voided", "wrong_account", "wrong_seller", "held"])
async def test_refuses_local_invoice_mismatch_before_contacting_provider(database_session, case):
    calls = []
    adapter = FanyuInvoiceAdapter(fanyu_settings(), provider_transport(calls))
    order, invoice = await issued_invoice(database_session, adapter)
    expected_error = InvoiceApplicationError
    if case == "missing":
        await database_session.delete(invoice)
    elif case == "voided":
        invoice.status = InvoiceStatus.VOIDED
    elif case == "wrong_account":
        adapter = FanyuInvoiceAdapter(fanyu_settings(user_id="another-user"), provider_transport(calls))
    elif case == "wrong_seller":
        invoice.provider_request = {"sellerID": "00000000"}
    else:
        await begin_cutover(database_session, await admin_user(database_session), uuid4(), "a" * 64)
        expected_error = RecoveryHoldActive
    await database_session.commit()

    with pytest.raises(expected_error):
        await retrieve_fanyu_invoice_certificate(database_session, order.id, adapter)

    assert calls == []


@pytest.mark.parametrize(("endpoint", "fields", "error"), [
    ("queryInvoice", {"invNo": OTHER_NUMBER}, InvoiceApplicationError),
    ("queryInvoice", {"invDate": "20260909"}, InvoiceApplicationError),
    ("queryInvoice", {"status": "1"}, InvoiceApplicationError),
    ("queryInvoice", {"status": ""}, InvoiceApplicationError),
    ("queryInvoice", {"randomNumber": "9876"}, InvoiceApplicationError),
    ("getInvoicePrintCode", {"invNo": OTHER_NUMBER}, IntegrationResponseError),
    ("getInvoicePrintCode", {"barcode": BARCODE.replace(NUMBER, OTHER_NUMBER)}, IntegrationResponseError),
    ("getInvoicePrintCode", {"leftQrcode": QR_LEFT.replace(NUMBER, OTHER_NUMBER)}, IntegrationResponseError),
    ("getInvoicePrintCode", {"leftQrcode": QR_LEFT.replace(SELLER, "00000000")}, IntegrationResponseError),
    ("getInvoicePrintCode", {"rightQrcode": ""}, IntegrationResponseError),
    ("getInvoiceB2CPDF", {"invNo": OTHER_NUMBER}, IntegrationResponseError),
    ("getInvoiceB2CPDF", {"b2c_PDF": "in!valid"}, IntegrationResponseError),
    ("getInvoiceB2CPDF", {"b2c_PDF": base64.b64encode(b"<html>bad response</html>").decode()}, IntegrationResponseError),
])
async def test_rejects_mismatched_provider_data_without_attempting_later_calls(database_session, endpoint, fields, error):
    calls = []
    adapter = FanyuInvoiceAdapter(fanyu_settings(), provider_transport(calls, override=(endpoint, fields)))
    order, invoice = await issued_invoice(database_session, adapter)

    with pytest.raises(error):
        await retrieve_fanyu_invoice_certificate(database_session, order.id, adapter)

    order_of_calls = ["queryInvoice", "getInvoicePrintCode", "getInvoiceB2CPDF"]
    assert [name for name, _ in calls] == order_of_calls[:order_of_calls.index(endpoint) + 1]
    assert invoice.provider_response == {"keep": True}
    assert invoice.status == InvoiceStatus.ISSUED
