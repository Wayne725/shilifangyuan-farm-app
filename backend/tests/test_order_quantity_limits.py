from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import InventoryReservation, Order, PaymentAttempt, Product, User
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, make_test_settings


@pytest.fixture
async def quantity_context(database_session):
    buyer = User(email="quantity-buyer@example.com", display_name="數量測試買家", password_hash="test")
    products = [
        Product(
            slug=f"quantity-product-{index}", name=f"數量測試商品{index}",
            category="processed", unit="份", member_price=10, nonmember_price=12,
            stock_quantity=5000, is_active=True,
        )
        for index in range(2)
    ]
    database_session.add_all([buyer, *products])
    await database_session.commit()
    async with api_test_context(database_session, [orders_router], settings=make_test_settings()) as client:
        yield SimpleNamespace(session=database_session, client=client, buyer=buyer, products=products)


async def submit(context, path, items):
    payload = {"items": items}
    if path == "/v1/orders":
        payload.update(contact_email=context.buyer.email, fulfillment_method="cooperative_pickup")
    return await context.client.post(path, headers=auth_headers(context.buyer), json=payload)


@pytest.mark.parametrize("path", ["/v1/orders/quote", "/v1/orders"])
async def test_repeated_product_cannot_bypass_combined_quantity_limit(quantity_context, path):
    c = quantity_context
    response = await submit(c, path, [
        {"product_id": c.products[0].id, "quantity": 500},
        {"product_id": c.products[0].id, "quantity": 500},
    ])
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "同一商品合計數量不可超過 999"
    assert await c.session.scalar(select(func.count(Order.id))) == 0
    assert await c.session.scalar(select(func.count(InventoryReservation.id))) == 0
    assert await c.session.scalar(select(func.count(PaymentAttempt.id))) == 0
    assert [product.stock_quantity for product in c.products] == [5000, 5000]


@pytest.mark.parametrize("path", ["/v1/orders/quote", "/v1/orders"])
async def test_repeated_product_at_limit_is_consolidated_and_priced_once(quantity_context, path):
    c = quantity_context
    response = await submit(c, path, [
        {"product_id": c.products[0].id, "quantity": 500},
        {"product_id": c.products[0].id, "quantity": 499},
    ])
    assert response.status_code == (200 if path.endswith("/quote") else 201), response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 999
    assert body["items"][0]["unit_price"] == 12
    assert body["amount_total"] == 999 * 12


@pytest.mark.parametrize("path", ["/v1/orders/quote", "/v1/orders"])
async def test_quantity_limit_applies_to_each_product_not_whole_cart(quantity_context, path):
    c = quantity_context
    response = await submit(c, path, [
        {"product_id": product.id, "quantity": 999} for product in c.products
    ])
    assert response.status_code == (200 if path.endswith("/quote") else 201), response.text
    body = response.json()
    assert len(body["items"]) == 2
    assert all(item["quantity"] == 999 for item in body["items"])
    assert body["amount_total"] == 2 * 999 * 12
