import asyncio
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
import time_machine
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database import Base


BACKEND = Path(__file__).resolve().parents[1]
PREVIOUS = "0036_meeting_categories"
REVISION = "0037_group_review_window"
NOW = datetime(2030, 1, 2, 4, tzinfo=timezone.utc)
PG_URL = os.environ.get("GROUP_REVIEW_MIGRATION_POSTGRES_URL")


def _migration():
    spec = importlib.util.spec_from_file_location("group_review_migration", BACKEND / "alembic/versions/0037_group_review_window.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationDatabase:
    def __init__(self, url):
        self.url = url
        self.config = Config(str(BACKEND / "alembic.ini"))
        self.config.set_main_option("script_location", str(BACKEND / "alembic"))

    def run(self, operation):
        async def execute():
            engine = create_async_engine(self.url)
            try:
                async with engine.begin() as connection:
                    return await connection.run_sync(operation)
            finally:
                await engine.dispose()
        return asyncio.run(execute())

    def table(self, name):
        return self.run(lambda connection: sa.Table(name, sa.MetaData(), autoload_with=connection))

    def rows(self, name):
        table = self.table(name)
        return self.run(lambda connection: [dict(row) for row in connection.execute(sa.select(table).order_by(table.c.id)).mappings()])

    def insert(self, name, **values):
        table = self.table(name)
        self.run(lambda connection: connection.execute(table.insert().values(**values)))

    def upgrade(self):
        command.upgrade(self.config, REVISION)

    def assert_schema_matches(self):
        command.upgrade(self.config, "head")
        if self.url.startswith("sqlite"):
            command.check(self.config)
            return
        differences = self.run(lambda connection: compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata))
        flattened = [operation for difference in differences
                     for operation in (difference if isinstance(difference, list) else [difference])]
        assert flattened == []


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def migration_db(request, tmp_path, monkeypatch):
    database_name = None
    if request.param == "postgres":
        import asyncpg
        base_url = make_url(PG_URL)
        if base_url.host not in {"localhost", "127.0.0.1"} or base_url.database != "group_review_migration_test":
            pytest.fail("Migration tests require the isolated local group_review_migration_test database URL")
        database_name = f"group_review_migration_test_{uuid4().hex[:12]}"
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
        url = f"sqlite+aiosqlite:///{tmp_path / 'group-review.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    try:
        with time_machine.travel(NOW, tick=True):
            command.upgrade(database.config, PREVIOUS)
            yield database
    finally:
        get_settings.cache_clear()
        if database_name is not None:
            asyncio.run(database_command(f'DROP DATABASE "{database_name}"'))


def seed_user(database):
    database.insert("users", id="migration-admin", email="migration@example.test", display_name="遷移管理員",
                    password_hash="test", user_role="admin", membership_type="nonmember", is_active=True,
                    token_version=0, pending_member_claim=False, created_at=NOW, updated_at=NOW)


def seed_campaign(database, key, **overrides):
    values = dict(id=key, target_type="product", target_id="historical-product", title=f"歷史共同購買 {key}",
                  description="原始內容", member_price=100, nonmember_price=100, min_paid_quantity=10,
                  supply_cap=20, per_user_cap=5, paid_quantity=10, reserved_quantity=0,
                  deadline=NOW + timedelta(days=2), estimated_pickup_start=NOW + timedelta(days=8),
                  estimated_pickup_end=NOW + timedelta(days=9), decision_status="recruiting", intake_status="open",
                  threshold_version=7, can_ship=False, allowed_shipping_channels=[], created_by_id="migration-admin",
                  created_at=NOW - timedelta(days=10), updated_at=NOW - timedelta(days=1))
    values.update(overrides)
    database.insert("group_campaigns", **values)


def seed_payment(database, key, campaign_id=None, *, refund_purpose=None):
    database.insert("orders", id=f"order-{key}", order_number=f"MIG-{key}", order_kind="group" if campaign_id else "regular",
                    sales_channel="group" if campaign_id else "regular", fulfillment_method="cooperative_pickup",
                    user_id="migration-admin", group_campaign_id=campaign_id, membership_type_snapshot="nonmember",
                    amount_total=100, tax_amount=5, contact_email="migration@example.test", invoice_buyer_type="personal",
                    invoice_carrier_type="cloud", fulfillment_status="pending_confirmation", payment_status="pending",
                    invoice_status="not_eligible", created_at=NOW, updated_at=NOW)
    database.insert("payment_attempts", id=f"attempt-{key}", order_id=f"order-{key}", provider="raygate",
                    merchant_trade_no=f"MIG-{key}", amount=100, status="expired", expires_at=NOW - timedelta(hours=1),
                    checkout_payload={"original": key}, provider_response={"historical": key}, created_at=NOW, updated_at=NOW)
    if refund_purpose:
        database.insert("refunds", id=f"refund-{key}", order_id=f"order-{key}", payment_attempt_id=f"attempt-{key}",
                        provider="raygate", provider_response={"original": "evidence"}, amount=100, status="pending",
                        purpose=refund_purpose, reason="歷史單筆退款", requested_by_id="migration-admin", created_at=NOW)


