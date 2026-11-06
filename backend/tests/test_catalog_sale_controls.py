from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.integrations.payment_service import create_payment_attempt
from app.models import AdminAudit, GroupBundle, GroupBundleItem, Product, Supplier
from app.routers.catalog import catalog_router
from app.routers.orders import orders_router
from tests.support import auth_headers
from tests.test_group_campaign_shipping import campaign_context, campaign_payload
from tests.test_integrations import make_regular_order, payment_settings


def inactive_supplier(name):
    return Supplier(
        business_name=name, responsible_person_encrypted="test", contact_person_encrypted="test",
        phone_encrypted="test", email_encrypted="test", bank_account_encrypted="test", is_active=False,
    )


async def test_product_edit_can_keep_inactive_supplier_but_cannot_select_another(campaign_context):
    ctx = campaign_context
    client, session, product = ctx["client"], ctx["session"], ctx["product"]
    client._transport.app.include_router(catalog_router)
    current_supplier, another_supplier = inactive_supplier("原供應者"), inactive_supplier("其他供應者")
    session.add_all([current_supplier, another_supplier])
    await session.flush()
    product.supplier_id = current_supplier.id
    await session.commit()
    headers = auth_headers(ctx["admin"])

    saved = await client.patch(f"/v1/products/{product.id}", headers=headers, json={
        "is_active": False, "supplier_id": current_supplier.id,
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["is_active"] is False
    assert saved.json()["supplier_id"] == current_supplier.id
    rejected = await client.patch(f"/v1/products/{product.id}", headers=headers, json={
        "supplier_id": another_supplier.id,
    })
    assert rejected.status_code == 422, rejected.text
    assert product.supplier_id == current_supplier.id


async def test_admin_catalog_newest_first_preserves_public_category_order(campaign_context):
    ctx = campaign_context
    client, session, product = ctx["client"], ctx["session"], ctx["product"]
    client._transport.app.include_router(catalog_router)
    now = datetime.now(timezone.utc)
    product.name, product.created_at = "A 舊商品", now - timedelta(days=1)
    newer = Product(slug="catalog-newer", name="Z 新商品", category=product.category, unit="份",
                    member_price=10, nonmember_price=10, stock_quantity=5, created_at=now)
    newest_inactive = Product(slug="catalog-newest-inactive", name="停賣新商品", category=product.category, unit="份",
                             member_price=10, nonmember_price=10, stock_quantity=5, is_active=False,
                             created_at=now + timedelta(seconds=1))
    session.add_all([newer, newest_inactive])
    await session.commit()
    admin = await client.get("/v1/products", headers=auth_headers(ctx["admin"]))
    public = await client.get("/v1/products")
    assert [row["id"] for row in admin.json()] == [newest_inactive.id, newer.id, product.id]
    assert [row["id"] for row in public.json()] == [product.id, newer.id]


@pytest.mark.parametrize("target_type", ["product", "bundle"])
async def test_stopped_product_blocks_group_quote_join_and_increase_but_keeps_existing_order(campaign_context, target_type):
    ctx = campaign_context
    client, session, product = ctx["client"], ctx["session"], ctx["product"]
    client._transport.app.include_router(catalog_router)
    client._transport.app.include_router(orders_router)
    target = product
    if target_type == "bundle":
        target = GroupBundle(name="測試套組", member_price=480, nonmember_price=520,
                             items=[GroupBundleItem(product=product, quantity=1, position=0)])
        session.add(target)
        await session.commit()
    admin, customer = auth_headers(ctx["admin"]), auth_headers(ctx["proposer"])
    created = await client.post("/v1/group-campaigns", headers=admin, json=campaign_payload(target.id, target_type=target_type))
    assert created.status_code == 201, created.text
    path = f"/v1/group-campaigns/{created.json()['id']}"
    body = {"quantity": 2, "contact_email": "buyer@example.com"}
    joined = await client.post(f"{path}/join", headers=customer, json=body)
    assert joined.status_code == 201, joined.text
    order = joined.json()
    stopped = await client.patch(f"/v1/products/{product.id}", headers=admin, json={"is_active": False})
    assert stopped.status_code == 200, stopped.text
    for suffix in ("quote", "join"):
        blocked = await client.post(f"{path}/{suffix}", headers=customer, json={**body, "quantity": 1})
        assert blocked.status_code == 422, blocked.text
    amendment = {"expected_revision": order["revision"], "items": [{"order_item_id": order["items"][0]["id"], "quantity": 3}]}
    increase = await client.post(f"/v1/orders/{order['id']}/quantity-amendments/quote", headers=customer, json=amendment)
    assert increase.status_code == 409, increase.text
    amendment["items"][0]["quantity"] = 1
    reduction = await client.post(f"/v1/orders/{order['id']}/quantity-amendments/quote", headers=customer, json=amendment)
    assert reduction.status_code == 200, reduction.text
    existing = await client.get(f"/v1/orders/{order['id']}", headers=customer)
    assert existing.status_code == 200, existing.text
    assert existing.json()["payment_status"] == "pending"
    assert existing.json()["items"] == order["items"]
    assert existing.json()["amount_total"] == order["amount_total"]
    resumed = await client.patch(f"/v1/products/{product.id}", headers=admin, json={"is_active": True})
    assert resumed.status_code == 200, resumed.text
    allowed = await client.post(f"{path}/quote", headers=customer, json={**body, "quantity": 1})
    assert allowed.status_code == 200, allowed.text


async def test_stop_resume_updates_public_cart_checkout_without_changing_existing_order(campaign_context):
    ctx = campaign_context
    client, session, product = ctx["client"], ctx["session"], ctx["product"]
    client._transport.app.include_router(catalog_router)
    client._transport.app.include_router(orders_router)
    admin, customer = auth_headers(ctx["admin"]), auth_headers(ctx["proposer"])
    body = {"items": [{"product_id": product.id, "quantity": 2}], "contact_email": "buyer@example.com"}
    created = await client.post("/v1/orders", headers=customer, json=body)
    assert created.status_code == 201, created.text
    original = created.json()
    stock = product.stock_quantity
    for active in (False, True):
        changed = await client.patch(f"/v1/products/{product.id}", headers=admin, json={"is_active": active})
        assert changed.status_code == 200, changed.text
        assert changed.json()["is_active"] is active
        assert changed.json()["stock_quantity"] == stock
        assert changed.json()["daily_sales_enabled"] is True
        public = (await client.get("/v1/products")).json()
        assert any(row["id"] == product.id for row in public) is active
        assert (await client.get(f"/v1/products/{product.id}")).status_code == (200 if active else 404)
        for path, expected in (("/v1/orders/quote", 200), ("/v1/orders", 201)):
            result = await client.post(path, headers=customer, json=body)
            assert result.status_code == (expected if active else 422), result.text
        existing = await client.get(f"/v1/orders/{original['id']}", headers=customer)
        assert existing.status_code == 200, existing.text
        assert existing.json()["payment_status"] == original["payment_status"]
        assert existing.json()["items"] == original["items"]
    audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.aggregate_id == product.id, AdminAudit.action == "product.update")))
    assert len(audits) == 2
    assert all(row.actor_id == ctx["admin"].id and row.data["changed_fields"] == ["is_active"] for row in audits)


async def test_existing_unpaid_order_can_start_payment_after_product_is_stopped(database_session):
    buyer, product, order = await make_regular_order(database_session)
    product.is_active = False
    await database_session.commit()
    original_total, original_quantity = order.amount_total, order.items[0].quantity
    original_stock = product.stock_quantity

    attempt = await create_payment_attempt(database_session, order.id, buyer, payment_settings())

    assert attempt.amount == original_total
    assert order.items[0].quantity == original_quantity
    assert product.is_active is False
    assert product.stock_quantity == original_stock - original_quantity
