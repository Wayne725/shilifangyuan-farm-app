import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from tests.test_activity_payments import make_activity
from tests.test_group_review_window import group_fixture


def test_visibility_migration_backfills_and_refuses_reexposing_hidden_records(tmp_path, monkeypatch):
    path = tmp_path / 'visibility.db'
    url = f'sqlite+aiosqlite:///{path}'
    monkeypatch.setenv('DATABASE_URL', url)
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'alembic'))
    engine = create_engine(f'sqlite:///{path}')

    async def seed():
        async_engine = create_async_engine(url)
        try:
            async with async_sessionmaker(async_engine, expire_on_commit=False)() as session:
                await make_activity(session)
                await group_fixture(session, cutoff=False)
        finally:
            await async_engine.dispose()

    def financial_rows():
        with engine.connect() as connection:
            return {table: connection.execute(text(f'SELECT * FROM {table} ORDER BY id')).all()
                    for table in ['orders', 'activity_registrations', 'activity_charges', 'payment_attempts']}

    try:
        command.upgrade(config, 'head')
        asyncio.run(seed())
        saved = financial_rows()
        command.downgrade(config, '0053_email_registration_consent')
        for table in ['activities', 'group_campaigns']:
            assert 'is_public' not in {column['name'] for column in inspect(engine).get_columns(table)}
        command.upgrade(config, 'head')
        command.check(config)
        assert financial_rows() == saved
        with engine.connect() as connection:
            for table in ['activities', 'group_campaigns']:
                assert connection.execute(text(f'SELECT is_public FROM {table}')).scalar_one() == 1
            assert all(row == (None, None) for row in connection.execute(text('SELECT data_consent_version,data_consented_at FROM users')))
        with engine.begin() as connection:
            connection.execute(text('UPDATE activities SET is_public = false'))
        with pytest.raises(RuntimeError, match='不可降版重新公開'):
            command.downgrade(config, '0053_email_registration_consent')
        with engine.connect() as connection:
            assert connection.execute(text('SELECT is_public FROM activities')).scalar_one() == 0
        assert financial_rows() == saved
    finally:
        engine.dispose()
        get_settings.cache_clear()
