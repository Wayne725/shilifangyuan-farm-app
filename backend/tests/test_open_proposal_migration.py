import asyncio
from datetime import timedelta
import os
from uuid import uuid4

from alembic import command
import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, NOW


@pytest.fixture(params=["sqlite"] + (["postgresql"] if os.environ.get("TEST_POSTGRES_URL") else []))
def proposal_migration_db(request, tmp_path, monkeypatch):
    database_name = None
    if request.param == "postgresql":
        base = make_url(os.environ["TEST_POSTGRES_URL"])
        if base.host not in {"localhost", "127.0.0.1"} or base.database != "shilifangyuan_test":
            pytest.fail("Only the disposable local shilifangyuan_test server is allowed")
        database_name = f"open_proposal_migration_{uuid4().hex[:12]}"
        admin_url = base.set(drivername="postgresql").render_as_string(hide_password=False)
        async def execute(sql):
            connection = await asyncpg.connect(admin_url)
            try:
                await connection.execute(sql)
            finally:
                await connection.close()
        asyncio.run(execute(f'CREATE DATABASE "{database_name}"'))
        url = base.set(database=database_name, drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'open-proposals.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    try:
        command.upgrade(database.config, "0041_proposal_submission")
        database.insert("users", id="migration-author", email="proposal-migration@example.com", display_name="買家", password_hash="test",
                        user_role="customer", membership_type="nonmember", is_active=True, token_version=0,
                        pending_member_claim=False, created_at=NOW, updated_at=NOW)
        yield database
    finally:
        get_settings.cache_clear()
        if database_name:
            asyncio.run(execute(f'DROP DATABASE "{database_name}" WITH (FORCE)'))


def proposal(database, key, status, *, target_type="product", target_id="historical-product"):
    database.insert("vote_proposals", id=key, proposer_id="migration-author", target_type=target_type, target_id=target_id,
                    target_name_snapshot="歷史品項", request_details="歷史自訂需求" if target_type == "custom" else None,
                    status=status, threshold=10,
                    deadline=NOW+timedelta(days=1) if status == "voting" else NOW-timedelta(days=1) if status == "ended_unmet" else None,
                    frozen_vote_count=0 if status == "ended_unmet" else None, created_at=NOW, updated_at=NOW)


def test_open_target_index_preserves_closed_custom_history_and_roundtrips(proposal_migration_db):
    database = proposal_migration_db
    for key in ("closed-one", "closed-two"):
        proposal(database, key, "ended_unmet")
    for key in ("custom-one", "custom-two"):
        proposal(database, key, "pending_review", target_type="custom", target_id=None)
    before = database.rows("vote_proposals")
    command.upgrade(database.config, "0042_open_proposal_target")
    assert database.rows("vote_proposals") == before
    proposal(database, "new-open", "pending_review")
    with pytest.raises(sa.exc.IntegrityError):
        proposal(database, "duplicate-open", "voting")
    indexes = database.run(lambda connection: sa.inspect(connection).get_indexes("vote_proposals"))
    assert next(index for index in indexes if index["name"] == "uq_vote_proposals_open_target")["unique"]
    protected_rows = database.rows("vote_proposals")
    command.downgrade(database.config, "0041_proposal_submission")
    assert database.rows("vote_proposals") == protected_rows
    command.upgrade(database.config, "0042_open_proposal_target")
    assert database.rows("vote_proposals") == protected_rows
    database.assert_schema_matches()


def test_legacy_open_duplicates_block_upgrade_without_changing_history(proposal_migration_db):
    database = proposal_migration_db
    for key in ("legacy-open-one", "legacy-open-two"):
        proposal(database, key, "voting")
        database.insert("votes", id=f"vote-{key}", proposal_id=key, user_id="migration-author", estimated_quantity=1, created_at=NOW, updated_at=NOW)
    before, votes = database.rows("vote_proposals"), database.rows("votes")
    with pytest.raises(RuntimeError, match="多筆開放共同購買提案"):
        command.upgrade(database.config, "0042_open_proposal_target")
    assert database.rows("vote_proposals") == before
    assert database.rows("votes") == votes
    indexes = database.run(lambda connection: sa.inspect(connection).get_indexes("vote_proposals"))
    assert "uq_vote_proposals_open_target" not in {index["name"] for index in indexes}
    # Simulate an operator-confirmed closure; the migration itself never changes these rows.
    table = database.table("vote_proposals")
    database.run(lambda connection: connection.execute(table.update().where(table.c.id == "legacy-open-one").values(
        status="ended_unmet", deadline=NOW-timedelta(days=1), frozen_vote_count=1)))
    command.upgrade(database.config, "0042_open_proposal_target")
    assert len(database.rows("vote_proposals")) == 2
    assert database.rows("votes") == votes
