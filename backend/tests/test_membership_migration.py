from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import get_settings


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_customer_number_backfill_skips_admin_and_sequences_by_year(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "membership-migration.db"
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(BACKEND_ROOT / "alembic"),
    )

    try:
        command.upgrade(config, "0004_cooperative_core")
        engine = create_engine(f"sqlite:///{database_path}")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id, email, display_name, password_hash, user_role,
                        membership_type, is_active, email_verified_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, :email, :display_name, 'hash', :user_role,
                        'nonmember', 1, :created_at, :created_at, :created_at
                    )
                    """
                ),
                [
                    {
                        "id": "admin",
                        "email": "admin@example.test",
                        "display_name": "管理員",
                        "user_role": "admin",
                        "created_at": "2025-01-01 00:00:00",
                    },
                    {
                        "id": "customer-b",
                        "email": "b@example.test",
                        "display_name": "買家乙",
                        "user_role": "customer",
                        "created_at": "2025-02-01 00:00:00",
                    },
                    {
                        "id": "customer-a",
                        "email": "a@example.test",
                        "display_name": "買家甲",
                        "user_role": "customer",
                        "created_at": "2025-01-15 00:00:00",
                    },
                    {
                        "id": "customer-next-year",
                        "email": "next@example.test",
                        "display_name": "隔年買家",
                        "user_role": "customer",
                        "created_at": "2026-01-01 00:00:00",
                    },
                ],
            )
        engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{database_path}")
        with engine.connect() as connection:
            numbers = dict(
                connection.execute(
                    text("SELECT id, customer_number FROM users")
                ).all()
            )
        engine.dispose()

        assert numbers == {
            "admin": None,
            "customer-a": "SLF-C-2025-0001",
            "customer-b": "SLF-C-2025-0002",
            "customer-next-year": "SLF-C-2026-0001",
        }
    finally:
        command.downgrade(config, "base")
        get_settings.cache_clear()
