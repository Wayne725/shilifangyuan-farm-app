import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import time_machine
from sqlalchemy import func, select

from app.models import GroupCampaign, InventoryReservation, Order, OrderFulfillment, OrderItem, OrderKind, OutboxEvent, PaymentAttempt, Product, SalesChannel, TargetType
from app.routers import orders
from pgtests.test_order_number_collisions import real_http
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import auth_headers
from tests.test_regular_checkout_submission import seed_regular_checkout


async def seed(factory):
    factory.configure(autoflush=False)
    async with factory() as session:
        return await seed_regular_checkout(session)


async def counts(factory):
    async with factory() as session:
        return {model.__name__: await session.scalar(select(func.count()).select_from(model))
                for model in (Order, OrderItem, OrderFulfillment, InventoryReservation, PaymentAttempt, OutboxEvent)}


@pytest.mark.asyncio
async def test_eight_concurrent_http_replays_pass_checkout_gates_once(postgres_sessions, monkeypatch):
    c = await seed(postgres_sessions)
    gate_calls = []
    original_gate = orders.require_accepting_orders
    async def gate(session):
        gate_calls.append(1)
        await original_gate(session)
    monkeypatch.setattr(orders, "require_accepting_orders", gate)
    async with real_http(postgres_sessions) as client:
        replies = await asyncio.wait_for(asyncio.gather(*(
            client.post("/v1/orders", headers=auth_headers(c.buyer), json=c.payload) for _ in range(8)
        )), timeout=20)
    assert [reply.status_code for reply in replies] == [201] * 8
    assert len({reply.json()["id"] for reply in replies}) == 1
    assert gate_calls == [1]
    assert await counts(postgres_sessions) == {"Order": 1, "OrderItem": 2, "OrderFulfillment": 1,
                                                "InventoryReservation": 0, "PaymentAttempt": 0, "OutboxEvent": 0}


@pytest.mark.asyncio
async def test_same_key_conflicting_http_bodies_have_one_winner(postgres_sessions):
    c = await seed(postgres_sessions)
    changed = {**c.payload, "expected_total": 500,
               "items": [{"product_id": c.products[0].id, "quantity": 3}, c.payload["items"][1]]}
    async with real_http(postgres_sessions) as client:
        replies = await asyncio.wait_for(asyncio.gather(*(
            client.post("/v1/orders", headers=auth_headers(c.buyer), json=body) for body in (c.payload, changed)
        )), timeout=20)
    assert sorted(reply.status_code for reply in replies) == [201, 409]
    accepted = next(reply for reply in replies if reply.status_code == 201)
    async with postgres_sessions() as session:
        stored = await session.scalar(select(Order))
        assert stored.id == accepted.json()["id"] and stored.amount_total == accepted.json()["amount_total"]
        assert await session.scalar(select(func.count(Order.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["new_request_keys", "different_buyers"])
async def test_independent_http_purchases_are_not_deduplicated(postgres_sessions, scope):
    c = await seed(postgres_sessions)
    if scope == "new_request_keys":
        requests = [(c.buyer, {**c.payload, "client_request_id": str(uuid4())}) for _ in range(8)]
    else:
        requests = [(buyer, c.payload) for buyer in (c.buyer, c.other)]
    async with real_http(postgres_sessions) as client:
        replies = await asyncio.wait_for(asyncio.gather(*(
            client.post("/v1/orders", headers=auth_headers(buyer), json=body) for buyer, body in requests
        )), timeout=20)
    assert [reply.status_code for reply in replies] == [201] * len(requests)
    assert len({reply.json()["id"] for reply in replies}) == len(requests)
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Order.id))) == len(requests)
        assert list(await session.scalars(select(Product.stock_quantity))) == [100, 100]


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_channel", ["group", "meal"])
async def test_request_key_used_by_other_channel_cannot_return_or_overwrite_it(postgres_sessions, foreign_channel):
    from app.models import Meal, MealEvent, MealEventOffering, MealEventStatus, MembershipType
    from tests.support import tomorrow_meal_timeline
    c = await seed(postgres_sessions)
    async with postgres_sessions() as session:
        now = datetime.now(timezone.utc)
        foreign = Order(order_number="OTHERCHANNEL01", user_id=c.buyer.id, client_request_id=c.payload["client_request_id"],
                        request_fingerprint="f" * 64, order_kind=OrderKind.REGULAR,
                        membership_type_snapshot=MembershipType.NONMEMBER, amount_total=100, contact_email=c.buyer.email)
        if foreign_channel == "group":
            campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=c.products[0].id, title="跨通路既有訂單",
                                     member_price=100, nonmember_price=100, min_paid_quantity=1, supply_cap=100,
                                     deadline=now + timedelta(days=1), estimated_pickup_start=now + timedelta(days=2),
                                     estimated_pickup_end=now + timedelta(days=3), created_by_id=c.buyer.id)
            session.add(campaign)
            await session.flush()
            foreign.order_kind, foreign.sales_channel, foreign.group_campaign_id = OrderKind.GROUP, SalesChannel.GROUP, campaign.id
        else:
            event = MealEvent(title="既有便當訂單", location="合成地點", created_by_id=c.buyer.id,
                              status=MealEventStatus.PUBLISHED, **tomorrow_meal_timeline(now),
                              offerings=[MealEventOffering(meal=Meal(slug="foreign", name="便當", price=100), price=100, capacity=10)])
            session.add(event)
            await session.flush()
            foreign.sales_channel, foreign.meal_event_id = SalesChannel.MEAL_PREORDER, event.id
        session.add(foreign)
        await session.commit()
        foreign_id = foreign.id
    async with real_http(postgres_sessions) as client:
        reply = await client.post("/v1/orders", headers=auth_headers(c.buyer), json=c.payload)
    assert reply.status_code == 409 and reply.headers["X-Error-Code"] == "idempotency_conflict"
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Order.id))) == 1
        foreign = await session.get(Order, foreign_id)
        assert foreign.request_fingerprint == "f" * 64 and foreign.amount_total == 100


