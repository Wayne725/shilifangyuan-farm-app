from alembic import command
import sqlalchemy as sa

from tests.test_open_proposal_migration import proposal_migration_db
from tests.test_group_review_migration import NOW


def test_quota_migration_adds_only_ephemeral_table_and_roundtrips(proposal_migration_db):
    database = proposal_migration_db
    command.upgrade(database.config, "0046_reconciliation_retention")
    database.insert("system_settings", key="quota-migration-preserved", value={"enabled": True, "limits": [3, 7]},
                    updated_by_id="migration-author", updated_at=NOW)
    database.insert("reconciliation_runs", id=47, source="periodic", status="succeeded",
                    started_at=NOW, progress_at=NOW, finished_at=NOW, current_phase="completed",
                    deferred_reason=None, phases={"payments": "succeeded"}, counts={"updated": 2})

    def table_names():
        return set(database.run(lambda connection: sa.inspect(connection).get_table_names()))

    def snapshot():
        result = {}
        for name in ["users", "system_settings", "reconciliation_runs"]:
            table = database.table(name)
            result[name] = database.run(lambda connection: [dict(row) for row in connection.execute(
                sa.select(table).order_by(*table.primary_key.columns)).mappings()])
        return result

    original_tables = table_names()
    before = snapshot()
    command.upgrade(database.config, "0047_credential_rate_limits")
    assert table_names() == original_tables | {"credential_rate_limit_buckets"}
    table = database.table("credential_rate_limit_buckets")
    assert set(table.columns.keys()) == {"key_hash", "hits", "expires_at"}
    assert database.run(lambda connection: sa.inspect(connection).get_pk_constraint(table.name)["constrained_columns"]) == ["key_hash"]
    indexes = database.run(lambda connection: sa.inspect(connection).get_indexes(table.name))
    assert next(value for value in indexes if value["name"] == "ix_credential_rate_limit_expiry_key")["column_names"] == ["expires_at", "key_hash"]
    database.insert(table.name, key_hash="a" * 64, hits=[1, 2], expires_at=NOW)
    assert snapshot() == before
    command.downgrade(database.config, "0046_reconciliation_retention")
    assert not database.run(lambda connection: sa.inspect(connection).has_table(table.name))
    assert table_names() == original_tables
    assert snapshot() == before
    command.upgrade(database.config, "0047_credential_rate_limits")
    assert table_names() == original_tables | {table.name}
    assert database.run(lambda connection: connection.scalar(sa.select(sa.func.count()).select_from(table))) == 0
    assert snapshot() == before

    # Latest-head comparison is separate from the 0047 roundtrip contract.
    command.upgrade(database.config, "0052_business_meal_orders")
    assert snapshot() == before
    tables_before_policy = table_names()
    visibility_columns = {name: set(database.table(name).columns.keys()) for name in ["activities", "group_campaigns"]}
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
    command.upgrade(database.config, "0055_counter_member_pricing")
    assert table_names() == tables_before_policy | {policies.name, "walk_in_request_guards"}
    database.assert_schema_matches()
    assert snapshot() == {**expected, "users": [
        {**row, "meal_contact_phone_encrypted": None} for row in expected["users"]
    ]}
