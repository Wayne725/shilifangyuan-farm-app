from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import create_token
from app.models import FulfillmentMethod, Order, OrderItem, PaymentAttempt, Product, TaxType, User
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context
from tests.test_meals_production_configuration import meal_production_settings


PRIMARY_ID = "c320548d-3ff4-49ed-987f-8c0926e21483"
PRIMARY_SKU = "PAYMENT-ACCEPTANCE-10"
SECONDARY_ID = "40fc981b-5085-4009-a8a7-bd9a9c506c9d"
SECONDARY_SKU = "REMOTE-PAYMENT-10"


def two_product_settings(**overrides):
    return meal_production_settings(**{
        "meals_test_product_id": PRIMARY_ID, "meals_test_product_sku": PRIMARY_SKU,
        "meals_test_secondary_product_id": SECONDARY_ID, "meals_test_secondary_product_sku": SECONDARY_SKU,
        **overrides,
    })


@pytest.fixture
async def two_product_context(database_session):
    session = database_session
    products = [
        Product(id=PRIMARY_ID, sku=PRIMARY_SKU, slug="primary-test", name="金流與發票驗收品（測試）",
            category="測試", unit="件", member_price=10, nonmember_price=10,
            stock_quantity=1, can_ship=False, is_active=True),
        Product(id=SECONDARY_ID, sku=SECONDARY_SKU, slug="secondary-test", name="遠端付款驗收品（測試）",
            category="測試", unit="件", member_price=10, nonmember_price=10,
            stock_quantity=21, can_ship=False, is_active=True),
    ]
    buyer = User(email="two-products-buyer@example.com", display_name="測試買家", password_hash="test")
    session.add_all([*products, buyer])
    await session.commit()
    settings = two_product_settings()
    token = create_token(buyer, "access", timedelta(minutes=5), settings=settings)
    headers = {"Authorization": f"Bearer {token}"}
    async with api_test_context(session, [orders_router, payments_router], settings=settings) as client:
        yield client, session, settings, headers


@pytest.mark.asyncio
@pytest.mark.parametrize("product_id", [PRIMARY_ID, SECONDARY_ID])
async def test_each_explicit_test_product_completes_quote_order_attempt_and_checkout(two_product_context, product_id):
    client, session, _settings, headers = two_product_context
    items = [{"product_id": product_id, "quantity": 1}]
    quote = await client.post("/v1/orders/quote", headers=headers, json={"items": items})
    assert quote.status_code == 200, quote.text
    assert quote.json()["amount_total"] == 10
    created = await client.post("/v1/orders", headers=headers, json={
        "items": items, "contact_email": "two-products-buyer@example.com", "fulfillment_method": "cooperative_pickup",
    })
    assert created.status_code == 201, created.text
    assert created.json()["amount_total"] == 10
    assert created.json()["payment_status"] == "pending"
    order_id = created.json()["id"]
    started = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    assert started.status_code == 201, started.text
    attempt_id = started.json()["id"]
    session.expunge_all()
    checkout = await client.get(f"/payments/{attempt_id}/checkout")
    assert checkout.status_code == 303, checkout.text
    assert checkout.headers["location"].startswith("https://pay.example.test/calc/pay_encrypt/")


@pytest.mark.parametrize("overrides", [
    {"meals_test_product_id": ""}, {"meals_test_product_sku": ""},
    {"meals_test_secondary_product_id": ""}, {"meals_test_secondary_product_sku": ""},
    {"meals_test_secondary_product_id": PRIMARY_ID},
    {"meals_test_secondary_product_sku": PRIMARY_SKU},
    {"meals_test_secondary_product_id": "not-a-uuid"},
    {"meals_test_secondary_product_sku": "INVALID SKU"},
])
def test_product_pairs_reject_partial_duplicate_or_malformed_configuration(overrides):
    with pytest.raises(RuntimeError, match="MEALS_TEST"):
        two_product_settings(**overrides).validate_runtime_secrets()


@pytest.mark.parametrize("overrides", [
    {"environment": "preview"}, {"environment": "test"},
    {"environment": "development"}, {"sales_scope": "all"},
])
def test_secondary_only_configuration_cannot_enable_other_environments_or_scopes(overrides):
    with pytest.raises(RuntimeError, match="MEALS_TEST"):
        two_product_settings(meals_test_product_id="", meals_test_product_sku="", **overrides).validate_runtime_secrets()


def test_secondary_pair_is_optional_and_can_be_configured_independently():
    blank = meal_production_settings()
    assert blank.meals_test_secondary_product_id == ""
    assert blank.meals_test_secondary_product_sku == ""
    blank.validate_runtime_secrets()
    two_product_settings().validate_runtime_secrets()
    two_product_settings(meals_test_product_id="", meals_test_product_sku="").validate_runtime_secrets()