def seed_evidence(database, key, campaign_id, decision, when, *, source="audit", aggregate_type="group_campaign", payload=None):
    common = dict(id=key, aggregate_type=aggregate_type, aggregate_id=campaign_id, created_at=when)
    if source == "audit":
        database.insert("admin_audits", **common, actor_id="migration-admin", action=f"campaign.{decision}",
                        reason="既有管理員決定", data=payload or {})
    else:
        database.insert("outbox_events", **common, event_type=f"group.{decision}", payload=payload or {},
                        status="completed", attempts=1, available_at=when, processed_at=when)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def test_empty_database_can_upgrade_downgrade_reupgrade(migration_db):
    database = migration_db
    database.upgrade()
    database.assert_schema_matches()
    assert database.rows("admin_audits") == []
    command.downgrade(database.config, PREVIOUS)
    database.upgrade()
    database.assert_schema_matches()
    assert set(_migration().CAMPAIGN_COLUMNS) <= set(database.table("group_campaigns").c.keys())
    command.downgrade(database.config, "base")
    database.upgrade()
    database.assert_schema_matches()


def test_backfill_preserves_financial_history_terminal_decisions_and_commitments(migration_db):
    database = migration_db
    seed_user(database)
    seed_campaign(database, "future-pending", decision_status="pending_confirmation", intake_status="paused", confirmation_deadline=NOW + timedelta(days=2))
    seed_campaign(database, "future-full", decision_status="pending_confirmation", intake_status="paused", paid_quantity=18, reserved_quantity=2)
    seed_campaign(database, "past-pending", decision_status="pending_confirmation", intake_status="paused", deadline=NOW - timedelta(days=1),
                  confirmation_deadline=NOW - timedelta(hours=1), estimated_pickup_start=NOW - timedelta(hours=2), estimated_pickup_end=NOW - timedelta(hours=1))
    seed_campaign(database, "past-later-review", deadline=NOW - timedelta(days=1), confirmation_deadline=NOW + timedelta(days=8))
    seed_campaign(database, "confirmed", decision_status="confirmed", intake_status="open", confirmed_at=NOW - timedelta(days=1),
                  final_pickup_at=NOW + timedelta(days=3), confirmation_deadline=NOW + timedelta(days=2))
    for status in ("cancelled", "rejected", "failed_unmet", "expired_unconfirmed"):
        seed_campaign(database, status, decision_status=status, intake_status="closed", deadline=NOW - timedelta(days=1))
    seed_payment(database, "obsolete", "past-pending", refund_purpose="obsolete_order_revision")
    seed_payment(database, "individual", "past-later-review", refund_purpose="order_cancellation")
    before = {row["id"]: row for row in database.rows("group_campaigns")}
    finance_before = {table: database.rows(table) for table in ("orders", "payment_attempts", "refunds", "outbox_events")}
    started = datetime.now(timezone.utc)
    database.upgrade()
    finished = datetime.now(timezone.utc)
    rows = {row["id"]: row for row in database.rows("group_campaigns")}
    assert (rows["future-pending"]["decision_status"], rows["future-pending"]["intake_status"]) == ("recruiting", "open")
    assert rows["future-full"]["intake_status"] == "full"
    assert aware(rows["future-pending"]["confirmation_deadline"]) == NOW + timedelta(days=5)
    for key in ("past-pending", "past-later-review"):
        assert (rows[key]["decision_status"], rows[key]["intake_status"]) == ("recruiting", "settling")
    assert started + timedelta(hours=72) <= aware(rows["past-pending"]["confirmation_deadline"]) <= finished + timedelta(hours=72)
    assert aware(rows["past-later-review"]["confirmation_deadline"]) == NOW + timedelta(days=8)
    for key, row in rows.items():
        assert {field: row[field] for field in ("deadline", "estimated_pickup_start", "estimated_pickup_end", "final_pickup_at", "paid_quantity", "reserved_quantity", "updated_at", "threshold_version")} == {
            field: before[key][field] for field in ("deadline", "estimated_pickup_start", "estimated_pickup_end", "final_pickup_at", "paid_quantity", "reserved_quantity", "updated_at", "threshold_version")}
        if key in ("confirmed", "cancelled", "rejected", "failed_unmet", "expired_unconfirmed"):
            assert {field: row[field] for field in before[key]} == before[key]
        assert row["legacy_confirmed_intake"] == (key == "confirmed")
        assert row["confirmed_paid_quantity"] is None and row["confirmed_min_paid_quantity"] is None
    for table, expected in finance_before.items():
        current = database.rows(table)
        assert [{key: row[key] for key in original} for row, original in zip(current, expected)] == expected
        assert len(current) == len(expected)
    audits = {row["aggregate_id"]: row for row in database.rows("admin_audits")}
    assert len(audits) == 5
    assert audits["past-pending"]["data"]["requires_manual_review"] is True
    assert "pickup_time_has_passed" in audits["past-pending"]["data"]["issues"]
    assert "pickup_conflicts_with_review_deadline" in audits["past-later-review"]["data"]["issues"]
    database.run(lambda connection: _migration()._backfill_campaigns(connection, NOW + timedelta(days=30)))
    assert {row["id"]: row for row in database.rows("group_campaigns")} == rows
    assert len(database.rows("admin_audits")) == 5
    with pytest.raises(RuntimeError, match="恢復自動退款"):
        command.downgrade(database.config, PREVIOUS)


