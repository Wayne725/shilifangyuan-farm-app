import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import FulfillmentMethod, OrderFulfillment
from tests.test_integrations import make_regular_order


def test_meal_pickup_migration_preserves_existing_fulfillment_and_sets_compatible_defaults(tmp_path, monkeypatch):
    path = tmp_path / "meal-pickup-preferences.db"
    database_url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(database_url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            _, _, order = await make_regular_order(session)
            order.fulfillment = OrderFulfillment(
                method=FulfillmentMethod.EVENT_PICKUP, pickup_location="原取餐地點", pickup_code="654321",
            )
            await session.commit()
        await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0033_saved_mobile_barcode")
        query = text("SELECT id, order_id, pickup_location, pickup_code, status FROM order_fulfillments")
        with engine.connect() as connection:
            before = connection.execute(query).all()
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(query).all() == before
            assert connection.execute(text("SELECT pickup_address, pickup_instructions, needs_utensils FROM order_fulfillments")).one() == ("", "", 0)
    finally:
        engine.dispose()
        get_settings.cache_clear()