@pytest.mark.asyncio
@time_machine.travel(datetime(2030, 1, 2, 4, 0, tzinfo=timezone.utc), tick=True)
async def test_unique_constraint_rejects_concurrent_other_channel_after_initial_lookup(postgres_sessions, monkeypatch):
    from app.models import Meal, MealEvent, MealEventOffering, MealEventStatus
    from app.routers.meals import meals_router
    from tests.support import api_test_context, make_test_settings, tomorrow_meal_timeline
    c = await seed(postgres_sessions)
    async with postgres_sessions() as session:
        event = MealEvent(title="並行跨通路便當", location="隔離地點", created_by_id=c.buyer.id,
                          status=MealEventStatus.PUBLISHED, **tomorrow_meal_timeline(datetime.now(timezone.utc)),
                          offerings=[MealEventOffering(meal=Meal(slug="racing-meal", name="便當", price=100), price=100, capacity=10)])
        session.add(event)
        await session.commit()
        event_id, offering_id = event.id, event.offerings[0].id
    original_insert = orders.insert_order_with_unique_number
    winning_id = None
    async def insert_after_other_writer(session, pending, number_factory):
        nonlocal winning_id
        async with postgres_sessions() as other:
            async with api_test_context(other, [meals_router], settings=make_test_settings()) as other_client:
                winner = await other_client.post(f"/v1/meal-events/{event_id}/orders", headers=auth_headers(c.buyer),
                                                 json={"client_request_id": c.payload["client_request_id"],
                                                       "expected_total": 100, "contact_phone": "0912345678", "contact_email": c.buyer.email,
                                                       "items": [{"offering_id": offering_id, "quantity": 1}]})
                assert winner.status_code == 201, winner.text
                winning_id = winner.json()["id"]
        await original_insert(session, pending, number_factory)
    monkeypatch.setattr(orders, "insert_order_with_unique_number", insert_after_other_writer)
    async with real_http(postgres_sessions) as client:
        reply = await client.post("/v1/orders", headers=auth_headers(c.buyer), json=c.payload)
    assert reply.status_code == 409, reply.text
    assert reply.headers["X-Error-Code"] == "idempotency_conflict"
    assert (await counts(postgres_sessions))["Order"] == 1
    async with postgres_sessions() as session:
        assert (await session.get(Order, winning_id)).sales_channel == SalesChannel.MEAL_PREORDER


@pytest.mark.asyncio
async def test_quote_fingerprint_blocks_equal_total_price_changes_but_not_original_replay(postgres_sessions):
    c = await seed(postgres_sessions)
    async with real_http(postgres_sessions) as client:
        before = (await client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": c.payload["items"]})).json()
        body = {**c.payload, "expected_quote_fingerprint": before["confirmation_fingerprint"]}
        original = await client.post("/v1/orders", headers=auth_headers(c.buyer), json=body)
        assert original.status_code == 201
        async with postgres_sessions() as session:
            first = await session.get(Product, c.products[0].id)
            second = await session.get(Product, c.products[1].id)
            first.nonmember_price = 120
            second.member_price = second.nonmember_price = 160
            await session.commit()
        replies = await asyncio.wait_for(asyncio.gather(*(
            client.post("/v1/orders", headers=auth_headers(c.buyer), json=body) for _ in range(8)
        )), timeout=20)
        assert [reply.status_code for reply in replies] == [201] * 8
        assert {reply.json()["id"] for reply in replies} == {original.json()["id"]}
        rejected = await client.post("/v1/orders", headers=auth_headers(c.buyer), json={**body, "client_request_id": str(uuid4())})
        assert rejected.status_code == 409 and rejected.headers["X-Error-Code"] == "quote_changed"
        current = (await client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": c.payload["items"]})).json()
        assert before["amount_total"] == current["amount_total"] == 400
        assert before["tax_amount"] == current["tax_amount"]
        assert before["confirmation_fingerprint"] != current["confirmation_fingerprint"]
        confirmed = await client.post("/v1/orders", headers=auth_headers(c.buyer),
                                      json={**body, "client_request_id": str(uuid4()), "expected_quote_fingerprint": current["confirmation_fingerprint"]})
        assert confirmed.status_code == 201
    assert (await counts(postgres_sessions))["Order"] == 2
