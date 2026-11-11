from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AdminAudit, Invoice, OutboxEvent, OutboxStatus, PaymentAttempt, PaymentStatus, PrintJob,
    RecoveryCutover, RecoveryHold, Refund, User, UserRole, utcnow,
)

Target = tuple[str, str]
Scope = Literal[
    "order", "outbox", "print_job", "print_lineage", "payment_attempt",
    "activity_charge", "membership_charge", "provider_payment",
]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PENDING_PAYMENTS = frozenset({PaymentStatus.PENDING, PaymentStatus.FAILED, PaymentStatus.EXPIRED,
                              PaymentStatus.REFUND_PENDING, PaymentStatus.LATE_PAID_REFUND_REQUIRED})
FINAL_PRINT_STATES = ("printed", "simulated")


class RecoveryHoldActive(ValueError):
    def __init__(self):
        super().__init__("交易正在進行災難復原對帳，暫停重送。")


def provider_payment_reference(provider: str, merchant_trade_no: str) -> str:
    return sha256(f"{provider.strip().casefold()}\0{merchant_trade_no.strip()}".encode()).hexdigest()


def cutover_preparing():
    return select(RecoveryCutover.id).where(RecoveryCutover.armed_at.is_(None)).exists()


def active_hold(scope: str, reference):
    return select(RecoveryHold.cutover_id).where(
        RecoveryHold.scope == scope,
        RecoveryHold.reference == reference,
        RecoveryHold.resolved_at.is_(None),
    ).correlate_except(RecoveryHold).exists()


async def has_active_hold(session: AsyncSession, *targets: Target) -> bool:
    preparing = cutover_preparing()
    if not targets:
        return bool(await session.scalar(select(preparing)))
    batch_size = 300
    keys = tuple(dict.fromkeys(targets))
    active = select(RecoveryHold.cutover_id).where(RecoveryHold.resolved_at.is_(None))
    # A summary can span thousands of orders. Bound bind counts, and avoid
    # querying each batch on installations that have no active holds.
    if len(keys) > batch_size and not await session.scalar(select(or_(preparing, active.exists()))):
        return False
    for offset in range(0, len(keys), batch_size):
        held = active.where(tuple_(RecoveryHold.scope, RecoveryHold.reference).in_(keys[offset:offset + batch_size])).exists()
        if await session.scalar(select(or_(preparing, held))):
            return True
    return False


async def require_unheld(session: AsyncSession, *targets: Target) -> None:
    if await has_active_hold(session, *targets):
        raise RecoveryHoldActive()


def payment_targets(attempt: PaymentAttempt) -> set[Target]:
    targets = {
        ("payment_attempt", attempt.id),
        ("provider_payment", provider_payment_reference(attempt.provider, attempt.merchant_trade_no)),
    }
    for scope in ("order", "activity_charge", "membership_charge"):
        if identifier := getattr(attempt, f"{scope}_id"):
            targets.add((scope, identifier))
    return targets


def payment_attempt_is_held():
    return or_(
        cutover_preparing(),
        active_hold("payment_attempt", PaymentAttempt.id),
        active_hold("order", PaymentAttempt.order_id),
        active_hold("activity_charge", PaymentAttempt.activity_charge_id),
        active_hold("membership_charge", PaymentAttempt.membership_charge_id),
    )


async def outbox_targets(session: AsyncSession, event: OutboxEvent) -> set[Target]:
    targets = {("outbox", event.id)}
    if event.aggregate_type in {"order", "activity_charge", "membership_charge"}:
        targets.add((event.aggregate_type, event.aggregate_id))
    payload = event.payload or {}
    invoice_id = payload.get("invoice_id") or (event.aggregate_id if event.aggregate_type == "invoice" else None)
    if invoice_id and (invoice := await session.get(Invoice, invoice_id)):
        targets.add(("order", invoice.order_id))
    refund_id = payload.get("refund_id") or (event.aggregate_id if event.aggregate_type == "refund" else None)
    if refund_id and (refund := await session.get(Refund, refund_id)):
        for scope in ("order", "activity_charge", "membership_charge", "payment_attempt"):
            if identifier := getattr(refund, f"{scope}_id"):
                targets.add((scope, identifier))
    for scope in ("order", "activity_charge", "membership_charge"):
        if identifier := payload.get(f"{scope}_id"):
            targets.add((scope, str(identifier)))
    return targets


class QuarantineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Item(QuarantineInput):
    scope: Scope
    reference: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_reference(self):
        scope, reference = self.scope, self.reference
        if scope == "provider_payment":
            valid = bool(re.fullmatch(r"[0-9a-f]{64}", reference))
        elif scope == "print_lineage":
            kind, _, value = reference.partition(":")
            valid = (value == "all" or bool(re.fullmatch(r"[0-9a-f]{64}", value))) if kind == "summary" else (
                kind in {"order", "summary-order"} and _uuid(value)
            )
        else:
            valid = _uuid(reference)
        if not valid:
            raise ValueError("隔離識別格式不正確")
        return self

    def key(self) -> Target:
        return self.scope, self.reference


def _uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


class Decision(QuarantineInput):
    target: Item
    disposition: Literal["hold", "clear"]
    evidence_sha256: Digest


class Manifest(QuarantineInput):
    cutover_id: UUID
    checkpoint_sha256: Digest
    decisions: list[Decision]

    @model_validator(mode="after")
    def reject_duplicate_targets(self):
        if len({decision.target.key() for decision in self.decisions}) != len(self.decisions):
            raise ValueError("隔離清單含重複識別")
        return self


async def inventory(session: AsyncSession) -> dict[Target, set[Target]]:
    from .recovery_work import print_job_targets

    output: dict[Target, set[Target]] = {}
    events = await session.scalars(select(OutboxEvent).where(OutboxEvent.status != OutboxStatus.COMPLETED))
    for event in events:
        output[("outbox", event.id)] = await outbox_targets(session, event)
    prints = await session.scalars(select(PrintJob).where(PrintJob.status.not_in(FINAL_PRINT_STATES)))
    for job in prints:
        output[("print_job", job.id)] = set(print_job_targets(job))
    attempts = await session.scalars(select(PaymentAttempt).where(PaymentAttempt.status.in_(PENDING_PAYMENTS)))
    for attempt in attempts:
        output[("payment_attempt", attempt.id)] = payment_targets(attempt)
    return output


def _audit(session: AsyncSession, actor: User, cutover_id: str, action: str, data: dict) -> None:
    session.add(AdminAudit(actor_id=actor.id, action=action, aggregate_type="recovery_cutover",
                           aggregate_id=cutover_id, data=data))


def _require_admin(actor: User) -> None:
    if not actor.is_active or actor.user_role != UserRole.ADMIN:
        raise PermissionError("需要有效的管理員帳號")


async def begin_cutover(session: AsyncSession, actor: User, cutover_id: UUID, checkpoint_sha256: str) -> bool:
    _require_admin(actor)
    key = str(cutover_id)
    row = await session.get(RecoveryCutover, key)
    if row:
        if row.checkpoint_sha256 != checkpoint_sha256:
            raise ValueError("隔離作業已綁定其他還原切點")
        return False
    session.add(RecoveryCutover(id=key, checkpoint_sha256=checkpoint_sha256, created_by_id=actor.id))
    _audit(session, actor, key, "recovery.cutover_started", {"checkpoint_sha256": checkpoint_sha256})
    await session.flush()
    return True


async def _lock_cutover(session: AsyncSession, key: str) -> RecoveryCutover | None:
    if session.get_bind().dialect.name == "sqlite":
        await session.execute(update(RecoveryCutover).where(RecoveryCutover.id == key).values(id=RecoveryCutover.id))
    return await session.scalar(select(RecoveryCutover).where(RecoveryCutover.id == key).with_for_update()
                                .execution_options(populate_existing=True))


async def _existing_targets(session: AsyncSession, target: Target) -> set[Target]:
    from .recovery_work import print_job_targets

    scope, reference = target
    model = {"outbox": OutboxEvent, "print_job": PrintJob, "payment_attempt": PaymentAttempt}.get(scope)
    if model is None:
        return {target}
    row = await session.get(model, reference)
    if row is None:
        raise ValueError("隔離清單內的工作識別不存在，請核對還原副本")
    if isinstance(row, OutboxEvent):
        return await outbox_targets(session, row)
    if isinstance(row, PrintJob):
        return set(print_job_targets(row))
    return payment_targets(row)


