from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main
from app.config import (
    DEFAULT_JWT_SECRET,
    DEFAULT_RECONCILE_SECRET,
    Settings,
    get_settings,
)
from app.database import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]
INITIAL_MIGRATION = (
    BACKEND_ROOT / "alembic" / "versions" / "0001_initial.py"
)
V2_MIGRATION = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "0002_v2_social_commerce.py"
)
COOPERATIVE_MIGRATION = (
    BACKEND_ROOT / "alembic" / "versions" / "0004_cooperative_core.py"
)


def settings(**overrides) -> Settings:
    values = {"_env_file": None}
    values.update(overrides)
    return Settings(**values)


def preview_raygate_settings(**overrides) -> Settings:
    values = {
        "environment": "preview",
        "app_base_url": "https://api.example.test",
        "web_base_url": "https://app.example.test",
        "jwt_secret": "j" * 32,
        "internal_reconcile_secret": "r" * 32,
        "demo_admin_password": "preview-admin-strong",
        "demo_member_password": "preview-member-strong",
        "demo_nonmember_password": "preview-customer-strong",
        "resend_api_key": "re_test-secret",
        "email_from_email": "noreply@example.com",
        "payment_provider": "raygate",
        "raygate_payment_store_identifier": "acceptance-store",
        "raygate_payment_key_hex": "11" * 32,
        "raygate_payment_iv_hex": "22" * 16,
        "raygate_payment_merchant_id": "merchant",
        "raygate_payment_terminal_id": "terminal",
        "raygate_payment_base_url": "https://pay.example.test",
        "raygate_payment_allowed_hostname": "pay.example.test",
        "raygate_payment_stage": False,
        **overrides,
    }
    return settings(**values)


@pytest.mark.parametrize("environment", ["preview", "sandbox", "production"])
def test_secure_environments_reject_default_secrets(environment: str) -> None:
    runtime_settings = settings(environment=environment)

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    message = str(error.value)
    assert "JWT_SECRET" in message
    assert "RECONCILE_SECRET" in message
    assert DEFAULT_JWT_SECRET not in message
    assert DEFAULT_RECONCILE_SECRET not in message


@pytest.mark.parametrize("environment", ["preview", "sandbox", "production"])
def test_secure_environments_reject_short_custom_secrets(
    environment: str,
) -> None:
    runtime_settings = settings(
        environment=environment,
        jwt_secret="custom-but-short",
        internal_reconcile_secret="another-short-secret",
    )

    with pytest.raises(RuntimeError):
        runtime_settings.validate_runtime_secrets()


@pytest.mark.parametrize("environment", ["development", "test"])
def test_local_environments_allow_convenient_default_secrets(
    environment: str,
) -> None:
    settings(environment=environment).validate_runtime_secrets()


@pytest.mark.parametrize("environment", ["prod", "staging", "productionn", ""])
def test_unknown_environments_are_rejected(environment: str) -> None:
    with pytest.raises(ValueError, match="APP_ENV"):
        settings(environment=environment)


def test_secure_environments_accept_long_nondefault_secrets() -> None:
    settings(
        environment="sandbox",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_reset_confirmation="reset-code-strong",
        demo_admin_password="preview-admin-strong",
        demo_member_password="preview-member-strong",
        demo_nonmember_password="preview-customer-strong",
        ecpay_payment_merchant_id="3002607",
        ecpay_payment_hash_key="pwFHCqoQZGmho4w6",
        ecpay_payment_hash_iv="EkRm7iFT261dpevs",
        ecpay_invoice_merchant_id="2000132",
        ecpay_invoice_hash_key="ejCk326UnaZWKisg",
        ecpay_invoice_hash_iv="q9jcZX8Ib9LM8wYk",
        ecpay_logistics_merchant_id="2000132",
        ecpay_logistics_hash_key="5294y06JbISpM5x9",
        ecpay_logistics_hash_iv="v77hoKGq4kWxNNIS",
        cloudflare_r2_account_id="account-id",
        cloudflare_r2_access_key_id="access-key",
        cloudflare_r2_secret_access_key="secret-key",
        cloudflare_r2_bucket="private-documents",
        resend_api_key="re_test-secret",
        email_from_email="verified@example.test",
        pii_encryption_keys_json=json.dumps(
            {
                "v1": base64.b64encode(b"p" * 32).decode("ascii"),
            }
        ),
    ).validate_runtime_secrets()


