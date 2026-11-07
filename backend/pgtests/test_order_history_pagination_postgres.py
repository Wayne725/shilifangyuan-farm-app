from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.order_history as history
from app.models import (FulfillmentMethod, GroupCampaign, Meal, MealEvent, MealEventOffering, MealEventStatus,
    Order, OrderFulfillment, OrderItem, OrderItemOption, PaymentStatus, Product, SalesChannel, TargetType, TaxType, User, UserRole)
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings


class TrackedStream:
    def __init__(self, raw):
        self.raw, self.rows, self.close_calls = raw, 0, 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = await self.raw.__anext__()
        self.rows += 1
        return row

    async def close(self):
        self.close_calls += 1
        await self.raw.close()


class HistorySession(AsyncSession):
    async def stream(self, *args, **kwargs):
        stream = TrackedStream(await super().stream(*args, **kwargs))
        self.info.setdefault("streams", []).append(stream)
        return stream


@pytest.fixture
async def pg_history(postgres_sessions):
    factory = async_sessionmaker(postgres_sessions.kw["bind"], class_=HistorySession, expire_on_commit=False, autoflush=False)
    base = datetime(2030, 1, 10, 4, 5, 6, 123456, tzinfo=timezone.utc)
    expected = {"general": [], "meal": []}
    async with factory() as session:
        buyer = User(email="pg-history-buyer@example.com", display_name="買家", password_hash="test")
        admin = User(email="pg-history-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        stranger = User(email="pg-history-stranger@example.com", display_name="無訂單買家", password_hash="test")
        products = [Product(slug=f"pg-history-{index}", name=f"歷史商品{index}", category="測試", unit="份",
            member_price=100, nonmember_price=100, stock_quantity=1000) for index in range(2)]
        meal_event = MealEvent(title="ＡＢＣ 午餐", location="城南餐點站", created_by=admin,
            status=MealEventStatus.PUBLISHED, ordering_starts_at=base-timedelta(days=10),
            ordering_ends_at=base+timedelta(days=1), pickup_starts_at=base+timedelta(days=2), pickup_ends_at=base+timedelta(days=3))
        offerings = [MealEventOffering(event=meal_event, meal=Meal(slug=f"pg-history-meal-{index}", name=f"便當{index}", price=100),
            price=100, capacity=1000) for index in range(2)]
        session.add_all([buyer, admin, stranger, *products, *offerings])
        await session.flush()
        for kind in ["general", "meal"]:
            for owner, count in [(buyer, 110), (admin, 3)]:
                for index in range(count):
                    identity = f"pg-{'buyer' if owner is buyer else 'admin'}-{kind}-{index:03d}"
                    instant = base - timedelta(minutes=index // 3)
                    stamp = instant.astimezone([timezone.utc, timezone(timedelta(hours=8)), timezone(timedelta(hours=-5))][index % 3])
                    tail = owner is buyer and index == 109
                    names = (["ＡＢＣ", "白米"] if tail else ["普通商品", "第二品項"]) if kind == "general" else ["尾頁便當" if tail else "白米便當", "配菜"]
                    if owner is admin:
                        names = ["ADMIN_PRIVATE", "管理員私有明細"]
                    method = FulfillmentMethod.EVENT_PICKUP if kind == "meal" else FulfillmentMethod.COOPERATIVE_PICKUP
                    order = Order(id=identity, order_number=identity.upper(), user_id=owner.id, order_kind="regular",
                        sales_channel=SalesChannel.MEAL_PREORDER if kind == "meal" else SalesChannel.REGULAR,
                        fulfillment_method=method, meal_event_id=meal_event.id if kind == "meal" else None,
                        membership_type_snapshot="nonmember", amount_total=200, contact_email=owner.email,
                        created_at=stamp, payment_status=PaymentStatus.PAID if index % 2 else PaymentStatus.PENDING,
                        paid_at=instant+timedelta(seconds=1) if index % 2 else None,
                        items=[OrderItem(source_meal_offering_id=offerings[line].id if kind == "meal" else None,
                            source_product_id=products[line].id if kind == "general" else None,
                            product_name=name, unit_label="份", quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE,
                            selected_options=[OrderItemOption(group_name="份量", option_name="標準", price_delta=0)] if kind == "meal" else [])
                            for line, name in enumerate(names)],
                        fulfillment=OrderFulfillment(method=method, pickup_location=None if kind == "meal" else "城南合作社"))
                    session.add(order)
                    if owner is buyer:
                        expected[kind].append((instant, identity))
        campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=products[0].id, title="歷史共同購買",
            member_price=100, nonmember_price=100, min_paid_quantity=1, supply_cap=20, per_user_cap=5,
            deadline=base+timedelta(days=1), estimated_pickup_start=base+timedelta(days=2),
            estimated_pickup_end=base+timedelta(days=3), created_by_id=admin.id)
        session.add(campaign)
        await session.flush()
        session.add(Order(id="pg-buyer-group", order_number="PG-BUYER-GROUP", user_id=buyer.id,
            order_kind="group", sales_channel=SalesChannel.GROUP, group_campaign_id=campaign.id,
            membership_type_snapshot="nonmember", amount_total=100, contact_email=buyer.email, created_at=base-timedelta(days=8),
            items=[OrderItem(source_product_id=products[0].id, product_name="共同採購", unit_label="份", quantity=1,
                unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE)],
            fulfillment=OrderFulfillment(method=FulfillmentMethod.COOPERATIVE_PICKUP)))
        expected["general"].append((base-timedelta(days=8), "pg-buyer-group"))
        await session.commit()
        headers = {"buyer": auth_headers(buyer), "admin": auth_headers(admin), "stranger": auth_headers(stranger)}
    return SimpleNamespace(factory=factory, expected={kind: [identity for _, identity in sorted(rows, reverse=True)]
        for kind, rows in expected.items()}, headers=headers)


async def read(context, kind, params=None, *, actor="buyer"):
    path = "/v1/meal-orders" if kind == "meal" else "/v1/orders"
    async with context.factory() as session:
        hydrated, statements = [], []
        def loaded(_session, instance):
            if isinstance(instance, Order):
                hydrated.append(instance.id)
        def selected(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)
        event.listen(session.sync_session, "loaded_as_persistent", loaded)
        event.listen(session.bind.sync_engine, "before_cursor_execute", selected)
        try:
            async with api_test_context(session, [orders_router, meals_router], settings=make_test_settings()) as client:
                response = await client.get(path, params=params or {}, headers=context.headers[actor])
            streams = session.info.get("streams", [])
            # Check before closing the Session, which would otherwise hide a leaked cursor.
            assert all(stream.raw.closed and stream.close_calls == 1 for stream in streams)
            return response, {"hydrated": hydrated, "stream_rows": [stream.rows for stream in streams], "selects": statements}
        finally:
            assert all(stream.raw.closed for stream in session.info.get("streams", []))
            event.remove(session.sync_session, "loaded_as_persistent", loaded)
            event.remove(session.bind.sync_engine, "before_cursor_execute", selected)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["general", "meal"])
