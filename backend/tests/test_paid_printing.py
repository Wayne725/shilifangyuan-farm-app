from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.models import Order, PaymentStatus, PrintJob
from app.printer_worker import process_print_job
from app.printing import format_receipt, printer_fingerprint
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import cash_context, create_cash, action, approve_cancellation
from tests.test_feie_printing import FakeFeie, live_settings, print_context, read_job, set_job
from tests.test_integrations import payment_settings, signed_payment_callback


async def set_payment(c, status, *, paid_at=True):
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_status = status
        order.paid_at = datetime.now(timezone.utc) if paid_at else None
        await session.commit()


async def test_unpaid_outbox_waits_then_confirmed_cash_prints_once(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    created = await create_cash(c, client_request_id=str(uuid4()))
    assert created.status_code == 201, created.text
    c.order_id = created.json()["id"]
    c.job_id = await c.session.scalar(select(PrintJob.id).where(PrintJob.order_id == c.order_id))
    c.factory = async_sessionmaker(c.session.bind, expire_on_commit=False)
    settings = live_settings()
    await set_job(c, mode="live", printer_fingerprint=printer_fingerprint(settings))
    fake = FakeFeie(printed=True)
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert not await process_print_job(settings, factory=c.factory, client=fake, now=now)
    assert fake.submissions == 0 and (await read_job(c)).attempts == 0
    async with api_test_context(c.session, [printers_router], settings=settings) as client:
        detail = await client.get(f"/v1/admin/printers/jobs/{c.job_id}", headers=auth_headers(c.admin))
        listing = await client.get("/v1/admin/printers/jobs", headers=auth_headers(c.admin))
        assert detail.json()["awaiting_payment"] is True
        assert listing.json()["items"][0]["awaiting_payment"] is True
    for _ in range(2):
        paid = await action(c, c.order_id)
        assert paid.status_code == 200, paid.text
    assert await process_print_job(settings, factory=c.factory, client=fake, now=now)
    assert (await read_job(c)).status == "submitted" and fake.submissions == 1
    await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(minutes=1))
    assert not await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(minutes=2))
    assert fake.submissions == 1
    assert await c.session.scalar(select(func.count(PrintJob.id))) == 1


@pytest.mark.parametrize("status,paid_at", [(PaymentStatus.PENDING, False), (PaymentStatus.PAID, False), (PaymentStatus.FAILED, False), (PaymentStatus.EXPIRED, False)])
async def test_unconfirmed_or_refunded_order_never_submits(print_context, status, paid_at):
    c = print_context
    await set_payment(c, status, paid_at=paid_at)
    fake = FakeFeie()
    assert not await process_print_job(c.settings, factory=c.factory, client=fake)
    job = await read_job(c)
    assert job.status == "pending" and job.attempts == 0 and fake.submissions == 0


@pytest.mark.parametrize("status", [PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED])
async def test_refund_pending_or_completed_order_stops_unsent_receipt(print_context, status):
    c = print_context
    await set_payment(c, status)
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = await read_job(c)
    assert job.status == "failed" and job.last_error == "order_payment_reversed_before_send"
    assert job.attempts == 0 and fake.submissions == 0


