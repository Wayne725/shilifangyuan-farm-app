from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import get_settings


def test_nullable_email_preserves_accounts_and_blocks_unsafe_rollback(tmp_path, monkeypatch):
    path = tmp_path / "line-customer-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{path}")
    insert_user = text("""INSERT INTO users
        (id, email, display_name, password_hash, user_role, membership_type, is_active,
         created_at, updated_at, token_version, pending_member_claim)
        VALUES (:id, :email, '保留帳號', 'unchanged-hash', 'customer', 'nonmember', 1,
                '2026-10-15 00:00:00', '2026-10-15 00:00:00', 0, 0)""")
    try:
        command.upgrade(config, "0021_line_login")
        with engine.begin() as connection:
            connection.execute(insert_user, {"id": "old", "email": "old@example.com"})
            connection.execute(text("""INSERT INTO external_identities
                (id, user_id, provider, subject, registration_intent, verified_at, last_login_at)
                VALUES ('existing-line', 'old', 'line', 'U-old', 'nonmember',
                        '2026-10-15 00:00:00', '2026-10-15 00:00:00')"""))
        command.upgrade(config, "head")
        command.check(config)
        columns = {column["name"]: column for column in inspect(engine).get_columns("users")}
        assert columns["email"]["nullable"] and columns["notification_email"]["nullable"]
        with engine.begin() as connection:
            assert connection.execute(text("SELECT email, password_hash FROM users WHERE id='old'")).one() == ("old@example.com", "unchanged-hash")
            assert connection.execute(text("SELECT user_id FROM external_identities WHERE id='existing-line'")).scalar_one() == "old"
            for identifier in ("new-a", "new-b"):
                connection.execute(insert_user, {"id": identifier, "email": None})
        with pytest.raises(RuntimeError, match="未填Email"):
            command.downgrade(config, "0021_line_login")
        with engine.begin() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM users")).scalar_one() == 3
            connection.execute(text("DELETE FROM users WHERE email IS NULL"))
        command.downgrade(config, "0021_line_login")
        assert not next(column for column in inspect(engine).get_columns("users") if column["name"] == "email")["nullable"]
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        get_settings.cache_clear()
