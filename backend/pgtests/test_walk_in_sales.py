import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.config import get_settings
from app.database import get_session
from app.models import AdminAudit, Order, OrderNumberCounter, OutboxEvent, PaymentAttempt, PointTransaction, User, UserRole, WalkInSale
from app.routers.walk_in_sales import walk_in_sales_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import auth_headers, make_test_settings
from tests.test_walk_in_member_pricing import check_concurrent_requote_fence, collection_payload, quote_payload, seed_member
from tests.test_walk_in_sales import payload


@pytest.mark.parametrize("verified", [False, True], ids=["guest", "verified-member"])
async def test_parallel_retries_share_one_durable_counter_collection(postgres_sessions, verified):
    settings = make_test_settings()
    body = payload(client_request_id=str(uuid4()))
    async with postgres_sessions() as session:
        admin = User(email="parallel-admin@example.com", display_name="並行測試收款員", password_hash="test", user_role=UserRole.ADMIN)
        session.add(admin)
        if verified:
            member_user = User(email="parallel-member@example.com", display_name="並行測試社員", password_hash="test")
            session.add(member_user)
            await session.flush()
            membership, product = await seed_member(session, member_user, settings)
            body = collection_payload(quote_payload(membership, product), {
                "product_name": product.name, "unit_price": product.member_price, "tax_type": "tax_exempt",
            })
        await session.commit()
        headers = auth_headers(admin)
    app = FastAPI()
    app.include_router(walk_in_sales_router)
    async def sessions():
        async with postgres_sessions() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_settings] = lambda: settings
    gate = asyncio.Event()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def submit():
            await gate.wait()
            return await client.post("/v1/admin/walk-in-sales", json=body, headers=headers)
        tasks = [asyncio.create_task(submit()) for _ in range(8)]
        gate.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 20)
        assert sorted(result.status_code for result in results) == [200] * 7 + [201], [r.text for r in results]
        assert len({result.json()["order_id"] for result in results}) == 1
        conflict = await client.post("/v1/admin/walk-in-sales", json={**body, "unit_price": 200}, headers=headers)
        assert conflict.status_code == 409
    async with postgres_sessions() as session:
        for model in (Order, WalkInSale, PaymentAttempt, OutboxEvent, AdminAudit):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        assert await session.scalar(select(OrderNumberCounter.last_value)) == 1
        assert await session.scalar(select(func.count()).select_from(PointTransaction)) == int(verified)


@pytest.mark.parametrize("stale_first", [False, True], ids=["order-wins", "stale-quote-wins"])
async def test_real_pg_concurrent_requote_fence(postgres_sessions, monkeypatch, stale_first):
    await check_concurrent_requote_fence(postgres_sessions, monkeypatch, stale_first)
