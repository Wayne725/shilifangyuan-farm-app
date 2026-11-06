from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings


def test_summary_print_migration_roundtrip_and_preserves_print_history(tmp_path, monkeypatch):
    database = tmp_path / "summary-print-migration.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, "head")
        command.check(config)
        command.downgrade(config, "0040_membership_admission")
        assert not next(column for column in inspect(engine).get_columns("print_jobs") if column["name"] == "order_id")["nullable"]
        command.upgrade(config, "head")
        command.check(config)
        assert next(column for column in inspect(engine).get_columns("print_jobs") if column["name"] == "order_id")["nullable"]
        insert = text("""INSERT INTO print_jobs
            (id, event_key, order_id, kind, mode, status, printer_fingerprint, snapshot, attempts,
             next_attempt_at, created_at, updated_at)
            VALUES ('summary-page', 'summary:request:01', :order_id, :kind, 'dry_run', 'simulated',
                    'test', '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""")
        for values in [{"kind": "order", "order_id": None}, {"kind": "summary", "order_id": "existing-order"}]:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert, values)
        with engine.begin() as connection:
            connection.execute(insert, {"kind": "summary", "order_id": None})
        with pytest.raises(RuntimeError, match="已有訂單彙總出單紀錄"):
            command.downgrade(config, "0040_membership_admission")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM print_jobs WHERE kind='summary'")) == 1
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0041_order_summary_printing"
    finally:
        engine.dispose()
