import asyncio

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.database import create_engine
from tests.test_group_review_migration import MigrationDatabase
from tests.test_integrations import make_regular_order


def test_old_product_source_is_preserved_without_guessing_supplier_and_downgrade_is_guarded(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'supplier-orders.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    previous, revision = "0059_recovery_quarantine", "0060_procurement_snapshot"

    async def seed():
        engine = create_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                _, product, order = await make_regular_order(session)
                await session.commit()
                return order.items[0].id, product.id
        finally:
            await engine.dispose()

    try:
        command.upgrade(database.config, revision)
        item_id, product_id = asyncio.run(seed())
        command.downgrade(database.config, previous)
        assert "procurement_snapshot" not in database.run(lambda connection: {
            col["name"] for col in inspect(connection).get_columns("order_items")
        })
        command.upgrade(database.config, revision)
        item = database.rows("order_items")[0]
        assert (item["id"], item["source_product_id"], item["procurement_snapshot"]) == (item_id, product_id, None)
        table = database.table("order_items")
        database.run(lambda connection: connection.execute(table.update().where(table.c.id == item_id)
            .values(procurement_snapshot={"product_id": product_id, "supplier_id": None})))
        with pytest.raises(RuntimeError, match="已有商品供應者快照"):
            command.downgrade(database.config, previous)
        assert database.rows("order_items")[0]["procurement_snapshot"] == {"product_id": product_id, "supplier_id": None}
    finally:
        get_settings.cache_clear()
