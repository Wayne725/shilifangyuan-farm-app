from datetime import timedelta

from alembic import command
import sqlalchemy as sa

from tests.test_group_review_migration import NOW
from tests.test_open_proposal_migration import proposal_migration_db


def test_retention_index_preserves_all_rows_across_up_down_up(proposal_migration_db):
    database = proposal_migration_db
    command.upgrade(database.config, "0045_reconciliation_runs")
    database.insert("reconciliation_runs", source="job", status="succeeded", started_at=NOW - timedelta(days=40, minutes=1),
                    progress_at=NOW - timedelta(days=40), finished_at=NOW - timedelta(days=40), phases={}, counts={})
    database.insert("system_settings", key="retention-migration-preserved", value={"enabled": True, "days": 40},
                    updated_by_id="migration-author", updated_at=NOW)

    def table_names():
        return set(database.run(lambda connection: sa.inspect(connection).get_table_names()))

    def index_names():
        return {index["name"] for index in database.run(lambda connection: sa.inspect(connection).get_indexes("reconciliation_runs"))}

    def snapshot():
        result = {}
        for name in ("reconciliation_runs", "users", "system_settings"):
            table = database.table(name)
            result[name] = database.run(lambda connection: [dict(row) for row in connection.execute(
                sa.select(table).order_by(*table.primary_key.columns)).mappings()])
        return result

    before = snapshot()
    original_tables = table_names()
    original_indexes = index_names()
    command.upgrade(database.config, "0046_reconciliation_retention")
    indexes = database.run(lambda connection: sa.inspect(connection).get_indexes("reconciliation_runs"))
    index = next(index for index in indexes if index["name"] == "ix_reconciliation_runs_finished_id")
    assert index["column_names"] == ["finished_at", "id"] and index["dialect_options"]
    assert index_names() == original_indexes | {index["name"]}
    assert table_names() == original_tables
    assert snapshot() == before
    command.downgrade(database.config, "0045_reconciliation_runs")
    assert index_names() == original_indexes
    assert table_names() == original_tables
    assert snapshot() == before
    command.upgrade(database.config, "0046_reconciliation_retention")
    assert index_names() == original_indexes | {index["name"]}
    assert table_names() == original_tables
    assert snapshot() == before

    # Later schema increments do not change the 0046 index-only contract.
    command.upgrade(database.config, "0052_business_meal_orders")
    assert snapshot() == before
    tables_before_policy = table_names()
    visibility_columns = {name: set(database.table(name).columns.keys()) for name in ("activities", "group_campaigns")}
    command.upgrade(database.config, "0053_email_registration_consent")
    assert table_names() == tables_before_policy | {"public_policy_versions"}
    expected = {**before, "users": [
        {**row, "data_consent_version": None, "data_consented_at": None} for row in before["users"]
    ]}
    assert snapshot() == expected
    policies = database.table("public_policy_versions")
    assert set(policies.columns.keys()) == {"version", "content_hash", "content", "published_at", "published_by_id"}
    assert database.run(lambda connection: connection.scalar(sa.select(sa.func.count()).select_from(policies))) == 0
    command.upgrade(database.config, "0054_catalog_visibility")
    assert table_names() == tables_before_policy | {policies.name}
    for name, columns in visibility_columns.items():
        current = database.table(name)
        assert set(current.columns.keys()) == columns | {"is_public"}
        assert current.c.is_public.nullable is False
    assert snapshot() == expected
    database.assert_schema_matches()
    assert snapshot() == expected
