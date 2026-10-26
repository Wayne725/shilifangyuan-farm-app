import asyncio
import itertools
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
import time_machine
from alembic import command
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, NOW, seed_payment, seed_user


PREVIOUS = "0037_group_review_window"
REVISION = "0038_activity_payments"
PG_URL = os.environ.get("ACTIVITY_PAYMENT_MIGRATION_POSTGRES_URL")


class ActivityMigrationDatabase(MigrationDatabase):
    def upgrade(self):
        command.upgrade(self.config, REVISION)

    def run(self, operation):
        async def execute():
            engine = create_async_engine(self.url)
            if self.url.startswith("sqlite"):
                @sa.event.listens_for(engine.sync_engine, "connect")
                def enable_foreign_keys(connection, _record):
                    cursor = connection.cursor()
                    cursor.execute("PRAGMA foreign_keys=ON")
                    cursor.close()
            try:
                async with engine.begin() as connection:
                    return await connection.run_sync(operation)
            finally:
                await engine.dispose()
        return asyncio.run(execute())


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def activity_migration_db(request, tmp_path, monkeypatch):
    database_name = None
    if request.param == "postgres":
        import asyncpg
        base_url = make_url(PG_URL)
        if base_url.host not in {"localhost", "127.0.0.1"} or base_url.database != "activity_payment_migration_test":
            pytest.fail("Use the isolated local activity_payment_migration_test database URL")
        database_name = f"activity_payment_migration_test_{uuid4().hex[:12]}"
        admin_url = base_url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)

        async def database_command(sql):
            connection = await asyncpg.connect(admin_url)
            try:
                await connection.execute(sql)
            finally:
                await connection.close()

        asyncio.run(database_command(f'CREATE DATABASE "{database_name}"'))
        url = base_url.set(drivername="postgresql+asyncpg", database=database_name).render_as_string(hide_password=False)
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'activity-payment.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = ActivityMigrationDatabase(url)
    try:
        with time_machine.travel(NOW, tick=True):
            command.upgrade(database.config, PREVIOUS)
            yield database
    finally:
        get_settings.cache_clear()
        if database_name is not None:
            asyncio.run(database_command(f'DROP DATABASE "{database_name}"'))


def seed_activity(database, key="activity", status="published", registration_status="registered"):
    database.insert("activities", id=key, created_by_id="migration-admin", title="歷史免費活動",
                    description="保留活動內容", location="合作社", starts_at=NOW + timedelta(days=2),
                    ends_at=NOW + timedelta(days=2, hours=2), registration_deadline=NOW + timedelta(days=1),
                    capacity=10, waitlist_enabled=True, status=status, created_at=NOW, updated_at=NOW)
    database.insert("activity_registrations", id=f"registration-{key}", activity_id=key, user_id="migration-admin",
                    status=registration_status, queue_position=1, registered_at=NOW,
                    cancelled_at=NOW if registration_status == "cancelled" else None,
                    checked_in_at=NOW if registration_status == "attended" else None)


def seed_membership(database):
    database.insert("membership_applications", id="application", user_id="migration-admin", status="approved", created_at=NOW, updated_at=NOW)
    database.insert("memberships", id="membership", user_id="migration-admin", application_id="application", status="active",
                    share_capital_amount=1000, share_count=10, created_at=NOW, updated_at=NOW)
    database.insert("membership_fee_schedules", id="fee", charge_kind="admission_fee", amount=100,
                    effective_from=NOW.date(), is_active=True, created_at=NOW)
    database.insert("membership_charges", id="member-charge", user_id="migration-admin", application_id="application",
                    membership_id="membership", fee_schedule_id="fee", charge_kind="admission_fee", amount=100,
                    status="paid", created_at=NOW, updated_at=NOW)


def charge_values(key="charge", **overrides):
    values = dict(id=key, number=f"AC-{key}", activity_id="activity", user_id="migration-admin",
                  registration_id="registration-activity", registration_revision=1, activity_title="活動名稱快照",
                  contact_name="報名人", contact_email="participant@example.com", membership_type="member", amount=100,
                  expires_at=NOW + timedelta(hours=24), created_at=NOW, updated_at=NOW)
    values.update(overrides)
    return values


