import asyncio
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import PaymentAttempt, PaymentStatus
from tests.test_integrations import make_regular_order


def test_poll_migration_preserves_legacy_payment_data(tmp_path, monkeypatch):
    database_path = tmp_path / "isolated-upgrade.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed_payment():
        engine = create_async_engine(database_url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            _user, _product, order = await make_regular_order(session)
            session.add(PaymentAttempt(
                order_id=order.id, merchant_trade_no="LEGACY-POLL-MIGRATION", provider="raygate",
                amount=order.amount_total, status=PaymentStatus.PENDING,
                expires_at=datetime.now(timezone.utc), checkout_payload={"opaque": "unchanged"},
            ))
            await session.commit()
        await engine.dispose()

    try:
        command.upgrade(config, "head")
        asyncio.run(seed_payment())
        command.downgrade(config, "0012_raygate_payments")
        engine = create_engine(f"sqlite:///{database_path}")
        with engine.connect() as connection:
            before = connection.execute(text("SELECT id, order_id, provider, amount, status, checkout_payload FROM payment_attempts")).all()
        assert len(before) == 1
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, order_id, provider, amount, status, checkout_payload FROM payment_attempts")).all() == before
            assert connection.execute(text("SELECT next_reconcile_at FROM payment_attempts")).scalar() is None
        command.downgrade(config, "0012_raygate_payments")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, order_id, provider, amount, status, checkout_payload FROM payment_attempts")).all() == before
        engine.dispose()
    finally:
        get_settings.cache_clear()
