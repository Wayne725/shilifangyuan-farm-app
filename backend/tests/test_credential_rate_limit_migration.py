from alembic import command
import sqlalchemy as sa

from tests.test_open_proposal_migration import proposal_migration_db


def test_quota_migration_adds_only_ephemeral_table_and_roundtrips(proposal_migration_db):
    database = proposal_migration_db
    command.upgrade(database.config, "0046_reconciliation_retention")
    def snapshot():
        result = {}
        for name in ["users", "system_settings", "reconciliation_runs"]:
            table = database.table(name)
            result[name] = database.run(lambda connection: [dict(row) for row in connection.execute(sa.select(table)).mappings()])
        return result
    before = snapshot()
    command.upgrade(database.config, "0047_credential_rate_limits")
    table = database.table("credential_rate_limit_buckets")
    assert set(table.columns.keys()) == {"key_hash", "hits", "expires_at"}
    assert database.run(lambda connection: sa.inspect(connection).get_pk_constraint(table.name)["constrained_columns"]) == ["key_hash"]
    indexes = database.run(lambda connection: sa.inspect(connection).get_indexes(table.name))
    assert next(value for value in indexes if value["name"] == "ix_credential_rate_limit_expiry_key")["column_names"] == ["expires_at", "key_hash"]
    assert snapshot() == before
    command.downgrade(database.config, "0046_reconciliation_retention")
    assert not database.run(lambda connection: sa.inspect(connection).has_table(table.name))
    assert snapshot() == before
    command.upgrade(database.config, "0047_credential_rate_limits")
    database.assert_schema_matches()
    assert snapshot() == before
