"""Real TCP HTTP regressions against the guarded disposable local PostgreSQL fixture."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import socket

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
import uvicorn

from app.config import get_settings
from app.database import get_session
from app.models import GroupCampaign, InventoryReservation, Membership, Notification, Order, OrderFulfillment, OrderItem, OutboxEvent, Product, TargetType, User
from app.routers import groups, orders
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import auth_headers, make_test_settings


@asynccontextmanager
async def real_http(factory):
    application = FastAPI()
    application.include_router(orders.orders_router)
    application.include_router(groups.groups_router)

    async def sessions():
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = sessions
    application.dependency_overrides[get_settings] = lambda: make_test_settings()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port, loop="asyncio",
                                          lifespan="off", access_log=False, log_level="critical"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
                raise RuntimeError("Regression HTTP server exited before startup")
            await asyncio.sleep(.01)
        else:
            raise RuntimeError("Regression HTTP server did not start")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        listener.close()


async def checkout_fixture(factory, channel):
    factory.configure(autoflush=False)
    async with factory() as session:
        buyer = User(email="order-number-buyer@example.com", display_name="單號測試買家", password_hash="test")
        product = Product(slug="order-number-product", name="單號測試商品", category="測試", unit="份",
                          member_price=100, nonmember_price=100, stock_quantity=100)
        session.add_all([buyer, product])
        await session.flush()
        payload = {"contact_email": "first@example.com"}
        path = "/v1/orders"
        campaign_id = None
        if channel == "regular":
            payload["items"] = [{"product_id": product.id, "quantity": 1}]
        else:
            now = datetime.now(timezone.utc)
            campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=product.id, title="單號測試共同購買",
                                     member_price=100, nonmember_price=100, min_paid_quantity=1, supply_cap=100,
                                     deadline=now + timedelta(days=1), estimated_pickup_start=now + timedelta(days=2),
                                     estimated_pickup_end=now + timedelta(days=3), created_by_id=buyer.id)
            session.add(campaign)
            await session.flush()
            campaign_id = campaign.id
            path = f"/v1/group-campaigns/{campaign.id}/join"
            payload["quantity"] = 1
        await session.commit()
        return buyer, product.id, campaign_id, path, payload


async def assert_checkout_state(factory, buyer_id, product_id, campaign_id, *, count, email):
    async with factory() as session:
        rows = list(await session.scalars(select(Order)))
        assert len(rows) == count
        assert len({row.order_number for row in rows}) == count
        assert all(row.payment_status.value == "pending" for row in rows)
        assert all(row.amount_total == 100 and row.group_campaign_id == campaign_id for row in rows)
        items = list(await session.scalars(select(OrderItem)))
        fulfillments = list(await session.scalars(select(OrderFulfillment)))
        assert len(items) == len(fulfillments) == count
        assert {item.order_id for item in items} == {row.id for row in rows}
        assert {fulfillment.order_id for fulfillment in fulfillments} == {row.id for row in rows}
        assert all(item.source_product_id == product_id and item.quantity == 1
                   and item.unit_price == item.subtotal == 100 for item in items)
        assert all(fulfillment.method.value == "cooperative_pickup" for fulfillment in fulfillments)
        assert (await session.get(Product, product_id)).stock_quantity == 100
        buyer = await session.get(User, buyer_id)
        assert buyer.notification_email == email
        assert buyer.membership_type.value == "nonmember"
        assert await session.scalar(select(func.count(Membership.id))) == 0
        assert await session.scalar(select(func.count(InventoryReservation.id))) == 0
        assert await session.scalar(select(func.count(OutboxEvent.id))) == 0
        assert await session.scalar(select(func.count(Notification.id))) == 0
        if campaign_id:
            campaign = await session.get(GroupCampaign, campaign_id)
            assert campaign.reserved_quantity == campaign.paid_quantity == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["regular", "group"])
async def test_http_duplicate_order_number_retries_only_new_insert(postgres_sessions, monkeypatch, channel):
    buyer, product_id, campaign_id, path, payload = await checkout_fixture(postgres_sessions, channel)
    numbers = iter(["O2030100101", "O2030100101", "O2030100102"])
    async def next_number(_session, prefix):
        assert prefix == "O"
        return next(numbers)
    monkeypatch.setattr(orders if channel == "regular" else groups, "allocate_order_number", next_number)
    commit = AsyncSession.commit
    locks_held = []
    async def verify_original_lock_then_commit(session):
        async with postgres_sessions() as contender:
            subject = select(Product.id).where(Product.id == product_id) if channel == "regular" else select(GroupCampaign.id).where(GroupCampaign.id == campaign_id)
            try:
                await contender.execute(subject.with_for_update(nowait=True))
            except DBAPIError as exc:
                assert exc.orig.sqlstate == "55P03", str(exc)
                locks_held.append(True)
            else:
                locks_held.append(False)
        await commit(session)
    monkeypatch.setattr(AsyncSession, "commit", verify_original_lock_then_commit)
    async with real_http(postgres_sessions) as client:
        first = await client.post(path, headers=auth_headers(buyer), json=payload)
        assert first.status_code == 201, first.text
        second = await client.post(path, headers=auth_headers(buyer), json={**payload, "contact_email": "second@example.com"})
        assert second.status_code == 201, second.text
        assert first.json()["id"] != second.json()["id"]
        assert first.json()["order_number"] != second.json()["order_number"]
    assert locks_held == [True, True]
    await assert_checkout_state(postgres_sessions, buyer.id, product_id, campaign_id, count=2, email="second@example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["regular", "group"])
async def test_http_exhausted_number_collision_conflicts_and_leaves_no_partial_order(postgres_sessions, monkeypatch, channel):
    buyer, product_id, campaign_id, path, payload = await checkout_fixture(postgres_sessions, channel)
    router = orders if channel == "regular" else groups
    calls = []
    async def same_number(_session, prefix):
        assert prefix == "O"
        calls.append(1)
        return "O2030100101"
    monkeypatch.setattr(router, "allocate_order_number", same_number)
    async with real_http(postgres_sessions) as client:
        assert (await client.post(path, headers=auth_headers(buyer), json=payload)).status_code == 201
        rejected = await client.post(path, headers=auth_headers(buyer), json={**payload, "contact_email": "should-rollback@example.com"})
        assert rejected.status_code == 409, rejected.text
        assert len(calls) == 4  # One successful initial order + three bounded conflicting attempts.
        await assert_checkout_state(postgres_sessions, buyer.id, product_id, campaign_id, count=1, email="first@example.com")
        async def available_number(_session, prefix):
            assert prefix == "O"
            return "O2030100102"
        monkeypatch.setattr(router, "allocate_order_number", available_number)
        assert (await client.post(path, headers=auth_headers(buyer), json=payload)).status_code == 201
    await assert_checkout_state(postgres_sessions, buyer.id, product_id, campaign_id, count=2, email="first@example.com")


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["regular", "group"])
async def test_http_other_unique_violation_is_not_retried_or_misreported(postgres_sessions, monkeypatch, channel):
    buyer, product_id, campaign_id, path, payload = await checkout_fixture(postgres_sessions, channel)
    async with real_http(postgres_sessions) as client:
        first = await client.post(path, headers=auth_headers(buyer), json=payload)
        assert first.status_code == 201
        calls = []
        async def different_number(_session, prefix):
            assert prefix == "O"
            calls.append(1)
            return "O2030100102"
        monkeypatch.setattr(orders if channel == "regular" else groups, "allocate_order_number", different_number)
        def collide_primary_key(_mapper, _connection, target):
            target.id = first.json()["id"]
        event.listen(Order, "before_insert", collide_primary_key)
        try:
            rejected = await client.post(path, headers=auth_headers(buyer), json=payload)
        finally:
            event.remove(Order, "before_insert", collide_primary_key)
        assert rejected.status_code == 500
        assert len(calls) == 1
    await assert_checkout_state(postgres_sessions, buyer.id, product_id, campaign_id, count=1, email="first@example.com")
