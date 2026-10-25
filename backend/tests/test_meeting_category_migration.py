import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Meeting, MeetingAttendance, MeetingResolution, MeetingType, User


def test_meeting_category_migration_preserves_history_and_rejects_lossy_downgrade(tmp_path, monkeypatch):
    path = tmp_path / "meeting-migration.db"
    database_url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                user = User(id="meeting-history-user", email="history@example.test", display_name="歷史社員", password_hash="test")
                session.add(user)
                await session.flush()
                for index, meeting_type in enumerate(MeetingType, 1):
                    meeting = Meeting(id=f"history-{index}", category_id=f"00000000-0000-4000-8000-00000000000{index}",
                        meeting_type=meeting_type, title=f"歷史會議{index}", agenda=[{"title": "舊議程"}],
                        starts_at=datetime(2026, 1, index, tzinfo=timezone.utc), location="合作社", created_by_id=user.id)
                    session.add(meeting)
                    await session.flush()
                    session.add_all([MeetingAttendance(meeting_id=meeting.id, member_id=user.id, attended=True),
                                     MeetingResolution(meeting_id=meeting.id, title="舊決議", resolution_text="保留紀錄")])
                await session.commit()
        finally:
            await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0035_order_amendments")
        with engine.connect() as connection:
            before = connection.execute(text("SELECT id,meeting_type,title,agenda,starts_at,location,created_by_id,created_at FROM meetings ORDER BY id")).all()
            attendances = connection.execute(text("SELECT * FROM meeting_attendances ORDER BY id")).all()
            resolutions = connection.execute(text("SELECT * FROM meeting_resolutions ORDER BY id")).all()
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id,meeting_type,title,agenda,starts_at,location,created_by_id,created_at FROM meetings ORDER BY id")).all() == before
            assert connection.execute(text("SELECT * FROM meeting_attendances ORDER BY id")).all() == attendances
            assert connection.execute(text("SELECT * FROM meeting_resolutions ORDER BY id")).all() == resolutions
            assert connection.execute(text("SELECT c.name FROM meetings m JOIN meeting_categories c ON c.id=m.category_id ORDER BY m.id")).scalars().all() == ["社員大會", "社務會議"]
        with engine.begin() as connection:
            connection.execute(text("UPDATE meeting_categories SET name='新名稱' WHERE legacy_type='affairs'"))
        with pytest.raises(RuntimeError, match="會議類別已有"):
            command.downgrade(config, "0035_order_amendments")
        with engine.begin() as connection:
            connection.execute(text("UPDATE meeting_categories SET name='社務會議' WHERE legacy_type='affairs'"))
            connection.execute(text("INSERT INTO meeting_categories(id,name,sort_order,is_active,created_at,updated_at) VALUES ('custom','理事會',2,true,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            connection.execute(text("UPDATE meetings SET category_id='custom',meeting_type='affairs' WHERE id='history-2'"))
        with pytest.raises(RuntimeError, match="會議類別已有"):
            command.downgrade(config, "0035_order_amendments")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT category_id FROM meetings WHERE id='history-2'")).scalar_one() == "custom"
            assert connection.execute(text("SELECT COUNT(*) FROM meeting_attendances")).scalar_one() == 2
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0036_meeting_categories"
    finally:
        engine.dispose()
        get_settings.cache_clear()
