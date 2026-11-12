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


def test_core_release_keeps_one_merged_migration_head():
    backend = Path(__file__).resolve().parents[1]
    migrations = ScriptDirectory(str(backend / "alembic"))
    assert migrations.get_heads() == ["0064_meal_operations"]
    assert migrations.get_revision("0064_meal_operations").down_revision == "0063_meal_ordering_pause"
    assert migrations.get_revision("0063_meal_ordering_pause").down_revision == "0062_meal_business_only"
    assert migrations.get_revision("0062_meal_business_only").down_revision == "0061_meal_same_day"
    assert migrations.get_revision("0061_meal_same_day").down_revision == "0060_procurement_snapshot"
    assert migrations.get_revision("0060_procurement_snapshot").down_revision == "0059_recovery_quarantine"
    assert migrations.get_revision("0059_recovery_quarantine").down_revision == "0058_admission_fee_snapshot"
    assert len(list(migrations.walk_revisions())) == 69
    assert migrations.get_revision("0058_admission_fee_snapshot").down_revision == "0057_browser_refresh_recovery"
    assert migrations.get_revision("0056_payment_return_storefront").down_revision == "0055_counter_member_pricing"
    assert migrations.get_revision("0057_browser_refresh_recovery").down_revision == "0056_payment_return_storefront"
    assert migrations.get_revision("0055_counter_member_pricing").down_revision == "0054_catalog_visibility"
    assert migrations.get_revision("0054_catalog_visibility").down_revision == "0053_email_registration_consent"
    assert migrations.get_revision("0053_email_registration_consent").down_revision == "0052_business_meal_orders"
    assert migrations.get_revision("0052_business_meal_orders").down_revision == "0051_walk_in_sale_category"
    assert migrations.get_revision("0051_walk_in_sale_category").down_revision == "0050_meal_workday_policy"
    assert migrations.get_revision("0050_meal_workday_policy").down_revision == "0049_merge_commerce_remediation"
    assert migrations.get_revision("0049_merge_commerce_remediation").down_revision == (
        "0048_merge_remediation_print", "0045_walk_in_sales"
    )
    assert migrations.get_revision("0045_walk_in_sales").down_revision == "0044_invoice_timing"
    assert migrations.get_revision("0044_invoice_timing").down_revision == "0043_daily_order_numbers"
    assert migrations.get_revision("0043_daily_order_numbers").down_revision == "0042_order_reference_printing"
    assert migrations.get_revision("0048_merge_remediation_print").down_revision == (
        "0047_credential_rate_limits", "0042_order_reference_printing"
    )
    assert migrations.get_revision("0047_credential_rate_limits").down_revision == "0046_reconciliation_retention"
    assert migrations.get_revision("0046_reconciliation_retention").down_revision == "0045_reconciliation_runs"
    assert migrations.get_revision("0045_reconciliation_runs").down_revision == "0044_group_order_contract"
    assert migrations.get_revision("0044_group_order_contract").down_revision == "0043_notification_dedupe"
    assert migrations.get_revision("0043_notification_dedupe").down_revision == "0042_open_proposal_target"
    assert migrations.get_revision("0042_open_proposal_target").down_revision == "0041_proposal_submission"
    assert migrations.get_revision("0041_proposal_submission").down_revision == "0040_membership_admission"
    assert migrations.get_revision("0042_order_reference_printing").down_revision == "0041_order_summary_printing"
    assert migrations.get_revision("0041_order_summary_printing").down_revision == "0040_membership_admission"
    assert migrations.get_revision("0040_membership_admission").down_revision == "0039_product_daily_sales"
    assert migrations.get_revision("0039_product_daily_sales").down_revision == "0038_activity_payments"
    assert migrations.get_revision("0038_activity_payments").down_revision == "0037_group_review_window"
    assert migrations.get_revision("0037_group_review_window").down_revision == "0036_meeting_categories"
    assert migrations.get_revision("0036_meeting_categories").down_revision == "0035_order_amendments"
    assert migrations.get_revision("0035_order_amendments").down_revision == "0034_meal_pickup_utensils"
    assert migrations.get_revision("0034_meal_pickup_utensils").down_revision == "0033_saved_mobile_barcode"
    assert migrations.get_revision("0033_saved_mobile_barcode").down_revision == "0032_product_member_photos"
    assert migrations.get_revision("0032_product_member_photos").down_revision == "0031_meal_photos_custom_requests"
    assert migrations.get_revision("0031_meal_photos_custom_requests").down_revision == "0030_feie_print_jobs"
    assert migrations.get_revision("0030_feie_print_jobs").down_revision == "0029_onsite_payments"
    assert migrations.get_revision("0024_line_data_consent").down_revision == "0023_meal_option_images"
    assert migrations.get_revision("0022_line_customer_email").down_revision == "0021_line_login"
    assert migrations.get_revision("0021_line_login").down_revision == "0020_meal_booking_deadline"


def test_onsite_release_defaults_keep_existing_orders_online_and_events_opt_in():
    assert Order.__table__.columns.payment_method.server_default.arg == "online"
    assert MealEvent.__table__.columns.allow_cash_payment.default.arg is False


def test_onsite_release_exposes_admin_collection_and_preserves_online_payment_api():
    application = FastAPI()
    for router in ALL_ROUTERS:
        application.include_router(router)
    schema = application.openapi()
    paths = schema["paths"]
    assert "/v1/meal-events" in paths
    assert "/v1/orders/{order_id}/payment-attempts" in paths
    for operation in ("cash-payment", "cash-unpaid", "cash-refund"):
        assert f"/v1/orders/{{order_id}}/admin/{operation}" in paths
    assert "/v1/admin/meal-events/{event_id}/payment-options" in paths
    models = schema["components"]["schemas"]
    for name in ("MealOrderCreate", "MealOrderRead", "OrderRead"):
        assert "payment_method" in models[name]["properties"]
    for name in ("MealEventCreate", "MealEventRead"):
        assert models[name]["properties"]["allow_cash_payment"]["default"] is False
