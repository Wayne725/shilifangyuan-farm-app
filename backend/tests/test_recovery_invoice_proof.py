from argparse import Namespace
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import recovery_capture, recovery_operator
from app.auth import create_token
from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter, TAIPEI
from app.integrations.invoice import InvoiceIssueRequest, InvoiceLine
from app.integrations.invoice_service import enqueue_invoice_adjustment_after_refund
from app.models import (
    AdminAudit, Invoice, InvoiceAllowance, InvoiceBuyerType, InvoiceStatus, OutboxEvent, OutboxStatus, PaymentStatus,
    RecoveryCutover, RecoveryHold, Refund, RefundStatus, UserRole,
)
from app.recovery_invoice_proof import FanyuCaptureRequest, _signed_bytes, apply_fanyu_proof, capture_fanyu_proof
from app.recovery_quarantine import Item, Resolution, resolve_hold
from tests.support import make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order


pytestmark = pytest.mark.asyncio


async def _case(session, *, void=False):
    adapter_settings = fanyu_settings(recovery_mode=True)
    actor, _, order = await make_regular_order(session)
    actor.user_role = UserRole.ADMIN
    now = datetime.now(timezone.utc).replace(microsecond=0)
    issued = now.astimezone(TAIPEI)
    relate_number = "RECOVERYINVOICE1"
    buyer_type = "personal" if void else "company"
    requests = []
    summary = {
        "invNo": "AB12345678", "invDate": issued.strftime("%Y%m%d"), "invTime": issued.strftime("%H:%M:%S"),
        "status": "1" if void else "0", "randomNumber": "5678",
    }
    detail = {
        "invNo": "AB12345678", "invDate": issued.date().isoformat(), "sellerCompid": adapter_settings.seller_id,
        "sellerInvStatus": "1" if void else "0", "buyerName": "DO NOT EXPORT BUYER", "carrierid1": "PRIVATE-CARRIER",
        "buyerCompid": "12345675", "randomnumber": "5678", "note": "PRIVATE NOTE",
    }

    async def transport(url, payload, headers, timeout):
        requests.append((url, payload["reqData"]))
        endpoint = url.rsplit("/", 1)[1]
        assert endpoint in {"queryInvoice", "queryInvoiceDetail"}
        record = summary if endpoint == "queryInvoice" else detail
        return HTTPResponse(200, json.dumps({"statusCode": "0", "respData": record}), {})

    adapter = FanyuInvoiceAdapter(adapter_settings, transport=transport)
    order.invoice_provider_context = adapter.binding_context
    order.payment_status = PaymentStatus.REFUNDED if void else PaymentStatus.PAID
    order.invoice_status = InvoiceStatus.ISSUED if void else InvoiceStatus.PENDING
    lines = [InvoiceLine(name="免稅商品", quantity=1, unit_price=200, tax_type="3")] if void else [
        InvoiceLine(name="應稅商品", quantity=1, unit_price=105, tax_type="1"),
        InvoiceLine(name="免稅商品", quantity=1, unit_price=95, tax_type="3"),
    ]
    prepared = adapter.prepare_invoice(InvoiceIssueRequest(
        relate_number=relate_number, buyer_type=buyer_type, buyer_tax_id="" if void else "12345675",
        buyer_name="" if void else "測試商戶", customer_email="private@example.test", items=lines,
    ))
    for source, dest in (
        ("salesAmount", "amount"), ("freetaxSalesamount", "freetaxsalesamount"),
        ("zerotaxSalesamount", "zerotaxsalesamount"), ("taxAmount", "tax"), ("totalAmount", "total"),
    ):
        detail[dest] = prepared.provider_request[source]
    invoice = Invoice(
        order=order, relate_number=relate_number, provider="fanyu", provider_context=adapter.binding_context,
        buyer_type=InvoiceBuyerType(buyer_type), total_amount=int(prepared.total_amount), tax_amount=int(prepared.tax_amount),
        sales_amount=int(prepared.sales_amount), status=order.invoice_status, provider_request=dict(prepared.provider_request),
        provider_status="0" if void else None, invoice_number=summary["invNo"] if void else None,
        invoice_date=now if void else None, random_number="5678" if void else None, created_at=now - timedelta(minutes=5),
    )
    cutover = RecoveryCutover(id=str(uuid4()), checkpoint_sha256="a" * 64, manifest_sha256="b" * 64,
                              created_by_id=actor.id, created_at=now - timedelta(minutes=1), armed_at=now)
    session.add_all([invoice, cutover])
    await session.flush()
    if void:
        refund = Refund(order_id=order.id, amount=200, status=RefundStatus.COMPLETED, reason="測試",
                        requested_by_id=actor.id)
        session.add(refund)
        await session.flush()
        assert enqueue_invoice_adjustment_after_refund(session, order, refund)
        work = await session.scalar(select(OutboxEvent))
    else:
        work = OutboxEvent(event_type="invoice.issue_requested", aggregate_type="order", aggregate_id=order.id,
                           payload={"order_id": order.id})
        session.add(work)
        await session.flush()
        refund = None
    session.add_all([
        RecoveryHold(cutover_id=cutover.id, scope="outbox", reference=work.id, evidence_sha256="c" * 64),
        RecoveryHold(cutover_id=cutover.id, scope="order", reference=order.id, evidence_sha256="d" * 64),
    ])
    await session.commit()
    signer = Ed25519PrivateKey.generate()
    settings = make_test_settings(
        recovery_mode=True, invoice_provider="fanyu",
        recovery_proof_signer_public_key=signer.public_key().public_bytes_raw().hex(),
        fanyu_invoice_base_url=adapter_settings.base_url, fanyu_invoice_company_id=adapter_settings.company_id,
        fanyu_invoice_seller_id=adapter_settings.seller_id, fanyu_invoice_user_id=adapter_settings.user_id,
    )
    request = FanyuCaptureRequest(
        cutover_id=cutover.id, checkpoint_sha256=cutover.checkpoint_sha256, order_id=order.id, invoice_id=invoice.id,
        outbox_id=work.id, relate_number=relate_number, buyer_type=buyer_type, operation="void" if void else "issue",
    )
    return actor, order, invoice, work, refund, request, signer, adapter, settings, summary, detail, requests


