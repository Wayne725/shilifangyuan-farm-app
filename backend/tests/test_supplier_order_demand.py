import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.auth import create_token
from app.models import (
    GroupBundle, GroupCampaign, GroupDecisionStatus, MembershipType, Order, OrderItem, OrderKind,
    PaymentMethod, PaymentStatus, Product, Refund, RefundStatus, SalesChannel, ShippingTemperature, Supplier,
    TaxType, User, UserRole,
)
from app.routers.cooperative import cooperative_router
from app.routers.groups import groups_router
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_group_checkout_contract import prepare_group, join
from tests.test_order_quantity_amendments import submit


URL = "/v1/admin/supplier-order-demand.csv"
PERIOD = {"starts_on": "2030-01-03", "ends_on": "2030-01-03"}
MIDNIGHT = datetime(2030, 1, 2, 16, tzinfo=timezone.utc)


def supplier(name):
    return Supplier(business_name=name, responsible_person_encrypted="private",
                    contact_person_encrypted="private", phone_encrypted="private",
                    email_encrypted="private", bank_account_encrypted="private")


def catalog_product(vendor, sku):
    return Product(supplier=vendor, sku=sku, slug=f"product-{uuid4().hex}",
                   name="同名商品", category="米食", unit="袋", member_price=100, nonmember_price=100,
                   stock_quantity=20, shipping_temperature=ShippingTemperature.CHILLED)


def csv_rows(response):
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "private" not in response.text and "buyer@example.com" not in response.text
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"))))


async def download(client, headers, vendor):
    return await client.get(URL, headers=headers, params={
        **PERIOD, "scope": "supplier", "supplier_id": vendor.id, "expected_arrival_on": "2030-01-05",
    })


@pytest.mark.asyncio
async def test_regular_order_supplier_changes_amendment_and_missing_product(database_session):
    session = database_session
    admin = User(email="admin@example.test", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="buyer@example.com", display_name="買家", password_hash="test")
    a, b = supplier("=供應者甲"), supplier("供應者乙")
    first, second, other = catalog_product(a, "=甲SKU"), catalog_product(a, "甲SKU2"), catalog_product(b, "乙SKU")
    session.add_all([admin, buyer, first, second, other])
    await session.commit()
    async with api_test_context(session, [orders_router, cooperative_router], settings=make_test_settings()) as client:
        body = {"contact_email": buyer.email, "fulfillment_method": "cooperative_pickup",
                "items": [{"product_id": product.id, "quantity": 1} for product in (first, second, other)]}
        response = await client.post("/v1/orders", headers=auth_headers(buyer), json=body)
        assert response.status_code == 201, response.text
        assert "procurement_snapshot" not in response.text and a.id not in response.text
        order = await session.get(Order, response.json()["id"])
        await session.refresh(order, ["items"])
        item = next(item for item in order.items if item.source_product_id == first.id)
        original = dict(item.procurement_snapshot)
        assert original["supplier_id"] == a.id and original["sku"] == "=甲SKU"
        first.supplier_id, first.sku, first.shipping_temperature = b.id, "新SKU", ShippingTemperature.AMBIENT
        await session.commit()
        changed = await submit(client, buyer, order, 2, total=600)
        assert changed.status_code == 200, changed.text
        assert item.procurement_snapshot == original and item.quantity == 2
        assert "procurement_snapshot" not in changed.text
        order.payment_status, order.paid_at = PaymentStatus.PAID, MIDNIGHT
        await session.execute(delete(Product).where(Product.id == first.id))
        await session.commit()
        await session.refresh(item)
        assert item.source_product_id is None
        rows_a = csv_rows(await download(client, auth_headers(admin), a))
        rows_b = csv_rows(await download(client, auth_headers(admin), b))
        assert {row["下單SKU"] for row in rows_a} == {"'=甲SKU", "甲SKU2"}
        old_product = next(row for row in rows_a if row["下單SKU"] == "'=甲SKU")
        assert old_product["商品識別"] == original["product_id"]
        assert old_product["供應者現名（按下單歸屬）"] == "'=供應者甲"
        assert old_product["數量"] == "2" and old_product["下單溫層"] == "冷藏"
        assert [row["下單SKU"] for row in rows_b] == ["乙SKU"]
        assert (await download(client, {}, a)).status_code == 401
        assert (await download(client, auth_headers(buyer), a)).status_code == 403
        expired = create_token(admin, "access", -timedelta(minutes=5))
        assert (await download(client, {"Authorization": f"Bearer {expired}"}, a)).status_code == 401


