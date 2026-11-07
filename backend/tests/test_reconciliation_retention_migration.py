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
    def snapshot():
        result = {}
        for name in ("reconciliation_runs", "users", "system_settings"):
            table = database.table(name)
            result[name] = database.run(lambda connection: [dict(row) for row in connection.execute(
                sa.select(table).order_by(*table.primary_key.columns)).mappings()])
        return result

    before = snapshot()
    command.upgrade(database.config, "0046_reconciliation_retention")
    indexes = database.run(lambda connection: sa.inspect(connection).get_indexes("reconciliation_runs"))
    index = next(index for index in indexes if index["name"] == "ix_reconciliation_runs_finished_id")
    assert index["column_names"] == ["finished_at", "id"] and index["dialect_options"]
    assert snapshot() == before
    command.downgrade(database.config, "0045_reconciliation_runs")
    assert snapshot() == before
    command.upgrade(database.config, "0046_reconciliation_retention")
    database.assert_schema_matches()
    assert snapshot() == before
