import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from tests.test_meal_cancellation_review import seed_cancellation_order


def test_review_migration_preserves_existing_orders_and_protects_review_history(tmp_path, monkeypatch):
    path = tmp_path / "meal-review-migration.db"
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
        text("SELECT id, order_number, amount_total, payment_status, invoice_status FROM orders"),
        text("SELECT id, order_id, quantity, subtotal FROM order_items"),
        text("SELECT id, reserved_quantity, paid_quantity, capacity FROM meal_event_offerings"),
    ]
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0018_member_invoice_tax")
        with engine.connect() as connection:
            before = [connection.execute(query).all() for query in snapshots]
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert [connection.execute(query).all() for query in snapshots] == before
            assert connection.execute(text("SELECT meal_capacity_retained FROM order_fulfillments")).scalar_one() == 0
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("meal_cancellation_requests")}
        assert indexes["uq_meal_cancellation_pending_order"]["unique"]
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO meal_cancellation_requests (id, order_id, requested_by_id, reason, requested_at) SELECT 'migration-review', id, user_id, '保留紀錄', CURRENT_TIMESTAMP FROM orders"))
        with pytest.raises(RuntimeError, match="已有退單審核紀錄"):
            command.downgrade(config, "0018_member_invoice_tax")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT status FROM meal_cancellation_requests")).scalar_one() == "pending"
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0019_meal_cancellation_review"
            assert [connection.execute(query).all() for query in snapshots] == before
    finally:
        engine.dispose()
        get_settings.cache_clear()
