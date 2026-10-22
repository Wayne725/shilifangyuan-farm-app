import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import PrintJob
from tests.test_integrations import make_regular_order


def test_print_history_survives_migration_and_blocks_destructive_downgrade(tmp_path, monkeypatch):
    path = tmp_path / "print-migration.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            _, _, order = await make_regular_order(session)
            order.client_request_id = "a1cf76b7-581a-4ba6-91d1-d6db80bc3670"
            order.request_fingerprint = "f" * 64
            order.kitchen_note = "繁體中文備註"
            session.add(PrintJob(order_id=order.id, event_key=f"order:{order.id}:initial", kind="order", mode="dry_run",
                status="simulated", printer_fingerprint="f" * 64, snapshot={"order_number": order.order_number}))
            await session.commit()
        await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.check(config)
        with pytest.raises(RuntimeError, match="出單紀錄"):
            command.downgrade(config, "0029_onsite_payments")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT status FROM print_jobs")).scalar_one() == "simulated"
            assert connection.execute(text("SELECT kitchen_note FROM orders")).scalar_one() == "繁體中文備註"
    finally:
        engine.dispose()
        get_settings.cache_clear()
