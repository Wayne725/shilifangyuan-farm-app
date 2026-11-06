import pytest
import sqlalchemy as sa
from alembic import command

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, NOW


def test_proposal_submission_migration_preserves_history_and_retry_keys(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'proposal-submission.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    try:
        command.upgrade(database.config, "0040_membership_admission")
        database.insert("users", id="author", email="proposal-migration@example.com", display_name="社員", password_hash="unchanged",
                        user_role="customer", membership_type="nonmember", is_active=True, token_version=0,
                        pending_member_claim=False, created_at=NOW, updated_at=NOW)
        database.insert("member_proposals", id="history", created_by_id="author", title="歷史提案", body="保留內容", status="draft",
                        proposal_type="resolution", minimum_voters=10, created_at=NOW, updated_at=NOW)
        before = database.rows("member_proposals")[0]
        command.upgrade(database.config, "head")
        command.check(database.config)
        after = database.rows("member_proposals")[0]
        assert all(after[key] == value for key, value in before.items())
        assert after["client_request_id"] is None and after["request_fingerprint"] is None
        command.downgrade(database.config, "0040_membership_admission")
        assert database.rows("member_proposals")[0] == before
        command.upgrade(database.config, "head")
        table = database.table("member_proposals")
        database.run(lambda connection: connection.execute(table.update().where(table.c.id == "history").values(
            client_request_id="original-request", request_fingerprint="f" * 64,
        )))
        with pytest.raises(RuntimeError, match="防重複保護"):
            command.downgrade(database.config, "0040_membership_admission")
        assert database.rows("member_proposals")[0]["client_request_id"] == "original-request"
        with pytest.raises(sa.exc.IntegrityError):
            database.insert("member_proposals", **{**database.rows("member_proposals")[0], "id": "duplicate"})
        assert len(database.rows("member_proposals")) == 1
    finally:
        get_settings.cache_clear()
