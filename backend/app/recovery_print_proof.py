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
from .integrations.feie import FeieClient, configured_region
from .models import AdminAudit, Order, PrintJob, RecoveryCutover, RecoveryHold, User
from .printing import printer_fingerprint
from .recovery_quarantine import Digest, QuarantineInput, _lock_cutover, _require_admin, _uuid
from .recovery_work import SUMMARY_ALL_TARGET, summary_print_targets


class FeieCaptureRequest(QuarantineInput):
    cutover_id: UUID
    checkpoint_sha256: Digest
    print_job_id: UUID
    job_kind: Literal["order", "summary"]
    vendor_order_id: str = Field(pattern=r"^[!-~]{1,120}$")
    printer_region: Literal["jp", "cn"]
    printer_fingerprint: Digest
    job_binding_sha256: Digest


class FeieEvidence(QuarantineInput):
    version: Literal[1]
    kind: Literal["feie_print_query"]
    request: FeieCaptureRequest
    observed_at: AwareDatetime
    printed: Literal[True]


class SignedFeieEvidence(QuarantineInput):
    evidence: FeieEvidence
    signature: str = Field(pattern=r"^[0-9a-f]{128}$")


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _signed_bytes(evidence: FeieEvidence) -> bytes:
    body = json.dumps(evidence.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return b"slf/recovery/feie-print-query/v1\0" + body.encode()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def capture_feie_proof(
    request: FeieCaptureRequest, client: FeieClient, signer: Ed25519PrivateKey,
) -> SignedFeieEvidence:
    if not client.settings.recovery_mode:
        raise ValueError("請在飛鵝唯讀擷取模式執行")
    if (
        request.printer_region != configured_region(client.settings)
        or request.printer_fingerprint != printer_fingerprint(client.settings)
    ):
        raise ValueError("目前飛鵝帳戶、設備或區域與原工作不符")
    if await client.query(request.vendor_order_id) is not True:
        raise ValueError("飛鵝尚未確認這筆工作已列印")
    evidence = FeieEvidence(
        version=1, kind="feie_print_query", request=request, observed_at=datetime.now(timezone.utc), printed=True,
    )
    return SignedFeieEvidence(evidence=evidence, signature=signer.sign(_signed_bytes(evidence)).hex())


def _verify_proof(proof: SignedFeieEvidence, settings: Settings) -> tuple[str, str]:
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
    observed = proof.evidence.observed_at
    if proof.evidence.printed is not True or observed.utcoffset() != timedelta(0) or observed > datetime.now(timezone.utc):
        raise ValueError("飛鵝對帳證明的狀態或擷取時間不正確")
    return sha256(signed).hexdigest(), sha256(public_key).hexdigest()


def _summary_identity(job: PrintJob) -> dict:
    snapshot = job.snapshot
    request_id = snapshot.get("request_id")
    page, count = snapshot.get("page"), snapshot.get("page_count")
    fingerprint = snapshot.get("fingerprint")
    if (
        job.order_id is not None or snapshot.get("kind") != "summary"
        or not isinstance(request_id, str) or not _uuid(request_id)
        or type(page) is not int or type(count) is not int or not 1 <= page <= count <= 50
        or not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        or job.event_key != f"summary:{request_id}:{page:02d}"
        or SUMMARY_ALL_TARGET in summary_print_targets(snapshot)
    ):
        raise ValueError("彙總列印缺少可信的原批次與頁次")
    source_versions = snapshot["source_versions"]
    if len({source["id"] for source in source_versions}) != len(source_versions) or any(
        type(source.get("revision")) is not int or source["revision"] < 1 for source in source_versions
    ):
        raise ValueError("彙總列印缺少可信的原訂單版本")
    return {"request_id": request_id, "page": page, "page_count": count, "fingerprint": fingerprint,
            "source_versions": [{"id": source["id"], "revision": source["revision"]} for source in source_versions]}


async def _job_request(session: AsyncSession, cutover: RecoveryCutover, job: PrintJob) -> FeieCaptureRequest:
    region = job.snapshot.get("printer_region") if isinstance(job.snapshot, dict) else None
    if (
        job.mode != "live" or job.kind not in {"order", "summary"} or job.reprint_of_id is not None
        or region not in ("jp", "cn") or not job.vendor_order_id or not job.submitted_at or job.attempts < 1
        or not isinstance(job.content, str) or not job.content or _aware(job.submitted_at) < _aware(job.created_at)
    ):
        raise ValueError("原工作缺少可信的正式送印紀錄，請保留隔離")
    duplicate = await session.scalar(select(PrintJob.id).where(
        PrintJob.vendor_order_id == job.vendor_order_id, PrintJob.id != job.id,
    ).limit(1))
    if duplicate:
        raise ValueError("同一飛鵝編號對應多筆本機工作，請保留隔離")
    if job.kind == "summary":
        origin = _summary_identity(job)
    else:
        order = await session.get(Order, job.order_id) if job.order_id else None
        revision = job.snapshot.get("revision")
        if (
            not order or job.event_key != f"order:{order.id}:initial" or job.snapshot.get("manual")
            or job.snapshot.get("order_number") != order.order_number or type(revision) is not int or revision < 1
        ):
            raise ValueError("列印工作與原訂單來源不符")
        origin = {"order_id": order.id, "revision": revision}
    binding = _digest({
        "event_key": job.event_key, "origin": origin, "attempts": job.attempts,
        "created_at": _aware(job.created_at).astimezone(timezone.utc).isoformat(),
        "submitted_at": _aware(job.submitted_at).astimezone(timezone.utc).isoformat(),
        "content_sha256": sha256(job.content.encode()).hexdigest(),
    })
    return FeieCaptureRequest(
        cutover_id=cutover.id, checkpoint_sha256=cutover.checkpoint_sha256,
        print_job_id=job.id, job_kind=job.kind, vendor_order_id=job.vendor_order_id,
        printer_region=region, printer_fingerprint=job.printer_fingerprint,
        job_binding_sha256=binding,
    )


async def _require_print_hold(session: AsyncSession, cutover: RecoveryCutover, job: PrintJob) -> None:
    hold = await session.get(RecoveryHold, (cutover.id, "print_job", job.id))
    if not hold or hold.resolved_at or job.status not in {"submitted", "unknown"}:
        raise ValueError("原工作須保留在本次還原隔離中，且列印結果待確認")


async def prepare_feie_request(
    session: AsyncSession, actor: User, cutover_id: UUID, job_id: UUID,
) -> FeieCaptureRequest:
    _require_admin(actor)
    cutover = await session.get(RecoveryCutover, str(cutover_id))
    job = await session.get(PrintJob, str(job_id))
    if not cutover or not cutover.armed_at or not job:
        raise ValueError("找不到已封存的切點或原列印工作")
    await _require_print_hold(session, cutover, job)
    return await _job_request(session, cutover, job)


async def apply_feie_proof(
    session: AsyncSession, actor: User, settings: Settings, proof: SignedFeieEvidence,
) -> dict[str, str | bool]:
    if not settings.recovery_mode:
        raise ValueError("請在隔離還原模式補登對帳結果")
    _require_admin(actor)
    proof_sha, signer_sha = _verify_proof(proof, settings)
    evidence, request = proof.evidence, proof.evidence.request
    cutover = await _lock_cutover(session, str(request.cutover_id))
    if (not cutover or not cutover.armed_at or cutover.checkpoint_sha256 != request.checkpoint_sha256
            or evidence.observed_at < _aware(cutover.created_at)):
        raise ValueError("對帳證明與已封存還原切點不符")
    job = await session.scalar(select(PrintJob).where(PrintJob.id == str(request.print_job_id))
                               .with_for_update().execution_options(populate_existing=True))
    if (
        not job or await _job_request(session, cutover, job) != request
        or evidence.observed_at < _aware(job.submitted_at)
    ):
        raise ValueError("飛鵝對帳證明與原列印工作不符")
    audit_id = await session.scalar(select(AdminAudit.id).where(
        AdminAudit.aggregate_type == "recovery_cutover", AdminAudit.aggregate_id == cutover.id,
        AdminAudit.action == "recovery.feie_proof_applied", AdminAudit.data["proof_sha256"].as_string() == proof_sha,
    ))
    if audit_id:
        if job.status != "printed":
            raise ValueError("原列印工作狀態已更新")
        return {"applied": False, "status": "printed"}

    await _require_print_hold(session, cutover, job)
    job.status = "printed"
    job.printed_at = evidence.observed_at
    job.lease_token = job.lease_until = None
    job.last_error = None
    job.last_result = {"ret": 0, "printed": True, "source": "recovery_feie_query"}
    session.add(AdminAudit(
        actor_id=actor.id, action="recovery.feie_proof_applied", aggregate_type="recovery_cutover",
        aggregate_id=cutover.id,
        data={"print_job_id": job.id, "proof_sha256": proof_sha, "signer_sha256": signer_sha,
              "observed_at": evidence.observed_at.isoformat()},
    ))
    await session.flush()
    return {"applied": True, "status": "printed"}
