from pathlib import Path
import hashlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings


def test_reference_print_migration_preserves_history_and_requires_an_order(tmp_path, monkeypatch):
    database = tmp_path / "reference-print-migration.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{database}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")
    try:
        command.upgrade(config, "head")
        command.check(config)
        assert "reference" in next(check["sqltext"] for check in inspect(engine).get_check_constraints("print_jobs")
            if check["name"] == "ck_print_jobs_valid_kind")
        command.downgrade(config, "0040_membership_admission")
        # Preserve the original guard head exactly, independently of merge traversal order.
        command.upgrade(config, "0042_order_reference_printing")
        insert = text("""INSERT INTO print_jobs
            (id, event_key, order_id, kind, mode, status, printer_fingerprint, snapshot, attempts,
             next_attempt_at, created_at, updated_at)
            VALUES ('manual-record', 'manual-order:request', :order_id, 'reference', 'dry_run', 'simulated',
                    'test', '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""")
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert, {"order_id": None})
        with engine.begin() as connection:
            connection.execute(insert, {"order_id": "historical-order"})
        def snapshot():
            with engine.connect() as connection:
                raw = "\n".join(connection.connection.driver_connection.iterdump()).encode()
            return hashlib.sha256(raw).hexdigest()

        before_rejected_downgrade = snapshot()
        with pytest.raises(RuntimeError, match="已有訂單紀錄單"):
            command.downgrade(config, "0041_order_summary_printing")
        assert snapshot() == before_rejected_downgrade
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM print_jobs WHERE kind='reference'")) == 1
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0042_order_reference_printing"
    finally:
        engine.dispose()