async def test_submitted_work_is_queried_after_payment_reversal_without_resending(print_context):
    c = print_context
    settings = live_settings()
    await set_job(c, mode="live", printer_fingerprint=printer_fingerprint(settings), status="submitted", vendor_order_id="original-job")
    await set_payment(c, PaymentStatus.REFUNDED)
    fake = FakeFeie(printed=True)
    assert await process_print_job(settings, factory=c.factory, client=fake, now=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert fake.submissions == 0 and fake.queries == 1 and (await read_job(c)).status == "printed"


async def test_unpaid_cannot_manually_reprint_previously_printed_order(print_context):
    c = print_context
    await set_payment(c, PaymentStatus.PENDING, paid_at=False)
    await set_job(c, status="printed")
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        response = await client.post(f"/v1/admin/printers/jobs/{c.job_id}/reprint", headers=auth_headers(c.admin), json={"request_id": str(uuid4()), "reason": "核對未付款限制"})
    assert response.status_code == 409 and "付款" in response.json()["detail"]


async def test_old_frozen_unpaid_receipt_requires_new_paid_reprint(print_context):
    c = print_context
    original = await read_job(c)
    snapshot = {**original.snapshot, "payment_status": "未付款", "print_requested_at": "2026-11-01 12:00:00"}
    content = format_receipt(snapshot)
    await set_job(c, snapshot=snapshot, content=content, attempts=1)
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    stopped = await read_job(c)
    assert stopped.status == "failed" and stopped.last_error == "unpaid_receipt_snapshot"
    assert stopped.content == content and stopped.snapshot == snapshot and fake.submissions == 0
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        response = await client.post(f"/v1/admin/printers/jobs/{c.job_id}/reprint", headers=auth_headers(c.admin), json={"request_id": str(uuid4()), "reason": "付款後另印正確收據"})
    assert response.status_code == 201, response.text
    assert await process_print_job(c.settings, factory=c.factory)
    async with c.factory() as session:
        reprint = await session.get(PrintJob, response.json()["id"])
        assert reprint.status == "simulated" and reprint.snapshot["payment_status"] == "已付款"
        assert reprint.content != content and "補印" in reprint.content


async def test_verified_online_payment_dispatches_once_after_duplicate_callback(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    created = await create_cash(c, payment_method="online", client_request_id=str(uuid4()))
    assert created.status_code == 201, created.text
    c.order_id = created.json()["id"]
    c.job_id = await c.session.scalar(select(PrintJob.id).where(PrintJob.order_id == c.order_id))
    c.factory = async_sessionmaker(c.session.bind, expire_on_commit=False)
    attempt = await create_payment_attempt(c.session, c.order_id, c.buyer, payment_settings())
    settings = live_settings()
    await set_job(c, mode="live", printer_fingerprint=printer_fingerprint(settings))
    fake = FakeFeie(printed=True)
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert not await process_print_job(settings, factory=c.factory, client=fake, now=now)
    payload = signed_payment_callback(attempt, datetime.now(timezone.utc))
    repository = SQLAlchemyPaymentCallbackRepository(c.session)
    assert await repository.apply_ecpay_payment_callback("verified-online", payload) == "paid"
    assert await repository.apply_ecpay_payment_callback("verified-online", payload) == "duplicate"
    assert await process_print_job(settings, factory=c.factory, client=fake, now=now)
    assert (await read_job(c)).snapshot["payment_status"] == "已付款"
    await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(minutes=1))
    assert not await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(minutes=2))
    assert fake.submissions == 1 and await c.session.scalar(select(func.count(PrintJob.id))) == 1


async def test_older_unpaid_job_does_not_block_paid_job(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    first = await create_cash(c, client_request_id=str(uuid4()))
    second = await create_cash(c, client_request_id=str(uuid4()))
    assert first.status_code == second.status_code == 201
    c.factory = async_sessionmaker(c.session.bind, expire_on_commit=False)
    confirmed = await action(c, second.json()["id"])
    assert confirmed.status_code == 200, confirmed.text
    assert await process_print_job(c.settings, factory=c.factory)
    async with c.factory() as session:
        jobs = {job.order_id: job for job in await session.scalars(select(PrintJob))}
        assert jobs[first.json()["id"]].status == "pending" and jobs[first.json()["id"]].attempts == 0
        assert jobs[second.json()["id"]].status == "simulated"
    assert not await process_print_job(c.settings, factory=c.factory)


async def test_confirming_cash_after_legacy_print_does_not_dispatch_again(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    created = await create_cash(c, client_request_id=str(uuid4()))
    c.order_id = created.json()["id"]
    c.job_id = await c.session.scalar(select(PrintJob.id).where(PrintJob.order_id == c.order_id))
    c.factory = async_sessionmaker(c.session.bind, expire_on_commit=False)
    await set_job(c, status="printed", attempts=1, vendor_order_id="previous-unpaid-paper")
    confirmed = await action(c, c.order_id)
    assert confirmed.status_code == 200, confirmed.text
    assert not await process_print_job(c.settings, factory=c.factory)
    assert (await read_job(c)).status == "printed" and await c.session.scalar(select(func.count(PrintJob.id))) == 1
