import pytest
from alembic import command
from sqlalchemy import inspect

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, NOW, seed_user


def test_old_product_source_is_preserved_without_guessing_supplier_and_downgrade_is_guarded(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'supplier-orders.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    previous, revision = "0059_recovery_quarantine", "0060_procurement_snapshot"

    try:
        command.upgrade(database.config, previous)
        seed_user(database)
        item_id, product_id = "historical-item", "historical-product"
        products = database.table("products")
        database.run(lambda connection: connection.execute(products.insert().values(
            id=product_id, slug="test-rice", name="測試白米", description="", category="米食",
            unit="包", member_price=100, nonmember_price=120, stock_quantity=3, tax_type="taxable",
            can_ship=False, allowed_shipping_channels=[], is_active=True, created_at=NOW, updated_at=NOW,
        )))
        database.insert(
            "orders", id="historical-order", user_id="migration-admin", order_number="MIG-PROCUREMENT",
            order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
            membership_type_snapshot="nonmember", amount_total=200, contact_email="migration@example.test",
            invoice_buyer_type="personal", invoice_carrier_type="cloud", invoice_status="not_eligible",
            payment_status="pending", fulfillment_status="pending_confirmation", created_at=NOW, updated_at=NOW,
        )
        database.insert(
            "order_items", id=item_id, order_id="historical-order", source_product_id=product_id,
            product_name="測試白米", unit_label="包", quantity=2, unit_price=100, subtotal=200, tax_type="taxable",
        )
        before = database.rows("order_items")
        command.upgrade(database.config, revision)
        assert database.rows("order_items") == [{**before[0], "procurement_snapshot": None}]
        command.downgrade(database.config, previous)
        assert "procurement_snapshot" not in database.run(lambda connection: {
            col["name"] for col in inspect(connection).get_columns("order_items")
        })
        assert database.rows("order_items") == before
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
