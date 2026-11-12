from datetime import datetime, timedelta, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, select

from app.models import FulfillmentMethod, MembershipType, Order, OrderKind, SalesChannel, User, UserRole
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
    expected_users = [
        {**row, "data_consent_version": None, "data_consented_at": None, "meal_contact_phone_encrypted": None}
        for row in before_users
    ]
    assert await table_rows(database, "users") == expected_users
    def metadata_and_single_head(config, _):
        assert ScriptDirectory.from_config(config).get_heads() == [database.head]
        command.check(config)
    await database.migrate(metadata_and_single_head, None)
    await database.migrate(command.upgrade, "head")
    assert await table_rows(database, "orders") == [after_order]
    assert await table_rows(database, "users") == expected_users


@pytest.mark.asyncio
async def test_meal_cancellation_source_backfills_using_the_historical_rule(migrated_database):
    database = migrated_database
    await database.migrate(command.upgrade, "0065_admin_editing")
    admin_id, buyer_id, order_id = (str(uuid4()) for _ in range(3))
    now = datetime.now(timezone.utc)
    cases = [
        (admin_id, admin_id, now, "admin"),
        (admin_id, admin_id, now + timedelta(seconds=1), "buyer"),
        (buyer_id, admin_id, now, "buyer"),
        (buyer_id, None, None, "buyer"),
    ]
    expected_source = {}
    async with database.engine.begin() as connection:
        await connection.execute(User.__table__.insert(), [
            {"id": admin_id, "display_name": "歷史管理員", "password_hash": "test", "user_role": UserRole.ADMIN},
            {"id": buyer_id, "display_name": "歷史訂購者", "password_hash": "test", "user_role": UserRole.CUSTOMER},
        ])
        await connection.execute(Order.__table__.insert().values(
            id=order_id, user_id=buyer_id, order_number="M-LEGACY-CANCEL", order_kind=OrderKind.REGULAR,
            sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
            membership_type_snapshot=MembershipType.NONMEMBER, amount_total=100, contact_email="meal@example.test",
        ))
        requests = await connection.run_sync(lambda sync: Table("meal_cancellation_requests", MetaData(), autoload_with=sync))
        for requester, reviewer, reviewed_at, source in cases:
            request_id = str(uuid4())
            expected_source[request_id] = source
            await connection.execute(requests.insert().values(
                id=request_id, order_id=order_id, requested_by_id=requester, requested_at=now, reason="歷史退單",
                status="approved" if reviewer else "pending", reviewed_by_id=reviewer, reviewed_at=reviewed_at,
                review_reason="電話確認" if reviewer else None,
            ))
        before = [dict(row) for row in (await connection.execute(select(requests).order_by(requests.c.id))).mappings()]

    await database.migrate(command.upgrade, "head")
    async with database.engine.connect() as connection:
        requests = await connection.run_sync(lambda sync: Table("meal_cancellation_requests", MetaData(), autoload_with=sync))
        after = [dict(row) for row in (await connection.execute(select(requests).order_by(requests.c.id))).mappings()]
    assert after == [{**row, "source": expected_source[row["id"]]} for row in before]
