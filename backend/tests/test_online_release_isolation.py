from __future__ import annotations

from pathlib import Path

from alembic.script import ScriptDirectory
from fastapi import FastAPI
import pytest

from app.models import MealEvent, Order
from app.routers import ALL_ROUTERS
from tests.test_meals_production_configuration import meal_production_settings


TEST_PRODUCT_ID = "dc95cdd2-b1a0-4f5b-93f9-294dd78ba1a5"
TEST_PRODUCT_SKU = "ONLINE-TEST-10"


def test_online_product_exception_is_disabled_by_default():
    settings = meal_production_settings()
    assert settings.meals_test_product_id == ""
    assert settings.meals_test_product_sku == ""
    settings.validate_runtime_secrets()


def test_online_product_exception_accepts_an_explicit_production_meals_only_pair():
    settings = meal_production_settings(
        meals_test_product_id=TEST_PRODUCT_ID,
        meals_test_product_sku=TEST_PRODUCT_SKU,
    )
    settings.validate_runtime_secrets()
    assert settings.meals_test_product_id == TEST_PRODUCT_ID
    assert settings.meals_test_product_sku == TEST_PRODUCT_SKU


@pytest.mark.parametrize("configured_field", ["meals_test_product_id", "meals_test_product_sku"])
def test_online_product_exception_rejects_a_partial_pair(configured_field):
    value = TEST_PRODUCT_ID if configured_field == "meals_test_product_id" else TEST_PRODUCT_SKU
    with pytest.raises(RuntimeError, match="MEALS_TEST_PRODUCT"):
        meal_production_settings(**{configured_field: value}).validate_runtime_secrets()


@pytest.mark.parametrize("overrides", [
    {"environment": "preview"},
    {"environment": "test"},
    {"environment": "development"},
    {"sales_scope": "all"},
])
def test_online_product_exception_rejects_other_environments_or_sales_scopes(overrides):
    with pytest.raises(RuntimeError, match="MEALS_TEST_PRODUCT"):
        meal_production_settings(
            meals_test_product_id=TEST_PRODUCT_ID,
            meals_test_product_sku=TEST_PRODUCT_SKU,
            **overrides,
        ).validate_runtime_secrets()


@pytest.mark.parametrize("product_id", ["not-a-uuid", "dc95cdd2-b1a0-4f5b-93f9-294dd78ba1aZ"])
def test_online_product_exception_rejects_invalid_product_identifiers(product_id):
    with pytest.raises(RuntimeError, match="MEALS_TEST_PRODUCT_ID"):
        meal_production_settings(
            meals_test_product_id=product_id,
            meals_test_product_sku=TEST_PRODUCT_SKU,
        ).validate_runtime_secrets()


@pytest.mark.parametrize("sku", ["invalid sku", "測試商品", "-TEST", "/TEST", "TEST@10"])
def test_online_product_exception_rejects_invalid_skus(sku):
    with pytest.raises(RuntimeError, match="MEALS_TEST_PRODUCT_SKU"):
        meal_production_settings(
            meals_test_product_id=TEST_PRODUCT_ID,
            meals_test_product_sku=sku,
        ).validate_runtime_secrets()


@pytest.mark.parametrize("sku", ["A", "test.product_10-OK", "T" * 80])
def test_online_product_exception_accepts_the_existing_acceptance_sku_format(sku):
    meal_production_settings(
        meals_test_product_id=TEST_PRODUCT_ID,
        meals_test_product_sku=sku,
    ).validate_runtime_secrets()


def test_online_product_exception_rejects_skus_longer_than_eighty_characters():
    with pytest.raises(ValueError, match="meals_test_product_sku"):
        meal_production_settings(
            meals_test_product_id=TEST_PRODUCT_ID,
            meals_test_product_sku="T" * 81,
        )


def test_online_release_keeps_invoice_binding_as_the_only_migration_head():
    backend = Path(__file__).resolve().parents[1]
    migrations = ScriptDirectory(str(backend / "alembic"))
    assert migrations.get_heads() == ["0017_meal_order_pickup_overlap"]


def test_online_release_models_do_not_add_cash_payment_fields():
    assert "payment_method" not in Order.__table__.columns
    assert "allow_cash_payment" not in MealEvent.__table__.columns


def test_online_release_public_api_excludes_cash_routes_and_schemas():
    application = FastAPI()
    for router in ALL_ROUTERS:
        application.include_router(router)
    schema = application.openapi()
    paths = schema["paths"]
    assert "/v1/meal-events" in paths
    assert "/v1/orders/{order_id}/payment-attempts" in paths
    assert not [path for path in paths if "cash" in path.lower() or "payment-options" in path]
    models = schema["components"]["schemas"]
    assert "MealOrderRead" in models
    assert "PaymentMethod" not in models
    assert not [name for name in models if "cash" in name.lower()]
    for name in ("MealOrderCreate", "MealOrderRead", "MealEventCreate", "MealEventRead", "OrderRead"):
        properties = models[name].get("properties", {})
        assert "payment_method" not in properties, name
        assert "allow_cash_payment" not in properties, name