async def create_test_order(client, headers, product_id=SECONDARY_ID, **overrides):
    return await client.post("/v1/orders", headers=headers, json={
        "items": [{"product_id": product_id, "quantity": 1}],
        "contact_email": "two-products-buyer@example.com", "fulfillment_method": "cooperative_pickup", **overrides,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["create", "new_attempt", "checkout"])
@pytest.mark.parametrize("overrides", [
    {"meals_test_product_id": ""}, {"meals_test_product_sku": ""},
    {"meals_test_secondary_product_id": ""}, {"meals_test_secondary_product_sku": ""},
    {"meals_test_secondary_product_id": PRIMARY_ID},
    {"meals_test_secondary_product_sku": PRIMARY_SKU},
    {"meals_test_secondary_product_id": "not-a-uuid"},
    {"meals_test_secondary_product_sku": "INVALID SKU"},
    {"meals_test_product_id": "", "meals_test_product_sku": "",
        "meals_test_secondary_product_id": "", "meals_test_secondary_product_sku": ""},
])
async def test_invalid_runtime_pairs_block_all_transaction_entrypoints(two_product_context, entrypoint, overrides):
    client, session, settings, headers = two_product_context
    order_id = attempt_id = None
    if entrypoint != "create":
        order_id = (await create_test_order(client, headers)).json()["id"]
    if entrypoint == "checkout":
        attempt_id = (await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)).json()["id"]
    for name, value in overrides.items():
        setattr(settings, name, value)
    session.expunge_all()
    if entrypoint == "create":
        response = await create_test_order(client, headers)
    elif entrypoint == "new_attempt":
        response = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    else:
        response = await client.get(f"/payments/{attempt_id}/checkout")
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["create", "new_attempt", "checkout"])
@pytest.mark.parametrize("case", ["cross_pair", "mixed_cart", "qty2", "shipping", "inactive", "member_price", "nonmember_price", "can_ship"])
async def test_secondary_product_preserves_strict_pair_and_single_item_policy(two_product_context, entrypoint, case):
    client, session, settings, headers = two_product_context
    order_id = attempt_id = None
    if entrypoint != "create":
        order_id = (await create_test_order(client, headers)).json()["id"]
    if entrypoint == "checkout":
        attempt_id = (await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)).json()["id"]
    body = {}
    if case == "cross_pair":
        settings.meals_test_product_sku, settings.meals_test_secondary_product_sku = SECONDARY_SKU, PRIMARY_SKU
    elif case in {"inactive", "member_price", "nonmember_price", "can_ship"}:
        product = await session.get(Product, SECONDARY_ID)
        if case == "inactive":
            product.is_active = False
        elif case == "member_price":
            product.member_price = 9
        elif case == "nonmember_price":
            product.nonmember_price = 11
        else:
            product.can_ship = True
    elif entrypoint == "create":
        if case == "mixed_cart":
            body["items"] = [{"product_id": product_id, "quantity": 1} for product_id in (PRIMARY_ID, SECONDARY_ID)]
        elif case == "qty2":
            body["items"] = [{"product_id": SECONDARY_ID, "quantity": 2}]
        else:
            body["fulfillment_method"] = "ecpay_logistics"
    else:
        order = await session.scalar(select(Order).where(Order.id == order_id).options(selectinload(Order.items)))
        if case == "mixed_cart":
            order.items.append(OrderItem(source_product_id=PRIMARY_ID, product_name="主測試品", unit_label="件",
                quantity=1, unit_price=10, subtotal=10, tax_type=TaxType.TAXABLE))
        elif case == "qty2":
            order.items[0].quantity = 2
        else:
            order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    await session.commit()
    session.expunge_all()
    if entrypoint == "create":
        response = await create_test_order(client, headers, **body)
    elif entrypoint == "new_attempt":
        response = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    else:
        response = await client.get(f"/payments/{attempt_id}/checkout")
    assert response.status_code == (422 if case == "inactive" and entrypoint == "create" else 409), response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["new_attempt", "checkout"])
@pytest.mark.parametrize("old_context", [None, {"provider": "fanyu", "user_id": "OLD-ACCOUNT"}])
async def test_secondary_product_never_rebinds_old_invoice_context(two_product_context, entrypoint, old_context):
    client, session, _settings, headers = two_product_context
    order_id = (await create_test_order(client, headers)).json()["id"]
    attempt_id = None
    if entrypoint == "checkout":
        attempt_id = (await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)).json()["id"]
    order = await session.get(Order, order_id)
    order.invoice_provider_context = old_context
    await session.commit()
    session.expunge_all()
    if entrypoint == "new_attempt":
        response = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
    else:
        response = await client.get(f"/payments/{attempt_id}/checkout")
    assert response.status_code == 409, response.text
    assert "發票平台資料已過期或未設定" in response.json()["detail"]
    detail = await client.get(f"/v1/orders/{order_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["payment_status"] == "pending"


@pytest.mark.asyncio
async def test_secondary_checkout_rejects_changed_attempt_amount(two_product_context):
    client, session, _settings, headers = two_product_context
    order_id = (await create_test_order(client, headers)).json()["id"]
    attempt_id = (await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)).json()["id"]
    attempt = await session.get(PaymentAttempt, attempt_id)
    attempt.amount = 11
    await session.commit()
    session.expunge_all()
    response = await client.get(f"/payments/{attempt_id}/checkout")
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_secondary_only_pair_can_create_an_order(two_product_context):
    client, _session, settings, headers = two_product_context
    settings.meals_test_product_id = settings.meals_test_product_sku = ""
    created = await create_test_order(client, headers)
    assert created.status_code == 201, created.text
