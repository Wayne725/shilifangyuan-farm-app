from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import selectinload

from .config import Settings, get_settings
from .database import SessionLocal
from .integrations.feie import FeieClient, FeieError, validate_live_config
from .models import FulfillmentStatus, Order, PaymentStatus, PrintJob
from .manual_order_printing import current_manual_snapshot, load_manual_print_order, manual_print_kind, manual_print_status_error
from .printing import (business_preparation_print_allowed, business_preparation_window_open, business_receipt_fields, confirmed_print_payment, format_receipt, job_matches_printer, open_receipt_content, payment_snapshot_from_session,
                       receipt_time, render_receipt_snapshot, seal_receipt_content)
from .summary_printing import summary_job_is_current

logger = logging.getLogger(__name__)
_wake: asyncio.Event | None = None


def wake_printer() -> None:
    if _wake is not None:
        _wake.set()


async def process_print_job(settings: Settings, *, factory=SessionLocal, client=None, now=None) -> bool:
    if settings.recovery_mode or not settings.printer_enabled:
        return False
    now = now or datetime.now(timezone.utc)
    # A crashed sender may already have delivered its HTTP request. Never reclaim it for submission.
    async with factory() as session:
        await session.execute(update(PrintJob).where(PrintJob.status == "sending", PrintJob.lease_until <= now).values(
            status="unknown", last_error="sender_interrupted_result_unknown", lease_until=None, lease_token=None, next_attempt_at=now))
        await session.commit()
        candidate = (await session.execute(select(PrintJob.id, PrintJob.order_id).outerjoin(Order, Order.id == PrintJob.order_id).where(
            PrintJob.next_attempt_at <= now,
            or_(and_(PrintJob.status == "pending", or_(
                PrintJob.kind.in_({"cancellation", "summary", "reference"}),
                PrintJob.reprint_of_id.is_not(None),
                and_(PrintJob.kind == "order", PrintJob.snapshot["manual"].as_boolean().is_(True)),
                and_(Order.meal_booking_type == "business", Order.payment_status == PaymentStatus.PENDING),
                Order.cancelled_at.is_not(None), Order.fulfillment_status == FulfillmentStatus.CANCELLED,
                Order.payment_status.in_([PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED]),
                and_(Order.payment_status == PaymentStatus.PAID, Order.paid_at.is_not(None)),
            )), (PrintJob.status.in_(["submitted", "unknown"]) & PrintJob.vendor_order_id.is_not(None))),
            or_(PrintJob.lease_until.is_(None), PrintJob.lease_until <= now),
        ).order_by(PrintJob.created_at, PrintJob.event_key, PrintJob.id).limit(1))).first()
        if candidate is None:
            return False
        order = (await session.scalar(select(Order).where(Order.id == candidate.order_id)
                 .options(selectinload(Order.fulfillment))
                 .execution_options(populate_existing=True).with_for_update())) if candidate.order_id else None
        job = await session.scalar(select(PrintJob).where(PrintJob.id == candidate.id).with_for_update())
        if job.status not in {"pending", "submitted", "unknown"} or _aware(job.next_attempt_at) > now or (job.lease_until and _aware(job.lease_until) > now):
            return True
        if job.kind == "reference" or job.snapshot.get("manual") or job.reprint_of_id:
            order = await load_manual_print_order(session, job.order_id)
            if job.status == "pending" and (blocked := manual_print_status_error(order)):
                job.status, job.last_error = "failed", blocked
                await session.commit()
                return True
        if job.kind in {"cancellation", "amendment"} and job.status == "pending":
            sending = await session.scalar(select(PrintJob.id).where(PrintJob.order_id == job.order_id, PrintJob.id != job.id, PrintJob.kind.in_({"order", "amendment"}), PrintJob.status == "sending").limit(1))
            if sending:
                job.next_attempt_at = now + timedelta(seconds=settings.printer_poll_seconds)
                await session.commit()
                return True
        if job.status == "pending" and job.kind in {"order", "amendment"}:
            if order.cancelled_at is not None or order.fulfillment_status == FulfillmentStatus.CANCELLED:
                job.status, job.last_error = "failed", "order_cancelled_before_send"
                await session.commit()
                return True
            if order.payment_status in {PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED}:
                job.status, job.last_error = "failed", "order_payment_reversed_before_send"
                await session.commit()
                return True
            if business_receipt_fields(order) and not business_preparation_window_open(order, now=now):
                job.status, job.last_error = "failed", "business_preparation_closed_before_send"
                await session.commit()
                return True
            if not confirmed_print_payment(order) and not business_preparation_print_allowed(order, job, now=now):
                if job.snapshot.get("manual") or business_receipt_fields(order):
                    job.status, job.last_error = "failed", "order_payment_unconfirmed_before_send"
                    await session.commit()
                return True
            if job.snapshot.get("manual") and manual_print_kind(order) != "order":
                job.status, job.last_error = "failed", "order_cancelled_before_send"
                await session.commit()
                return True
        if job.status == "pending" and job.kind in {"order", "amendment"} and job.snapshot.get("revision", 1) != order.revision:
            job.status, job.last_error = "failed", "order_revision_superseded"
            await session.commit()
            return True
        mode = "dry_run" if settings.printer_dry_run else "live"
        if not job_matches_printer(job, settings):
            if job.status == "pending" and (job.kind in {"summary", "reference"} or job.snapshot.get("manual")):
                job.status = "failed"
            job.last_error = "printer_configuration_changed"
            job.next_attempt_at = now + timedelta(seconds=60)
            await session.commit()
            return True
        if mode == "live":
            try:
                validate_live_config(settings)
            except FeieError as exc:
                job.last_error = exc.code
                job.next_attempt_at = now + timedelta(seconds=60)
                await session.commit()
                return True
        if job.kind == "summary" and job.status == "pending":
            source_ids = [source["id"] for source in job.snapshot["source_versions"]]
            await session.execute(select(Order.id).where(Order.id.in_(source_ids)).order_by(Order.id).with_for_update())
            if not await summary_job_is_current(session, job, settings):
                job.status, job.last_error = "failed", "summary_changed_before_send"
                await session.commit()
                return True
            if job.content is None:
                job.status, job.last_error = "failed", "summary_content_missing"
                await session.commit()
                return True
        if (job.status == "pending" and job.kind in {"order", "amendment"} and job.content is not None
                and job.snapshot.get("payment_status") != "已付款" and not business_preparation_print_allowed(order, job, now=now)
                and not business_receipt_fields(order)):
            job.status, job.last_error = "failed", "unpaid_receipt_snapshot"
            await session.commit()
            return True
        submitting = job.status == "pending"
        token = str(uuid4())
        # The compare-and-swap also protects SQLite and a claim racing another process.
        claimed = await session.execute(update(PrintJob).where(PrintJob.id == job.id, PrintJob.status == job.status,
            or_(PrintJob.lease_until.is_(None), PrintJob.lease_until <= now)).values(
                status="sending" if submitting else job.status, lease_token=token,
                lease_until=now + timedelta(seconds=settings.printer_lease_seconds)))
        if claimed.rowcount != 1:
            await session.rollback()
            return True
        job_id, vendor_id, attempts = job.id, job.vendor_order_id, job.attempts + int(submitting)
        content = job.content
        if submitting:
            try:
                if job.kind == "reference" or (job.kind == "order" and job.snapshot.get("manual")):
                    snapshot = await current_manual_snapshot(session, order, settings, reference=job.kind == "reference")
                    snapshot["print_requested_at"] = receipt_time(now)
                    job.snapshot = snapshot
                    content = format_receipt({**render_receipt_snapshot(snapshot, job.order_id, settings),
                        "reprint": bool(job.reprint_of_id), "dry_run": mode == "dry_run"}, width=settings.printer_line_width)
                    job.content = seal_receipt_content(content, snapshot, job.order_id, settings, force=True)
                elif content is None or business_receipt_fields(order):
                    snapshot = {key: value for key, value in job.snapshot.items() if key != "customer_phone"}
                    snapshot.update(business_receipt_fields(order))
                    snapshot.update(await payment_snapshot_from_session(session, order))
                    if not snapshot.get("print_requested_at"):
                        snapshot["print_requested_at"] = receipt_time(now)
                    job.snapshot = snapshot
                    rendered = render_receipt_snapshot(snapshot, job.order_id, settings)
                    content = format_receipt({**rendered, "reprint": bool(job.reprint_of_id), "dry_run": mode == "dry_run"}, width=settings.printer_line_width)
                    job.content = seal_receipt_content(content, snapshot, job.order_id, settings)
                else:
                    content = open_receipt_content(content, job.order_id or f"summary:{job.id}", settings)
            except FeieError as exc:
                job.status, job.last_error, job.attempts = "failed", exc.code, attempts
                job.lease_token = job.lease_until = None
                await session.commit()
                return True
        await session.commit()
    values = {"lease_token": None, "lease_until": None, "last_error": None, "updated_at": now,
              "next_attempt_at": now + timedelta(seconds=settings.printer_query_seconds)}
    try:
        if submitting:
            values["attempts"] = attempts
            if mode == "dry_run":
                values.update(status="simulated", last_result={"dry_run": True})
            else:
                vendor_id = await (client or FeieClient(settings)).submit(content)
                values.update(status="submitted", vendor_order_id=vendor_id, submitted_at=now, last_result={"ret": 0, "accepted": True})
        else:
            printed = await (client or FeieClient(settings)).query(vendor_id)
            values.update(status="printed" if printed else "submitted", last_result={"ret": 0, "printed": printed})
            if printed:
                values["printed_at"] = now
    except FeieError as exc:
        values["last_error"] = exc.code
        if submitting:
            values["attempts"] = attempts
            values["status"] = "pending" if exc.safe_to_retry and attempts < settings.printer_max_attempts else "unknown" if exc.uncertain else "failed"
            values["next_attempt_at"] = now + timedelta(seconds=min(300, 5 * 2 ** attempts))
        else:
            # Query errors never cause a second submission.
            values["status"] = "unknown"
    async with factory() as session:
        await session.execute(update(PrintJob).where(PrintJob.id == job_id, PrintJob.lease_token == token).values(**values))
        await session.commit()
    return True


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@asynccontextmanager
async def periodic_printing(settings: Settings):
    global _wake
    if settings.recovery_mode or not settings.printer_enabled or not settings.printer_worker_enabled:
        yield
        return
    event = asyncio.Event()
    _wake = event

    async def run():
        while True:
            try:
                if await process_print_job(settings):
                    continue
            except Exception:
                # No exception bodies: HTTP exceptions can carry credentials or receipt PII.
                logger.error("printer_worker_iteration_failed")
            try:
                await asyncio.wait_for(event.wait(), timeout=settings.printer_poll_seconds)
            except TimeoutError:
                pass
            event.clear()

    task = asyncio.create_task(run(), name="feie-printer")
    try:
        yield
    finally:
        _wake = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def main():
    settings = get_settings().model_copy(update={"printer_worker_enabled": True})
    if settings.recovery_mode:
        logger.info("printer_worker_paused_recovery_mode")
        return
    async with periodic_printing(settings):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
