from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
from app.integrations.invoice_service import issue_paid_order_invoice
from app.integrations.invoice_void import void_refunded_order_invoice
from app.invoice_timing import cancel_unissued_invoice_jobs
from app.models import (
    Invoice, InvoiceStatus, OutboxEvent, OutboxStatus, PaymentAttempt, PrintJob, RecoveryCutover, RecoveryHold,
    Refund, RefundStatus, User, UserRole,
)
from app.printer_worker import process_print_job
from app.printing import enqueue_cancellation_print
from app.recovery_quarantine import (
    Item, RecoveryHoldActive, Resolution, arm_cutover, begin_cutover, resolve_hold,
)
from app.recovery_work import SUMMARY_ALL_TARGET, print_job_targets
from app.routers.operations import job_retry_blocker
from tests.support import auth_headers, make_test_settings
from tests.test_integrations import make_regular_order
from tests.test_order_summary import PARAMS, line, summary_context
from tests.test_recovery_quarantine import CHECKPOINT, RESOLUTION, decision, manifest
from tests.test_summary_printing import BASE, create_batch, paid_order, preview, summary_print_context


pytestmark = pytest.mark.asyncio


async def _hold(session, scope, reference):
    cutover = await session.scalar(select(RecoveryCutover))
    if cutover is None:
        actor = User(email="recovery-admin@example.test", display_name="復原測試", password_hash="test", user_role=UserRole.ADMIN)
        session.add(actor)
        await session.flush()
        cutover = RecoveryCutover(id=str(uuid4()), created_by_id=actor.id, checkpoint_sha256="a" * 64,
                                  manifest_sha256="b" * 64, armed_at=datetime.now(timezone.utc))
        session.add(cutover)
        await session.flush()
    session.add(RecoveryHold(cutover_id=cutover.id, scope=scope, reference=reference, evidence_sha256="c" * 64))
    await session.commit()


def _event(event_type, aggregate_type, aggregate_id, now, **kwargs):
    return OutboxEvent(event_type=event_type, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                       available_at=now, created_at=now, **kwargs)


async def test_outbox_holds_skip_stale_exact_and_alternate_intents_before_limit(database_session, monkeypatch):
    session = database_session
    user, _, order = await make_regular_order(session)
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=20)
    attempt = PaymentAttempt(order_id=order.id, provider="raygate", merchant_trade_no="HELDRECOVERY1",
                             amount=order.amount_total, expires_at=now)
    invoice = Invoice(order=order, relate_number="RECOVERY-INVOICE-1", provider="fanyu", status=InvoiceStatus.PENDING,
                      total_amount=order.amount_total)
    session.add_all([attempt, invoice])
    await session.flush()
    refund = Refund(order=order, payment_attempt=attempt, provider="raygate", amount=order.amount_total,
                    status=RefundStatus.PENDING, reason="", requested_by_id=user.id)
    session.add(refund)
    await session.flush()
    exact = _event("send_email", "order", order.id, old, status=OutboxStatus.PROCESSING, attempts=2)
    issue = _event("issue_invoice", "order", order.id, old)
    void = _event("invoice.adjustment_required", "invoice", invoice.id, old)
    legacy_refund = _event("sandbox_refund", "order", order.id, old)
    modern_refund = _event("refund.requested", "refund", refund.id, old, payload={"refund_id": refund.id})
    unrelated = _event("inventory.domain_event", "order", str(uuid4()), now)
    session.add_all([exact, issue, void, legacy_refund, modern_refund, unrelated])
    await session.commit()
    await _hold(session, "outbox", exact.id)
    await _hold(session, "order", order.id)
    dispatch = AsyncMock()
    monkeypatch.setattr(jobs, "_dispatch_outbox_event", dispatch)
    assert await jobs._process_outbox(session, make_test_settings(recovery_mode=False), now, limit=1) == (1, 0)
    dispatch.assert_awaited_once_with(session, make_test_settings(recovery_mode=False), unrelated.id)
    assert exact.status == OutboxStatus.PROCESSING and exact.attempts == 2
    assert unrelated.status == OutboxStatus.COMPLETED
    assert all(row.status == OutboxStatus.PENDING and row.attempts == 0
               for row in (issue, void, legacy_refund, modern_refund))
    await session.execute(delete(RecoveryHold).where(RecoveryHold.scope == "order"))
    await _hold(session, "payment_attempt", attempt.id)
    assert await jobs._process_outbox(session, make_test_settings(), now, limit=10) == (2, 0)
    assert legacy_refund.status == modern_refund.status == OutboxStatus.PENDING
    assert legacy_refund.attempts == modern_refund.attempts == 0