def payment_values(key, **subjects):
    return dict(id=key, provider="raygate", merchant_trade_no=f"PAY-{key}", amount=100, status="pending",
                checkout_payload={}, provider_response={}, expires_at=NOW + timedelta(hours=1),
                created_at=NOW, updated_at=NOW, **subjects)


def refund_values(key, **subjects):
    return dict(id=key, amount=100, status="pending", provider="raygate", provider_response={},
                reason="整筆退款", requested_by_id="migration-admin", created_at=NOW, **subjects)


def request_values(key, attempt_id):
    return dict(id=key, activity_charge_id="charge", payment_attempt_id=attempt_id, kind="cancellation",
                amount=100, reason="申請取消", requested_by_id="migration-admin", created_at=NOW, updated_at=NOW)


def update(database, table_name, **values):
    table = database.table(table_name)
    database.run(lambda connection: connection.execute(table.update().values(**values)))


def test_empty_database_roundtrip_and_schema(activity_migration_db):
    database = activity_migration_db
    database.upgrade()
    database.assert_schema_matches()
    assert database.rows("activity_charges") == database.rows("activity_refund_requests") == []
    command.downgrade(database.config, PREVIOUS)
    database.upgrade()
    database.assert_schema_matches()
    command.downgrade(database.config, "base")
    database.upgrade()
    database.assert_schema_matches()


def test_legacy_free_activity_and_finance_are_preserved(activity_migration_db):
    database = activity_migration_db
    seed_user(database)
    for status in ("published", "cancelled", "completed", "pending_review"):
        seed_activity(database, status, status, "attended" if status == "completed" else "waitlisted")
    seed_membership(database)
    seed_payment(database, "regular", refund_purpose="order_cancellation")
    database.insert("payment_attempts", **payment_values("membership", membership_charge_id="member-charge"))
    database.insert("refunds", **refund_values("membership", membership_charge_id="member-charge", payment_attempt_id="membership"))
    tables = ("activities", "activity_registrations", "orders", "membership_charges", "payment_attempts", "refunds", "outbox_events", "admin_audits")
    before = {name: database.rows(name) for name in tables}
    database.upgrade()
    for name in tables:
        current = database.rows(name)
        assert [{key: row[key] for key in previous} for row, previous in zip(current, before[name])] == before[name]
        assert len(current) == len(before[name])
    assert all(row["member_price"] == row["non_member_price"] == 0 and row["payment_window_hours"] == 24 for row in database.rows("activities"))
    assert all(row["revision"] == 1 and row["contact_name"] == "" and row["contact_email"] is None for row in database.rows("activity_registrations"))
    assert database.rows("activity_charges") == database.rows("activity_refund_requests") == []
    command.downgrade(database.config, PREVIOUS)
    for name in tables:
        assert database.rows(name) == before[name]
    database.upgrade()
    assert database.rows("activity_charges") == []


def test_exactly_one_of_three_payment_and_refund_subjects(activity_migration_db):
    database = activity_migration_db
    seed_user(database)
    seed_activity(database)
    seed_membership(database)
    seed_payment(database, "regular")
    database.upgrade()
    database.insert("activity_charges", **charge_values())
    subject_ids = {"order_id": "order-regular", "membership_charge_id": "member-charge", "activity_charge_id": "charge"}
    for bits in itertools.product((False, True), repeat=3):
        key = "".join(str(int(bit)) for bit in bits)
        subjects = {name: value if selected else None for (name, value), selected in zip(subject_ids.items(), bits)}
        for table, factory in (("payment_attempts", payment_values), ("refunds", refund_values)):
            if sum(bits) == 1:
                database.insert(table, **factory(key, **subjects))
            else:
                with pytest.raises(sa.exc.IntegrityError):
                    database.insert(table, **factory(key, **subjects))


