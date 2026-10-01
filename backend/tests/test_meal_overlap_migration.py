import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import FulfillmentMethod, MealEvent, MealEventStatus, OrderFulfillment
from tests.test_integrations import make_regular_order


def test_meal_overlap_migration_preserves_data_and_protects_downgrade(tmp_path, monkeypatch):
    path = tmp_path / "isolated-meal-overlap.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    now = datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc)

    async def seed():
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                _, _, order = await make_regular_order(session)
                event = MealEvent(
                    title="原便當場次", location="保留取餐點", created_by_id=order.user_id,
                    ordering_starts_at=now, ordering_ends_at=now + timedelta(hours=5),
                    pickup_starts_at=now + timedelta(hours=5), pickup_ends_at=now + timedelta(hours=6),
                    status=MealEventStatus.PUBLISHED,
                )
                session.add(event)
                await session.flush()
                order.meal_event_id = event.id
                order.fulfillment = OrderFulfillment(
                    method=FulfillmentMethod.EVENT_PICKUP, pickup_code="654321",
                    pickup_at=now + timedelta(hours=5, minutes=10),
                )
                await session.commit()
        finally:
            await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    snapshots = [
        text("SELECT id, title, location, status, ordering_starts_at, ordering_ends_at, pickup_starts_at, pickup_ends_at FROM meal_events"),
        text("SELECT id, order_id, pickup_code, pickup_at, status FROM order_fulfillments"),
        text("SELECT id, order_number, amount_total, meal_event_id FROM orders"),
    ]
    try:
        command.upgrade(config, "0016_meal_daily_schedules")
        asyncio.run(seed())
        with engine.connect() as connection:
            before = [connection.execute(query).all() for query in snapshots]
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert [connection.execute(query).all() for query in snapshots] == before
            assert connection.execute(text("SELECT COUNT(*) FROM meal_schedule_templates")).scalar() == 0

        with engine.begin() as connection:
            connection.execute(text("UPDATE meal_events SET pickup_starts_at = :start"), {
                "start": (now + timedelta(hours=3)).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds"),
            })
        with engine.connect() as connection:
            overlapping = [connection.execute(query).all() for query in snapshots]
        with pytest.raises(RuntimeError, match="已有接單與取餐重疊"):
            command.downgrade(config, "0016_meal_daily_schedules")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0017_meal_order_pickup_overlap"
            assert [connection.execute(query).all() for query in snapshots] == overlapping

        for cutoff_update in [
            "UPDATE meal_events SET ordering_ends_at = pickup_ends_at",
            "UPDATE meal_events SET ordering_ends_at = datetime(pickup_ends_at, '+1 hour')",
        ]:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(cutoff_update))

        with engine.begin() as connection:
            connection.execute(text("UPDATE meal_events SET pickup_starts_at = ordering_ends_at"))
        command.downgrade(config, "0016_meal_daily_schedules")
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE meal_events SET pickup_starts_at = :start"), {
                    "start": (now + timedelta(hours=3)).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds"),
                })
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        get_settings.cache_clear()
