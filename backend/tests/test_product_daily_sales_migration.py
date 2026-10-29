import pytest
import sqlalchemy as sa
from alembic import command

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, NOW


def test_product_daily_listing_migration_preserves_legacy_and_guards_downgrade(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'daily-sales.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    try:
        command.upgrade(database.config, "0038_activity_payments")
        products = database.table("products")
        database.run(lambda connection: connection.execute(products.insert().values(id="legacy", slug="legacy", name="原有商品", description="",
                        category="當季蔬果", unit="顆", member_price=10, nonmember_price=15,
                        stock_quantity=20, tax_type="taxable", can_ship=False,
                        allowed_shipping_channels=[], is_active=True, created_at=NOW, updated_at=NOW)))
        before = database.rows("products")[0]
        command.upgrade(database.config, "0039_product_daily_sales")
        after = database.rows("products")[0]
        assert after.pop("daily_sales_enabled") is True
        assert after == before
        command.upgrade(database.config, "head")
        command.check(database.config)
        command.downgrade(database.config, "0039_product_daily_sales")
        command.downgrade(database.config, "0038_activity_payments")
        assert database.rows("products")[0] == before
        command.upgrade(database.config, "0039_product_daily_sales")
        database.run(lambda connection: connection.execute(sa.text("UPDATE products SET daily_sales_enabled = false")))
        with pytest.raises(RuntimeError, match="販售範圍"):
            command.downgrade(database.config, "0038_activity_payments")
        assert database.rows("products")[0]["daily_sales_enabled"] is False
    finally:
        get_settings.cache_clear()
