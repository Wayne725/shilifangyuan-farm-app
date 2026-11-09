from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table

from app.models import User, UserRole
from app.invoice_timing import invoice_handling
from pgtests.test_walk_in_sales_migration import migrated_database, table_rows, version


@pytest.mark.asyncio
@pytest.mark.parametrize("start", ["0042_order_reference_printing", "0045_walk_in_sales", "0048_merge_remediation_print"])
async def test_both_heads_and_live_graph_preserve_orders_at_merged_head(migrated_database, start):
    database = migrated_database
    await database.migrate(command.upgrade, start)
    user_id, order_id = str(uuid4()), str(uuid4())
    async with database.engine.begin() as connection:
        await connection.execute(User.__table__.insert().values(id=user_id, email="merge-customer@example.test",
            display_name="合成原訂購者", password_hash="test", user_role=UserRole.CUSTOMER))
        orders = await connection.run_sync(lambda sync: Table("orders", MetaData(), autoload_with=sync))
        await connection.execute(orders.insert().values(id=order_id, user_id=user_id, order_number="M-LEGACY-MERGE", revision=1,
            order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
            membership_type_snapshot="nonmember", member_tax_exempt_snapshot=False, amount_total=105, tax_amount=5,
            contact_email="merge-customer@example.test", invoice_buyer_type="personal", invoice_carrier_type="cloud",
            invoice_status="not_eligible", payment_method="online", payment_status="pending",
            fulfillment_status="pending_confirmation", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
    before_order = (await table_rows(database, "orders"))[0]
    before_users = await table_rows(database, "users")
    await database.migrate(command.upgrade, "head")
    assert await version(database) == database.head
    after_order = (await table_rows(database, "orders"))[0]
    assert {key: after_order[key] for key in before_order} == before_order
    assert after_order["customer_kind"] == "account"
    assert after_order["invoice_handling"] == before_order.get("invoice_handling", "on_payment")
    assert invoice_handling(SimpleNamespace(**after_order)) == "on_payment"
    assert await table_rows(database, "users") == before_users
    def metadata_and_single_head(config, _):
        assert ScriptDirectory.from_config(config).get_heads() == [database.head]
        command.check(config)
    await database.migrate(metadata_and_single_head, None)
    await database.migrate(command.upgrade, "head")
    assert await table_rows(database, "orders") == [after_order]
    assert await table_rows(database, "users") == before_users
