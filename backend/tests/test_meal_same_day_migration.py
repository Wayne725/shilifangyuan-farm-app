from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models import MealEventStatus, UserRole
from tests.test_group_review_migration import MigrationDatabase


def test_same_day_migration_preserves_unconfigured_event_and_protects_enabled_rules(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'same-day.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    previous, revision = "0060_procurement_snapshot", "0061_meal_same_day"

    event_id = "preserved-meal-event"
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    try:
        command.upgrade(database.config, revision)
        database.insert("users", id="migration-admin", email="migration-admin@example.test",
                        display_name="管理員", password_hash="test", user_role=UserRole.ADMIN.value,
                        membership_type="nonmember", is_active=True, token_version=0, pending_member_claim=False,
                        created_at=now, updated_at=now)
        database.insert(
            "meal_events", id=event_id, title="保留場次", location="合作社",
            booking_policy="special", created_by_id="migration-admin",
            ordering_starts_at=now, ordering_ends_at=now + timedelta(hours=2),
            pickup_starts_at=now + timedelta(hours=3), pickup_ends_at=now + timedelta(hours=5),
            status=MealEventStatus.DRAFT.value, created_at=now, updated_at=now,
        )
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

        command.upgrade(database.config, "0062_meal_business_only")
        assert database.rows("meal_events")[0]["business_only"] is False
        table = database.table("meal_events")
        event_update = table.update().where(table.c.id == event_id)
        for values in (
            {"business_only": True},
            {"business_only": True, "same_day_preparation_minutes": None, "allow_cash_payment": True},
            {"business_only": True, "same_day_preparation_minutes": None, "booking_policy": "legacy"},
        ):
            with pytest.raises(IntegrityError):
                database.run(lambda connection: connection.execute(event_update.values(**values)))
        database.run(lambda connection: connection.execute(
            event_update.values(business_only=True, same_day_preparation_minutes=None),
        ))
        with pytest.raises(RuntimeError, match="已有僅限會餐的場次"):
            command.downgrade(database.config, revision)
        assert database.rows("meal_events")[0]["business_only"] is True
        database.run(lambda connection: connection.execute(
            event_update.values(business_only=False, same_day_preparation_minutes=90),
        ))
        command.downgrade(database.config, revision)
        restored = database.rows("meal_events")[0]
        assert "business_only" not in restored
        assert (restored["id"], restored["title"], restored["same_day_preparation_minutes"]) == (event_id, "保留場次", 90)

        previous, revision = "0062_meal_business_only", "0063_meal_ordering_pause"
        command.upgrade(database.config, revision)
        default = database.rows("meal_events")[0]
        assert default["ordering_paused_at"] is None and default["ordering_pause_version"] == 0
        command.downgrade(database.config, previous)
        assert "ordering_paused_at" not in database.rows("meal_events")[0]
        command.upgrade(database.config, revision)
        table = database.table("meal_events")
        event_update = table.update().where(table.c.id == event_id)
        with pytest.raises(IntegrityError):
            database.run(lambda connection: connection.execute(event_update.values(ordering_paused_at=now)))
        database.run(lambda connection: connection.execute(
            event_update.values(ordering_paused_at=now, ordering_pause_version=1),
        ))
        with pytest.raises(RuntimeError, match="已有暫停接單紀錄"):
            command.downgrade(database.config, previous)
        database.run(lambda connection: connection.execute(
            event_update.values(ordering_paused_at=None, ordering_pause_version=2),
        ))
        with pytest.raises(RuntimeError, match="已有暫停接單紀錄"):
            command.downgrade(database.config, previous)
        resumed = database.rows("meal_events")[0]
        assert resumed["ordering_paused_at"] is None and resumed["ordering_pause_version"] == 2
        assert resumed["id"] == event_id and resumed["same_day_preparation_minutes"] == 90
    finally:
        get_settings.cache_clear()