def test_registration_versions_multiple_captures_and_financial_deletion_guards(activity_migration_db):
    database = activity_migration_db
    seed_user(database)
    seed_activity(database)
    database.upgrade()
    update(database, "activity_registrations", status="pending_payment")
    assert database.rows("activity_registrations")[0]["status"] == "pending_payment"
    database.insert("activity_charges", **charge_values())
    with pytest.raises(sa.exc.IntegrityError):
        database.insert("activity_charges", **charge_values("duplicate"))
    database.insert("activity_charges", **charge_values("second-version", registration_revision=2))
    for key in ("first", "late"):
        database.insert("payment_attempts", **payment_values(key, activity_charge_id="charge"))
        database.insert("activity_refund_requests", **request_values(f"request-{key}", key))
    with pytest.raises(sa.exc.IntegrityError):
        database.insert("activity_refund_requests", **request_values("duplicate", "first"))
    database.insert("refunds", **refund_values("approved", activity_charge_id="charge", payment_attempt_id="first"))
    with pytest.raises(sa.exc.IntegrityError):
        database.insert("refunds", **refund_values("repeat", activity_charge_id="charge", payment_attempt_id="first"))
    for table_name, key in (("activities", "activity"), ("activity_registrations", "registration-activity"),
                            ("users", "migration-admin"), ("activity_charges", "charge"), ("payment_attempts", "first")):
        table = database.table(table_name)
        with pytest.raises(sa.exc.IntegrityError):
            database.run(lambda connection: connection.execute(table.delete().where(table.c.id == key)))
    assert len(database.rows("activity_charges")) == 2
    assert len(database.rows("activity_refund_requests")) == 2
    with pytest.raises(RuntimeError, match="無法安全降版"):
        command.downgrade(database.config, PREVIOUS)
    assert len(database.rows("activity_charges")) == 2


def test_price_time_revision_and_review_constraints(activity_migration_db):
    database = activity_migration_db
    seed_user(database)
    seed_activity(database)
    database.upgrade()
    for values in ({"member_price": -1}, {"non_member_price": -1}, {"payment_window_hours": 0}, {"payment_window_hours": 169}):
        with pytest.raises(sa.exc.IntegrityError):
            update(database, "activities", **values)
    with pytest.raises(sa.exc.IntegrityError):
        update(database, "activity_registrations", revision=0)
    for values in ({"amount": 0}, {"registration_revision": 0}):
        with pytest.raises(sa.exc.IntegrityError):
            database.insert("activity_charges", **charge_values(**values))
    database.insert("activity_charges", **charge_values())
    database.insert("payment_attempts", **payment_values("first", activity_charge_id="charge"))
    for values in ({"amount": 0}, {"version": 0}, {"status": "completed"}):
        with pytest.raises(sa.exc.IntegrityError):
            database.insert("activity_refund_requests", **{**request_values("request", "first"), **values})
    database.insert("activity_refund_requests", **{**request_values("request", "first"), "status": "externally_refunded"})
    assert database.rows("activity_refund_requests")[0]["status"] == "externally_refunded"


@pytest.mark.parametrize("table_name,values", [
    ("activities", {"member_price": 100}),
    ("activities", {"non_member_price": 100}),
    ("activities", {"payment_window_hours": 48}),
    ("activity_registrations", {"status": "pending_payment"}),
    ("activity_registrations", {"revision": 2}),
    ("activity_registrations", {"contact_name": "新報名人"}),
    ("activity_registrations", {"contact_email": "contact@example.com"}),
])
def test_downgrade_rejects_new_settings_or_registration_snapshots(activity_migration_db, table_name, values):
    database = activity_migration_db
    seed_user(database)
    seed_activity(database)
    database.upgrade()
    update(database, table_name, **values)
    before = database.rows(table_name)
    with pytest.raises(RuntimeError, match="升版前備份"):
        command.downgrade(database.config, PREVIOUS)
    assert database.rows(table_name) == before
    assert database.run(lambda connection: connection.scalar(sa.text("SELECT version_num FROM alembic_version"))) == REVISION
