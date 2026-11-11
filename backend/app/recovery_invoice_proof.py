from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import AwareDatetime, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .integrations.fanyu_invoice import FanyuInvoiceAdapter, TAIPEI
from .integrations.invoice_service import _apply_provider_query_result, _parse_invoice_date, invoice_context_from_settings
from .invoice_timing import invoice_handling
from .models import (
    AdminAudit, Invoice, InvoiceAllowance, InvoiceAllowanceStatus, InvoiceStatus, Order, OutboxEvent,
    OutboxStatus, PaymentStatus, RecoveryHold, Refund, RefundStatus, User,
)
from .recovery_quarantine import Digest, QuarantineInput, _lock_cutover, _require_admin
from .recovery_work import INVOICE_ISSUE_EVENTS


class FanyuCaptureRequest(QuarantineInput):
    cutover_id: UUID
    checkpoint_sha256: Digest
    order_id: UUID
    invoice_id: UUID
    outbox_id: UUID
    relate_number: str = Field(pattern=r"^[A-Za-z0-9]{1,30}$")
    buyer_type: Literal["personal", "company"]
    operation: Literal["issue", "void"]


class FanyuInvoiceRecord(QuarantineInput):
    number: str = Field(pattern=r"^[A-Z]{2}[0-9]{8}$")
    issued_at: AwareDatetime
    seller_id: str = Field(pattern=r"^[0-9]{8,10}$")
    status: Literal["0", "1"]
    taxable_sales: int = Field(strict=True, ge=0)
    exempt_sales: int = Field(strict=True, ge=0)
    zero_rated_sales: int = Field(strict=True, ge=0)
    tax_amount: int = Field(strict=True, ge=0)
    total_amount: int = Field(strict=True, gt=0)


class FanyuEvidence(QuarantineInput):
    version: Literal[1]
    kind: Literal["fanyu_invoice_queries"]
    request: FanyuCaptureRequest
    account_sha256: Digest
    observed_at: AwareDatetime
    record: FanyuInvoiceRecord


class SignedFanyuEvidence(QuarantineInput):
    evidence: FanyuEvidence
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")


def _digest_account(context: dict) -> str:
    return sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _signed_bytes(evidence: FanyuEvidence) -> bytes:
    body = json.dumps(evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return b"slf/recovery/fanyu-invoice-queries/v1\0" + body.encode()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _money(value: object) -> int:
    if type(value) is int and 0 <= value <= 2**63 - 1:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}(?:\.0{1,6})?", value):
        number = int(value.split(".")[0])
        if number <= 2**63 - 1:
            return number
    raise ValueError("汎宇金額欄位缺漏或格式不明")


def _consistent_record(record: FanyuInvoiceRecord, request: FanyuCaptureRequest, observed_at: datetime) -> bool:
    return (
        (request.operation, record.status) in {("issue", "0"), ("void", "1")}
        and record.issued_at <= observed_at
        and record.taxable_sales + record.exempt_sales + record.zero_rated_sales + record.tax_amount == record.total_amount
    )


async def capture_fanyu_proof(
    request: FanyuCaptureRequest, adapter: FanyuInvoiceAdapter, signer: Ed25519PrivateKey,
) -> SignedFanyuEvidence:
    if not adapter.settings.recovery_mode:
        raise ValueError("請在汎宇唯讀擷取模式執行")
    summary = await adapter.query_invoice(request.relate_number, buyer_type=request.buyer_type)
    raw = summary["ProviderResponse"]
    issued_at = _parse_invoice_date(summary.get("InvoiceDate"))
    if (
        summary["RtnCode"] != 1 or issued_at is None
        or not re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", str(raw.get("invTime", "")))
        or ("orderID" in raw and raw["orderID"] != request.relate_number)
    ):
        raise ValueError("汎宇銷貨單查詢缺少有效的原發票識別或開立時間")
    number, date = summary["InvoiceNo"], issued_at.astimezone(TAIPEI).strftime("%Y%m%d")
    detail = await adapter.query_invoice_detail(number, date, buyer_type=request.buyer_type)
    detail_date = str(detail.get("invDate", ""))
    if (
        detail.get("invNo") != number
        or detail_date not in {date, issued_at.astimezone(TAIPEI).date().isoformat()}
        or detail.get("sellerCompid") != adapter.settings.seller_id
        or ("orderID" in detail and detail["orderID"] != request.relate_number)
        or str(detail.get("sellerInvStatus", "")) != summary["ProviderStatus"]
    ):
        raise ValueError("汎宇銷貨單與明細的發票、賣方、日期或狀態不一致")
    record = FanyuInvoiceRecord(
        number=number, issued_at=issued_at, seller_id=detail["sellerCompid"],
        status=str(detail["sellerInvStatus"]), taxable_sales=_money(detail.get("amount")),
        exempt_sales=_money(detail.get("freetaxsalesamount")), zero_rated_sales=_money(detail.get("zerotaxsalesamount")),
        tax_amount=_money(detail.get("tax")), total_amount=_money(detail.get("total")),
    )
    observed_at = datetime.now(timezone.utc)
    if not _consistent_record(record, request, observed_at):
        raise ValueError("汎宇查票時間、金額或狀態與待處理工作不符")
    evidence = FanyuEvidence(
        version=1, kind="fanyu_invoice_queries", request=request, account_sha256=_digest_account(adapter.binding_context),
        observed_at=observed_at, record=record,
    )
    return SignedFanyuEvidence(evidence=evidence, signature=signer.sign(_signed_bytes(evidence)).hex())


