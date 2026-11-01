"""Quantity changes must serialize with print claims and logistics picker callbacks."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import time_machine
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

import app.routers.logistics as logistics_module
from app.models import (
    MealEventOffering, Order, OrderAmendment, OrderItem, PaymentStatus, PrintJob,
    ShippingChannel, ShippingRate, ShippingTemperature, ShipmentStatus, User,
)
from app.order_amendments import prepare_quantity_amendment
from app.printer_worker import process_print_job
from app.printing import enqueue_order_print
from app.routers.logistics import _load_order, logistics_router
from app.schemas import OrderQuantityAmendmentConfirm
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments_concurrency import cash_api, order_body
from tests.test_feie_printing import live_settings
from tests.test_integrations import make_regular_order
from tests.test_logistics_router import FakeLogisticsAdapter, logistics_settings, selection_payload


@pytest.fixture(autouse=True)
def ordering_hours():
    with time_machine.travel(datetime.now(timezone.utc).replace(hour=4, minute=0, second=0, microsecond=0), tick=True):
        yield


async def wait_for_lock_waiter(sessions, blocking_pid):
    async def observe():
        async with sessions() as session:
            while not await session.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
            ), {"blocker": blocking_pid}):
                await asyncio.sleep(0.01)
    await asyncio.wait_for(observe(), timeout=5)


def amendment_body(item_id, *, quantity, total):
    return OrderQuantityAmendmentConfirm(
        expected_revision=1, expected_total=total, client_request_id=uuid4(),
        items=[{"order_item_id": item_id, "quantity": quantity}],
    )


async def change_quantity(session, user_id, order_id, body, settings):
    user = await session.get(User, user_id)
    return await prepare_quantity_amendment(session, order_id, body, user, settings, confirm=True)


class ControlledPrinter:
    def __init__(self, *, hold_first=False):
        self.hold_first = hold_first
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.submissions = []

    async def submit(self, content):
        self.submissions.append(content)
        self.started.set()
        if self.hold_first and len(self.submissions) == 1:
            await asyncio.wait_for(self.release.wait(), timeout=10)
        return f"isolated-print-{len(self.submissions)}"

    async def query(self, vendor_id):
        assert vendor_id.startswith("isolated-print-")
        return True


@pytest.mark.parametrize("first", ["amendment", "print_claim"])
async def test_print_claim_and_quantity_change_preserve_latest_receipt(postgres_sessions, cash_api, first):
    request, buyer, _other, _admin, _second_admin, event = cash_api
    settings = live_settings()
    async with postgres_sessions() as session:
        offering = await session.get(MealEventOffering, event["offerings"][0]["id"])
        offering.capacity = 3
        await session.commit()
    created = await request("POST", f"/v1/meal-events/{event['id']}/orders", buyer, order_body(event, buyer))
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    async with postgres_sessions() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id).options(
            selectinload(Order.items).selectinload(OrderItem.selected_options), selectinload(Order.fulfillment)))
        job = await enqueue_order_print(session, order, buyer, settings)
        body = amendment_body(order.items[0].id, quantity=2, total=200)
        await session.commit()
        original_id = job.id

    printer = ControlledPrinter(hold_first=first == "print_claim")
    if first == "amendment":
        async with postgres_sessions() as session:
            blocker = await session.scalar(text("SELECT pg_backend_pid()"))
            await change_quantity(session, buyer.id, order_id, body, settings)
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
    else:
        worker = asyncio.create_task(process_print_job(settings, factory=postgres_sessions, client=printer))
        try:
            await asyncio.wait_for(printer.started.wait(), timeout=5)
            async with postgres_sessions() as session:
                # The printer HTTP call is in flight; it must no longer own the order lock.
                await asyncio.wait_for(change_quantity(session, buyer.id, order_id, body, settings), timeout=5)
                await session.commit()
            assert await process_print_job(settings, factory=postgres_sessions, client=printer)
            assert len(printer.submissions) == 1
            async with postgres_sessions() as session:
                latest = await session.scalar(select(PrintJob).where(PrintJob.order_id == order_id, PrintJob.id != original_id))
                assert latest.kind == "amendment" and latest.status == "pending"
        finally:
            printer.release.set()
            await asyncio.wait_for(worker, timeout=10)

    run_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    for _ in range(4):
        await process_print_job(settings, factory=postgres_sessions, client=printer, now=run_at)
        run_at += timedelta(minutes=1)
    async with postgres_sessions() as session:
        order = await session.get(Order, order_id)
        assert (order.revision, order.amount_total, order.payment_status) == (2, 200, PaymentStatus.PENDING)
        assert await session.scalar(select(OrderItem.quantity).where(OrderItem.order_id == order_id)) == 2
        assert await session.scalar(select(func.count(OrderAmendment.id)).where(OrderAmendment.order_id == order_id)) == 1
        jobs = list(await session.scalars(select(PrintJob).where(PrintJob.order_id == order_id)))
        assert len(jobs) == 2
        original = next(job for job in jobs if job.id == original_id)
        latest = next(job for job in jobs if job.id != original_id)
        assert latest.status == "printed" and latest.snapshot["revision"] == 2
        assert latest.snapshot["items"][0]["quantity"] == 2
        if first == "amendment":
            assert original.status == "failed" and original.attempts == 0
            assert original.last_error == "order_revision_superseded"
            assert latest.kind == "order" and len(printer.submissions) == 1
        else:
            assert original.status == "printed" and original.attempts == 1
            assert latest.kind == "amendment" and len(printer.submissions) == 2
            assert latest.snapshot["quantity_changes"][0]["before"] == 1
            assert latest.snapshot["quantity_changes"][0]["after"] == 2
            assert "數量修訂單" in printer.submissions[-1]
        assert latest.attempts == 1


@pytest.mark.parametrize("first", ["amendment", "selection_callback"])
async def test_logistics_selection_and_quantity_change_use_latest_shipping_total(postgres_sessions, monkeypatch, first):
    settings = logistics_settings()
    adapter = FakeLogisticsAdapter()
    monkeypatch.setattr(logistics_module, "ecpay_logistics_adapter_from_settings", lambda _: adapter)
    async with postgres_sessions() as session:
        buyer, product, order = await make_regular_order(session)
        product.can_ship = True
        product.shipping_temperature = ShippingTemperature.AMBIENT
        product.allowed_shipping_channels = [ShippingChannel.HOME_DELIVERY.value]
        session.add(ShippingRate(channel=ShippingChannel.HOME_DELIVERY, temperature=ShippingTemperature.AMBIENT,
            fee=60, free_shipping_threshold=300, effective_from=date(2020, 1, 1)))
        await session.commit()
        order_id, item_id, buyer_id = order.id, order.items[0].id, buyer.id
        async with api_test_context(session, [logistics_router], settings=settings) as client:
            selected = await client.post(f"/v1/orders/{order_id}/logistics/selection",
                headers=auth_headers(buyer), json=selection_payload())
        assert selected.status_code == 201, selected.text
        token = selected.json()["selection_url"].rsplit("/", 2)[-2]
        current = await _load_order(session, order_id)
        assert current.amount_total == 260 and current.fulfillment.shipment.shipping_fee == 60
        await session.commit()
    body = amendment_body(item_id, quantity=3, total=300)

    async def callback(session):
        async with api_test_context(session, [logistics_router], settings=settings) as client:
            return await client.post("/logistics/selection-result", params={"order_id": order_id, "token": token}, json={})

    if first == "amendment":
        async with postgres_sessions() as change_session, postgres_sessions() as callback_session:
            blocker = await change_session.scalar(text("SELECT pg_backend_pid()"))
            await change_quantity(change_session, buyer_id, order_id, body, settings)
            task = asyncio.create_task(callback(callback_session))
            try:
                await wait_for_lock_waiter(postgres_sessions, blocker)
                await change_session.commit()
                response = await asyncio.wait_for(task, timeout=10)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    else:
        ready, release = asyncio.Event(), asyncio.Event()
        async with postgres_sessions() as callback_session, postgres_sessions() as change_session:
            blocker = await callback_session.scalar(text("SELECT pg_backend_pid()"))
            commit = callback_session.commit

            async def held_commit():
                ready.set()
                await asyncio.wait_for(release.wait(), timeout=10)
                await commit()

            monkeypatch.setattr(callback_session, "commit", held_commit)
            callback_task = asyncio.create_task(callback(callback_session))
            change_task = None
            try:
                await asyncio.wait_for(ready.wait(), timeout=5)
                change_task = asyncio.create_task(change_quantity(change_session, buyer_id, order_id, body, settings))
                await wait_for_lock_waiter(postgres_sessions, blocker)
                release.set()
                response = await asyncio.wait_for(callback_task, timeout=10)
                await asyncio.wait_for(change_task, timeout=10)
                await change_session.commit()
            finally:
                release.set()
                tasks = [task for task in (callback_task, change_task) if task is not None]
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    assert response.status_code == 303, response.text

    async with postgres_sessions() as session:
        current = await _load_order(session, order_id)
        assert (current.revision, current.amount_total, current.payment_status) == (2, 300, PaymentStatus.PENDING)
        assert current.items[0].quantity == 3 and current.items[0].subtotal == 300
        shipment = current.fulfillment.shipment
        assert shipment.shipping_fee == 0 and shipment.status == ShipmentStatus.READY_TO_CREATE
        assert shipment.provider_payload["TempLogisticsID"] == "80001"
        assert shipment.selection_token_hash is None and shipment.selection_token_expires_at is None
        assert await session.scalar(select(func.count(OrderAmendment.id)).where(OrderAmendment.order_id == order_id)) == 1
        await session.commit()
        replay = await callback(session)
        assert replay.status_code == 400
        await session.rollback()
        current = await _load_order(session, order_id)
        assert (current.revision, current.amount_total, current.fulfillment.shipment.shipping_fee) == (2, 300, 0)
