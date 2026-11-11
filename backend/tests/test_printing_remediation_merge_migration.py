from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import Settings


@pytest.mark.parametrize("start", [
    "0040_membership_admission", "0042_order_reference_printing", "0047_credential_rate_limits", "0045_walk_in_sales", "0048_merge_remediation_print",
])
def test_both_migration_histories_reach_one_merged_head_with_metadata_parity_and_noop(tmp_path, monkeypatch, start):
    database = tmp_path / "merged-release.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, start)
        with engine.connect() as connection:
            assert list(connection.scalars(text("SELECT version_num FROM alembic_version"))) == [start]
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert list(connection.scalars(text("SELECT version_num FROM alembic_version"))) == ["0062_meal_business_only"]
            before = list(connection.connection.driver_connection.iterdump())
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert list(connection.connection.driver_connection.iterdump()) == before
    finally:
        engine.dispose()