def test_latest_explicit_termination_repairs_only_nonterminal_campaigns(migration_db):
    database = migration_db
    seed_user(database)
    for key, status in (("repair", "recruiting"), ("keep-confirmed", "confirmed")):
        seed_campaign(database, key, decision_status=status)
        seed_evidence(database, f"cancel-{key}", key, "cancelled", NOW - timedelta(days=2))
        seed_evidence(database, f"reject-{key}", key, "rejected", NOW - timedelta(days=1), source="outbox")
    old_outbox = database.rows("outbox_events")
    database.upgrade()
    rows = {row["id"]: row for row in database.rows("group_campaigns")}
    assert (rows["repair"]["decision_status"], rows["repair"]["intake_status"]) == ("rejected", "closed")
    assert rows["keep-confirmed"]["decision_status"] == "confirmed"
    assert rows["keep-confirmed"]["intake_status"] == "open"
    audit = next(row for row in database.rows("admin_audits") if row["action"] == _migration().AUDIT_ACTION and row["aggregate_id"] == "repair")
    assert audit["data"]["before"]["decision_status"] == "recruiting"
    assert audit["data"]["termination_evidence"][0]["id"] == "reject-repair"
    assert database.rows("outbox_events") == old_outbox
    assert database.rows("refunds") == []


@pytest.mark.parametrize("conflict", ["same-time", "wrong-aggregate", "wrong-payload"])
def test_ambiguous_termination_aborts_before_schema_or_data_changes(migration_db, conflict):
    database = migration_db
    seed_user(database)
    seed_campaign(database, "ambiguous", decision_status="pending_confirmation", intake_status="paused")
    seed_evidence(database, "cancel", "ambiguous", "cancelled", NOW,
                  aggregate_type="order" if conflict == "wrong-aggregate" else "group_campaign",
                  payload={"campaign_id": "other"} if conflict == "wrong-payload" else None)
    if conflict == "same-time":
        seed_evidence(database, "reject", "ambiguous", "rejected", NOW, source="outbox")
    before = database.rows("group_campaigns")
    with pytest.raises(RuntimeError, match="人工核對：ambiguous"):
        database.upgrade()
    assert database.rows("group_campaigns") == before
    assert "legacy_confirmed_intake" not in database.table("group_campaigns").c
    version = database.run(lambda connection: connection.scalar(sa.text("SELECT version_num FROM alembic_version")))
    assert version == PREVIOUS
    assert not any(row["action"] == _migration().AUDIT_ACTION for row in database.rows("admin_audits"))


@pytest.mark.parametrize("field", ["unpaid_verified_at", "settlement_unknown_since"])
def test_downgrade_preserves_payment_verification_evidence(migration_db, field):
    database = migration_db
    seed_user(database)
    seed_payment(database, "verification")
    database.upgrade()
    attempts = database.table("payment_attempts")
    database.run(lambda connection: connection.execute(attempts.update().values(**{field: NOW})))
    with pytest.raises(RuntimeError, match="付款核對紀錄"):
        command.downgrade(database.config, PREVIOUS)
    assert aware(database.rows("payment_attempts")[0][field]) == NOW
