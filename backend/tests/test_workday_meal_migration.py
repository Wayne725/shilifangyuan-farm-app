import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from tests.test_meal_cancellation_review import seed_cancellation_order


def test_workday_migration_preserves_legacy_orders_and_blocks_policy_loss(tmp_path, monkeypatch):
    path = tmp_path / "workday-migration.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await seed_cancellation_order(session)
        finally:
            await engine.dispose()

    snapshots = [
        text("SELECT id, ordering_starts_at, ordering_ends_at, pickup_starts_at, pickup_ends_at FROM meal_events"),
        text("SELECT id, order_number, amount_total, payment_status FROM orders"),
        text("SELECT id, quantity, unit_price, subtotal FROM order_items"),
        text("SELECT id, pickup_at, pickup_starts_at, pickup_ends_at FROM order_fulfillments"),
    ]
    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0049_merge_commerce_remediation")
        with engine.connect() as connection:
            before = [connection.execute(query).all() for query in snapshots]
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert [connection.execute(query).all() for query in snapshots] == before
            assert set(connection.scalars(text("SELECT booking_policy FROM meal_events"))) == {"legacy"}
        for policy in ("workday", "special"):
            with engine.begin() as connection:
                connection.execute(text("UPDATE meal_events SET booking_policy=:policy"), {"policy": policy})
            with pytest.raises(RuntimeError, match="不可直接降版"):
                command.downgrade(config, "0049_merge_commerce_remediation")
            with engine.connect() as connection:
                assert set(connection.scalars(text("SELECT booking_policy FROM meal_events"))) == {policy}
                assert [connection.execute(query).all() for query in snapshots] == before
    finally:
        engine.dispose()
        get_settings.cache_clear()
