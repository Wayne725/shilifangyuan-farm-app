from datetime import datetime, timedelta, timezone

import pytest

from app.models import (FulfillmentMethod, Order, OrderFulfillment, OrderItem, PaymentStatus,
                        SalesChannel, TaxType, User, UserRole)
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, make_test_settings


@pytest.fixture
async def history_context(database_session):
    session = database_session
    owner = User(email="history-owner@example.com", display_name="訂單擁有人", password_hash="test", user_role=UserRole.ADMIN)
    other = User(email="history-other@example.com", display_name="另一買家", password_hash="test")
    session.add_all([owner, other])
    await session.flush()
    now = datetime(2030, 1, 10, 4, tzinfo=timezone.utc)
    rows = []
    for index in range(8):
        order = Order(id=f"history-{index:02d}", order_number=f"HISTORY-{index:04d}", user_id=owner.id,
            order_kind="regular", sales_channel=SalesChannel.REGULAR,
            membership_type_snapshot="nonmember", amount_total=100, contact_email=owner.email,
            created_at=now-timedelta(days=index//2),
            payment_status=PaymentStatus.PAID if index % 2 else PaymentStatus.PENDING,
            items=[OrderItem(product_name="ＡＢＣ 白米" if index == 7 else f"一般商品{index}", unit_label="份",
                quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE)],
            fulfillment=OrderFulfillment(method=FulfillmentMethod.COOPERATIVE_PICKUP, pickup_location="城南合作社"))
        rows.append(order)
    foreign = Order(id="history-foreign", order_number="HISTORY-FOREIGN", user_id=other.id,
        order_kind="regular", membership_type_snapshot="nonmember", amount_total=100, contact_email=other.email,
        created_at=now+timedelta(days=1), items=[OrderItem(product_name="另一人的私密商品", unit_label="份",
            quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE)])
    session.add_all([*rows, foreign])
    await session.commit()
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        yield client, owner, other, rows


@pytest.mark.asyncio
async def test_order_history_is_bounded_and_keyset_cursor_is_stable_for_equal_timestamps(history_context):
    client, owner, _other, rows = history_context
    params = {"limit": 3, "own_only": True, "history_scope": "general"}
    first = await client.get("/v1/orders", params=params, headers=auth_headers(owner))
    assert first.status_code == 200, first.text
    assert len(first.json()) == 3
    assert [row["id"] for row in first.json()] == [rows[i].id for i in [1, 0, 3]]
    last = first.json()[-1]
    second = await client.get("/v1/orders", params={**params,
        "before_created_at": last["created_at"], "before_id": last["id"]}, headers=auth_headers(owner))
    assert second.status_code == 200, second.text
    assert [row["id"] for row in second.json()] == [rows[i].id for i in [2, 5, 4]]
    assert set(row["id"] for row in first.json()).isdisjoint(row["id"] for row in second.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["abc 白米", "ＡＢＣ", "城南 白米", "history-0007", " \ufeffＡＢＣ\u00a0白米\ufeff "])
async def test_order_history_searches_all_pages_with_original_normalized_multiword_semantics(history_context, query):
    client, owner, _other, rows = history_context
    response = await client.get("/v1/orders", params={"limit": 2, "own_only": True,
        "history_scope": "general", "q": query, "payment_status": "paid"}, headers=auth_headers(owner))
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [rows[7].id]


@pytest.mark.asyncio
async def test_order_history_treats_bom_and_js_whitespace_only_query_as_empty(history_context):
    client, owner, _other, _rows = history_context
    response = await client.get("/v1/orders", params={"limit": 3, "own_only": True, "q": "\ufeff\u00a0\ufeff"},
        headers=auth_headers(owner))
    assert response.status_code == 200, response.text
    assert len(response.json()) == 3


@pytest.mark.asyncio
async def test_legacy_admin_array_and_customer_authorization_remain_compatible(history_context):
    client, owner, other, rows = history_context
    legacy = await client.get("/v1/orders", headers=auth_headers(owner))
    assert legacy.status_code == 200
    assert len(legacy.json()) == 9
    customer = await client.get("/v1/orders", params={"limit": 2}, headers=auth_headers(other))
    assert customer.status_code == 200
    assert [row["id"] for row in customer.json()] == ["history-foreign"]
    denied = await client.get(f"/v1/orders/{rows[0].id}", headers=auth_headers(other))
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_my_history_detail_scope_does_not_turn_admin_old_account_url_into_another_persons_order(history_context):
    client, owner, _other, rows = history_context
    legacy = await client.get("/v1/orders/history-foreign", headers=auth_headers(owner))
    assert legacy.status_code == 200, legacy.text
    private = await client.get("/v1/orders/history-foreign", params={"own_only": True}, headers=auth_headers(owner))
    assert private.status_code == 404, private.text
    mine = await client.get(f"/v1/orders/{rows[0].id}", params={"own_only": True}, headers=auth_headers(owner))
    assert mine.status_code == 200, mine.text


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"limit": 2, "before_id": "one-half"},
    {"limit": 2, "before_created_at": "2030-01-01T00:00:00Z"}, {"limit": 2, "payment_status": "invalid"}])
async def test_order_history_rejects_invalid_limits_and_incomplete_cursors(history_context, params):
    client, owner, _other, _rows = history_context
    response = await client.get("/v1/orders", params=params, headers=auth_headers(owner))
    assert response.status_code == 422, response.text
