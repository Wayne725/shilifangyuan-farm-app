import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Invoice, InvoiceStatus
from tests.test_integrations import make_regular_order


def test_member_tax_migration_preserves_legacy_invoices_and_protects_new_snapshots(
    tmp_path, monkeypatch,
):
    path = tmp_path / "member-tax-migration.db"
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
                _, _, order = await make_regular_order(session)
                session.add(Invoice(
                    order=order, provider="fanyu", relate_number="LEGACYMEMBERTAX",
                    invoice_number="AB12345678", status=InvoiceStatus.ISSUED,
                    provider_request={"taxType": "1", "totalAmount": "1000"},
                ))
                await session.commit()
        finally:
            await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    snapshots = [
        text("SELECT id, order_number, amount_total, tax_amount, membership_type_snapshot FROM orders"),
        text("SELECT id, order_id, subtotal, tax_type FROM order_items"),
        text("SELECT id, order_id, invoice_number, status, provider_request FROM invoices"),
    ]
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0017_meal_order_pickup_overlap")
        with engine.connect() as connection:
            before = [connection.execute(query).all() for query in snapshots]
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert [connection.execute(query).all() for query in snapshots] == before
            assert connection.execute(text("SELECT member_tax_exempt_snapshot FROM orders")).scalar() == 0
        with engine.begin() as connection:
            connection.execute(text("UPDATE orders SET member_tax_exempt_snapshot = true"))
        with pytest.raises(RuntimeError, match="已有社員免稅訂單"):
            command.downgrade(config, "0017_meal_order_pickup_overlap")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0018_member_invoice_tax"
            assert connection.execute(text("SELECT member_tax_exempt_snapshot FROM orders")).scalar() == 1
            assert [connection.execute(query).all() for query in snapshots] == before
        with engine.begin() as connection:
            connection.execute(text("UPDATE orders SET member_tax_exempt_snapshot = false"))
        command.downgrade(config, "0017_meal_order_pickup_overlap")
        assert "member_tax_exempt_snapshot" not in {
            column["name"] for column in inspect(engine).get_columns("orders")
        }
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        get_settings.cache_clear()