def _verify_proof(proof: SignedFanyuEvidence, settings: Settings) -> tuple[dict, str, str]:
    key_hex = settings.recovery_proof_signer_public_key
    try:
        public_key = bytes.fromhex(key_hex)
        if public_key.hex() != key_hex:
            raise ValueError
        verifier = Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError:
        raise ValueError("尚未固定對帳擷取者的公開金鑰") from None
    signed = _signed_bytes(proof.evidence)
    try:
        verifier.verify(bytes.fromhex(proof.signature), signed)
    except InvalidSignature:
        raise ValueError("對帳證明簽章無法驗證") from None
    evidence, record = proof.evidence, proof.evidence.record
    context = invoice_context_from_settings(settings)
    if (
        context["provider"] != "fanyu" or record.seller_id != context["seller_id"]
        or evidence.account_sha256 != _digest_account(context)
    ):
        raise ValueError("汎宇證明與原賣方或帳戶不符")
    if (
        evidence.observed_at.utcoffset() != timedelta(0) or record.issued_at.utcoffset() != timedelta(0)
        or evidence.observed_at > datetime.now(timezone.utc)
        or not _consistent_record(record, evidence.request, evidence.observed_at)
    ):
        raise ValueError("汎宇證明的時間、金額或狀態不明")
    return context, sha256(signed).hexdigest(), sha256(public_key).hexdigest()


def _invoice_binding(invoice: Invoice, order: Order, proof: FanyuEvidence, context: dict) -> bool:
    request, record = proof.request, proof.record
    original = invoice.provider_request
    if not isinstance(original, dict):
        return False
    original_amounts = tuple(_money(original.get(field)) for field in (
        "salesAmount", "freetaxSalesamount", "zerotaxSalesamount", "taxAmount", "totalAmount",
    ))
    return (
        invoice.order_id == order.id and invoice.provider == "fanyu"
        and order.invoice_provider_context == invoice.provider_context == context
        and invoice.relate_number == original.get("orderID") == request.relate_number
        and original.get("sellerID") == record.seller_id
        and invoice.buyer_type.value == request.buyer_type
        and original.get("process_type") == ("B" if request.buyer_type == "company" else "C")
        and original_amounts == (record.taxable_sales, record.exempt_sales, record.zero_rated_sales,
                                 record.tax_amount, record.total_amount)
        and (invoice.sales_amount, invoice.tax_amount, invoice.total_amount)
            == (record.taxable_sales, record.tax_amount, record.total_amount)
        and order.amount_total == record.total_amount
        and (invoice.invoice_number is None or invoice.invoice_number == record.number)
        and (invoice.invoice_date is None or _aware(invoice.invoice_date) == record.issued_at)
        and record.issued_at >= _aware(invoice.created_at).replace(microsecond=0)
    )


def _check_issue(order: Order, invoice: Invoice, work: OutboxEvent, record: FanyuInvoiceRecord) -> None:
    if (
        work.event_type not in INVOICE_ISSUE_EVENTS or work.aggregate_type != "order" or work.aggregate_id != order.id
        or (work.payload.get("order_id") or order.id) != order.id
        or order.payment_status != PaymentStatus.PAID or order.cancelled_at
        or order.invoice_status not in {InvoiceStatus.PENDING, InvoiceStatus.FAILED, InvoiceStatus.ISSUED}
        or invoice.status not in {InvoiceStatus.PENDING, InvoiceStatus.FAILED, InvoiceStatus.ISSUED}
        or invoice_handling(order) == "external"
    ):
        raise ValueError("原開票工作與發票狀態不符")
    if invoice_handling(order) == "pickup_day" and (
        order.invoice_issue_after is None
        or _aware(order.invoice_issue_after).astimezone(TAIPEI).date() != record.issued_at.astimezone(TAIPEI).date()
    ):
        raise ValueError("汎宇發票日期與取餐開票日不符")


