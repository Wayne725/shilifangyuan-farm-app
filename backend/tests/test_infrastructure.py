from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
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


def settings(**overrides) -> Settings:
    values = {"_env_file": None}
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_secure_environments_reject_default_secrets(environment: str) -> None:
    runtime_settings = settings(environment=environment)

    with pytest.raises(RuntimeError) as error:
        runtime_settings.validate_runtime_secrets()

    message = str(error.value)
    assert "JWT_SECRET" in message
    assert "RECONCILE_SECRET" in message
    assert DEFAULT_JWT_SECRET not in message
    assert DEFAULT_RECONCILE_SECRET not in message


@pytest.mark.parametrize("environment", ["sandbox", "production"])
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
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_reset_confirmation="reset-code-strong",
    ).validate_runtime_secrets()


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


def test_initial_migration_is_fixed_and_complete() -> None:
    source = INITIAL_MIGRATION.read_text(encoding="utf-8")

    assert "Base.metadata" not in source
    assert "from app" not in source
    assert source.count("op.create_table(") == len(Base.metadata.tables)
    assert source.count("op.drop_table(") == len(Base.metadata.tables)


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