async def arm_cutover(session: AsyncSession, actor: User, manifest: Manifest) -> int:
    _require_admin(actor)
    key = str(manifest.cutover_id)
    row = await _lock_cutover(session, key)
    if row is None or row.checkpoint_sha256 != manifest.checkpoint_sha256:
        raise ValueError("還原切點與隔離清單不一致")
    ordered = manifest.model_copy(update={"decisions": sorted(manifest.decisions, key=lambda decision: decision.target.key())})
    digest = sha256(ordered.model_dump_json().encode()).hexdigest()
    if row.armed_at:
        if row.manifest_sha256 != digest:
            raise ValueError("已封存的隔離清單無法覆寫")
        return 0
    current = await inventory(session)
    decisions = {decision.target.key(): decision for decision in manifest.decisions}
    if current.keys() - decisions.keys():
        raise ValueError("仍有還原工作未納入對帳清單")
    if any(decision.disposition == "clear" and target not in current for target, decision in decisions.items()):
        raise ValueError("放行項目與目前還原清單不符")
    held: dict[Target, str] = {}
    provider_holds = {target[1] for target, decision in decisions.items()
                      if target[0] == "provider_payment" and decision.disposition == "hold"}
    linked_provider_targets: dict[Target, set[Target]] = {}
    if provider_holds:
        for attempt in await session.scalars(select(PaymentAttempt)):
            reference = provider_payment_reference(attempt.provider, attempt.merchant_trade_no)
            if reference in provider_holds:
                linked_provider_targets[("provider_payment", reference)] = payment_targets(attempt)
    for decision in ordered.decisions:
        target = decision.target.key()
        if decision.disposition == "hold":
            targets = current.get(target) or linked_provider_targets.get(target) or await _existing_targets(session, target)
            for item in targets:
                Item(scope=item[0], reference=item[1])
                held.setdefault(item, decision.evidence_sha256)
    for (scope, reference), evidence in held.items():
        session.add(RecoveryHold(cutover_id=key, scope=scope, reference=reference, evidence_sha256=evidence))
    row.manifest_sha256 = digest
    row.armed_at = utcnow()
    _audit(session, actor, key, "recovery.cutover_armed", {"manifest_sha256": digest, "hold_count": len(held)})
    await session.flush()
    return len(held)


class Resolution(QuarantineInput):
    cutover_id: UUID
    target: Item
    outcome: Literal["confirmed_unsent", "reconciled"]
    evidence_sha256: Digest


async def resolve_hold(session: AsyncSession, actor: User, resolution: Resolution) -> bool:
    _require_admin(actor)
    key = str(resolution.cutover_id)
    cutover = await _lock_cutover(session, key)
    if cutover is None or cutover.armed_at is None:
        raise ValueError("隔離清單尚未封存")
    target = resolution.target.key()
    hold = await session.get(RecoveryHold, (key, *target), populate_existing=True)
    if hold is None:
        raise ValueError("找不到隔離項目")
    if hold.resolved_at:
        if hold.resolution != resolution.outcome or hold.resolution_sha256 != resolution.evidence_sha256:
            raise ValueError("已結案的隔離紀錄無法覆寫")
        return False
    active = set((await session.execute(select(RecoveryHold.scope, RecoveryHold.reference).where(
        RecoveryHold.cutover_id == key, RecoveryHold.resolved_at.is_(None),
    ))).tuples())
    current = await inventory(session)
    if any(source != target and source in active and target in linked for source, linked in current.items()):
        raise ValueError("先完成關聯工作的對帳，再解除共同識別的隔離")
    if resolution.outcome == "reconciled":
        model = {"outbox": OutboxEvent, "print_job": PrintJob, "payment_attempt": PaymentAttempt}.get(hold.scope)
        if model is not None and (target in current or await session.get(model, hold.reference) is None):
            raise ValueError("工作尚未登錄為完成，請保留隔離")
    hold.resolved_at = utcnow()
    hold.resolved_by_id = actor.id
    hold.resolution = resolution.outcome
    hold.resolution_sha256 = resolution.evidence_sha256
    _audit(session, actor, key, "recovery.hold_resolved", {
        "scope": hold.scope,
        "reference_sha256": sha256(hold.reference.encode()).hexdigest(),
        "outcome": resolution.outcome,
        "evidence_sha256": resolution.evidence_sha256,
    })
    await session.flush()
    return True
