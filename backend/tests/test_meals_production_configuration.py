from __future__ import annotations

import base64
import json
from unittest.mock import Mock

import pytest

from app import startup
from app.config import Settings


def meal_production_settings(**overrides):
    values = {
        '_env_file': None,
        'environment': 'production',
        'sales_scope': 'meals_only',
        'database_url': 'postgresql://db.example.test/cooperative',
        'app_base_url': 'https://api.example.test',
        'web_base_url': 'https://app.example.test',
        'jwt_secret': 'j' * 32,
        'internal_reconcile_secret': 'r' * 32,
        'pii_encryption_keys_json': json.dumps({'v1': base64.b64encode(b'p' * 32).decode()}),
        'resend_api_key': 're_test-secret',
        'email_from_email': 'noreply@example.test',
        'payment_provider': 'raygate',
        'raygate_payment_store_identifier': 'meal-test-store',
        'raygate_payment_key_hex': '11' * 32,
        'raygate_payment_iv_hex': '22' * 16,
        'raygate_payment_merchant_id': 'merchant',
        'raygate_payment_terminal_id': 'terminal',
        'raygate_payment_base_url': 'https://pay.example.test',
        'raygate_payment_allowed_hostname': 'pay.example.test',
        'raygate_payment_stage': False,
        'raygate_payment_contract_verified': True,
        'invoice_provider': 'fanyu',
        'fanyu_invoice_company_id': '12345678',
        'fanyu_invoice_user_id': 'TESTADMIN',
        'fanyu_invoice_auth_password': 'synthetic-password',
        'fanyu_invoice_api_key': 'synthetic-api-key',
        'fanyu_invoice_seller_id': '12345678',
        'fanyu_invoice_base_url': 'https://api01.einvoice.com.tw/einv',
        'fanyu_invoice_stage': False,
        'fanyu_invoice_signature_verified': True,
        'reconciliation_enabled': True,
    }
    values.update(overrides)
    return Settings(**values)


def test_meals_only_production_accepts_no_disabled_storage_or_logistics():
    settings = meal_production_settings()
    assert not settings.cloudflare_r2_bucket
    assert not settings.ecpay_logistics_merchant_id
    assert settings.ecpay_logistics_stage
    settings.validate_runtime_secrets()


@pytest.mark.parametrize(('overrides', 'setting_name'), [
    ({'jwt_secret': 'short'}, 'JWT_SECRET'),
    ({'internal_reconcile_secret': 'short'}, 'RECONCILE_SECRET'),
    ({'pii_encryption_keys_json': ''}, 'PII_ENCRYPTION_KEYS_JSON'),
    ({'pii_encryption_keys_json': '{broken'}, 'PII_ENCRYPTION_KEYS_JSON'),
    ({'resend_api_key': ''}, 'RESEND_API_KEY'),
    ({'app_base_url': 'http://api.example.test'}, 'APP_BASE_URL'),
    ({'web_base_url': 'http://app.example.test'}, 'WEB_BASE_URL'),
    ({'debug': True}, 'DEBUG'),
    ({'database_url': 'sqlite+aiosqlite:///:memory:'}, 'DATABASE_URL'),
    ({'payment_provider': 'ecpay'}, 'PAYMENT_PROVIDER'),
    ({'invoice_provider': 'ecpay'}, 'INVOICE_PROVIDER'),
    ({'raygate_payment_stage': True}, 'RAYGATE_PAYMENT_STAGE'),
    ({'raygate_payment_contract_verified': False}, 'RAYGATE_PAYMENT_CONTRACT_VERIFIED'),
    ({'raygate_payment_key_hex': ''}, 'RAYGATE_PAYMENT_KEY_HEX'),
    ({'fanyu_invoice_stage': True}, 'FANYU_INVOICE_STAGE'),
    ({'fanyu_invoice_signature_verified': False}, 'FANYU_INVOICE_SIGNATURE_VERIFIED'),
    ({'fanyu_invoice_api_key': ''}, 'FANYU_INVOICE_API_KEY'),
    ({'fanyu_invoice_base_url': 'https://webtest.einvoice.com.tw/einv'}, 'FANYU_INVOICE_BASE_URL'),
    ({'fanyu_invoice_base_url': 'https://web2.einvoice.com.tw/einv'}, 'FANYU_INVOICE_BASE_URL'),
    ({'fanyu_invoice_base_url': 'https://api01.einvoice.com.tw'}, 'FANYU_INVOICE_BASE_URL'),
    ({'fanyu_invoice_base_url': 'https://api01.einvoice.com.tw/einv?key=secret'}, 'FANYU_INVOICE_BASE_URL'),
    ({'reconciliation_enabled': False}, 'RECONCILIATION_ENABLED'),
    ({'raygate_payment_acceptance_sku': 'TEST-10'}, 'RAYGATE_PAYMENT_ACCEPTANCE_'),
])
def test_meals_only_production_preserves_required_safety_checks(overrides, setting_name):
    with pytest.raises(RuntimeError, match=setting_name):
        meal_production_settings(**overrides).validate_runtime_secrets()


def test_all_sales_keeps_storage_and_logistics_requirements():
    with pytest.raises(RuntimeError) as error:
        meal_production_settings(sales_scope='all').validate_runtime_secrets()
    assert 'CLOUDFLARE_R2_BUCKET' in str(error.value)
    assert 'ECPAY_LOGISTICS_MERCHANT_ID' in str(error.value)
    assert 'ECPAY_LOGISTICS_STAGE' in str(error.value)


@pytest.mark.parametrize('scope', ['', 'meal_only', 'MEALS_ONLY', 'production'])
def test_unknown_sales_scope_fails_closed(scope):
    with pytest.raises(ValueError, match='sales_scope'):
        meal_production_settings(sales_scope=scope)


def test_sales_scope_does_not_silently_change_legacy_default():
    assert Settings(_env_file=None).sales_scope == 'all'


def test_meals_production_startup_validates_then_migrates_without_demo_seed(monkeypatch):
    settings = meal_production_settings()
    migrate = Mock()
    seed = Mock()
    monkeypatch.setattr(startup, 'get_settings', lambda: settings)
    monkeypatch.setattr(startup, 'migrate_database', migrate)
    monkeypatch.setattr(startup, 'seed_preview_database', seed)
    monkeypatch.setattr('sys.argv', ['startup', 'prepare'])
    startup.main()
    migrate.assert_called_once_with()
    seed.assert_not_called()


def test_invalid_meals_production_stops_before_migration_or_seed(monkeypatch):
    settings = meal_production_settings(reconciliation_enabled=False)
    migrate = Mock()
    seed = Mock()
    monkeypatch.setattr(startup, 'get_settings', lambda: settings)
    monkeypatch.setattr(startup, 'migrate_database', migrate)
    monkeypatch.setattr(startup, 'seed_preview_database', seed)
    monkeypatch.setattr('sys.argv', ['startup', 'prepare'])
    with pytest.raises(RuntimeError, match='RECONCILIATION_ENABLED'):
        startup.main()
    migrate.assert_not_called()
    seed.assert_not_called()