def test_preview_accepts_resend_without_commerce_or_storage_integrations() -> None:
    settings(
        environment="preview",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_admin_password="preview-admin-strong",
        demo_member_password="preview-member-strong",
        demo_nonmember_password="preview-customer-strong",
        resend_api_key="re_test-secret",
        email_from_email="noreply@example.com",
    ).validate_runtime_secrets()


def test_preview_accepts_a_scoped_production_raygate_order() -> None:
    preview_raygate_settings(
        raygate_payment_acceptance_order_id=(
            "00000000-0000-4000-8000-000000000001"
        ),
    ).validate_runtime_secrets()


def test_preview_accepts_a_scoped_production_raygate_product_sku() -> None:
    preview_raygate_settings(
        raygate_payment_acceptance_sku="REMOTE-PAYMENT-10",
    ).validate_runtime_secrets()


def test_preview_rejects_multiple_raygate_acceptance_selectors() -> None:
    runtime_settings = preview_raygate_settings(
        raygate_payment_acceptance_order_id=(
            "00000000-0000-4000-8000-000000000001"
        ),
        raygate_payment_acceptance_sku="REMOTE-PAYMENT-10",
    )

    with pytest.raises(RuntimeError, match="只能擇一"):
        runtime_settings.validate_runtime_secrets()


def test_preview_rejects_an_invalid_raygate_acceptance_sku() -> None:
    runtime_settings = preview_raygate_settings(
        raygate_payment_acceptance_sku="REMOTE PAYMENT 10",
    )

    with pytest.raises(RuntimeError, match="SKU（格式不正確）"):
        runtime_settings.validate_runtime_secrets()


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"payment_provider": "ecpay"}, "PAYMENT_PROVIDER"),
        ({"raygate_payment_stage": True}, "RAYGATE_PAYMENT_STAGE"),
        (
            {"raygate_payment_acceptance_order_id": "not-an-order-id"},
            "訂單 UUID",
        ),
    ],
)
def test_preview_rejects_an_unsafe_raygate_acceptance_configuration(
    overrides: dict[str, object],
    expected: str,
) -> None:
    values = {
        "environment": "preview",
        "app_base_url": "https://api.example.test",
        "web_base_url": "https://app.example.test",
        "jwt_secret": "j" * 32,
        "internal_reconcile_secret": "r" * 32,
        "demo_admin_password": "preview-admin-strong",
        "demo_member_password": "preview-member-strong",
        "demo_nonmember_password": "preview-customer-strong",
        "resend_api_key": "re_test-secret",
        "email_from_email": "noreply@example.com",
        "payment_provider": "raygate",
        "raygate_payment_store_identifier": "acceptance-store",
        "raygate_payment_key_hex": "11" * 32,
        "raygate_payment_iv_hex": "22" * 16,
        "raygate_payment_merchant_id": "merchant",
        "raygate_payment_terminal_id": "terminal",
        "raygate_payment_base_url": "https://pay.example.test",
        "raygate_payment_allowed_hostname": "pay.example.test",
        "raygate_payment_stage": False,
        "raygate_payment_acceptance_order_id": (
            "00000000-0000-4000-8000-000000000001"
        ),
        **overrides,
    }

    with pytest.raises(RuntimeError, match=expected):
        settings(**values).validate_runtime_secrets()