async def _count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("void", [False, True])
async def test_signed_fanyu_proof_applies_exact_work_once_without_provider_mutations(database_session, void):
    session = database_session
    actor, order, invoice, work, _, request, signer, adapter, settings, summary, _, requests = await _case(session, void=void)
    proof = await capture_fanyu_proof(request, adapter, signer)
    assert [(url.rsplit("/", 1)[1], data) for url, data in requests] == [
        ("queryInvoice", {"orderID": request.relate_number, "sellerID": adapter.settings.seller_id,
                          "process_type": "C" if void else "B"}),
        ("queryInvoiceDetail", {"invNo": summary["invNo"], "invDate": summary["invDate"],
                                "sellerID": adapter.settings.seller_id, "process_type": "C" if void else "B"}),
    ]
    signed = proof.model_dump_json()
    assert all(word not in signed for word in ("private@", "PRIVATE", "DO NOT EXPORT", "buyerCompid", "randomNumber", "randomnumber"))
    original_snapshot = dict(invoice.provider_request)
    expected = InvoiceStatus.VOIDED if void else InvoiceStatus.ISSUED
    async with session.begin():
        assert await apply_fanyu_proof(session, actor, settings, proof) == {"applied": True, "status": expected.value}
    assert (invoice.status, order.invoice_status, work.status) == (expected, expected, OutboxStatus.COMPLETED)
    assert invoice.invoice_number == summary["invNo"] and invoice.provider_request == original_snapshot
    assert invoice.voided_at is None
    assert invoice.random_number == ("5678" if void else None)
    async with session.begin():
        assert await apply_fanyu_proof(session, actor, settings, proof) == {"applied": False, "status": expected.value}
    assert await _count(session, AdminAudit) == 1
    assert await _count(session, OutboxEvent) == 1
    assert len(requests) == 2
    resolution = Resolution(cutover_id=request.cutover_id, target=Item(scope="outbox", reference=work.id),
                            outcome="reconciled", evidence_sha256="e" * 64)
    assert await resolve_hold(session, actor, resolution)


@pytest.mark.parametrize("field", ["sellerCompid", "invNo", "invDate", "sellerInvStatus", "tax", "total"])
async def test_capture_rejects_incomplete_or_contradictory_query_fields(database_session, field):
    actor, order, invoice, work, _, request, signer, adapter, settings, _, detail, requests = await _case(database_session)
    detail.pop(field)
    with pytest.raises(ValueError):
        await capture_fanyu_proof(request, adapter, signer)
    assert len(requests) == 2
    assert invoice.status == InvoiceStatus.PENDING and work.status == OutboxStatus.PENDING


