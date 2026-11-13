from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models import Order, OrderItem, PrintJob
from app.printing import enqueue_amendment_print, format_receipt, stop_cancelled_order_prints
from app.printer_worker import process_print_job
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import cash_context
from tests.test_feie_printing import print_context, set_job

async def amend(c, *, revision, quantity):
    async with c.factory() as session:
        order = await session.scalar(select(Order).where(Order.id == c.order_id).options(
            selectinload(Order.items).selectinload(OrderItem.selected_options), selectinload(Order.fulfillment)))
        order.revision = revision
        order.items[0].quantity = quantity
        order.items[0].subtotal = order.items[0].unit_price * quantity
        order.amount_total = sum(item.subtotal for item in order.items)
        await enqueue_amendment_print(session, order, {}, c.settings)
        await session.flush()
        await enqueue_amendment_print(session, order, {}, c.settings)
        await session.commit()
        return list(await session.scalars(select(PrintJob).where(PrintJob.order_id == order.id)))

@pytest.mark.parametrize('old_status', ['pending', 'printed', 'submitted', 'sending', 'unknown', 'simulated'])
async def test_amendment_replaces_unsent_and_distinguishes_sent_receipts(print_context, old_status):
    c = print_context
    await set_job(c, status=old_status)
    jobs = await amend(c, revision=2, quantity=3)
    assert len(jobs) == 2
    original = next(job for job in jobs if job.id == c.job_id)
    current = next(job for job in jobs if job.id != c.job_id)
    assert current.snapshot['revision'] == 2
    assert current.snapshot['items'][0]['quantity'] == 3
    assert current.snapshot['needs_utensils'] == original.snapshot['needs_utensils']
    if old_status == 'pending':
        assert original.status == 'failed'
        assert original.last_error == 'order_revision_superseded'
        assert current.kind == 'order'
    else:
        assert original.status == old_status
        assert current.kind == 'amendment'
        assert current.snapshot['quantity_changes'][0]['after'] == 3
        assert '數量修訂單' in format_receipt(current.snapshot)
        assert '請依最新版調整原單' in format_receipt(current.snapshot)

async def test_second_amendment_compares_with_last_sent_and_cancellation_stops_latest(print_context):
    c = print_context
    await set_job(c, status='printed')
    await amend(c, revision=2, quantity=3)
    jobs = await amend(c, revision=3, quantity=4)
    latest = next(job for job in jobs if job.snapshot.get('revision') == 3)
    original = next(job for job in jobs if job.id == c.job_id)
    assert latest.snapshot['quantity_changes'][0]['before'] == original.snapshot['items'][0]['quantity']
    assert latest.snapshot['quantity_changes'][0]['after'] == 4
    assert next(job for job in jobs if job.snapshot.get('revision') == 2).status == 'failed'
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = '核准退單'
        await stop_cancelled_order_prints(session, order)
        await session.commit()
        stopped = await session.get(PrintJob, latest.id)
        assert stopped.status == 'failed'
        assert stopped.last_error == 'order_cancelled_before_send'
        assert await session.scalar(select(PrintJob).where(PrintJob.order_id == order.id, PrintJob.kind == 'cancellation')) is None

async def test_worker_prints_only_current_revision(print_context):
    c = print_context
    await amend(c, revision=2, quantity=3)
    assert await process_print_job(c.settings, factory=c.factory)
    assert not await process_print_job(c.settings, factory=c.factory)
    async with c.factory() as session:
        jobs = list(await session.scalars(select(PrintJob).where(PrintJob.order_id == c.order_id)))
        assert sum(job.status == 'simulated' for job in jobs) == 1
        assert next(job for job in jobs if job.status == 'simulated').snapshot['revision'] == 2

async def test_old_receipt_cannot_be_reprinted_after_amendment(print_context):
    c = print_context
    await set_job(c, status='printed')
    await amend(c, revision=2, quantity=3)
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        response = await client.post(f'/v1/admin/printers/jobs/{c.job_id}/reprint',
            headers=auth_headers(c.admin), json={'request_id': str(uuid4()), 'reason': '核對新版'})
        assert response.status_code == 409
        assert '最新版' in response.json()['detail']
