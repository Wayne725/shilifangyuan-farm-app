from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import inspect, text

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, seed_user


def test_browser_refresh_downgrade_refuses_active_sqlite_selection(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'browser-refresh.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    try:
        command.upgrade(database.config, "head")
        seed_user(database)
        now = datetime.now(timezone.utc)
        database.insert("refresh_sessions", id="migration-session", user_id="migration-admin",
                        created_at=now, expires_at=now + timedelta(days=1))
        database.insert("browser_refresh_selections", id="a" * 32, family_id="migration-session",
                        current_session_id="migration-session")
        with pytest.raises(RuntimeError, match="仍有有效的新版瀏覽器登入"):
            command.downgrade(database.config, "0056_payment_return_storefront")
        assert database.run(lambda conn: conn.scalar(text("SELECT version_num FROM alembic_version"))) == "0057_browser_refresh_recovery"
        assert database.rows("browser_refresh_selections")[0]["current_session_id"] == "migration-session"

        database.run(lambda conn: conn.execute(text(
            "UPDATE refresh_sessions SET expires_at = CURRENT_TIMESTAMP WHERE id = 'migration-session'"
        )))
        command.downgrade(database.config, "0056_payment_return_storefront")
        assert database.run(lambda conn: conn.scalar(text("SELECT version_num FROM alembic_version"))) == "0056_payment_return_storefront"
        assert not database.run(lambda conn: inspect(conn).has_table("browser_refresh_selections"))
        assert database.rows("refresh_sessions")[0]["id"] == "migration-session"
    finally:
        get_settings.cache_clear()
