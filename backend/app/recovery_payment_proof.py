from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import AwareDatetime, Field, JsonValue
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import Settings
from .integrations.common import IntegrationError
from .integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository, _aware, _provider_payment_time,
    is_obsolete_order_attempt, lock_order_payment_subject,
)
from .integrations.raygate import RAYGATE_PAYMENT_PROVIDER, RayGateAdapter, RayGatePaymentResult
from .models import (
    AdminAudit, FulfillmentState, FulfillmentStatus, Order, OrderKind, OutboxEvent, OutboxStatus,
    PaymentAttempt, PaymentMethod, PaymentStatus, RecoveryHold, Refund,
    RefundStatus, ReservationStatus, SalesChannel, User,
)
from .recovery_quarantine import Digest, QuarantineInput, _lock_cutover, _require_admin
from .recovery_work import INVOICE_ISSUE_EVENTS, REFUND_EVENTS


MAX_PROOF_BYTES = 8192


class RayGateCaptureRequest(QuarantineInput):
    cutover_id: UUID
    checkpoint_sha256: Digest
    attempt_id: UUID
    merchant_trade_no: str = Field(min_length=1, max_length=50)
    refund_outbox_id: UUID | None = None


class RayGateEvidence(QuarantineInput):
    version: Literal[1]
    kind: Literal["raygate_query"]
    request: RayGateCaptureRequest
    observed_at: AwareDatetime
    account_sha256: Digest
    record: dict[str, JsonValue]


class SignedRayGateEvidence(QuarantineInput):
    evidence: RayGateEvidence
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")


def _signed_bytes(evidence: RayGateEvidence) -> bytes:
    body = json.dumps(evidence.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return b"slf/recovery/raygate-query/v1\0" + body.encode("utf-8")


def _account_digest(base_url: str, store: str, merchant: str, terminal: str) -> str:
    account = (base_url.strip().rstrip("/"), store.strip(), merchant.strip(), terminal.strip())
    if not all(account):
        raise ValueError("雷門對帳商戶設定不完整")
    return sha256(json.dumps(account, separators=(",", ":")).encode()).hexdigest()


async def capture_raygate_proof(
    request: RayGateCaptureRequest, adapter: RayGateAdapter, signer: Ed25519PrivateKey,
) -> SignedRayGateEvidence:
    if not adapter.settings.recovery_mode:
        raise ValueError("請在雷門唯讀擷取模式執行")
    account = _account_digest(adapter.settings.base_url, adapter.settings.store_identifier,
                              adapter.settings.merchant_id, adapter.settings.terminal_id)
    record = await adapter.query_order_record(request.merchant_trade_no)
    if (record.disposition, request.refund_outbox_id is not None) not in {("paid", False), ("refunded", True)}:
        raise ValueError("雷門查單尚未確認可補登的付款或原退款")
    evidence = RayGateEvidence(
        version=1, kind="raygate_query", request=request, observed_at=datetime.now(timezone.utc),
        account_sha256=account, record=asdict(record),
    )
    return SignedRayGateEvidence(evidence=evidence, signature=signer.sign(_signed_bytes(evidence)).hex())


def _verify_proof(proof: SignedRayGateEvidence, settings: Settings) -> tuple[RayGatePaymentResult, str, str]:
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
    evidence = proof.evidence
    expected_account = _account_digest(
        settings.raygate_payment_base_url, settings.raygate_payment_store_identifier,
        settings.raygate_payment_merchant_id, settings.raygate_payment_terminal_id,
    )
    if evidence.account_sha256 != expected_account:
        raise ValueError("雷門對帳商戶與原設定不符")
    if evidence.observed_at.utcoffset() != timedelta(0) or evidence.observed_at > datetime.now(timezone.utc):
        raise ValueError("對帳擷取時間不正確")
    try:
        record = RayGateAdapter.parse_payment_result(evidence.record, settings.raygate_payment_store_identifier)
    except IntegrationError:
        raise ValueError("雷門查單證明格式不正確") from None
    request = evidence.request
    if record.pos_order_number != request.merchant_trade_no or (
        record.disposition, request.refund_outbox_id is not None
    ) not in {("paid", False), ("refunded", True)}:
        raise ValueError("雷門查單結果與對帳項目不符")
    return record, sha256(signed).hexdigest(), sha256(public_key).hexdigest()


def _eligible_order(order: Order, attempt: PaymentAttempt) -> bool:
    return (
        order.order_kind == OrderKind.REGULAR
        and order.sales_channel == SalesChannel.REGULAR
        and order.group_campaign_id is None
        and order.meal_event_id is None
        and order.payment_method == PaymentMethod.ONLINE
        and not is_obsolete_order_attempt(attempt, order)
    )


def _has_order_reservation(order: Order, attempt: PaymentAttempt) -> bool:
    expected: Counter[str] = Counter()
    actual: Counter[str] = Counter()
    for item in order.items:
        if not item.source_product_id:
            return False
        expected[item.source_product_id] += item.quantity
    for reservation in attempt.reservations:
        if (reservation.order_id != order.id or not reservation.source_product_id
                or reservation.status != ReservationStatus.ACTIVE):
            return False
        actual[reservation.source_product_id] += reservation.quantity
    return bool(expected) and expected == actual


async def _load_refund(
    session: AsyncSession, attempt: PaymentAttempt, event_id: UUID, provider_refund_id: str,
) -> tuple[Refund, OutboxEvent]:
    refund = await session.scalar(select(Refund).where(Refund.payment_attempt_id == attempt.id).with_for_update())
    event = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == str(event_id)).with_for_update())
    if (
        not refund or not event or refund.order_id != attempt.order_id
        or refund.provider != RAYGATE_PAYMENT_PROVIDER or refund.amount != attempt.amount
        or (refund.provider_refund_id and refund.provider_refund_id != provider_refund_id)
        or event.event_type not in REFUND_EVENTS or event.aggregate_type != "refund"
        or event.aggregate_id != refund.id or (event.payload.get("refund_id") or refund.id) != refund.id
    ):
        raise ValueError("找不到與證明相符的原退款及工作")
    return refund, event


