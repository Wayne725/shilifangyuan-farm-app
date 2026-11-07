import base64
import json

import pytest

from app.integrations.common import HTTPResponse, IntegrationResponseError
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter, MAX_INVOICE_PDF_BYTES
from tests.test_fanyu_invoice import fanyu_settings


def adapter_for(data, calls, status="0"):
    async def transport(url, envelope, *_):
        calls.append((url.rsplit("/", 1)[-1], envelope["reqData"]))
        return HTTPResponse(200, json.dumps({"statusCode": status, "respData": data}), {})
    return FanyuInvoiceAdapter(fanyu_settings(), transport)


@pytest.mark.asyncio
@pytest.mark.parametrize("buyer_type,process", [("personal", "C"), ("company", "B")])
async def test_print_codes_reuse_verified_envelope_and_case_sensitive_vendor_contract(buyer_type, process):
    calls = []
    adapter = adapter_for({"invNo": "AB12345678", "barcode": "BARCODE", "leftQrcode": "opaque:left", "rightQrcode": "opaque:right"}, calls)
    codes = await adapter.get_invoice_print_codes("AB12345678", "20261107", buyer_type=buyer_type)
    assert codes.invoice_number == "AB12345678" and codes.left_qrcode == "opaque:left" and codes.right_qrcode == "opaque:right"
    assert calls == [("getInvoicePrintCode", {"invNo": "AB12345678", "invDate": "20261107", "processType": process, "sellerID": adapter.settings.seller_id})]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"invNo": "CD12345678"}, {"barcode": ""}, {"leftQrcode": None}, {"rightQrcode": 123}, {"rightQrcode": "x" * 8193}])
async def test_incomplete_or_wrong_invoice_codes_cannot_be_used_for_proof(changes):
    calls = []
    adapter = adapter_for({"invNo": "AB12345678", "barcode": "BARCODE", "leftQrcode": "left", "rightQrcode": "right", **changes}, calls)
    with pytest.raises(IntegrationResponseError):
        await adapter.get_invoice_print_codes("AB12345678", "20261107")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("number,day,kind", [("bad", "20261107", "personal"), ("AB12345678", "20260230", "personal"), ("AB12345678", "2026-11-07", "personal"), ("AB12345678", "20261107", "unknown")])
async def test_invalid_identity_is_rejected_before_any_transport(number, day, kind):
    calls = []
    with pytest.raises(ValueError):
        await adapter_for({}, calls).get_invoice_print_codes(number, day, buyer_type=kind)
    assert calls == []


@pytest.mark.asyncio
async def test_pdf_retrieval_never_opens_invoice_or_marks_printer_success():
    # A synthetic envelope tests decoding only, not PDF rendering or legal format acceptance.
    document = b"%PDF-1.7\nsynthetic transport fixture\n%%EOF\n"
    calls = []
    adapter = adapter_for({"b2c_PDF": base64.b64encode(document).decode()}, calls)
    assert await adapter.get_invoice_b2c_pdf("AB12345678", "20261107", random_number="1234", no_details=True) == document
    assert calls == [("getInvoiceB2CPDF", {"invNo": "AB12345678", "invDate": "20261107", "sellerID": adapter.settings.seller_id, "ranNo": "1234", "isNoDetails": "Y"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [None, "not base64!", base64.b64encode(b"not a PDF").decode(), base64.b64encode(b"%PDF-1.7 truncated").decode(), "A" * ((((MAX_INVOICE_PDF_BYTES + 2) // 3) * 4) + 4)])
async def test_pdf_decoder_rejects_wrong_truncated_or_oversized_payload(encoded):
    with pytest.raises(IntegrationResponseError):
        await adapter_for({"b2c_PDF": encoded}, []).get_invoice_b2c_pdf("AB12345678", "20261107")


@pytest.mark.asyncio
async def test_missing_provider_proof_is_not_a_successful_print():
    calls = []
    with pytest.raises(IntegrationResponseError):
        await adapter_for({}, calls, status="3").get_invoice_print_codes("AB12345678", "20261107")
    assert len(calls) == 1 and calls[0][0] == "getInvoicePrintCode"
