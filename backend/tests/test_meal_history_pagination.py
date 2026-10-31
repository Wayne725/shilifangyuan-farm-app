from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.models import (FulfillmentMethod, Meal, MealEvent, MealEventOffering, MealEventStatus, Order,
                        OrderFulfillment, OrderItem, OrderItemOption, SalesChannel, TaxType, User, UserRole)
from app.routers.meals import meals_router
from tests.support import api_test_context, auth_headers, make_test_settings


@pytest.fixture
async def meal_history(database_session):
    session = database_session
    owner = User(email="meal-history@example.com", display_name="訂餐者", password_hash="test", user_role=UserRole.ADMIN)
    other = User(email="meal-history-other@example.com", display_name="另一訂餐者", password_hash="test")
    session.add_all([owner, other])
    await session.flush()
    now = datetime(2030, 1, 10, 4, tzinfo=timezone.utc)
    meal_event = MealEvent(title="ＡＢＣ 午餐", location="城南取餐點", created_by_id=owner.id,
        status=MealEventStatus.PUBLISHED, ordering_starts_at=now-timedelta(days=1),
        ordering_ends_at=now+timedelta(hours=1), pickup_starts_at=now+timedelta(hours=2),
        pickup_ends_at=now+timedelta(hours=3))
    offerings = [MealEventOffering(event=meal_event, meal=Meal(slug=f"history-meal-{i}", name=f"便當{i}", price=100),
        price=100, capacity=100) for i in range(2)]
    session.add_all(offerings)
    await session.flush()
    rows = []
    for index in range(31):
        offering = offerings[index % 2]
        order = Order(id=f"meal-history-{index:02d}", order_number=f"MEAL-HISTORY-{index:04d}",
            user_id=other.id if index == 30 else owner.id, order_kind="regular",
            sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
            meal_event=meal_event, membership_type_snapshot="nonmember", amount_total=120, contact_email=owner.email,
            created_at=now-timedelta(minutes=index),
            items=[OrderItem(source_meal_offering_id=offering.id, product_name="白米便當", unit_label="份",
                quantity=1, unit_price=120, subtotal=120, tax_type=TaxType.TAXABLE,
                selected_options=[OrderItemOption(group_name="份量", option_name="加飯", price_delta=20)])],
            fulfillment=OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP))
        session.add(order)
        rows.append(order)
    await session.commit()
    headers = auth_headers(owner)
    async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
        yield session, client, headers, rows


@pytest.mark.asyncio
async def test_meal_history_pages_preserve_full_snapshots_and_owner_scope(meal_history):
    session, client, headers, rows = meal_history
    legacy = await client.get("/v1/meal-orders", headers=headers)
    assert legacy.status_code == 200, legacy.text
    assert len(legacy.json()) == 30
    snapshots = {row["id"]: row for row in legacy.json()}
    session.expunge_all()
    first = await client.get("/v1/meal-orders", params={"limit": 25}, headers=headers)
    assert first.status_code == 200, first.text
    assert len(first.json()) == 25
    assert all(row == snapshots[row["id"]] for row in first.json())
    last = first.json()[-1]
    second = await client.get("/v1/meal-orders", params={"limit": 25, "before_id": last["id"],
        "before_created_at": last["created_at"]}, headers=headers)
    assert second.status_code == 200, second.text
    assert [row["id"] for row in second.json()] == [row.id for row in rows[25:30]]
    assert all(row == snapshots[row["id"]] for row in second.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["abc 白米", "城南 白米", "MEAL-history-0029"])
async def test_meal_search_uses_event_fallback_location_and_all_pages(meal_history, query):
    _session, client, headers, rows = meal_history
    response = await client.get("/v1/meal-orders", params={"limit": 2, "q": query}, headers=headers)
    assert response.status_code == 200, response.text
    expected = [rows[29].id] if "0029" in query else [rows[0].id, rows[1].id]
    assert [row["id"] for row in response.json()] == expected


@pytest.mark.asyncio
async def test_meal_page_renderer_batches_offering_lookups_instead_of_one_per_order(meal_history):
    session, client, headers, _rows = meal_history
    selects = []

    def count_selects(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(session.bind.sync_engine, "before_cursor_execute", count_selects)
    try:
        counts = []
        for limit in [1, 25]:
            session.expunge_all()
            selects.clear()
            response = await client.get("/v1/meal-orders", params={"limit": limit}, headers=headers)
            assert response.status_code == 200, response.text
            assert len(response.json()) == limit
            assert all(row["items"][0]["meal_id"] for row in response.json())
            counts.append(len(selects))
        assert counts[1] <= counts[0] + 2, counts
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", count_selects)