async def _hold_invoice_work(session: AsyncSession, cutover_id: str, order_id: str, evidence_sha256: str) -> None:
    event_ids = await session.scalars(select(OutboxEvent.id).where(
        OutboxEvent.status != OutboxStatus.COMPLETED,
        or_(
            OutboxEvent.event_type.in_(INVOICE_ISSUE_EVENTS) & (OutboxEvent.aggregate_id == order_id),
            (OutboxEvent.event_type == "invoice.adjustment_required")
            & (OutboxEvent.payload["order_id"].as_string() == order_id),
        ),
    ))
    for event_id in event_ids:
        key = (cutover_id, "outbox", event_id)
        existing = await session.get(RecoveryHold, key)
        if existing is None:
            session.add(RecoveryHold(cutover_id=cutover_id, scope="outbox", reference=event_id,
                                     evidence_sha256=evidence_sha256))
        elif existing.resolved_at:
            raise ValueError("發票工作已有對帳結案，請重新核對其狀態")


async def apply_raygate_proof(
    session: AsyncSession, actor: User, settings: Settings, proof: SignedRayGateEvidence,
) -> dict[str, str | bool]:
    if not settings.recovery_mode:
        raise ValueError("請在隔離還原模式補登對帳結果")
    _require_admin(actor)
    record, proof_sha, signer_sha = _verify_proof(proof, settings)
    evidence, request = proof.evidence, proof.evidence.request
    cutover = await _lock_cutover(session, str(request.cutover_id))
    if (not cutover or not cutover.armed_at or cutover.checkpoint_sha256 != request.checkpoint_sha256
            or evidence.observed_at < _aware(cutover.created_at)):
        raise ValueError("對帳證明與已封存還原切點不符")
    attempt = await session.get(PaymentAttempt, str(request.attempt_id))
    if attempt is None or not attempt.order_id:
        raise ValueError("缺少原始付款或訂單，請保留隔離")
    await lock_order_payment_subject(session, attempt.order_id)
    attempt = await session.scalar(select(PaymentAttempt).where(PaymentAttempt.id == attempt.id).options(
        selectinload(PaymentAttempt.reservations),
    ).with_for_update().execution_options(populate_existing=True))
    if attempt is None or not attempt.order_id:
        raise ValueError("缺少原始付款或訂單，請保留隔離")
    order = await session.scalar(select(Order).where(Order.id == attempt.order_id).options(
        selectinload(Order.items), selectinload(Order.fulfillment),
    ).execution_options(populate_existing=True))
    if (
        order is None or not _eligible_order(order, attempt)
        or attempt.provider != RAYGATE_PAYMENT_PROVIDER or attempt.merchant_trade_no != record.pos_order_number
        or attempt.amount != record.amount or order.amount_total != record.amount
        or (attempt.provider_trade_no and attempt.provider_trade_no != record.order_id)
    ):
        raise ValueError("雷門查單與原訂單、金額或交易編號不符")
    previous = attempt.provider_response
    if previous and any(previous.get(key) and previous[key] != value for key, value in (
        ("PaymentType", record.pay_type), ("RayGateStoreCode", record.store_code),
        ("RayGatePOSID", record.pos_id),
    )):
        raise ValueError("雷門付款方式或商戶資料與原紀錄不符")

    payload = record.to_canonical_payload()
    transaction_at = _provider_payment_time(payload)
    if transaction_at is None or transaction_at > evidence.observed_at or transaction_at < _aware(attempt.created_at).replace(microsecond=0):
        raise ValueError("雷門交易時間與原付款嘗試不符")
    audit_entries = select(AdminAudit).where(
        AdminAudit.aggregate_type == "recovery_cutover", AdminAudit.aggregate_id == cutover.id,
        AdminAudit.action == "recovery.raygate_proof_applied",
        AdminAudit.data["attempt_id"].as_string() == attempt.id,
    )
    matching = await session.scalar(audit_entries.where(AdminAudit.data["proof_sha256"].as_string() == proof_sha))
    if matching:
        if attempt.status.value != record.disposition or order.payment_status.value != record.disposition:
            raise ValueError("原交易狀態已更新，請核對最新紀錄")
        return {"applied": False, "status": record.disposition}
    latest = await session.scalar(audit_entries.order_by(AdminAudit.created_at.desc(), AdminAudit.id.desc()).limit(1))
    if latest and evidence.observed_at <= datetime.fromisoformat(latest.data["observed_at"]):
        raise ValueError("對帳證明早於已補登的交易狀態")

    targets = [("payment_attempt", attempt.id), ("order", order.id)]
    refund = refund_event = None
    if request.refund_outbox_id is not None:
        refund, refund_event = await _load_refund(session, attempt, request.refund_outbox_id, record.associated_order_id)
        targets.append(("outbox", refund_event.id))
    for scope, reference in targets:
        hold = await session.get(RecoveryHold, (cutover.id, scope, reference))
        if hold is None or hold.resolved_at:
            raise ValueError("付款及原工作須保留在本次還原隔離中")
    if await session.scalar(select(PaymentAttempt.id).where(
        PaymentAttempt.order_id == order.id, PaymentAttempt.id != attempt.id,
        PaymentAttempt.status.in_((PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.LATE_PAID_REFUND_REQUIRED)),
    )):
        raise ValueError("同一訂單另有已付款嘗試，請人工核對")

    if record.disposition == "paid":
        if (attempt.status != PaymentStatus.PENDING or order.payment_status != PaymentStatus.PENDING
                or order.cancelled_at or order.fulfillment_status == FulfillmentStatus.CANCELLED
                or (order.fulfillment and order.fulfillment.status == FulfillmentState.CANCELLED)
                or transaction_at > _aware(attempt.expires_at) or not _has_order_reservation(order, attempt)):
            raise ValueError("原付款狀態、有效期或庫存不符，請保留隔離")
        expected_status = PaymentStatus.PAID
    else:
        if (attempt.status not in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING}
                or order.payment_status not in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING}
                or not refund or refund.status not in {RefundStatus.PENDING, RefundStatus.FAILED}
                or refund_event.status == OutboxStatus.COMPLETED):
            raise ValueError("原退款狀態不符，請保留隔離")
        expected_status = PaymentStatus.REFUNDED

    # Keep the callback's commits inside a savepoint, so its result and audit commit together.
    async with AsyncSession(bind=await session.connection(), join_transaction_mode="create_savepoint",
                            expire_on_commit=False) as isolated_callback:
        outcome = await SQLAlchemyPaymentCallbackRepository(isolated_callback)._apply_verified_recovery_raygate(attempt.id, payload)
    await session.refresh(attempt)
    await session.refresh(order)
    if outcome != expected_status.value or attempt.status != expected_status or order.payment_status != expected_status:
        raise ValueError("交易結果無法安全補登，請保留隔離")
    if refund and refund_event:
        await session.refresh(refund)
        if refund.status != RefundStatus.COMPLETED or refund.provider_refund_id != record.associated_order_id:
            raise ValueError("退款結果與原工作不符，請保留隔離")
        refund_event.status = OutboxStatus.COMPLETED
        refund_event.processed_at = datetime.now(timezone.utc)
        refund_event.last_error = None

    await _hold_invoice_work(session, cutover.id, order.id, proof_sha)
    session.add(AdminAudit(
        actor_id=actor.id, action="recovery.raygate_proof_applied", aggregate_type="recovery_cutover",
        aggregate_id=cutover.id, data={"attempt_id": attempt.id, "proof_sha256": proof_sha,
                                      "signer_sha256": signer_sha, "disposition": record.disposition,
                                      "observed_at": evidence.observed_at.isoformat()},
    ))
    await session.flush()
    return {"applied": True, "status": record.disposition}
