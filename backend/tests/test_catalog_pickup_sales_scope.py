from datetime import timedelta

import pytest

from app.auth import create_token
from app.config import get_settings
from app.models import FulfillmentMethod, Product, SalesChannel, SystemSetting, User, UserRole
from app.platform_settings import PLATFORM_KEY
from app.routers.platform import platform_router
from app.sales_scope import SalesScopeError, ensure_sales_scope_allows
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context
from tests.test_secondary_test_product_payments import two_product_settings
from tests.test_group_campaign_shipping import campaign_context, campaign_payload
from tests.test_meals_production_configuration import meal_production_settings


@pytest.mark.parametrize("channel", [SalesChannel.REGULAR, SalesChannel.GROUP])
def test_pickup_scope_accepts_catalog_channels(channel):
    ensure_sales_scope_allows(
        meal_production_settings(sales_scope="pickup"), sales_channel=channel,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
    )


@pytest.mark.parametrize("context", [
    {},
    {"sales_channel": SalesChannel.REGULAR, "fulfillment_method": FulfillmentMethod.ECPAY_LOGISTICS},
    {"sales_channel": SalesChannel.GROUP, "fulfillment_method": FulfillmentMethod.ECPAY_LOGISTICS},
    {"sales_channel": SalesChannel.REGULAR, "fulfillment_method": FulfillmentMethod.COOPERATIVE_PICKUP, "meal_event_id": "fake-meal"},
    {"sales_channel": SalesChannel.MEAL_PREORDER, "fulfillment_method": FulfillmentMethod.EVENT_PICKUP, "meal_event_id": "meal-event-preorder-demo"},
])
def test_pickup_scope_rejects_disabled_or_malformed_channels(context):
    with pytest.raises(SalesScopeError):
        ensure_sales_scope_allows(meal_production_settings(sales_scope="pickup"), **context)


def test_pickup_scope_keeps_real_meal_events_open():
    ensure_sales_scope_allows(
        meal_production_settings(sales_scope="pickup"), sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP, meal_event_id="real-event",
    )


@pytest.mark.parametrize("field,value", [
    ("raygate_payment_stage", True), ("raygate_payment_contract_verified", False),
    ("fanyu_invoice_stage", True), ("fanyu_invoice_signature_verified", False),
    ("reconciliation_enabled", False), ("pii_encryption_keys_json", ""),
])
def test_pickup_production_retains_payment_invoice_and_privacy_requirements(field, value):
    with pytest.raises(RuntimeError):
        meal_production_settings(sales_scope="pickup", **{field: value}).validate_runtime_secrets()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,catalog,shipping", [("all", True, True), ("meals_only", False, False), ("pickup", True, False)])
async def test_public_and_admin_platform_report_the_actual_sales_scope(database_session, scope, catalog, shipping):
    settings = meal_production_settings(sales_scope=scope)
    admin = User(email="scope-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    database_session.add(admin)
    await database_session.commit()
    headers = {"Authorization": f"Bearer {create_token(admin, 'access', timedelta(minutes=5), settings=settings)}"}
    async with api_test_context(database_session, [platform_router], settings=settings) as client:
        for path in ("/v1/platform", "/v1/admin/platform"):
            response = await client.get(path, headers=headers)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["sales_scope"] == scope
            assert body["regular_orders_enabled"] is catalog
            assert body["group_orders_enabled"] is catalog
            assert body["shipping_enabled"] is shipping


@pytest.mark.asyncio
async def test_regular_catalog_product_can_complete_pickup_checkout(database_session):
    session = database_session
    product = Product(
        sku="CATALOG-RICE", slug="catalog-rice", name="友善白米",
        category="米・雜糧", unit="包", member_price=100, nonmember_price=120,
        stock_quantity=10, can_ship=True, is_active=True,
    )
    buyer = User(email="catalog@example.com", display_name="一般買家", password_hash="test")
    session.add_all([product, buyer])
    await session.commit()
    settings = two_product_settings(sales_scope="pickup")
    settings.validate_runtime_secrets()
    headers = {"Authorization": f"Bearer {create_token(buyer, 'access', timedelta(minutes=5), settings=settings)}"}
    async with api_test_context(session, [orders_router, payments_router], settings=settings) as client:
        items = [{"product_id": product.id, "quantity": 2}]
        quote = await client.post("/v1/orders/quote", headers=headers, json={"items": items})
        assert quote.status_code == 200, quote.text
        assert quote.json()["amount_total"] == 240
        created = await client.post("/v1/orders", headers=headers, json={
            "items": items, "contact_email": buyer.email,
            "fulfillment_method": "cooperative_pickup",
        })
        assert created.status_code == 201, created.text
        assert created.json()["amount_total"] == 240
        order_id = created.json()["id"]
        started = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=headers)
        assert started.status_code == 201, started.text
        attempt_id = started.json()["id"]
        session.expunge_all()
        checkout = await client.get(f"/payments/{attempt_id}/checkout")
        assert checkout.status_code == 303, checkout.text
        assert checkout.headers["location"].startswith("https://pay.example.test/calc/pay_encrypt/")


@pytest.mark.asyncio
async def test_group_catalog_product_completes_pickup_checkout(campaign_context):
    from tests.support import auth_headers

    client = campaign_context["client"]
    campaign = await client.post("/v1/group-campaigns", json=campaign_payload(campaign_context["product"].id), headers=auth_headers(campaign_context["admin"]))
    assert campaign.status_code == 201, campaign.text
    settings = meal_production_settings(sales_scope="pickup")
    client._transport.app.dependency_overrides[get_settings] = lambda: settings
    client._transport.app.include_router(payments_router)
    buyer = campaign_context["proposer"]
    headers = {"Authorization": f"Bearer {create_token(buyer, 'access', timedelta(minutes=5), settings=settings)}"}
    created = await client.post(f"/v1/group-campaigns/{campaign.json()['id']}/join", headers=headers, json={
        "quantity": 2, "contact_email": "catalog@example.com", "fulfillment_method": "cooperative_pickup",
    })
    assert created.status_code == 201, created.text
    started = await client.post(f"/v1/orders/{created.json()['id']}/payment-attempts", headers=headers)
    assert started.status_code == 201, started.text
    attempt_id = started.json()["id"]
    campaign_context["session"].expunge_all()
    checkout = await client.get(f"/payments/{attempt_id}/checkout")
    assert checkout.status_code == 303, checkout.text


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker,status", [("closed", 409), ("shipping", 409), ("inactive", 422), ("stock", 409)])
async def test_catalog_opening_preserves_order_and_inventory_guards(database_session, blocker, status):
    session = database_session
    product = Product(slug="guarded-rice", name="白米", category="米・雜糧", unit="包", member_price=100,
                      nonmember_price=120, stock_quantity=0 if blocker == "stock" else 5, is_active=blocker != "inactive")
    buyer = User(email="guarded@example.com", display_name="買家", password_hash="test")
    session.add_all([product, buyer])
    if blocker == "closed":
        session.add(SystemSetting(key=PLATFORM_KEY, value={"orders_open": False}))
    await session.commit()
    settings = meal_production_settings(sales_scope="pickup")
    headers = {"Authorization": f"Bearer {create_token(buyer, 'access', timedelta(minutes=5), settings=settings)}"}
    async with api_test_context(session, [orders_router], settings=settings) as client:
        created = await client.post("/v1/orders", headers=headers, json={
            "items": [{"product_id": product.id, "quantity": 1}], "contact_email": buyer.email,
            "fulfillment_method": "ecpay_logistics" if blocker == "shipping" else "cooperative_pickup",
        })
        assert created.status_code == status, created.text
