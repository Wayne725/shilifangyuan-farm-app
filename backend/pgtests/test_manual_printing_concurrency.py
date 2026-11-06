"""Disposable PostgreSQL only; all printer submissions are in memory."""

import asyncio
import base64
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.models import AdminAudit, FulfillmentStatus, Order, PaymentStatus, PrintJob, SalesChannel, User, UserRole
from app.printer_worker import process_print_job
from app.routers.printers import printers_router
from pgtests.test_amendment_fulfillment_concurrency import ControlledPrinter, wait_for_lock_waiter
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers
from tests.test_feie_printing import live_settings
from tests.test_integrations import make_regular_order


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Disposable PostgreSQL not configured")


@pytest.fixture
async def manual_pg_context(postgres_sessions):
    settings = live_settings(pii_encryption_keys_json=json.dumps({"test": base64.b64encode(b"m" * 32).decode()}),
        pii_encryption_current_version="test")
    async with postgres_sessions() as session:
        _buyer, _product, order = await make_regular_order(session)
        order.sales_channel = SalesChannel.MEAL_PREORDER
        order.payment_status = PaymentStatus.PAID
        order.paid_at = datetime.now(timezone.utc)
        order.fulfillment_status = FulfillmentStatus.PREPARING
        admin = User(email="manual-print-admin@example.test", display_name="列印管理員", password_hash="test", user_role=UserRole.ADMIN)
        session.add(admin)
        await session.commit()
        order_id, admin_id = order.id, admin.id

    async def request(method, suffix, body=None):
        async with postgres_sessions() as session:
            admin = await session.get(User, admin_id)
            async with api_test_context(session, [printers_router], settings=settings) as client:
                return await client.request(method, f"/v1/admin/printers/orders/{order_id}/{suffix}",
                    headers=auth_headers(admin), json=body)

    return SimpleNamespace(factory=postgres_sessions, settings=settings, order_id=order_id, request=request)


async def print_body(c):
    preview = await c.request("GET", "preview")
    assert preview.status_code == 200, preview.text
    return {"request_id": str(uuid4()), "reason": "隔離併發補印測試", "preview_token": preview.json()["preview_token"]}


@pytest.mark.parametrize("same_request", [True, False])
async def test_concurrent_manual_requests_create_one_pending_job_and_audit(manual_pg_context, same_request):
    c = manual_pg_context
    first = await print_body(c)
    second = first if same_request else {**first, "request_id": str(uuid4())}
    responses = await asyncio.wait_for(asyncio.gather(c.request("POST", "print", first), c.request("POST", "print", second)), timeout=15)
    assert sorted(response.status_code for response in responses) == ([201, 201] if same_request else [201, 409]), [r.text for r in responses]
    if same_request:
        assert responses[0].json()["id"] == responses[1].json()["id"]
    async with c.factory() as session:
        assert await session.scalar(select(func.count(PrintJob.id)).where(PrintJob.order_id == c.order_id)) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "printer.order.print")) == 1


async def test_manual_worker_rechecks_payment_after_waiting_on_order_lock(manual_pg_context):
    c = manual_pg_context
    response = await c.request("POST", "print", await print_body(c))
    assert response.status_code == 201, response.text
    printer = ControlledPrinter()
    async with c.factory() as session:
        blocker = await session.scalar(text("SELECT pg_backend_pid()"))
        order = await session.scalar(select(Order).where(Order.id == c.order_id).with_for_update())
        order.payment_status, order.paid_at = PaymentStatus.PENDING, None
        await session.flush()
        worker = asyncio.create_task(process_print_job(c.settings, factory=c.factory, client=printer))
        try:
            await wait_for_lock_waiter(c.factory, blocker)
            assert not printer.submissions
            await session.commit()
            assert await asyncio.wait_for(worker, timeout=10)
        finally:
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    async with c.factory() as session:
        job = await session.get(PrintJob, response.json()["id"])
        assert job.status == "failed" and job.attempts == 0 and not printer.submissions


async def test_manual_two_workers_submit_once(manual_pg_context):
    c = manual_pg_context
    response = await c.request("POST", "print", await print_body(c))
    assert response.status_code == 201, response.text
    printer = ControlledPrinter(hold_first=True)
    worker = asyncio.create_task(process_print_job(c.settings, factory=c.factory, client=printer))
    try:
        await asyncio.wait_for(printer.started.wait(), timeout=5)
        assert not await process_print_job(c.settings, factory=c.factory, client=printer)
        assert len(printer.submissions) == 1
    finally:
        printer.release.set()
        await asyncio.wait_for(worker, timeout=10)
    async with c.factory() as session:
        job = await session.get(PrintJob, response.json()["id"])
        assert job.status == "submitted" and job.attempts == 1
