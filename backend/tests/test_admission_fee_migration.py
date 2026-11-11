from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import inspect, text

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, seed_user


PREVIOUS = "0057_browser_refresh_recovery"
REVISION = "0058_admission_fee_snapshot"


def test_existing_round_defaults_and_guarded_downgrade(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'admission-fees.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    now = datetime.now(timezone.utc)
    try:
        command.upgrade(database.config, PREVIOUS)
        seed_user(database)
        database.insert("membership_admission_rounds", id="original-round", user_id="migration-admin",
                        idempotency_key="original-start", round_number=1, status="draft",
                        requested_share_count=12, created_at=now, updated_at=now)
        command.upgrade(database.config, REVISION)
        row = database.rows("membership_admission_rounds")[0]
        assert (row["admission_fee_amount"], row["admission_fee_version"], row["share_unit_amount"]) == (500, 0, 100)
        assert (row["id"], row["requested_share_count"], row["status"]) == ("original-round", 12, "draft")
        command.downgrade(database.config, PREVIOUS)
        assert "admission_fee_amount" not in database.run(lambda conn: {
            column["name"] for column in inspect(conn).get_columns("membership_admission_rounds")
        })
        command.upgrade(database.config, REVISION)
        database.insert("system_settings", key="membership_admission_fee",
                        value={"admission_fee_amount": 750, "version": 1}, updated_at=now)
        with pytest.raises(RuntimeError, match="已有新版入社費設定或申請金額快照"):
            command.downgrade(database.config, PREVIOUS)
        assert database.run(lambda conn: conn.scalar(text("SELECT version_num FROM alembic_version"))) == REVISION
        assert database.rows("membership_admission_rounds")[0]["admission_fee_amount"] == 500
    finally:
        get_settings.cache_clear()
