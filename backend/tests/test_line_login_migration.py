from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings


def test_line_migration_preserves_users_and_prevents_erasing_linked_accounts(tmp_path, monkeypatch):
    path = tmp_path / "line-login-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "0020_meal_booking_deadline")
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO users (id, email, display_name, password_hash, user_role,
                    membership_type, is_active, created_at, updated_at, token_version, pending_member_claim)
                VALUES ('existing-admin', 'admin@example.com', '既有管理員', 'unchanged-hash',
                    'admin', 'nonmember', 1, '2026-10-01 00:00:00', '2026-10-01 00:00:00', 4, 0),
                    ('existing-customer', 'customer@example.com', '既有使用者', 'unchanged-hash',
                    'customer', 'nonmember', 1, '2026-10-01 00:00:00', '2026-10-01 00:00:00', 0, 0)
            """))
            original = connection.execute(text("SELECT * FROM users")).all()
        command.upgrade(config, "0021_line_login")
        command.check(config)
        assert {"external_identities", "line_oauth_states"}.issubset(set(inspect(engine).get_table_names()))
        with engine.begin() as connection:
            assert connection.execute(text("SELECT * FROM users")).all() == original
            connection.execute(text("""
                INSERT INTO external_identities
                    (id, user_id, provider, subject, registration_intent, verified_at, last_login_at)
                VALUES ('identity-1', 'existing-admin', 'line', 'U-line-1', 'member',
                    '2026-10-13 00:00:00', '2026-10-13 00:00:00')
            """))
        for user_id, subject in (("existing-customer", "U-line-1"), ("existing-admin", "U-line-2")):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text("""
                        INSERT INTO external_identities
                            (id, user_id, provider, subject, registration_intent, verified_at, last_login_at)
                        VALUES ('identity-2', :user_id, 'line', :subject, 'nonmember',
                            '2026-10-13 00:00:00', '2026-10-13 00:00:00')
                    """), {"subject": subject, "user_id": user_id})
        with pytest.raises(RuntimeError, match="已有LINE連結帳號"):
            command.downgrade(config, "0020_meal_booking_deadline")
        with engine.begin() as connection:
            assert connection.execute(text("SELECT * FROM users")).all() == original
            assert connection.execute(text("SELECT COUNT(*) FROM external_identities")).scalar_one() == 1
            connection.execute(text("DELETE FROM external_identities"))
        command.downgrade(config, "0020_meal_booking_deadline")
        assert "external_identities" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM users")).all() == original
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        get_settings.cache_clear()
