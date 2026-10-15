import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import FulfillmentMethod, MealEvent, MealEventStatus, OrderFulfillment
from tests.test_integrations import make_regular_order


def test_meal_schedule_migration_preserves_old_events_and_fulfillment(tmp_path, monkeypatch):
    path = tmp_path / "isolated-meal-schedules.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            _, _, order = await make_regular_order(session)
            now = datetime.now(timezone.utc)
            event = MealEvent(
                title="保留舊場次", location="原取餐點", created_by_id=order.user_id,
                ordering_starts_at=now, ordering_ends_at=now + timedelta(hours=1),
                pickup_starts_at=now + timedelta(hours=2), pickup_ends_at=now + timedelta(hours=3),
                status=MealEventStatus.PUBLISHED,
            )
            session.add(event)
            await session.flush()
            order.meal_event_id = event.id
            order.fulfillment = OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP, pickup_code="456789")
            await session.commit()
        await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0015_document_retention")
        snapshots = [
            text("SELECT id, title, location, status, ordering_starts_at, ordering_ends_at, pickup_starts_at, pickup_ends_at FROM meal_events"),
            text("SELECT id, order_id, pickup_code, status FROM order_fulfillments"),
            text("SELECT id, order_number, amount_total, meal_event_id FROM orders"),
        ]
        with engine.connect() as connection:
            before = [connection.execute(query).all() for query in snapshots]
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert [connection.execute(query).all() for query in snapshots] == before
            assert connection.execute(text("SELECT COUNT(*) FROM meal_schedule_templates")).scalar() == 0
            assert connection.execute(text("SELECT schedule_template_id, service_date, meal_period FROM meal_events")).one() == (None, None, None)
            assert connection.execute(text("SELECT pickup_at FROM order_fulfillments")).scalar() is None
    finally:
        engine.dispose()
        get_settings.cache_clear()
