from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.auth import create_token
from app.integrations.invoice_service import invoice_context_from_settings
from app.models import (
    FulfillmentMethod, InventoryReservation, MembershipCharge, Order, OrderItem,
    OrderKind, OutboxEvent, PaymentAttempt, PaymentStatus, Product, SalesChannel, TaxType,
)
from app.models import User
from app.routers import groups, logistics, membership
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context
from tests.test_meals_production_configuration import meal_production_settings


@pytest.fixture
async def product_context(database_session):
    session = database_session
    product = Product(id=str(uuid4()), sku="RELEASE-ACCEPTANCE-10", slug="release-test",
        name="正式商品測試", category="測試", unit="件", member_price=10,
        nonmember_price=10, stock_quantity=1, can_ship=False, is_active=True)
    buyer = User(email="release-buyer@example.com", display_name="測試買家", password_hash="test")
    session.add_all([product, buyer])
    await session.commit()
    settings = meal_production_settings(meals_test_product_id=product.id, meals_test_product_sku=product.sku)
    token = create_token(buyer, "access", timedelta(minutes=5), settings=settings)
    headers = {"Authorization": f"Bearer {token}"}
    async with api_test_context(session, [orders_router, payments_router, groups.groups_router,
        logistics.logistics_router, membership.membership_router], settings=settings) as client:
        yield client, session, product, buyer, settings, headers


async def create_product(context, **overrides):
    client, _session, product, buyer, _settings, headers = context
    return await client.post("/v1/orders", headers=headers, json={
        "items": [{"product_id": product.id, "quantity": 1}],
        "contact_email": buyer.email, "fulfillment_method": "cooperative_pickup", **overrides,
    })


@pytest.mark.asyncio
async def test_exact_product_reserves_last_unit_and_fresh_checkout_works(product_context):
    client, session, product, _buyer, settings, headers = product_context
    created = await create_product(product_context)
    assert created.status_code == 201, created.text
    order_id, product_id = created.json()["id"], product.id
    assert created.json()["amount_total"] == 10
    order = await session.get(Order, order_id)
    assert order.invoice_provider_context == invoice_context_from_settings(settings)
    assert await session.scalar(select(func.count(OutboxEvent.id))) == 0
    started = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    assert started.status_code == 201, started.text
    attempt_id = started.json()["id"]
    assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 0
    assert await session.scalar(select(func.count(InventoryReservation.id))) == 1
    session.expunge_all()
    checkout = await client.get(f"/payments/{attempt_id}/checkout")
    assert checkout.status_code == 303, checkout.text
    assert checkout.headers["location"].startswith("https://pay.example.test/calc/pay_encrypt/")
    duplicate = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == attempt_id
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == 1
    assert await session.scalar(select(Product.stock_quantity).where(Product.id == product_id)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["disabled", "wrong_id", "wrong_sku", "inactive", "member_price",
    "nonmember_price", "can_ship", "no_stock", "qty2", "duplicate_lines", "mixed_cart", "shipping",
    "group_pickup", "preview", "payment_stage", "invoice_stage", "unverified"])
