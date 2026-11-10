import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from tests.test_meal_cancellation_review import seed_cancellation_order


def test_business_migration_backfills_without_changing_transactions_and_refuses_loss(tmp_path, monkeypatch):
    path = tmp_path / 'business-migration.db'
    url = f'sqlite+aiosqlite:///{path}'
    monkeypatch.setenv('DATABASE_URL', url)
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))

    async def seed():
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await seed_cancellation_order(session)
        finally:
            await engine.dispose()

    engine = create_engine(f'sqlite:///{path}')
    try:
        command.upgrade(config, 'head')
        asyncio.run(seed())
        command.downgrade(config, '0051_walk_in_sale_category')
        assert 'meal_booking_type' not in {row['name'] for row in inspect(engine).get_columns('orders')}
        with engine.connect() as connection:
            before = connection.execute(text('SELECT id, order_number, amount_total, payment_status FROM orders')).all()
        command.upgrade(config, 'head')
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT id, order_number, amount_total, payment_status FROM orders')).all() == before
            assert connection.execute(text('SELECT meal_booking_type FROM orders')).scalar_one() == 'standard'
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE orders SET meal_booking_type = 'invalid'"))
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("UPDATE orders SET meal_booking_type = 'business'"))
        with engine.begin() as connection:
            connection.execute(text("UPDATE orders SET meal_booking_type='business', payment_method='online', invoice_buyer_type='company', invoice_handling='pickup_day', invoice_issue_after='2030-01-01 16:00:00', invoice_buyer_tax_id='15989995', invoice_buyer_name='合成單位'"))
        with pytest.raises(RuntimeError, match='已有會餐訂單'):
            command.downgrade(config, '0051_walk_in_sale_category')
        with engine.connect() as connection:
            assert connection.execute(text('SELECT meal_booking_type FROM orders')).scalar_one() == 'business'
    finally:
        engine.dispose()
        get_settings.cache_clear()