def test_sandbox_requires_public_https_callback_urls() -> None:
    runtime_settings = settings(
        environment="sandbox",
        app_base_url="http://localhost:8000",
        web_base_url="http://localhost:8081",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_reset_confirmation="reset-code-strong",
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    message = str(error.value)
    assert "APP_BASE_URL" in message
    assert "WEB_BASE_URL" in message


@pytest.mark.parametrize("environment", ["preview", "sandbox", "production"])
@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://2130706433",
        "https://0177.0.0.1",
        "https://user:password@app.example.test",
    ],
)
def test_remote_environments_reject_nonpublic_or_credentialed_origins(
    environment: str,
    invalid_url: str,
) -> None:
    runtime_settings = settings(
        environment=environment,
        app_base_url=invalid_url,
        web_base_url=invalid_url,
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_admin_password="preview-admin-strong",
        demo_member_password="preview-member-strong",
        demo_nonmember_password="preview-customer-strong",
        demo_reset_confirmation="reset-code-strong",
        resend_api_key="re_test-secret",
        email_from_email="noreply@example.com",
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    message = str(error.value)
    assert "APP_BASE_URL" in message
    assert "WEB_BASE_URL" in message


@pytest.mark.parametrize(
    "secret_name, overrides",
    [
        (
            "JWT_SECRET",
            {"jwt_secret": "replace-with-a-long-random-string"},
        ),
        (
            "RECONCILE_SECRET",
            {
                "internal_reconcile_secret": (
                    "replace-with-a-long-random-string"
                )
            },
        ),
    ],
)
def test_remote_environments_reject_documented_secret_placeholders(
    secret_name: str,
    overrides: dict[str, str],
) -> None:
    values = {
        "environment": "preview",
        "app_base_url": "https://api.example.test",
        "web_base_url": "https://app.example.test",
        "jwt_secret": "j" * 32,
        "internal_reconcile_secret": "r" * 32,
        "demo_admin_password": "preview-admin-strong",
        "demo_member_password": "preview-member-strong",
        "demo_nonmember_password": "preview-customer-strong",
        "resend_api_key": "re_test-secret",
        "email_from_email": "noreply@example.com",
    }
    values.update(overrides)
    runtime_settings = settings(**values)

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert secret_name in str(error.value)


def test_remote_environments_require_distinct_runtime_secrets() -> None:
    shared_secret = "shared-runtime-secret-value-123456"
    runtime_settings = settings(
        environment="preview",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret=shared_secret,
        internal_reconcile_secret=shared_secret,
        demo_admin_password="preview-admin-strong",
        demo_member_password="preview-member-strong",
        demo_nonmember_password="preview-customer-strong",
        resend_api_key="re_test-secret",
        email_from_email="noreply@example.com",
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "JWT_SECRET/RECONCILE_SECRET" in str(error.value)


def test_demo_environments_require_distinct_role_passwords() -> None:
    shared_password = "shared-demo-password"
    runtime_settings = settings(
        environment="preview",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_admin_password=shared_password,
        demo_member_password=shared_password,
        demo_nonmember_password=shared_password,
        resend_api_key="re_test-secret",
        email_from_email="noreply@example.com",
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "必須使用三組不同的密碼" in str(error.value)


def test_sandbox_reset_confirmation_must_differ_from_login_passwords() -> None:
    runtime_settings = settings(
        environment="sandbox",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_admin_password="sandbox-admin-strong",
        demo_member_password="sandbox-member-strong",
        demo_nonmember_password="sandbox-customer-strong",
        demo_reset_confirmation="sandbox-admin-strong",
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "DEMO_RESET_CONFIRMATION" in str(error.value)


@pytest.mark.parametrize(
    "flagged_field",
    ["store_identifier", "key_hex", "iv_hex", "merchant_id", "terminal_id"],
)
def test_production_rejects_each_fingerprinted_raygate_example_credential(
    monkeypatch: pytest.MonkeyPatch,
    flagged_field: str,
) -> None:
    import app.integrations.raygate as raygate_module

    credential_values = {
        "store_identifier": "store-1",
        "key_hex": "a" * 64,
        "iv_hex": "b" * 32,
        "merchant_id": "merchant-1",
        "terminal_id": "term-1",
    }
    monkeypatch.setattr(
        raygate_module,
        "RAYGATE_DOCUMENT_EXAMPLE_CREDENTIAL_FINGERPRINTS",
        frozenset(
            {
                hashlib.sha256(
                    credential_values[flagged_field].encode("utf-8")
                ).hexdigest()
            }
        ),
    )
    runtime_settings = valid_production_settings(
        payment_provider="raygate",
        raygate_payment_store_identifier=credential_values["store_identifier"],
        raygate_payment_key_hex=credential_values["key_hex"],
        raygate_payment_iv_hex=credential_values["iv_hex"],
        raygate_payment_merchant_id=credential_values["merchant_id"],
        raygate_payment_terminal_id=credential_values["terminal_id"],
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
        raygate_payment_stage=False,
        raygate_payment_contract_verified=True,
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "規格書範例憑證" in str(error.value)


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_error"),
    [
        ("raygate_payment_key_hex", f"{'a' * 2} {'a' * 62}", "KEY_HEX"),
        ("raygate_payment_iv_hex", f"{'b' * 2}\t{'b' * 30}", "IV_HEX"),
    ],
)
def test_production_rejects_whitespace_in_raygate_hex_credentials(
    field_name: str,
    field_value: str,
    expected_error: str,
) -> None:
    raygate_overrides = {
        "payment_provider": "raygate",
        "raygate_payment_store_identifier": "store-1",
        "raygate_payment_key_hex": "a" * 64,
        "raygate_payment_iv_hex": "b" * 32,
        "raygate_payment_merchant_id": "merchant-1",
        "raygate_payment_terminal_id": "term-1",
        "raygate_payment_base_url": "https://pay.example.test",
        "raygate_payment_allowed_hostname": "pay.example.test",
        "raygate_payment_stage": False,
        "raygate_payment_contract_verified": True,
    }
    raygate_overrides[field_name] = field_value
    runtime_settings = valid_production_settings(**raygate_overrides)

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert expected_error in str(error.value)


def test_production_raygate_rejects_overlong_derived_return_url() -> None:
    runtime_settings = valid_production_settings(
        app_base_url=f"https://{'a' * 28}.example.test",
        payment_provider="raygate",
        raygate_payment_store_identifier="store-1",
        raygate_payment_key_hex="a" * 64,
        raygate_payment_iv_hex="b" * 32,
        raygate_payment_merchant_id="merchant-1",
        raygate_payment_terminal_id="term-1",
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
        raygate_payment_stage=False,
        raygate_payment_contract_verified=True,
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "callback／return URL" in str(error.value)


def test_production_raygate_accepts_maximum_length_app_base_url() -> None:
    runtime_settings = valid_production_settings(
        app_base_url=f"https://{'a' * 27}.example.test",
        payment_provider="raygate",
        raygate_payment_store_identifier="store-1",
        raygate_payment_key_hex="a" * 64,
        raygate_payment_iv_hex="b" * 32,
        raygate_payment_merchant_id="merchant-1",
        raygate_payment_terminal_id="term-1",
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
        raygate_payment_stage=False,
        raygate_payment_contract_verified=True,
    )

    runtime_settings.validate_runtime_secrets()


def test_sandbox_requires_payment_and_invoice_credentials() -> None:
    runtime_settings = settings(
        environment="sandbox",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_reset_confirmation="reset-code-strong",
        ecpay_logistics_merchant_id="2000132",
        ecpay_logistics_hash_key="5294y06JbISpM5x9",
        ecpay_logistics_hash_iv="v77hoKGq4kWxNNIS",
        cloudflare_r2_account_id="account-id",
        cloudflare_r2_access_key_id="access-key",
        cloudflare_r2_secret_access_key="secret-key",
        cloudflare_r2_bucket="private-documents",
        pii_encryption_keys_json=json.dumps(
            {
                "v1": base64.b64encode(b"p" * 32).decode("ascii"),
            }
        ),
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    message = str(error.value)
    for name in (
        "ECPAY_PAYMENT_MERCHANT_ID",
        "ECPAY_PAYMENT_HASH_KEY",
        "ECPAY_PAYMENT_HASH_IV",
        "ECPAY_INVOICE_MERCHANT_ID",
        "ECPAY_INVOICE_HASH_KEY",
        "ECPAY_INVOICE_HASH_IV",
    ):
        assert name in message


@pytest.mark.parametrize("environment", ["preview", "sandbox", "production"])
def test_remote_environments_require_an_email_provider_and_sender(
    environment: str,
) -> None:
    runtime_settings = settings(
        environment=environment,
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_reset_confirmation="reset-code-strong",
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    message = str(error.value)
    assert "RESEND_API_KEY" in message
    assert "MAILERSEND_API_TOKEN" in message
    assert "EMAIL_FROM_EMAIL" in message
    assert "MAILERSEND_FROM_EMAIL" in message


def test_production_rejects_email_tokens_when_no_provider_is_usable() -> None:
    runtime_settings = settings(
        environment="production",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        resend_api_key="re_test-secret",
        email_from_email="broken@sender",
    )

    with pytest.raises(RuntimeError, match="至少一組設定必須完整有效"):
        runtime_settings.validate_runtime_secrets()


def valid_production_settings(**overrides) -> Settings:
    values = dict(
        environment="production",
        database_url="postgresql://db.example.test/cooperative",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        ecpay_payment_merchant_id="3002607",
        ecpay_payment_hash_key="pwFHCqoQZGmho4w6",
        ecpay_payment_hash_iv="EkRm7iFT261dpevs",
        ecpay_payment_stage=False,
        ecpay_payment_aio_url=(
            "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"
        ),
        ecpay_payment_query_url=(
            "https://payment.ecpay.com.tw/Cashier/QueryTradeInfo/V5"
        ),
        ecpay_invoice_merchant_id="2000132",
        ecpay_invoice_hash_key="ejCk326UnaZWKisg",
        ecpay_invoice_hash_iv="q9jcZX8Ib9LM8wYk",
        ecpay_invoice_stage=False,
        ecpay_invoice_issue_url=(
            "https://einvoice.ecpay.com.tw/B2CInvoice/Issue"
        ),
        ecpay_invoice_query_url=(
            "https://einvoice.ecpay.com.tw/B2CInvoice/GetIssue"
        ),
        ecpay_invoice_barcode_url=(
            "https://einvoice.ecpay.com.tw/B2CInvoice/CheckBarcode"
        ),
        ecpay_logistics_merchant_id="2000132",
        ecpay_logistics_hash_key="5294y06JbISpM5x9",
        ecpay_logistics_hash_iv="v77hoKGq4kWxNNIS",
        ecpay_logistics_stage=False,
        ecpay_logistics_selection_url=(
            "https://logistics.ecpay.com.tw/Express/v2/"
            "RedirectToLogisticsSelection"
        ),
        ecpay_logistics_update_temp_url=(
            "https://logistics.ecpay.com.tw/Express/v2/UpdateTempTrade"
        ),
        ecpay_logistics_create_url=(
            "https://logistics.ecpay.com.tw/Express/v2/CreateByTempTrade"
        ),
        ecpay_logistics_query_url=(
            "https://logistics.ecpay.com.tw/Express/v2/"
            "QueryLogisticsTradeInfo"
        ),
        ecpay_logistics_print_url=(
            "https://logistics.ecpay.com.tw/Express/v2/PrintTradeDocument"
        ),
        ecpay_logistics_sender_address="臺北市中正區正式路一號",
        cloudflare_r2_account_id="account-id",
        cloudflare_r2_access_key_id="access-key",
        cloudflare_r2_secret_access_key="secret-key",
        cloudflare_r2_bucket="private-documents",
        pii_encryption_keys_json=json.dumps(
            {
                "v1": base64.b64encode(b"p" * 32).decode("ascii"),
            }
        ),
        resend_api_key="re_test-secret",
        email_from_email="noreply@example.com",
        mailersend_api_token="mlsn.test-secret",
        mailersend_from_email="noreply@example.com",
    )
    values.update(overrides)
    return settings(**values)


def test_production_accepts_usable_resend_with_optional_fallback() -> None:
    runtime_settings = valid_production_settings()

    runtime_settings.validate_runtime_secrets()


def test_production_does_not_allow_mailersend_to_replace_resend() -> None:
    runtime_settings = settings(
        environment="production",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        mailersend_api_token="mlsn.test-secret",
        mailersend_from_email="noreply@example.com",
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "Preview／正式環境必須設定可用的 Resend" in str(error.value)


def test_production_rejects_non_postgresql_database() -> None:
    runtime_settings = settings(
        environment="production",
        database_url="sqlite+aiosqlite:///./production.db",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "DATABASE_URL" in str(error.value)


def test_production_raygate_requires_verified_contract() -> None:
    runtime_settings = settings(
        environment="production",
        payment_provider="raygate",
        raygate_payment_store_identifier="store-1",
        raygate_payment_key_hex="a" * 64,
        raygate_payment_iv_hex="b" * 32,
        raygate_payment_merchant_id="merchant-1",
        raygate_payment_terminal_id="term-1",
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
        raygate_payment_stage=False,
        raygate_payment_contract_verified=False,
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
    )

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    assert "RAYGATE_PAYMENT_CONTRACT_VERIFIED" in str(error.value)


def test_production_accepts_verified_raygate_origin_and_contract() -> None:
    runtime_settings = valid_production_settings(
        payment_provider="raygate",
        raygate_payment_store_identifier="store-1",
        raygate_payment_key_hex="a" * 64,
        raygate_payment_iv_hex="b" * 32,
        raygate_payment_merchant_id="merchant-1",
        raygate_payment_terminal_id="term-1",
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
        raygate_payment_stage=False,
        raygate_payment_contract_verified=True,
    )

    runtime_settings.validate_runtime_secrets()


@pytest.mark.asyncio
async def test_production_lifespan_never_seeds_demo_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_settings = settings(environment="production")
    seed_demo_data = AsyncMock()
    monkeypatch.setattr(main, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(main, "seed_demo_data", seed_demo_data)

    async with main.lifespan(FastAPI()):
        pass

    seed_demo_data.assert_not_awaited()


def test_sandbox_requires_a_reset_confirmation_secret() -> None:
    runtime_settings = settings(
        environment="sandbox",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
    )

    with pytest.raises(RuntimeError, match="DEMO_RESET_CONFIRMATION"):
        runtime_settings.validate_runtime_secrets()


def test_sandbox_cannot_disable_ecpay_stage() -> None:
    runtime_settings = settings(
        environment="sandbox",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_reset_confirmation="reset-code-strong",
        ecpay_payment_stage=False,
    )

    with pytest.raises(RuntimeError, match="ECPAY_PAYMENT_STAGE"):
        runtime_settings.validate_runtime_secrets()


def test_reset_confirmation_only_defaults_in_development() -> None:
    assert settings(environment="development").demo_reset_confirmation == "RESET"
    assert settings(environment="test").demo_reset_confirmation == ""
    assert settings(environment="sandbox").demo_reset_confirmation == ""


def test_create_app_fails_fast_with_unsafe_sandbox_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsafe_settings = settings(environment="sandbox")
    monkeypatch.setattr(main, "get_settings", lambda: unsafe_settings)

    with pytest.raises(RuntimeError):
        main.create_app()


@pytest.mark.asyncio
async def test_remote_app_sets_security_headers_and_rejects_unknown_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_settings = settings(
        environment="preview",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_admin_password="preview-admin-strong",
        demo_member_password="preview-member-strong",
        demo_nonmember_password="preview-customer-strong",
        resend_api_key="re_test-secret",
        email_from_email="noreply@example.com",
    )
    monkeypatch.setattr(main, "get_settings", lambda: runtime_settings)
    application = main.create_app()
    transport = ASGITransport(app=application)

    async with AsyncClient(
        transport=transport,
        base_url="https://api.example.test",
    ) as client:
        response = await client.get("/health")
        private_response = await client.get("/v1/me/points")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "max-age=31536000" in response.headers[
        "strict-transport-security"
    ]
    assert "frame-ancestors 'none'" in response.headers[
        "content-security-policy"
    ]
    assert private_response.headers["cache-control"] == "no-store"

    async with AsyncClient(
        transport=transport,
        base_url="https://evil.example",
    ) as client:
        rejected = await client.get("/health")

    assert rejected.status_code == 400


@pytest.mark.asyncio
async def test_readiness_reports_database_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_settings = settings(environment="test")
    monkeypatch.setattr(main, "get_settings", lambda: runtime_settings)
    database_check = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "check_database_connection", database_check)
    application = main.create_app()

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "available",
    }
    database_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_returns_503_when_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_settings = settings(environment="test")
    monkeypatch.setattr(main, "get_settings", lambda: runtime_settings)
    database_check = AsyncMock(
        side_effect=TimeoutError("database-password-should-not-leak")
    )
    monkeypatch.setattr(main, "check_database_connection", database_check)
    application = main.create_app()

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database": "unavailable",
    }
    assert "database-password-should-not-leak" not in response.text


def test_auth_email_requests_are_marked_for_immediate_delivery() -> None:
    assert main.AUTH_EMAIL_PATHS == {
        "/v1/auth/register",
        "/v1/auth/register-existing-member",
        "/v1/auth/resend-verification",
        "/v1/auth/forgot-password",
    }


def test_fixed_migrations_do_not_depend_on_runtime_metadata() -> None:
    sources = [
        INITIAL_MIGRATION.read_text(encoding="utf-8"),
        V2_MIGRATION.read_text(encoding="utf-8"),
    ]

    assert all("Base.metadata" not in source for source in sources)
    assert all("from app" not in source for source in sources)
    fixed_table_count = sum(source.count("op.create_table(") for source in sources)
    assert fixed_table_count == 37
    assert sum(source.count("op.drop_table(") for source in sources) == fixed_table_count
    cooperative_source = COOPERATIVE_MIGRATION.read_text(encoding="utf-8")
    assert "NEW_TABLES" in cooperative_source
    assert "checkfirst=True" in cooperative_source


def test_initial_migration_upgrades_matches_metadata_and_downgrades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(BACKEND_ROOT / "alembic"),
    )

    try:
        command.upgrade(config, "head")
        command.check(config)

        sync_engine = create_engine(f"sqlite:///{database_path}")
        with sync_engine.connect() as connection:
            migrated_tables = set(inspect(connection).get_table_names())
        sync_engine.dispose()

        assert set(Base.metadata.tables) <= migrated_tables

        command.downgrade(config, "base")
        sync_engine = create_engine(f"sqlite:///{database_path}")
        with sync_engine.connect() as connection:
            remaining_tables = set(inspect(connection).get_table_names())
        sync_engine.dispose()

        assert remaining_tables == {"alembic_version"}
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_app_lifespan_does_not_create_an_unmigrated_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "unmigrated.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}"
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", session_factory)

    with pytest.raises(OperationalError):
        async with main.lifespan(FastAPI()):
            pass

    await engine.dispose()
