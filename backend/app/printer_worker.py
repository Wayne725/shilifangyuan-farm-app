from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import or_, select, update

from .config import Settings, get_settings
from .database import SessionLocal
from .integrations.feie import FeieClient, FeieError, validate_live_config
from .models import Order, PrintJob
from .printing import format_receipt, printer_fingerprint

logger = logging.getLogger(__name__)
_wake: asyncio.Event | None = None


def wake_printer() -> None:
    if _wake is not None:
        _wake.set()


async def process_print_job(settings: Settings, *, factory=SessionLocal, client=None, now=None) -> bool:
    if not settings.printer_enabled:
        return False
    now = now or datetime.now(timezone.utc)
    # A crashed sender may already have delivered its HTTP request. Never reclaim it for submission.
    async with factory() as session:
        await session.execute(update(PrintJob).where(PrintJob.status == "sending", PrintJob.lease_until <= now).values(
            status="unknown", last_error="sender_interrupted_result_unknown", lease_until=None, lease_token=None, next_attempt_at=now))
        await session.commit()
        candidate = (await session.execute(select(PrintJob.id, PrintJob.order_id).where(
            PrintJob.next_attempt_at <= now,
            or_(PrintJob.status == "pending", (PrintJob.status.in_(["submitted", "unknown"]) & PrintJob.vendor_order_id.is_not(None))),
            or_(PrintJob.lease_until.is_(None), PrintJob.lease_until <= now),
        ).order_by(PrintJob.created_at, PrintJob.id).limit(1))).first()
        if candidate is None:
            return False
        await session.scalar(select(Order.id).where(Order.id == candidate.order_id).with_for_update())
        job = await session.scalar(select(PrintJob).where(PrintJob.id == candidate.id).with_for_update())
        if job.status not in {"pending", "submitted", "unknown"} or _aware(job.next_attempt_at) > now or (job.lease_until and _aware(job.lease_until) > now):
            return True
        if job.kind in {"cancellation", "amendment"} and job.status == "pending":
            sending = await session.scalar(select(PrintJob.id).where(PrintJob.order_id == job.order_id, PrintJob.id != job.id, PrintJob.kind.in_({"order", "amendment"}), PrintJob.status == "sending").limit(1))
            if sending:
                job.next_attempt_at = now + timedelta(seconds=settings.printer_poll_seconds)
                await session.commit()
                return True
        order = await session.get(Order, job.order_id)
        if job.status == "pending" and job.kind in {"order", "amendment"} and job.snapshot.get("revision", 1) != order.revision:
            job.status, job.last_error = "failed", "order_revision_superseded"
            await session.commit()
            return True
        mode = "dry_run" if settings.printer_dry_run else "live"
        if job.mode != mode or job.printer_fingerprint != printer_fingerprint(settings):
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
        snapshot = {**job.snapshot, "reprint": bool(job.reprint_of_id), "dry_run": mode == "dry_run"}
        job_id, vendor_id, attempts = job.id, job.vendor_order_id, job.attempts + int(submitting)
        await session.commit()
    values = {"lease_token": None, "lease_until": None, "last_error": None, "updated_at": now,
              "next_attempt_at": now + timedelta(seconds=settings.printer_query_seconds)}
    try:
        if submitting:
            content = format_receipt(snapshot, width=settings.printer_line_width)
            values.update(content=content, attempts=attempts)
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
    if not settings.printer_enabled or not settings.printer_worker_enabled:
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
    async with periodic_printing(settings):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
