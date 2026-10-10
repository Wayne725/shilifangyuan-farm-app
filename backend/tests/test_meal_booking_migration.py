import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from tests.test_meal_cancellation_review import seed_cancellation_order


def test_booking_migration_preserves_legacy_snapshots_and_protects_new_policy_orders(tmp_path, monkeypatch):
    path = tmp_path / "meal-booking-migration.db"
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
                await seed_cancellation_order(session)
        finally:
            await engine.dispose()
    engine = create_engine(f"sqlite:///{path}")
    snapshots = [
        text("SELECT id, order_number, amount_total, payment_status FROM orders"),
        text("SELECT id, order_id, quantity, unit_price, subtotal FROM order_items"),
        text("SELECT id, pickup_at, pickup_starts_at, pickup_ends_at FROM order_fulfillments"),
        text("SELECT id, ordering_starts_at, ordering_ends_at, pickup_starts_at, pickup_ends_at FROM meal_events"),
    ]
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0019_meal_cancellation_review")
        with engine.connect() as connection:
            before = [connection.execute(query).all() for query in snapshots]
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert [connection.execute(query).all() for query in snapshots] == before
            assert connection.execute(text("SELECT meal_ordering_ends_at FROM order_fulfillments")).scalar_one() is None
            assert connection.execute(text("SELECT pickup_slot_ends_at FROM meal_events")).scalar_one() is None
        assert "meal_ordering_ends_at" in {column["name"] for column in inspect(engine).get_columns("order_fulfillments")}
        with engine.begin() as connection:
            connection.execute(text("UPDATE order_fulfillments SET meal_ordering_ends_at = '2030-01-01 16:00:00'"))
        with pytest.raises(RuntimeError, match="已有新制便當訂單"):
            command.downgrade(config, "0019_meal_cancellation_review")
        with engine.connect() as connection:
            assert [connection.execute(query).all() for query in snapshots] == before
            assert connection.execute(text("SELECT meal_ordering_ends_at FROM order_fulfillments")).scalar_one() is not None
    finally:
        engine.dispose()
        get_settings.cache_clear()
