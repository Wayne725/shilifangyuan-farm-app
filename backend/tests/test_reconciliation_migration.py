from alembic import command
import pytest
import sqlalchemy as sa

from tests.test_open_proposal_migration import proposal_migration_db


PREVIOUS = "0044_group_order_contract"
REVISION = "0045_reconciliation_runs"


def test_monitoring_schema_roundtrips_without_changing_existing_business_data(proposal_migration_db):
    database = proposal_migration_db
    command.upgrade(database.config, PREVIOUS)
    before = database.rows("users")
    command.upgrade(database.config, REVISION)
    assert database.rows("users") == before
    checks = database.run(lambda connection: sa.inspect(connection).get_check_constraints("reconciliation_runs"))
    assert {item["name"] for item in checks} >= {"ck_reconciliation_runs_status_valid", "ck_reconciliation_runs_completion_consistent"}
    with pytest.raises(sa.exc.IntegrityError):
        database.insert("reconciliation_runs", source="job", status="not-a-status", phases={}, counts={})
    command.downgrade(database.config, PREVIOUS)
    assert "reconciliation_runs" not in database.run(lambda connection: sa.inspect(connection).get_table_names())
    assert database.rows("users") == before
    command.upgrade(database.config, REVISION)
    database.assert_schema_matches()


def test_monitoring_history_blocks_destructive_downgrade(proposal_migration_db):
    database = proposal_migration_db
    command.upgrade(database.config, REVISION)
    database.insert("reconciliation_runs", source="job", status="running", current_phase="expired_payments", phases={}, counts={})
    before = database.rows("reconciliation_runs")
    users = database.rows("users")
    with pytest.raises(RuntimeError, match="保存監測歷史"):
        command.downgrade(database.config, PREVIOUS)
    assert database.rows("reconciliation_runs") == before
    assert database.rows("users") == users