async def test_held_invoice_issue_void_and_ordinary_admin_retry_fail_before_effect(database_session):
    session = database_session
    _, _, order = await make_regular_order(session)
    now = datetime.now(timezone.utc)
    event = _event("invoice.issue_requested", "order", order.id, now, status=OutboxStatus.FAILED)
    session.add(event)
    await session.commit()
    await _hold(session, "order", order.id)
    adapter = AsyncMock()
    with pytest.raises(RecoveryHoldActive):
        await issue_paid_order_invoice(session, order.id, adapter)
    with pytest.raises(RecoveryHoldActive):
        await void_refunded_order_invoice(session, order.id, adapter, reason="", actor_id=order.user_id)
    assert not adapter.mock_calls
    assert "復原核對" in await job_retry_blocker(session, event)
    assert event.status == OutboxStatus.FAILED and event.attempts == 0


async def test_printer_hold_excludes_stale_and_recreated_summary_with_overlapping_sources(database_session):
    session = database_session
    _, _, order = await make_regular_order(session)
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=20)
    settings = make_test_settings(recovery_mode=False, printer_enabled=True, printer_dry_run=True)
    pending = PrintJob(kind="summary", event_key="new-request", mode="dry_run", printer_fingerprint="X",
                       snapshot={"source_versions": [{"id": order.id}], "fingerprint": "a" * 64},
                       created_at=old, next_attempt_at=old)
    sending = PrintJob(kind="summary", event_key="old-request", mode="live", status="sending",
                       printer_fingerprint="X", snapshot={}, created_at=old, next_attempt_at=old, lease_until=old)
    other = PrintJob(kind="summary", event_key="unrelated", mode="live", status="sending",
                     printer_fingerprint="X", snapshot={"source_versions": ["bad legacy value"]},
                     created_at=old, next_attempt_at=old, lease_until=old)
    session.add_all([pending, sending, other])
    await session.commit()
    await _hold(session, "print_lineage", f"summary-order:{order.id}")
    await _hold(session, "print_job", sending.id)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    vendor = AsyncMock()
    assert await process_print_job(settings, factory=factory, client=vendor, now=now) is False
    assert not vendor.mock_calls
    for row in (pending, sending, other):
        await session.refresh(row)
    assert pending.status == "pending" and pending.attempts == 0
    assert sending.status == "sending" and sending.lease_until is not None
    assert other.status == "unknown" and other.lease_until is None
    assert len(list(await session.scalars(select(PrintJob)))) == 3


async def test_order_cancellation_preserves_exact_held_invoice_and_print_evidence(database_session):
    session = database_session
    _, _, order = await make_regular_order(session)
    now = datetime.now(timezone.utc)
    held_invoice = _event("invoice.issue_requested", "order", order.id, now)
    other_invoice = _event("invoice.issue_requested", "order", order.id, now)
    held_print = PrintJob(order_id=order.id, kind="order", event_key="held", mode="dry_run",
                         printer_fingerprint="X", snapshot={})
    other_print = PrintJob(order_id=order.id, kind="order", event_key="unheld", mode="dry_run",
                          printer_fingerprint="X", snapshot={})
    session.add_all([held_invoice, other_invoice, held_print, other_print])
    await session.commit()
    await _hold(session, "outbox", held_invoice.id)
    await _hold(session, "print_job", held_print.id)
    await cancel_unissued_invoice_jobs(session, order)
    await enqueue_cancellation_print(session, order, make_test_settings())
    await session.commit()
    for row in (held_invoice, other_invoice, held_print, other_print):
        await session.refresh(row)
    assert held_invoice.status == OutboxStatus.PENDING and held_invoice.processed_at is None
    assert other_invoice.status == OutboxStatus.COMPLETED
    assert held_print.status == "pending" and held_print.last_error is None
    assert other_print.status == "failed"


async def test_new_summary_request_with_held_source_is_blocked_but_other_batch_runs(summary_print_context):
    context = summary_print_context
    held = await paid_order(context, label="HELD-RECOVERY", items=[line(name="Held product")])
    await paid_order(context, label="UNRELATED-ORDER", items=[line(name="Unrelated product")])
    data = await preview(context, search="Held product")
    await _hold(context.session, "print_lineage", f"summary-order:{held.id}")
    with pytest.raises(RecoveryHoldActive):
        await context.client.post(f"{BASE}/print-jobs", headers=auth_headers(context.admin), json={
            "request_id": str(uuid4()),
            "fingerprint": data["fingerprint"],
            "filters": PARAMS | {"search": "Held product"},
        })
    await create_batch(context, search="Unrelated product")
    vendor = AsyncMock()
    assert await process_print_job(context.settings, factory=context.factory, client=vendor) is True
    assert not vendor.mock_calls
    async with context.factory() as session:
        jobs = list(await session.scalars(select(PrintJob)))
        assert [job.status for job in jobs] == ["simulated"]


