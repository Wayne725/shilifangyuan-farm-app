"""Real Alembic upgrades in a private schema of the disposable local PG database."""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_engine_from_config, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import MembershipType, Order, User, UserRole
from app.routers.walk_in_sales import walk_in_sales_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_walk_in_sales import payload


@pytest.fixture
async def migrated_database(monkeypatch):
    url = make_url(os.environ["TEST_POSTGRES_URL"])
    if url.drivername != "postgresql+asyncpg" or url.host not in {"localhost", "127.0.0.1"} or url.database != "shilifangyuan_test":
        pytest.fail("Only the disposable local shilifangyuan_test database is allowed")
    schema = f"walkin_migration_{uuid4().hex}"
    admin_engine = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    settings = Settings(_env_file=None, environment="test", database_url=url.render_as_string(hide_password=False))
    monkeypatch.setattr("app.config.get_settings", lambda: settings)

    def scoped_migration_engine(configuration, **kwargs):
        migration_url = make_url(configuration["sqlalchemy.url"])
        assert migration_url == url
        kwargs["connect_args"] = {**kwargs.get("connect_args", {}), "server_settings": {"search_path": schema}}
        return async_engine_from_config(configuration, **kwargs)

    # env.py still runs its actual online path; only its connection search_path is isolated.
    monkeypatch.setattr("sqlalchemy.ext.asyncio.async_engine_from_config", scoped_migration_engine)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def migrate(operation, revision):
        try:
            await asyncio.to_thread(operation, config, revision)
        finally:
            # Deployment starts a fresh API process after Alembic, with no pre-DDL reader plans.
            await engine.dispose()

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield SimpleNamespace(engine=engine, sessions=async_sessionmaker(engine, expire_on_commit=False), migrate=migrate)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


async def version(database):
    async with database.engine.connect() as connection:
        return await connection.scalar(text("SELECT version_num FROM alembic_version"))


async def table_rows(database, name):
    assert name in {"orders", "users", "walk_in_sales", "payment_attempts", "refunds", "admin_audits", "outbox_events"}
    async with database.engine.connect() as connection:
        rows = await connection.execute(text(f'SELECT * FROM "{name}"'))
        return sorted((dict(row) for row in rows.mappings()), key=lambda row: str(row.get("id") or row.get("order_id")))


@pytest.mark.asyncio
async def test_real_pg_42_to_45_then_merged_head_preserves_account_and_guest_collection(migrated_database):
    database = migrated_database
    await database.migrate(command.upgrade, "0042_order_reference_printing")
    admin_id, old_order_id = str(uuid4()), str(uuid4())
    async with database.engine.begin() as connection:
        await connection.execute(User.__table__.insert().values(id=admin_id, email="migration-admin@example.com",
            display_name="原帳號", password_hash="test", user_role=UserRole.ADMIN))
        old_orders = await connection.run_sync(lambda sync: Table("orders", MetaData(), autoload_with=sync))
        await connection.execute(old_orders.insert().values(
            id=old_order_id, order_number="OLD-MIGRATION-ORDER", user_id=admin_id, revision=1,
            order_kind="regular", sales_channel="regular", fulfillment_method="cooperative_pickup",
            membership_type_snapshot="nonmember", member_tax_exempt_snapshot=False,
            amount_total=105, tax_amount=5, contact_email="migration-admin@example.com",
            invoice_buyer_type="personal", invoice_carrier_type="cloud", invoice_status="not_eligible",
            payment_method="online", payment_status="pending", fulfillment_status="pending_confirmation",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
    before_order = (await table_rows(database, "orders"))[0]
    before_users = await table_rows(database, "users")
    await database.migrate(command.upgrade, "0045_walk_in_sales")
    assert await version(database) == "0045_walk_in_sales"
    after_order = (await table_rows(database, "orders"))[0]
    assert {key: after_order[key] for key in before_order} == before_order
    assert after_order["customer_kind"] == "account" and after_order["invoice_handling"] == "on_payment"
    assert await table_rows(database, "users") == before_users
    async with database.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM orders JOIN users ON users.id=orders.user_id WHERE orders.id=:id"), {"id": old_order_id}) == 1
        columns = await connection.run_sync(lambda sync: inspect(sync).get_columns("orders"))
        assert next(row for row in columns if row["name"] == "user_id")["nullable"] is True

    # The merged application must run against its complete migration graph.
    await database.migrate(command.upgrade, "head")
    assert await version(database) == "0049_merge_commerce_remediation"
    async with database.sessions() as session:
        admin = await session.get(User, admin_id)
        body = payload()
        async with api_test_context(session, [walk_in_sales_router], settings=make_test_settings()) as client:
            created = await client.post("/v1/admin/walk-in-sales", json=body, headers=auth_headers(admin))
            assert created.status_code == 201, created.text
            guest_id = created.json()["order_id"]
            guest = await session.get(Order, guest_id)
            assert guest.user_id is None and guest.membership_type_snapshot == MembershipType.NONMEMBER
            assert guest.tax_amount == 10 and not guest.member_tax_exempt_snapshot
            assert (await client.post("/v1/admin/walk-in-sales", json=body, headers=auth_headers(admin))).status_code == 200
            for mutation in ({"user_id": admin_id}, {"member_tax_exempt_snapshot": True}, {"membership_type_snapshot": MembershipType.MEMBER}):
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        await session.execute(update(Order).where(Order.id == guest_id).values(**mutation))
            refund_body = {"amount": 210, "reason": "遷移驗收已退現金", "confirm_returned": True}
            for _ in range(2):
                refunded = await client.post(f"/v1/admin/walk-in-sales/{guest_id}/refund", json=refund_body, headers=auth_headers(admin))
                assert refunded.status_code == 200, refunded.text
                assert refunded.json()["payment_status"] == "refunded"
    assert len(await table_rows(database, "walk_in_sales")) == 1
    assert len(await table_rows(database, "payment_attempts")) == 1
    assert len(await table_rows(database, "refunds")) == 1
    tables = ("orders", "users", "walk_in_sales", "payment_attempts", "refunds", "admin_audits", "outbox_events")
    before_refused_downgrade = {name: await table_rows(database, name) for name in tables}
    with pytest.raises(RuntimeError, match="已有現場交易"):
        await database.migrate(command.downgrade, "0044_invoice_timing")
    assert await version(database) == "0049_merge_commerce_remediation"
    assert {name: await table_rows(database, name) for name in tables} == before_refused_downgrade
    assert await table_rows(database, "users") == before_users


@pytest.mark.asyncio
async def test_real_pg_empty_42_to_45_down_to_42_and_up_again(migrated_database):
    database = migrated_database
    await database.migrate(command.upgrade, "0042_order_reference_printing")
    await database.migrate(command.upgrade, "0045_walk_in_sales")
    assert await version(database) == "0045_walk_in_sales"
    await database.migrate(command.downgrade, "0042_order_reference_printing")
    assert await version(database) == "0042_order_reference_printing"
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        columns = await connection.run_sync(lambda sync: inspect(sync).get_columns("orders"))
    assert "walk_in_sales" not in tables and "order_number_counters" not in tables
    assert not {"customer_kind", "customer_name_snapshot", "invoice_handling", "invoice_issue_after"}.intersection(row["name"] for row in columns)
    assert next(row for row in columns if row["name"] == "user_id")["nullable"] is False
    await database.migrate(command.upgrade, "0045_walk_in_sales")
    assert await version(database) == "0045_walk_in_sales"
    assert await table_rows(database, "orders") == [] and await table_rows(database, "walk_in_sales") == []
