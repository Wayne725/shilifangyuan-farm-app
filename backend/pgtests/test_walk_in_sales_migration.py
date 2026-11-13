"""Real Alembic upgrades in a private schema of the disposable local PG database."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_engine_from_config, async_sessionmaker, create_async_engine

from app.integrations.pii_crypto import pii_cipher_from_settings
from app.member_claims import roster_aad
from app.models import BrowserRefreshSelection, MembershipType, Order, RefreshSession, User, UserRole
from app.phone_lookup import phone_lookup_digest
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
    settings = make_test_settings(database_url=url.render_as_string(hide_password=False))
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
    scripts = ScriptDirectory.from_config(config)
    head = scripts.get_current_head()
    assert head is not None
    assert "0049_merge_commerce_remediation" in {revision.revision for revision in scripts.walk_revisions(head=head)}

    async def migrate(operation, revision):
        try:
            await asyncio.to_thread(operation, config, revision)
        finally:
            # Deployment starts a fresh API process after Alembic, with no pre-DDL reader plans.
            await engine.dispose()

    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        yield SimpleNamespace(engine=engine, sessions=async_sessionmaker(engine, expire_on_commit=False), migrate=migrate, head=head, settings=settings)
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


async def test_real_pg_counter_phone_migration_backfills_ciphertexts_and_matches_roster(migrated_database):
    database = migrated_database
    await database.migrate(command.upgrade, "0067_meal_event_archive")
    cipher = pii_cipher_from_settings(database.settings)
    owner_id, roster_id = str(uuid4()), str(uuid4())
    claimed_user_id, claimed_roster_id = str(uuid4()), str(uuid4())
    phones = ("0900123456", "0900234567", "+886 900-345-678")
    ciphertexts = tuple(
        cipher.encrypt_text(phone, associated_data=aad)
        for phone, aad in zip(phones, (f"meal-contact:{owner_id}", f"member-profile:{owner_id}", roster_aad(roster_id)))
    )
    now = datetime.now(timezone.utc)
    async with database.engine.begin() as connection:
        await connection.execute(User.__table__.insert().values(
            id=owner_id, email="migration-counter@example.com", display_name="合成收款員", password_hash="test",
            user_role=UserRole.ADMIN, meal_contact_phone_encrypted=ciphertexts[0],
        ))
        await connection.execute(User.__table__.insert().values(
            id=claimed_user_id, email="migration-claimed@example.com", display_name="合成認領社員", password_hash="test",
        ))
        profiles = await connection.run_sync(lambda sync: Table("member_profiles", MetaData(), autoload_with=sync))
        await connection.execute(profiles.insert().values(
            id=str(uuid4()), user_id=owner_id, phone_encrypted=ciphertexts[1], legal_name_encrypted="unused",
            birth_date_encrypted="unused", address_encrypted="unused", emergency_contact_encrypted="unused",
            encryption_key_version=cipher.current_version, consent_version="test", consented_at=now, created_at=now, updated_at=now,
        ))
        roster = await connection.run_sync(lambda sync: Table("member_roster_entries", MetaData(), autoload_with=sync))
        await connection.execute(roster.insert().values(
            id=roster_id, member_number="COUNTER-ROSTER", legal_name_encrypted=cipher.encrypt_text("王合成", associated_data=roster_aad(roster_id)),
            email_encrypted="unused", phone_encrypted=ciphertexts[2], encryption_key_version=cipher.current_version,
            created_at=now, updated_at=now,
        ))
        await connection.execute(roster.insert().values(
            id=claimed_roster_id, member_number="COUNTER-CLAIMED", legal_name_encrypted="unused", email_encrypted="unused",
            phone_encrypted=cipher.encrypt_text("0900456789", associated_data=roster_aad(claimed_roster_id)),
            claimed_user_id=claimed_user_id, claimed_at=now, encryption_key_version=cipher.current_version,
            created_at=now, updated_at=now,
        ))
        memberships = await connection.run_sync(lambda sync: Table("memberships", MetaData(), autoload_with=sync))
        await connection.execute(memberships.insert().values(
            id=str(uuid4()), user_id=claimed_user_id, member_number="COUNTER-CLAIMED", status="active",
            created_at=now, updated_at=now,
        ))
    await database.migrate(command.upgrade, "head")
    async with database.engine.connect() as connection:
        for statement, phone, ciphertext in zip((
            "SELECT meal_contact_phone_encrypted, meal_contact_phone_lookup FROM users WHERE id = :id",
            "SELECT phone_encrypted, phone_lookup FROM member_profiles WHERE user_id = :id",
            "SELECT phone_encrypted, phone_lookup FROM member_roster_entries WHERE id = :id",
        ), phones, ciphertexts):
            row = (await connection.execute(text(statement), {"id": roster_id if "roster" in statement else owner_id})).one()
            assert tuple(row) == (ciphertext, phone_lookup_digest(phone, cipher))
        ciphertext, digest = (await connection.execute(text(
            "SELECT meal_contact_phone_encrypted, meal_contact_phone_lookup FROM users WHERE id = :id",
        ), {"id": claimed_user_id})).one()
        assert cipher.decrypt_text(ciphertext, associated_data=f"meal-contact:{claimed_user_id}") == "0900456789"
        assert digest == phone_lookup_digest("0900456789", cipher)
    async with database.sessions() as session:
        admin = await session.get(User, owner_id)
        async with api_test_context(session, [walk_in_sales_router], settings=database.settings) as client:
            headers = auth_headers(admin)
            lookup = await client.post("/v1/admin/walk-in-sales/member-phone", headers=headers, json={"phone": "0900345678"})
            assert lookup.status_code == 200, lookup.text
            assert lookup.json()["matches"][0]["id"] == f"roster:{roster_id}"
            claimed = await client.post("/v1/admin/walk-in-sales/member-phone", headers=headers, json={"phone": "0900456789"})
            assert claimed.status_code == 200 and claimed.json()["matches"][0]["id"].startswith("membership:")
            sale = await client.post("/v1/admin/walk-in-sales", headers=headers, json=payload(phone="0900345678", phone_match_id=f"roster:{roster_id}"))
            assert sale.status_code == 201, sale.text
            order = await session.get(Order, sale.json()["order_id"])
            assert order.verified_counter_roster_entry_id == roster_id and order.member_tax_exempt_snapshot
    with pytest.raises(RuntimeError, match="已有名冊社員現場交易"):
        await database.migrate(command.downgrade, "0067_meal_event_archive")
    assert await version(database) == database.head


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
    assert await version(database) == database.head
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
    assert await version(database) == database.head
    assert {name: await table_rows(database, name) for name in tables} == before_refused_downgrade
    assert await table_rows(database, "users") == [
        {**row, "data_consent_version": None, "data_consented_at": None, "meal_contact_phone_encrypted": None,
         "meal_contact_phone_lookup": None}
        for row in before_users
    ]


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


@pytest.mark.asyncio
async def test_counter_membership_migration_times_out_behind_live_order_reader_and_can_retry(migrated_database):
    database = migrated_database
    previous = "0054_catalog_visibility"
    await database.migrate(command.upgrade, previous)
    async with database.engine.begin() as live_reader:
        await live_reader.execute(text("LOCK TABLE orders IN ACCESS SHARE MODE"))
        with pytest.raises(DBAPIError) as blocked:
            await database.migrate(command.upgrade, "0055_counter_member_pricing")
        assert blocked.value.orig.sqlstate == "55P03"
        assert await live_reader.scalar(text("SELECT version_num FROM alembic_version")) == previous
        assert await live_reader.scalar(text("SELECT count(*) FROM orders")) == 0
    await database.migrate(command.upgrade, "0055_counter_member_pricing")
    assert await version(database) == "0055_counter_member_pricing"


@pytest.mark.asyncio
async def test_payment_storefront_migration_times_out_behind_live_payment_reader_and_can_retry(migrated_database):
    database = migrated_database
    previous = "0055_counter_member_pricing"
    payment_revision = "0056_payment_return_storefront"
    await database.migrate(command.upgrade, previous)
    async with database.engine.begin() as live_reader:
        await live_reader.execute(text("LOCK TABLE payment_attempts IN ACCESS SHARE MODE"))
        with pytest.raises(DBAPIError) as blocked:
            await database.migrate(command.upgrade, payment_revision)
        assert blocked.value.orig.sqlstate == "55P03"
        assert await live_reader.scalar(text("SELECT version_num FROM alembic_version")) == previous
        assert await live_reader.scalar(text("SELECT count(*) FROM payment_attempts")) == 0
    await database.migrate(command.upgrade, payment_revision)
    assert await version(database) == payment_revision
    async with database.engine.connect() as connection:
        assert await connection.scalar(text(
            "SELECT convalidated FROM pg_constraint WHERE conrelid = 'payment_attempts'::regclass "
            "AND conname = 'ck_payment_attempts_browser_return_storefront_supported'"
        )) is False
    await database.migrate(command.downgrade, previous)
    assert await version(database) == previous


@pytest.mark.asyncio
async def test_browser_refresh_downgrade_refuses_active_selection(migrated_database):
    database = migrated_database
    await database.migrate(command.upgrade, "head")
    user_id, session_id, selector_id = str(uuid4()), str(uuid4()), uuid4().hex
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    async with database.engine.begin() as connection:
        await connection.execute(User.__table__.insert().values(
            id=user_id, display_name="隔離登入", password_hash="test",
        ))
        await connection.execute(RefreshSession.__table__.insert().values(
            id=session_id, user_id=user_id, expires_at=expires_at,
        ))
        await connection.execute(BrowserRefreshSelection.__table__.insert().values(
            id=selector_id, family_id=session_id, current_session_id=session_id,
        ))

    with pytest.raises(RuntimeError, match="仍有有效的新版瀏覽器登入"):
        await database.migrate(command.downgrade, "0056_payment_return_storefront")
    assert await version(database) == database.head
    async with database.engine.begin() as connection:
        assert await connection.scalar(select(BrowserRefreshSelection.current_session_id)) == session_id
        assert await connection.scalar(select(RefreshSession.expires_at)) == expires_at
        await connection.execute(update(RefreshSession).values(revoked_at=datetime.now(timezone.utc)))

    await database.migrate(command.downgrade, "0056_payment_return_storefront")
    assert await version(database) == "0056_payment_return_storefront"
    async with database.engine.connect() as connection:
        assert await connection.scalar(select(RefreshSession.id)) == session_id
        assert not await connection.run_sync(lambda sync: inspect(sync).has_table("browser_refresh_selections"))


@pytest.mark.asyncio
async def test_empty_browser_refresh_downgrade_times_out_behind_reader_and_can_retry(migrated_database):
    database = migrated_database
    await database.migrate(command.upgrade, "head")
    async with database.engine.begin() as live_reader:
        await live_reader.execute(text("LOCK TABLE browser_refresh_selections IN ACCESS SHARE MODE"))
        with pytest.raises(DBAPIError) as blocked:
            await database.migrate(command.downgrade, "0056_payment_return_storefront")
        assert blocked.value.orig.sqlstate == "55P03"
        assert await live_reader.scalar(text("SELECT version_num FROM alembic_version")) == database.head
        assert await live_reader.scalar(text("SELECT count(*) FROM browser_refresh_selections")) == 0

    await database.migrate(command.downgrade, "0056_payment_return_storefront")
    assert await version(database) == "0056_payment_return_storefront"
    await database.migrate(command.upgrade, "head")
    assert await version(database) == database.head
