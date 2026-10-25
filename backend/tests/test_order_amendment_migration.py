import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.integrations.payment_service import create_payment_attempt
from tests.test_integrations import make_regular_order, payment_settings


def test_revision_migration_preserves_payment_evidence_and_prevents_unsafe_downgrade(tmp_path, monkeypatch):
    path = tmp_path / "amendment-migration.db"
    database_url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                buyer, _, order = await make_regular_order(session)
                await create_payment_attempt(session, order.id, buyer, payment_settings())
                await session.commit()
        finally:
            await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0034_meal_pickup_utensils")
        with engine.connect() as connection:
            before = connection.execute(text("SELECT id, order_id, amount, checkout_payload FROM payment_attempts")).all()
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, order_id, amount, checkout_payload FROM payment_attempts")).all() == before
            assert connection.execute(text("SELECT revision FROM orders")).scalar_one() == 1
            assert connection.execute(text("SELECT order_revision FROM payment_attempts")).scalar_one() == 1
            assert connection.execute(text("SELECT superseded_at FROM payment_attempts")).scalar_one() is None
        with engine.begin() as connection:
            connection.execute(text("UPDATE orders SET revision=2"))
        with pytest.raises(RuntimeError, match="已有訂單數量修訂"):
            command.downgrade(config, "0034_meal_pickup_utensils")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT revision FROM orders")).scalar_one() == 2
            assert connection.execute(text("SELECT id, order_id, amount, checkout_payload FROM payment_attempts")).all() == before
    finally:
        engine.dispose()
        get_settings.cache_clear()
