from alembic import command
import pytest

from tests.test_group_review_migration import NOW, seed_campaign, seed_payment
from tests.test_open_proposal_migration import proposal_migration_db


def seed_legacy(database):
    database.insert("users", id="migration-admin", email="group-contract-migration@example.com", display_name="歷史買家",
        password_hash="test", user_role="customer", membership_type="nonmember", is_active=True,
        token_version=0, pending_member_claim=False, created_at=NOW, updated_at=NOW)
    seed_campaign(database, "legacy-group")
    seed_payment(database, "legacy-group", "legacy-group")


def test_group_contract_upgrade_preserves_legacy_orders_and_safe_roundtrip(proposal_migration_db):
    database = proposal_migration_db
    command.upgrade(database.config, "0043_notification_dedupe")
    seed_legacy(database)
    orders, attempts, campaigns = database.rows("orders"), database.rows("payment_attempts"), database.rows("group_campaigns")
    command.upgrade(database.config, "0044_group_order_contract")
    assert [{key: value for key, value in row.items() if key != "group_contract_fingerprint"} for row in database.rows("orders")] == orders
    assert all(row["group_contract_fingerprint"] is None for row in database.rows("orders"))
    assert database.rows("payment_attempts") == attempts
    assert database.rows("group_campaigns") == campaigns
    command.downgrade(database.config, "0043_notification_dedupe")
    assert database.rows("orders") == orders
    command.upgrade(database.config, "0044_group_order_contract")
    database.assert_schema_matches()


def test_group_contract_downgrade_refuses_silently_losing_new_protection(proposal_migration_db):
    database = proposal_migration_db
    command.upgrade(database.config, "0043_notification_dedupe")
    seed_legacy(database)
    command.upgrade(database.config, "0044_group_order_contract")
    table = database.table("orders")
    database.run(lambda connection: connection.execute(table.update().values(group_contract_fingerprint="a" * 64)))
    before = database.rows("orders")
    with pytest.raises(RuntimeError, match="共同購買合約快照"):
        command.downgrade(database.config, "0043_notification_dedupe")
    assert database.rows("orders") == before
    database.assert_schema_matches()
