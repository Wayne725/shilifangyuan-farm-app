from datetime import timedelta
from types import SimpleNamespace

import pytest
import time_machine
from alembic import command

from app.config import get_settings
from app.group_review import group_contract_fingerprint
from tests.test_group_review_migration import MigrationDatabase, NOW, seed_campaign, seed_payment, seed_user


PREVIOUS = "0071_business_pickup_locations"
REVISION = "0072_monthly_group_preorders"


@pytest.fixture
def preorder_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'monthly-preorder.db'}")
    get_settings.cache_clear()
    database = MigrationDatabase(get_settings().database_url)
    try:
        with time_machine.travel(NOW, tick=True):
            command.upgrade(database.config, PREVIOUS)
            yield database
    finally:
        get_settings.cache_clear()


def test_preorder_migration_preserves_orders_contracts_and_closed_history(preorder_migration):
    database = preorder_migration
    seed_user(database)
    active = {"open": "recruiting", "paused": "recruiting", "review": "pending_confirmation", "confirmed": "confirmed"}
    for key, decision in active.items():
        seed_campaign(database, key, decision_status=decision,
                      intake_status="paused" if key == "paused" else "closed" if key in {"review", "confirmed"} else "open",
                      paid_quantity=3, reserved_quantity=2, min_paid_quantity=15,
                      deadline=NOW + timedelta(days=2) if key in {"open", "paused"} else NOW - timedelta(days=1))
    seed_campaign(database, "confirmed-history", decision_status="confirmed", intake_status="closed",
                  deadline=NOW - timedelta(days=5), estimated_pickup_start=NOW - timedelta(days=2),
                  estimated_pickup_end=NOW - timedelta(days=1), final_pickup_at=NOW - timedelta(days=3))
    seed_campaign(database, "final-ended-before-estimate", decision_status="confirmed", intake_status="closed",
                  deadline=NOW - timedelta(days=5), estimated_pickup_start=NOW + timedelta(days=1),
                  estimated_pickup_end=NOW + timedelta(days=2), final_pickup_at=NOW - timedelta(days=1))
    seed_campaign(database, "later-final", decision_status="confirmed", intake_status="closed",
                  deadline=NOW - timedelta(days=5), estimated_pickup_start=NOW - timedelta(days=2),
                  estimated_pickup_end=NOW - timedelta(days=1), final_pickup_at=NOW + timedelta(days=1))
    active["later-final"] = "confirmed"
    terminal = {"cancelled", "rejected", "failed_unmet", "expired_unconfirmed"}
    for decision in terminal:
        seed_campaign(database, decision, decision_status=decision, intake_status="closed")
    seed_payment(database, "active", "open", refund_purpose="obsolete_order_revision")
    seed_payment(database, "cancelled", "cancelled", refund_purpose="order_cancellation")
    before = {row["id"]: row for row in database.rows("group_campaigns")}
    fingerprints = {key: group_contract_fingerprint(SimpleNamespace(**row)) for key, row in before.items()}
    orders = database.table("orders")
    database.run(lambda connection: connection.execute(orders.update().where(orders.c.id == "order-active").values(group_contract_fingerprint=fingerprints["open"])))
    immutable = {name: database.rows(name) for name in ("orders", "payment_attempts", "refunds", "outbox_events", "invoices", "notifications", "admin_audits")}

    command.upgrade(database.config, REVISION)

    after = {row["id"]: row for row in database.rows("group_campaigns")}
    for key, original in before.items():
        row = after[key]
        assert row["preorder_only"] is (key in active)
        if key in active:
            assert row["decision_status"] == "confirmed"
            assert row["legacy_confirmed_intake"] is True
        else:
            assert row["decision_status"] == original["decision_status"]
            assert row["legacy_confirmed_intake"] == original["legacy_confirmed_intake"]
        assert {field: row[field] for field in original if field not in {"decision_status", "legacy_confirmed_intake"}} == {
            field: original[field] for field in original if field not in {"decision_status", "legacy_confirmed_intake"}}
        assert group_contract_fingerprint(SimpleNamespace(**row)) == fingerprints[key]
    for name, rows in immutable.items():
        assert database.rows(name) == rows
    command.upgrade(database.config, REVISION)
    assert database.rows("group_campaigns") == list(after.values())
    assert database.rows("admin_audits") == immutable["admin_audits"]
    command.check(database.config)


def test_preorder_downgrade_keeps_confirmed_intake_and_prevents_lossy_constraint(preorder_migration):
    database = preorder_migration
    seed_user(database)
    seed_campaign(database, "active", min_paid_quantity=15)
    command.upgrade(database.config, REVISION)
    table = database.table("group_campaigns")
    database.run(lambda connection: connection.execute(table.update().values(supply_cap=10)))
    with pytest.raises(RuntimeError):
        command.downgrade(database.config, PREVIOUS)
    assert database.rows("group_campaigns")[0]["preorder_only"] is True
    database.run(lambda connection: connection.execute(table.update().values(supply_cap=20)))
    command.downgrade(database.config, PREVIOUS)
    row = database.rows("group_campaigns")[0]
    assert "preorder_only" not in row
    assert row["decision_status"] == "confirmed" and row["legacy_confirmed_intake"] is True
    command.upgrade(database.config, REVISION)
    assert database.rows("group_campaigns")[0]["preorder_only"] is True