async def _check_void(session: AsyncSession, order: Order, invoice: Invoice, work: OutboxEvent) -> None:
    if (
        work.event_type != "invoice.adjustment_required" or work.aggregate_type != "invoice"
        or work.aggregate_id != invoice.id or work.payload.get("invoice_id") != invoice.id
        or work.payload.get("order_id") != order.id or work.payload.get("adjustment") != "void"
        or work.payload.get("auto_void") is not True
        or invoice.invoice_number is None or invoice.invoice_date is None
        or order.payment_status != PaymentStatus.REFUNDED
        or order.invoice_status not in {InvoiceStatus.VOID_PENDING, InvoiceStatus.VOIDED}
        or invoice.status not in {InvoiceStatus.VOID_PENDING, InvoiceStatus.VOIDED}
    ):
        raise ValueError("原作廢工作與發票狀態不符")
    refund_id = work.payload.get("refund_id")
    refund = await session.get(Refund, refund_id) if refund_id else None
    if not refund or refund.order_id != order.id or refund.status != RefundStatus.COMPLETED or refund.amount != order.amount_total:
        raise ValueError("原作廢工作缺少相符的全額退款")
    allowance = await session.scalar(select(InvoiceAllowance.id).where(
        InvoiceAllowance.invoice_id == invoice.id, InvoiceAllowance.status != InvoiceAllowanceStatus.VOIDED,
    ).limit(1))
    if allowance:
        raise ValueError("此發票有折讓，請保留隔離")


async def apply_fanyu_proof(
    session: AsyncSession, actor: User, settings: Settings, proof: SignedFanyuEvidence,
) -> dict[str, str | bool]:
    if not settings.recovery_mode:
        raise ValueError("請在隔離還原模式補登對帳結果")
    _require_admin(actor)
    context, proof_sha, signer_sha = _verify_proof(proof, settings)
    evidence = proof.evidence
    request, record = evidence.request, evidence.record
    cutover = await _lock_cutover(session, str(request.cutover_id))
    if (not cutover or not cutover.armed_at or cutover.checkpoint_sha256 != request.checkpoint_sha256
            or evidence.observed_at < _aware(cutover.created_at)):
        raise ValueError("對帳證明與已封存還原切點不符")

    order = await session.scalar(select(Order).where(Order.id == str(request.order_id))
                                 .with_for_update().execution_options(populate_existing=True))
    invoice = await session.scalar(select(Invoice).where(Invoice.id == str(request.invoice_id))
                                   .with_for_update().execution_options(populate_existing=True))
    work = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == str(request.outbox_id))
                                .with_for_update().execution_options(populate_existing=True))
    if not order or not invoice or not work or not isinstance(work.payload, dict) or not _invoice_binding(invoice, order, evidence, context):
        raise ValueError("缺少原發票工作，或汎宇識別、日期、稅額與原紀錄不符")

    expected = InvoiceStatus.ISSUED if request.operation == "issue" else InvoiceStatus.VOIDED
    audits = select(AdminAudit).where(
        AdminAudit.aggregate_type == "recovery_cutover", AdminAudit.aggregate_id == cutover.id,
        AdminAudit.action == "recovery.fanyu_proof_applied", AdminAudit.data["invoice_id"].as_string() == invoice.id,
    )
    matching = await session.scalar(audits.where(AdminAudit.data["proof_sha256"].as_string() == proof_sha))
    if matching:
        if invoice.status != expected or order.invoice_status != expected or work.status != OutboxStatus.COMPLETED:
            raise ValueError("原發票狀態已更新，請核對最新紀錄")
        return {"applied": False, "status": expected.value}
    latest = await session.scalar(audits.order_by(AdminAudit.created_at.desc(), AdminAudit.id.desc()).limit(1))
    if latest and evidence.observed_at <= datetime.fromisoformat(latest.data["observed_at"]):
        raise ValueError("對帳證明早於已補登的發票狀態")

    for scope, reference in (("outbox", work.id), ("order", order.id)):
        hold = await session.get(RecoveryHold, (cutover.id, scope, reference))
        if not hold or hold.resolved_at:
            raise ValueError("原訂單及發票工作須保留在本次還原隔離中")
    if work.status == OutboxStatus.COMPLETED:
        raise ValueError("發票工作已有其他完成紀錄")
    if request.operation == "issue":
        _check_issue(order, invoice, work, record)
    else:
        await _check_void(session, order, invoice, work)
    if invoice.status == expected:
        if (invoice.invoice_number != record.number or invoice.invoice_date is None
                or order.invoice_status != expected or invoice.provider_status != record.status):
            raise ValueError("原發票狀態與汎宇證明不一致")
    else:
        voided_at = invoice.voided_at
        _apply_provider_query_result(invoice, order, {
            "RtnCode": 1, "InvoiceNo": record.number, "InvoiceDate": record.issued_at.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
            "RandomNumber": invoice.random_number or "", "ProviderStatus": record.status,
        })
        # 汎宇查詢證實作廢狀態，未提供實際作廢時間。
        invoice.voided_at = voided_at
        if invoice.status != expected or order.invoice_status != expected:
            raise ValueError("無法確認補登後的發票狀態")
    work.status = OutboxStatus.COMPLETED
    work.processed_at = datetime.now(timezone.utc)
    work.last_error = None
    session.add(AdminAudit(
        actor_id=actor.id, action="recovery.fanyu_proof_applied", aggregate_type="recovery_cutover", aggregate_id=cutover.id,
        data={"invoice_id": invoice.id, "outbox_id": work.id, "proof_sha256": proof_sha, "signer_sha256": signer_sha,
              "status": expected.value, "observed_at": evidence.observed_at.isoformat()},
    ))
    await session.flush()
    return {"applied": True, "status": expected.value}
