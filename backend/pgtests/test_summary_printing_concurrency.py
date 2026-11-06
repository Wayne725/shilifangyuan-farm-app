"""Only the disposable PostgreSQL CI database; all printer calls stay in memory."""

import asyncio
import base64
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

import app.routers.order_summary as summary_routes
from app.models import AdminAudit, FulfillmentStatus, Order, PaymentStatus, PrintJob, User, UserRole
from app.printer_worker import process_print_job
from app.routers.order_summary import order_summary_router
from pgtests.test_amendment_fulfillment_concurrency import ControlledPrinter, wait_for_lock_waiter
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers
from tests.test_feie_printing import live_settings
from tests.test_integrations import make_regular_order
from tests.test_order_summary import NOW, PARAMS


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Disposable PostgreSQL not configured")
BASE = "/v1/admin/order-summary"


@pytest.fixture
async def summary_pg_context(postgres_sessions):
    settings = live_settings(
        pii_encryption_keys_json=json.dumps({"test": base64.b64encode(b"s" * 32).decode()}),
        pii_encryption_current_version="test",
    )
    async with postgres_sessions() as session:
        _buyer, _product, order = await make_regular_order(session)
        order.payment_status = PaymentStatus.PAID
        order.paid_at = order.created_at = NOW
        order.fulfillment_status = FulfillmentStatus.PREPARING
        admin = User(email="summary-concurrency-admin@example.test", display_name="彙總管理員",
                     password_hash="test", user_role=UserRole.ADMIN)
        session.add(admin)
        await session.commit()
        admin_id, order_id = admin.id, order.id

    async def request(method, path, *, body=None):
        async with postgres_sessions() as session:
            admin = await session.get(User, admin_id)
            async with api_test_context(session, [order_summary_router], settings=settings) as client:
                return await client.request(method, path, headers=auth_headers(admin), json=body,
                    params=PARAMS if path.endswith("print-preview") else None)

    return SimpleNamespace(factory=postgres_sessions, settings=settings, order_id=order_id, request=request)


async def print_request(c):
    response = await c.request("GET", f"{BASE}/print-preview")
    assert response.status_code == 200, response.text
    assert response.json()["page_count"] == 1
    return {"filters": PARAMS, "request_id": str(uuid4()), "fingerprint": response.json()["fingerprint"]}


async def queue_summary(c):
    body = await print_request(c)
    response = await c.request("POST", f"{BASE}/print-jobs", body=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_concurrent_summary_requests_create_one_batch_and_one_audit(summary_pg_context, monkeypatch):
    c = summary_pg_context
    body = await print_request(c)
    barrier = asyncio.Barrier(2)
    read_jobs = summary_routes.read_summary_jobs

    async def synchronized_first_read(session, request_id):
        existing = await read_jobs(session, request_id)
        if not existing:
            await asyncio.wait_for(barrier.wait(), timeout=5)
        return existing

    monkeypatch.setattr(summary_routes, "read_summary_jobs", synchronized_first_read)
    responses = await asyncio.wait_for(asyncio.gather(*(
        c.request("POST", f"{BASE}/print-jobs", body=body) for _ in range(2)
    )), timeout=15)
    assert all(response.status_code == 201 for response in responses), [response.text for response in responses]
    first, second = [response.json() for response in responses]
    assert first["request_id"] == second["request_id"] == body["request_id"]
    assert first["pages"] == second["pages"]
    async with c.factory() as session:
        jobs = list(await session.scalars(select(PrintJob).where(PrintJob.kind == "summary")))
        assert len(jobs) == 1 and jobs[0].order_id is None and jobs[0].status == "pending"
        assert await session.scalar(select(func.count(AdminAudit.id)).where(
            AdminAudit.action == "printer.summary", AdminAudit.aggregate_id == body["request_id"],
        )) == 1


async def test_two_summary_workers_submit_each_page_once(summary_pg_context):
    c = summary_pg_context
    batch = await queue_summary(c)
    printer = ControlledPrinter(hold_first=True)
    first = asyncio.create_task(process_print_job(c.settings, factory=c.factory, client=printer))
    try:
        await asyncio.wait_for(printer.started.wait(), timeout=5)
        assert not await process_print_job(c.settings, factory=c.factory, client=printer)
        assert len(printer.submissions) == 1
    finally:
        printer.release.set()
        await asyncio.wait_for(first, timeout=10)
    async with c.factory() as session:
        job = await session.get(PrintJob, batch["pages"][0]["id"])
        assert job.status == "submitted" and job.attempts == 1
        assert job.lease_token is None and job.lease_until is None
        assert job.vendor_order_id == "isolated-print-1"


async def test_summary_waits_for_payment_correction_then_rejects_stale_snapshot(summary_pg_context):
    c = summary_pg_context
    batch = await queue_summary(c)
    printer = ControlledPrinter()
    async with c.factory() as session:
        blocker = await session.scalar(text("SELECT pg_backend_pid()"))
        order = await session.scalar(select(Order).where(Order.id == c.order_id).with_for_update())
        order.payment_status = PaymentStatus.PENDING
        order.paid_at = None
        await session.flush()
        worker = asyncio.create_task(process_print_job(c.settings, factory=c.factory, client=printer))
        try:
            await wait_for_lock_waiter(c.factory, blocker)
            assert not worker.done() and not printer.submissions
            await session.commit()
            assert await asyncio.wait_for(worker, timeout=10)
        finally:
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    assert not printer.submissions
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        job = await session.get(PrintJob, batch["pages"][0]["id"])
        assert order.payment_status == PaymentStatus.PENDING and order.paid_at is None
        assert job.status == "failed" and job.last_error == "summary_changed_before_send"
        assert job.attempts == 0 and job.vendor_order_id is None