@pytest.mark.parametrize("error", [
    "wrong_signer", "modified", "provider", "source", "amount", "tax", "wrong_outbox",
    "wrong_invoice", "wrong_cutover", "date", "partial_refund", "no_hold", "allowance",
])
async def test_offline_rejects_mismatched_evidence_and_keeps_exact_work_held(database_session, error):
    session = database_session
    is_void = error in {"partial_refund", "allowance"}
    actor, order, invoice, work, refund, request, signer, adapter, settings, _, _, requests = await _case(session, void=is_void)
    if error == "wrong_signer":
        signer = Ed25519PrivateKey.generate()
    elif error == "provider":
        settings.invoice_provider = "ecpay"
    elif error == "source":
        invoice.provider_context = {**invoice.provider_context, "seller_id": "12345675"}
    elif error == "amount":
        invoice.total_amount += 1
    elif error == "tax":
        invoice.tax_amount += 1
    elif error == "wrong_outbox":
        request = request.model_copy(update={"outbox_id": uuid4()})
    elif error == "wrong_invoice":
        request = request.model_copy(update={"invoice_id": uuid4()})
    elif error == "wrong_cutover":
        request = request.model_copy(update={"cutover_id": uuid4()})
    elif error == "date":
        invoice.invoice_date = datetime.now(timezone.utc) - timedelta(days=1)
    elif error == "partial_refund":
        refund.amount -= 1
    elif error == "allowance":
        session.add(InvoiceAllowance(
            invoice_id=invoice.id, sales_return_number="RECOVERY-ALLOWANCE", sales_amount=10,
            total_amount=10, reason="測試折讓",
        ))
    elif error == "no_hold":
        hold = await session.get(RecoveryHold, (str(request.cutover_id), "outbox", work.id))
        hold.resolved_at = datetime.now(timezone.utc)
    await session.commit()
    proof = await capture_fanyu_proof(request, adapter, signer)
    if error == "modified":
        proof.evidence.record.total_amount += 1
    with pytest.raises(ValueError):
        async with session.begin():
            await apply_fanyu_proof(session, actor, settings, proof)
    await session.refresh(invoice)
    await session.refresh(work)
    assert invoice.status == (InvoiceStatus.VOID_PENDING if is_void else InvoiceStatus.PENDING)
    assert work.status == OutboxStatus.PENDING and len(requests) == 2
    assert await _count(session, AdminAudit) == 0
    assert await _count(session, RecoveryHold) == 2


async def test_official_queries_and_authenticated_offline_operator_file_flow(database_session, monkeypatch, tmp_path):
    session = database_session
    actor, _, _, _, _, request, signer, adapter, settings, _, _, requests = await _case(session)
    request_path, key_path, output = (tmp_path / name for name in ("request.json", "capture.pem", "proof.json"))
    request_path.write_text(request.model_dump_json())
    key_path.write_bytes(signer.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    key_path.chmod(0o600)
    monkeypatch.setattr(recovery_capture, "get_settings", lambda: settings)
    monkeypatch.setattr(recovery_capture, "invoice_adapter_from_settings", lambda _settings: adapter)
    await recovery_capture.run(request_path, key_path, output, provider="fanyu")
    assert output.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(recovery_operator, "get_settings", lambda: settings)
    monkeypatch.setattr(recovery_operator, "SessionLocal", async_sessionmaker(session.bind, expire_on_commit=False))
    token = create_token(actor, "access", timedelta(minutes=5), settings)
    assert await recovery_operator.run(Namespace(action="fanyu-proof", input=output), token) == {
        "applied": True, "status": "issued",
    }
    assert len(requests) == 2


async def test_earlier_signed_invoice_observation_cannot_overwrite_newer_proof(database_session):
    session = database_session
    actor, _, _, _, _, request, signer, adapter, settings, _, _, _ = await _case(session)
    proof = await capture_fanyu_proof(request, adapter, signer)
    async with session.begin():
        await apply_fanyu_proof(session, actor, settings, proof)
    older = proof.evidence.model_copy(update={"observed_at": proof.evidence.observed_at - timedelta(microseconds=1)})
    stale = proof.model_copy(update={"evidence": older, "signature": signer.sign(_signed_bytes(older)).hex()})
    with pytest.raises(ValueError, match="早於已補登"):
        async with session.begin():
            await apply_fanyu_proof(session, actor, settings, stale)
    assert await _count(session, AdminAudit) == 1


async def test_confirmed_void_only_closes_unfinished_work_if_local_invoice_already_matches(database_session):
    session = database_session
    actor, order, invoice, work, _, request, signer, adapter, settings, _, _, _ = await _case(session, void=True)
    invoice.status = order.invoice_status = InvoiceStatus.VOIDED
    invoice.provider_status = "1"
    voided_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    invoice.voided_at = voided_at
    invoice.provider_response = {"original": True}
    await session.commit()
    proof = await capture_fanyu_proof(request, adapter, signer)
    async with session.begin():
        assert await apply_fanyu_proof(session, actor, settings, proof) == {"applied": True, "status": "voided"}
    assert work.status == OutboxStatus.COMPLETED
    assert invoice.voided_at.replace(tzinfo=timezone.utc) == voided_at
    assert invoice.provider_response == {"original": True}
