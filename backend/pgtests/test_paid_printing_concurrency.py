"""Only the disposable PostgreSQL CI database; Feie calls remain in memory."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.cash_payments import confirm_cash_payment, mark_cash_unpaid
from app.models import Order, OrderItem, PaymentStatus, PrintJob
from app.printer_worker import process_print_job
from app.printing import enqueue_order_print
from app.routers.orders import load_order
from pgtests.test_amendment_fulfillment_concurrency import ControlledPrinter, ordering_hours, wait_for_lock_waiter
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.test_cash_payments_concurrency import cash_api, order_body
from tests.test_feie_printing import live_settings


async def queued_order(sessions, request, buyer, event, settings):
    response = await request("POST", f"/v1/meal-events/{event['id']}/orders", buyer, order_body(event, buyer))
    assert response.status_code == 201, response.text
    order_id = response.json()["id"]
    async with sessions() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id).options(
            selectinload(Order.items).selectinload(OrderItem.selected_options), selectinload(Order.fulfillment)))
        await enqueue_order_print(session, order, buyer, settings)
        await session.commit()
    return order_id


async def test_uncommitted_collection_never_sends_then_two_workers_claim_once(postgres_sessions, cash_api):
    request, buyer, _other, admin, _second_admin, event = cash_api
    settings = live_settings()
    order_id = await queued_order(postgres_sessions, request, buyer, event, settings)
    printer = ControlledPrinter(hold_first=True)
    async with postgres_sessions() as session:
        order = await load_order(session, order_id, lock=True, refresh=True)
        await confirm_cash_payment(session, order, amount=100, reason="隔離核帳", actor_id=admin.id,
            settings=settings, now=datetime.now(timezone.utc))
        await session.flush()
        assert not await process_print_job(settings, factory=postgres_sessions, client=printer)
        assert not printer.submissions
        await session.commit()

    first = asyncio.create_task(process_print_job(settings, factory=postgres_sessions, client=printer))
    try:
        await asyncio.wait_for(printer.started.wait(), timeout=5)
        assert not await process_print_job(settings, factory=postgres_sessions, client=printer)
        assert len(printer.submissions) == 1
    finally:
        printer.release.set()
        await asyncio.wait_for(first, timeout=10)
    async with postgres_sessions() as session:
        job = await session.scalar(select(PrintJob).where(PrintJob.order_id == order_id))
        assert job.status == "submitted" and job.attempts == 1
        assert job.snapshot["payment_status"] == "已付款"


async def test_cash_correction_wins_order_lock_before_print_claim(postgres_sessions, cash_api):
    request, buyer, _other, admin, _second_admin, event = cash_api
    settings = live_settings()
    order_id = await queued_order(postgres_sessions, request, buyer, event, settings)
    paid = await request("POST", f"/v1/orders/{order_id}/admin/cash-payment", admin,
        {"amount": 100, "reason": "隔離已收款"})
    assert paid.status_code == 200, paid.text
    printer = ControlledPrinter()
    async with postgres_sessions() as session:
        blocker = await session.scalar(text("SELECT pg_backend_pid()"))
        order = await load_order(session, order_id, lock=True, refresh=True)
        await mark_cash_unpaid(session, order, amount=100, reason="隔離更正收款", actor_id=admin.id,
            now=datetime.now(timezone.utc))
        await session.flush()
        worker = asyncio.create_task(process_print_job(settings, factory=postgres_sessions, client=printer))
        try:
            await wait_for_lock_waiter(postgres_sessions, blocker)
            assert not printer.submissions
            await session.commit()
            assert await asyncio.wait_for(worker, timeout=10)
        finally:
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    assert not printer.submissions
    async with postgres_sessions() as session:
        order = await session.get(Order, order_id)
        job = await session.scalar(select(PrintJob).where(PrintJob.order_id == order_id))
        assert order.payment_status == PaymentStatus.PENDING and order.paid_at is None
        assert job.status == "pending" and job.attempts == 0 and job.content is None