async def test_legacy_summary_with_invalid_sources_holds_summaries_only(database_session):
    session = database_session
    _, _, order = await make_regular_order(session)
    now = datetime.now(timezone.utc)
    old = now - timedelta(minutes=20)
    settings = make_test_settings(recovery_mode=False, printer_enabled=True, printer_dry_run=True)
    legacy = PrintJob(kind="summary", event_key="legacy-uncertain", mode="live", status="sending",
                      printer_fingerprint="X", snapshot={"source_versions": [{"id": order.id}, "bad value"], "fingerprint": "bad fingerprint"},
                      created_at=old, next_attempt_at=old, lease_until=old)
    fresh = PrintJob(kind="summary", event_key="new-summary", mode="dry_run", printer_fingerprint="X",
                     snapshot={"source_versions": [{"id": order.id}]}, created_at=old, next_attempt_at=old)
    normal = PrintJob(kind="order", event_key="normal-order", order_id=order.id, mode="live", status="sending",
                      printer_fingerprint="X", snapshot={}, created_at=old, next_attempt_at=old, lease_until=old)
    session.add_all([legacy, fresh, normal])
    await session.commit()
    assert set(print_job_targets(legacy)) == {("print_job", legacy.id), SUMMARY_ALL_TARGET}
    assert SUMMARY_ALL_TARGET not in print_job_targets(fresh)
    await _hold(session, *SUMMARY_ALL_TARGET)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    vendor = AsyncMock()
    assert await process_print_job(settings, factory=factory, client=vendor, now=now) is False
    assert not vendor.mock_calls
    for row in (legacy, fresh, normal):
        await session.refresh(row)
    assert legacy.status == "sending" and legacy.lease_until is not None
    assert fresh.status == "pending" and fresh.attempts == 0
    assert normal.status == "unknown" and normal.lease_until is None


@pytest.mark.parametrize("field, value", [
    ("source_versions", None), ("source_versions", ["broken legacy entry"]),
    ("source_versions", []), ("source_versions", "wrong_sources"),
    ("filters", None), ("fingerprint", None),
])
async def test_resumed_printer_skips_invalid_legacy_summary_without_stalling_later_jobs(
    summary_print_context, field, value,
):
    context = summary_print_context
    await paid_order(context, label="LEGACY-SUMMARY", items=[line(name="Old product")])
    await paid_order(context, label="NEW-SUMMARY", items=[line(name="New product")])
    old_batch = await create_batch(context, search="Old product")
    new_batch = await create_batch(context, search="New product")
    old_job = await context.session.get(PrintJob, old_batch["pages"][0]["id"])
    new_job = await context.session.get(PrintJob, new_batch["pages"][0]["id"])
    snapshot = dict(old_job.snapshot)
    if value is None:
        del snapshot[field]
    else:
        snapshot[field] = new_job.snapshot[field] if value == "wrong_sources" else value
    old_job.snapshot = snapshot
    now = datetime.now(timezone.utc)
    old_job.created_at = now - timedelta(minutes=1)
    cutover_id = uuid4()
    await begin_cutover(context.session, context.admin, cutover_id, CHECKPOINT)
    targets = print_job_targets(old_job)
    await arm_cutover(context.session, context.admin, manifest(
        cutover_id, decision("print_job", old_job.id), decision("print_job", new_job.id, "clear"),
    ))
    await context.session.commit()

    # A held legacy snapshot remains available for reconciliation.
    vendor = AsyncMock()
    if ("print_lineage", "summary:all") in targets:
        assert not await process_print_job(context.settings, factory=context.factory, client=vendor, now=now)
    await context.session.refresh(old_job)
    assert old_job.status == "pending"
    for scope, reference in targets:
        await resolve_hold(context.session, context.admin, Resolution(
            cutover_id=cutover_id, target=Item(scope=scope, reference=reference),
            outcome="confirmed_unsent", evidence_sha256=RESOLUTION,
        ))
    await context.session.commit()

    # Confirmation releases the hold, but cannot make a broken page safe to print.
    assert await process_print_job(context.settings, factory=context.factory, client=vendor, now=now)
    await context.session.refresh(old_job)
    assert old_job.status == "failed" and old_job.last_error == "summary_changed_before_send"
    assert old_job.attempts == 0
    assert await process_print_job(context.settings, factory=context.factory, client=vendor, now=now)
    await context.session.refresh(new_job)
    assert new_job.status == "simulated"
    assert not vendor.mock_calls
