import pytest
import sqlalchemy as sa
from alembic import command

from app.config import get_settings
from tests.test_group_review_migration import MigrationDatabase, NOW


def test_admission_migration_matches_metadata_and_preserves_history(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'admission.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    database = MigrationDatabase(url)
    try:
        command.upgrade(database.config, "head")
        command.check(database.config)
        command.downgrade(database.config, "0039_product_daily_sales")
        command.upgrade(database.config, "head")
        command.check(database.config)
        users = database.table("users")
        database.run(lambda connection: connection.execute(users.insert().values(
            id="applicant", email="migration@example.test", display_name="遷移測試", password_hash="unused",
            user_role="customer", membership_type="nonmember", is_active=True, token_version=0,
            pending_member_claim=False, created_at=NOW, updated_at=NOW,
        )))
        rounds = database.table("membership_admission_rounds")
        database.run(lambda connection: connection.execute(rounds.insert().values(
            id="round", user_id="applicant", round_number=1, idempotency_key="key", created_at=NOW, updated_at=NOW,
        )))
        with pytest.raises(RuntimeError, match="資料保全"):
            command.downgrade(database.config, "0039_product_daily_sales")
        assert database.rows("membership_admission_rounds")[0]["status"] == "draft"
        with pytest.raises(sa.exc.IntegrityError):
            database.run(lambda connection: connection.execute(rounds.insert().values(
                id="duplicate", user_id="applicant", round_number=1, idempotency_key="other", created_at=NOW, updated_at=NOW,
            )))
    finally:
        get_settings.cache_clear()


def test_admission_unique_indexes_have_distinct_postgres_names():
    from app.database import Base
    import app.models

    names = [constraint.name for name, table in Base.metadata.tables.items()
             if name.startswith("membership_admission_") for constraint in table.constraints
             if isinstance(constraint, sa.UniqueConstraint)]
    assert len(names) == len(set(names))
    assert all(name and len(name) <= 63 for name in names)