async def test_product_creation_rejects_every_widening(product_context, case):
    client, session, product, _buyer, settings, _headers = product_context
    body = {}
    if case == "disabled":
        settings.meals_test_product_id = settings.meals_test_product_sku = ""
    elif case == "wrong_id":
        settings.meals_test_product_id = str(uuid4())
    elif case == "wrong_sku":
        settings.meals_test_product_sku = "SAME-ID-WRONG-SKU"
    elif case == "inactive":
        product.is_active = False
    elif case == "member_price":
        product.member_price = 9
    elif case == "nonmember_price":
        product.nonmember_price = 11
    elif case == "can_ship":
        product.can_ship = True
    elif case == "no_stock":
        product.stock_quantity = 0
    elif case == "qty2":
        body["items"] = [{"product_id": product.id, "quantity": 2}]
    elif case == "duplicate_lines":
        body["items"] = [{"product_id": product.id, "quantity": 1}] * 2
    elif case == "mixed_cart":
        body["items"] = [{"product_id": product.id, "quantity": 1}, {"product_id": str(uuid4()), "quantity": 1}]
    elif case == "shipping":
        body["fulfillment_method"] = "ecpay_logistics"
    elif case == "group_pickup":
        body["fulfillment_method"] = "group_pickup"
    elif case == "preview":
        settings.environment = "preview"
        settings.raygate_payment_acceptance_sku = product.sku
    elif case == "payment_stage":
        settings.raygate_payment_stage = True
    elif case == "invoice_stage":
        settings.fanyu_invoice_stage = True
    elif case == "unverified":
        settings.raygate_payment_contract_verified = False
    await session.commit()
    response = await create_product(product_context, **body)
    assert response.status_code in {409, 422}, response.text
    assert await session.scalar(select(func.count(Order.id))) == 0
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == 0
    assert await session.scalar(select(func.count(OutboxEvent.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["new_attempt", "checkout"])
@pytest.mark.parametrize("case", ["wrong_id", "wrong_sku", "inactive", "member_price", "nonmember_price",
    "can_ship", "quantity", "unit_price", "subtotal", "total", "mixed_items", "order_kind",
    "sales_channel", "fulfillment_method", "fulfillment_record", "group_source", "meal_source",
    "old_context", "missing_context", "group_campaign", "meal_event", "disabled"])
async def test_payment_rechecks_product_and_order_not_only_initial_creation(product_context, entrypoint, case):
    client, session, product, _buyer, settings, headers = product_context
    created = await create_product(product_context)
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    attempt_id = None
    if entrypoint == "checkout":
        started = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
        assert started.status_code == 201, started.text
        attempt_id = started.json()["id"]
    order = await session.scalar(select(Order).where(Order.id == order_id).options(
        selectinload(Order.items), selectinload(Order.fulfillment)))
    item = order.items[0]
    if case == "wrong_id":
        settings.meals_test_product_id = str(uuid4())
    elif case == "wrong_sku":
        product.sku = "REPLACED-SKU"
    elif case == "inactive":
        product.is_active = False
    elif case == "member_price":
        product.member_price = 9
    elif case == "nonmember_price":
        product.nonmember_price = 11
    elif case == "can_ship":
        product.can_ship = True
    elif case == "quantity":
        item.quantity = 2
    elif case == "unit_price":
        item.unit_price = 9
    elif case == "subtotal":
        item.subtotal = 9
    elif case == "total":
        order.amount_total = 9
    elif case == "mixed_items":
        order.items.append(OrderItem(product_name="混入", quantity=1, unit_label="件", unit_price=0, subtotal=0, tax_type=TaxType.TAXABLE))
    elif case == "order_kind":
        order.order_kind = OrderKind.GROUP
    elif case == "sales_channel":
        order.sales_channel = SalesChannel.GROUP
    elif case == "fulfillment_method":
        order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    elif case == "fulfillment_record":
        order.fulfillment.method = FulfillmentMethod.ECPAY_LOGISTICS
    elif case == "group_source":
        item.source_bundle_id = str(uuid4())
    elif case == "meal_source":
        item.source_meal_offering_id = str(uuid4())
    elif case == "old_context":
        order.invoice_provider_context = {**order.invoice_provider_context, "user_id": "OLD-ACCOUNT"}
    elif case == "missing_context":
        order.invoice_provider_context = None
    elif case == "group_campaign":
        order.group_campaign_id = str(uuid4())
    elif case == "meal_event":
        order.meal_event_id = str(uuid4())
    elif case == "disabled":
        settings.meals_test_product_id = settings.meals_test_product_sku = ""
    stored_context = order.invoice_provider_context
    await session.commit()
    session.expunge_all()
    if entrypoint == "new_attempt":
        response = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    else:
        response = await client.get(f"/payments/{attempt_id}/checkout")
    assert response.status_code == 409, response.text
    current = await session.get(Order, order_id)
    assert current.invoice_provider_context == stored_context
    assert current.payment_status == PaymentStatus.PENDING
    assert await session.scalar(select(func.count(PaymentAttempt.id))) == (1 if attempt_id else 0)
    assert await session.scalar(select(func.count(OutboxEvent.id))) == 0


@pytest.mark.asyncio
async def test_checkout_rejects_tampered_attempt_amount(product_context):
    client, session, _product, _buyer, _settings, headers = product_context
    created = await create_product(product_context)
    order_id = created.json()["id"]
    started = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    attempt_id = started.json()["id"]
    attempt = await session.get(PaymentAttempt, attempt_id)
    attempt.amount = 11
    await session.commit()
    session.expunge_all()
    checkout = await client.get(f"/payments/{attempt_id}/checkout")
    assert checkout.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("path,body", [
    ("/v1/group-campaigns/missing/join", {"quantity": 1, "contact_email": "buyer@example.com"}),
    ("/v1/membership/application/submit", {}),
    ("/v1/orders/missing/logistics/selection", {"channel": "home_delivery", "temperature": "ambient",
        "recipient_name": "測試買家", "recipient_phone": "0912345678", "shipping_address": "台北市中正區測試路1號"}),
])
async def test_controlled_product_does_not_enable_other_business_routes(product_context, path, body):
    client, session, _product, _buyer, _settings, headers = product_context
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 409, response.text
    assert await session.scalar(select(func.count(Order.id))) == 0
    assert await session.scalar(select(func.count(MembershipCharge.id))) == 0