@pytest.mark.asyncio
async def test_confirmed_group_direct_items_review_unknown_and_order_refunds(database_session):
    session = database_session
    context = await prepare_group(session)
    first = await session.get(Product, context.product_ids[0])
    a = supplier("單品供應者")
    first.supplier = a
    first.sku = "團購SKU"
    await session.commit()
    async with api_test_context(session, [groups_router, cooperative_router], settings=make_test_settings()) as client:
        context.client = client
        result = await join(context)
        assert result.status_code == 201, result.text
        assert "procurement_snapshot" not in result.text
        group_order = await session.get(Order, result.json()["id"])
        await session.refresh(group_order, ["items"])
        assert group_order.items[0].procurement_snapshot["supplier_id"] == a.id
        group_order.payment_status, group_order.paid_at = PaymentStatus.PAID, MIDNIGHT
        await session.commit()
        assert (await download(client, context.admin_headers, a)).status_code == 409
        group = await session.get(GroupCampaign, context.campaign_id)
        group.decision_status = GroupDecisionStatus.CONFIRMED
        await session.commit()
        rows = csv_rows(await download(client, context.admin_headers, a))
        assert [row["下單SKU"] for row in rows] == ["團購SKU"]

        def add_order(name, *, paid_at=MIDNIGHT, snapshot=None, channel=SalesChannel.REGULAR, status=PaymentStatus.PAID):
            order = Order(order_number=f"DEMAND-{uuid4().hex[:24]}", order_kind=OrderKind.REGULAR,
                          sales_channel=channel, user_id=context.buyer_ids[0], membership_type_snapshot=MembershipType.NONMEMBER,
                          amount_total=300, contact_email="buyer@example.com", payment_status=status, paid_at=paid_at,
                          items=[OrderItem(product_name=name, unit_label="袋", quantity=3, unit_price=100, subtotal=300,
                                           tax_type=TaxType.TAXABLE, source_product_id=first.id, procurement_snapshot=snapshot)])
            session.add(order)
            return order

        snapshot = dict(group_order.items[0].procurement_snapshot)
        add_order("舊品項")
        add_order("未指定供應者", snapshot={**snapshot, "supplier_id": None})
        refund = add_order("部分退款", snapshot=snapshot)
        add_order("前一天", paid_at=MIDNIGHT - timedelta(microseconds=1), snapshot=snapshot)
        add_order("下一天", paid_at=MIDNIGHT + timedelta(days=1), snapshot=snapshot)
        add_order("便當", snapshot=snapshot, channel=SalesChannel.MEAL_PREORDER)
        add_order("未付款", snapshot=snapshot, status=PaymentStatus.PENDING)
        walk_in = add_order("現場收款", snapshot=snapshot)
        walk_in.customer_kind, walk_in.user_id, walk_in.payment_method = "walk_in", None, PaymentMethod.CASH_ON_PICKUP
        bundle = GroupBundle(name="套組", member_price=300, nonmember_price=300)
        session.add(bundle)
        await session.flush()
        bundle_order = add_order("共同購買套組", snapshot=snapshot, channel=SalesChannel.GROUP)
        bundle_order.group_campaign_id = group.id
        bundle_order.items[0].source_bundle_id = bundle.id
        await session.flush()
        session.add(Refund(order_id=refund.id, amount=100, status=RefundStatus.COMPLETED,
                           reason="private", requested_by_id=context.buyer_ids[0]))
        session.add(Refund(order_id=group_order.id, amount=100, status=RefundStatus.COMPLETED,
                           purpose="obsolete_order_revision", reason="private", requested_by_id=context.buyer_ids[0]))
        await session.commit()
        reviewed = csv_rows(await client.get(URL, headers=context.admin_headers, params={**PERIOD, "scope": "review"}))
        assert {row["下單品名"]: (row["待核對原因"], row["原訂數量"]) for row in reviewed} == {
            "舊品項": ("歷史品項未留供應者快照", "3"),
            "未指定供應者": ("下單時未指定供應者", "3"),
            "部分退款": ("退款待核對", "3"),
        }
        assert [row["下單SKU"] for row in csv_rows(await download(client, context.admin_headers, a))] == ["團購SKU"]
        assert (await client.get(URL, headers=context.admin_headers, params={
            **PERIOD, "scope": "review", "supplier_id": a.id,
        })).status_code == 422
        assert (await client.get(URL, headers=context.admin_headers, params={
            "starts_on": "2030-01-04", "ends_on": "2030-01-03", "scope": "review",
        })).status_code == 422
