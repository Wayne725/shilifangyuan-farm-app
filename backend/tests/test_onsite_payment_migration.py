import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from tests.test_integrations import make_regular_order


def test_onsite_migration_preserves_legacy_orders_and_blocks_unsafe_downgrade(tmp_path, monkeypatch):
    path = tmp_path / "onsite-migration.db"
    database_url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(database_url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await make_regular_order(session)
            await session.commit()
        await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.check(config)
        with engine.connect() as connection:
            before = connection.execute(text("SELECT id, order_number, amount_total FROM orders")).all()
        command.downgrade(config, "0028_financial_statements")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, order_number, amount_total FROM orders")).all() == before
            assert connection.execute(text("SELECT payment_method FROM orders")).scalar_one() == "online"
        with engine.begin() as connection:
            connection.execute(text("UPDATE orders SET payment_method='cash_on_pickup'"))
        with pytest.raises(RuntimeError, match="現場付款紀錄"):
            command.downgrade(config, "0028_financial_statements")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT payment_method FROM orders")).scalar_one() == "cash_on_pickup"
    finally:
        engine.dispose()
        get_settings.cache_clear()
