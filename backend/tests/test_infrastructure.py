from __future__ import annotations

import base64
import json
from pathlib import Path

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


def test_secure_environments_accept_long_nondefault_secrets() -> None:
    settings(
        environment="sandbox",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_reset_confirmation="reset-code-strong",
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


def test_preview_accepts_strong_secrets_without_external_integrations() -> None:
    settings(
        environment="preview",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
    ).validate_runtime_secrets()


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


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_secure_environments_require_an_email_provider_and_sender(
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


def test_production_accepts_one_usable_email_provider() -> None:
    runtime_settings = settings(
        environment="production",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        resend_api_key="re_test-secret",
        email_from_email="broken@sender",
        mailersend_api_token="mlsn.test-secret",
        mailersend_from_email="noreply@example.com",
    )

    runtime_settings.validate_runtime_secrets()


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


def test_auth_email_requests_are_marked_for_immediate_delivery() -> None:
    assert main.AUTH_EMAIL_PATHS == {
        "/v1/auth/register",
        "/v1/auth/register-existing-member",
        "/v1/auth/resend-verification",
        "/v1/auth/forgot-password",
    }


@pytest.mark.asyncio
async def test_ready_reports_database_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(
        database_engine,
        expire_on_commit=False,
    )
    monkeypatch.setattr(main, "SessionLocal", session_factory)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: settings(environment="test"),
    )
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
    await database_engine.dispose()


@pytest.mark.asyncio
async def test_ready_returns_503_without_leaking_database_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_session_factory():
        raise OSError("database-password-should-not-leak")

    monkeypatch.setattr(main, "SessionLocal", unavailable_session_factory)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: settings(environment="test"),
    )
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
