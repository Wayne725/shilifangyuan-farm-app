import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import create_engine


@pytest.mark.asyncio
async def test_application_sqlite_connections_enforce_references_and_restrict_deletion(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'foreign-keys.db'}")
    try:
        async with engine.connect() as first, engine.connect() as second:
            assert await first.scalar(text("PRAGMA foreign_keys")) == 1
            assert await second.scalar(text("PRAGMA foreign_keys")) == 1
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)"))
            await connection.execute(text("CREATE TABLE fk_child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES fk_parent(id) ON DELETE RESTRICT)"))
            await connection.execute(text("INSERT INTO fk_parent (id) VALUES (1)"))
            await connection.execute(text("INSERT INTO fk_child (id, parent_id) VALUES (1, 1)"))
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            async with engine.begin() as connection:
                await connection.execute(text("INSERT INTO fk_child (id, parent_id) VALUES (2, 999)"))
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            async with engine.begin() as connection:
                await connection.execute(text("DELETE FROM fk_parent WHERE id = 1"))
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT COUNT(*) FROM fk_parent")) == 1
            assert await connection.scalar(text("SELECT COUNT(*) FROM fk_child")) == 1
            assert (await connection.execute(text("PRAGMA foreign_key_check"))).all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_common_database_session_enforces_foreign_keys(database_session):
    assert await database_session.scalar(text("PRAGMA foreign_keys")) == 1
