import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.database import create_engine
from app.models import MealEvent, User
from tests.test_group_review_migration import MigrationDatabase


def test_same_day_migration_preserves_unconfigured_event_and_protects_enabled_rules(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'same-day.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    previous, revision = "0060_procurement_snapshot", "0061_meal_same_day"

    async def seed():
        engine = create_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                admin = User(email="migration-admin@example.test", display_name="管理員", password_hash="test")
                session.add(admin)
                await session.flush()
                now = datetime(2026, 11, 9, tzinfo=timezone.utc)
                event = MealEvent(
                    title="保留場次", location="合作社", booking_policy="special", created_by_id=admin.id,
                    ordering_starts_at=now, ordering_ends_at=now + timedelta(hours=2),
                    pickup_starts_at=now + timedelta(hours=3), pickup_ends_at=now + timedelta(hours=5),
                )
                session.add(event)
                await session.commit()
                return event.id
        finally:
            await engine.dispose()

    try:
        command.upgrade(database.config, revision)
        event_id = asyncio.run(seed())
        command.downgrade(database.config, previous)
        assert "same_day_preparation_minutes" not in database.run(lambda connection: {
            col["name"] for col in inspect(connection).get_columns("meal_events")
        })
        command.upgrade(database.config, revision)
        assert [(row["id"], row["title"], row["same_day_preparation_minutes"])
                for row in database.rows("meal_events")] == [(event_id, "保留場次", None)]
        table = database.table("meal_events")
        event_update = table.update().where(table.c.id == event_id)
        with pytest.raises(IntegrityError):
            database.run(lambda connection: connection.execute(
                event_update.values(same_day_preparation_minutes=90, allow_cash_payment=True),
            ))
        database.run(lambda connection: connection.execute(event_update.values(same_day_preparation_minutes=90)))
        with pytest.raises(RuntimeError, match="已有當日訂餐設定"):
            command.downgrade(database.config, previous)
        assert database.rows("meal_events")[0]["same_day_preparation_minutes"] == 90
    finally:
        get_settings.cache_clear()