async def test_postgres_history_keyset_traversal_has_no_gaps_with_equal_instants_and_offset_cursors(pg_history, kind):
    params = {"limit": 17, "own_only": False, "history_scope": "general"}
    found = []
    for page_number in range(10):
        response, telemetry = await read(pg_history, kind, params)
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) <= 17
        assert sorted(telemetry["hydrated"]) == sorted(row["id"] for row in rows)
        if not rows:
            break
        found.extend(row["id"] for row in rows)
        stamp = datetime.fromisoformat(rows[-1]["created_at"].replace("Z", "+00:00"))
        offset = timezone(timedelta(hours=8 if page_number % 2 else -5))
        params.update(before_created_at=stamp.astimezone(offset).isoformat(), before_id=rows[-1]["id"])
    else:
        pytest.fail("Pagination did not finish")
    assert found == pg_history.expected[kind]
    assert len(found) == len(set(found))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,query,expected", [
    ("general", "abc 白米", "pg-buyer-general-109"),
    ("general", "城南 ＡＢＣ 白米", "pg-buyer-general-109"),
    ("meal", "ＡＢＣ 尾頁", "pg-buyer-meal-109"),
    ("meal", "城南 尾頁", "pg-buyer-meal-109"),
])
async def test_postgres_search_scans_all_candidate_pages_normalizes_multiword_and_only_hydrates_match(pg_history, kind, query, expected):
    response, telemetry = await read(pg_history, kind, {"limit": 2, "q": query, "payment_status": "paid", "history_scope": "general"})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [expected]
    assert telemetry["hydrated"] == [expected]
    assert telemetry["stream_rows"][0] > 100
    assert len(response.json()[0]["items"]) == 2
    if kind == "meal":
        assert all(item["meal_id"] and item["selections"] for item in response.json()[0]["items"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["general", "meal"])
async def test_postgres_search_stops_early_and_closes_before_page_hydration(pg_history, kind):
    response, telemetry = await read(pg_history, kind, {"limit": 3, "q": "城南", "history_scope": "general"})
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    assert telemetry["stream_rows"] == [7]  # Two saved lines per order plus one lookahead row.
    assert sorted(telemetry["hydrated"]) == sorted(row["id"] for row in response.json())


@pytest.mark.asyncio
async def test_postgres_history_preserves_admin_legacy_array_owner_scope_and_filters(pg_history):
    legacy, _ = await read(pg_history, "general", actor="admin")
    assert legacy.status_code == 200 and len(legacy.json()) == 227
    buyer_legacy, _ = await read(pg_history, "general")
    assert buyer_legacy.status_code == 200 and len(buyer_legacy.json()) == 221
    own, _ = await read(pg_history, "general", {"own_only": True, "history_scope": "general"}, actor="admin")
    assert len(own.json()) == 3 and all(row["id"].startswith("pg-admin-general") for row in own.json())
    meals, _ = await read(pg_history, "meal", actor="admin")
    assert len(meals.json()) == 3 and all(row["id"].startswith("pg-admin-meal") for row in meals.json())
    for kind in ["general", "meal"]:
        private, _ = await read(pg_history, kind, {"limit": 100, "q": "ADMIN_PRIVATE", "own_only": False})
        empty, _ = await read(pg_history, kind, {"limit": 100}, actor="stranger")
        assert private.status_code == empty.status_code == 200 and private.json() == empty.json() == []
    paid, _ = await read(pg_history, "general", {"limit": 100, "history_scope": "general", "payment_status": "paid"})
    assert len(paid.json()) == 55 and all(row["payment_status"] == "paid" for row in paid.json())
    async with pg_history.factory() as session:
        async with api_test_context(session, [orders_router, meals_router], settings=make_test_settings()) as client:
            assert (await client.get("/v1/orders")).status_code == 401
            assert (await client.get("/v1/meal-orders")).status_code == 401
            for path in ["/v1/orders/pg-admin-general-000", "/v1/meal-orders/pg-admin-meal-000"]:
                assert (await client.get(path, headers=pg_history.headers["buyer"])).status_code == 404


@pytest.mark.asyncio
async def test_postgres_meal_page_lookup_count_is_independent_of_order_count_and_snapshots_match_legacy(pg_history):
    legacy, _ = await read(pg_history, "meal")
    snapshots = {row["id"]: row for row in legacy.json()}
    offering_selects = []
    for limit in [1, 25]:
        response, telemetry = await read(pg_history, "meal", {"limit": limit})
        assert response.status_code == 200, response.text
        assert len(response.json()) == limit
        assert all(row == snapshots[row["id"]] for row in response.json())
        offering_selects.append(sum("meal_event_offerings" in statement for statement in telemetry["selects"]))
    assert all(count <= 1 for count in offering_selects)


@pytest.mark.asyncio
async def test_postgres_history_invalid_query_and_search_exception_do_not_leak_stream(pg_history, monkeypatch):
    for params in [{"limit": 0}, {"limit": 101}, {"limit": 3, "before_id": "partial"},
        {"before_created_at": "2030-01-01T00:00:00Z", "before_id": "no-limit"}]:
        response, _ = await read(pg_history, "general", params)
        assert response.status_code == 422, response.text
    normalize = history._normalized
    def controlled_failure(value):
        if value.startswith("PG-BUYER"):
            raise RuntimeError("controlled-stream-search-failure")
        return normalize(value)
    monkeypatch.setattr(history, "_normalized", controlled_failure)
    with pytest.raises(RuntimeError, match="controlled-stream-search-failure"):
        await read(pg_history, "general", {"limit": 2, "q": "abc", "history_scope": "general"})
