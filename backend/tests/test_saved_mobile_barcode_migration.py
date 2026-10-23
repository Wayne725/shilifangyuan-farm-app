from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.config import get_settings


def test_saved_mobile_barcode_migration_preserves_existing_accounts(tmp_path, monkeypatch):
    path = tmp_path / "saved-barcode-migration.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "0032_product_member_photos")
        with engine.begin() as connection:
            connection.execute(text("""INSERT INTO users
                (id, email, display_name, password_hash, user_role, membership_type,
                 is_active, created_at, updated_at, token_version, pending_member_claim)
                VALUES ('existing', 'existing@example.com', '既有帳號', 'unchanged-hash',
                        'customer', 'nonmember', 1, '2026-10-23 00:00:00',
                        '2026-10-23 00:00:00', 0, 0)"""))
        command.upgrade(config, "0033_saved_mobile_barcode")
        columns = {column["name"]: column for column in inspect(engine).get_columns("users")}
        assert columns["saved_mobile_barcode"]["nullable"]
        with engine.begin() as connection:
            assert connection.execute(text("SELECT saved_mobile_barcode FROM users WHERE id='existing'")).scalar_one() is None
            connection.execute(text("UPDATE users SET saved_mobile_barcode='/AB12+-.' WHERE id='existing'"))
            assert connection.execute(text("SELECT saved_mobile_barcode FROM users WHERE id='existing'")).scalar_one() == "/AB12+-."
        command.downgrade(config, "0032_product_member_photos")
        assert "saved_mobile_barcode" not in {column["name"] for column in inspect(engine).get_columns("users")}
        with engine.connect() as connection:
            assert connection.execute(text("SELECT email, password_hash FROM users WHERE id='existing'")).one() == ("existing@example.com", "unchanged-hash")
        command.upgrade(config, "0033_saved_mobile_barcode")
    finally:
        engine.dispose()
        get_settings.cache_clear()
