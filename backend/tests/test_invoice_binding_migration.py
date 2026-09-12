import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Invoice, InvoiceStatus
from tests.test_integrations import make_regular_order


def test_binding_migration_keeps_legacy_rows_unbound_and_preserves_snapshots(tmp_path, monkeypatch):
    path = tmp_path / "isolated-invoice-binding.db"
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
            session.add(Invoice(
                order=order, provider="fanyu", relate_number="LEGACYBINDINGTEST",
                invoice_number="AB12345678", status=InvoiceStatus.ISSUED,
                provider_request={"sellerID": "15989995", "snapshot": "unchanged"},
            ))
            await session.commit()
        await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0013_payment_poll_schedule")
        snapshot_sql = text("SELECT id, order_id, relate_number, provider, invoice_number, status, provider_request FROM invoices")
        with engine.connect() as connection:
            before = connection.execute(snapshot_sql).all()
            orders_before = connection.execute(text("SELECT id, order_number, amount_total FROM orders")).all()
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(snapshot_sql).all() == before
            assert connection.execute(text("SELECT id, order_number, amount_total FROM orders")).all() == orders_before
            assert connection.execute(text("SELECT provider_context FROM invoices")).scalar() is None
            assert connection.execute(text("SELECT invoice_provider_context FROM orders")).scalar() is None
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0014_invoice_account_binding"
        command.downgrade(config, "0013_payment_poll_schedule")
        with engine.connect() as connection:
            assert connection.execute(snapshot_sql).all() == before
    finally:
        engine.dispose()
        get_settings.cache_clear()
