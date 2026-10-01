import asyncio
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import User, MembershipApplication, MembershipDocument, MembershipDocumentType, MembershipDocumentStatus


def test_retention_migration_preserves_old_documents_without_auto_expiry(tmp_path, monkeypatch):
    path = tmp_path / "isolated-retention.db"
    url = f"sqlite+aiosqlite:///{path}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))

    async def seed():
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = User(email="retention@example.test", display_name="虛擬使用者", password_hash="not-a-password")
            session.add(user)
            await session.flush()
            application = MembershipApplication(user_id=user.id)
            session.add(application)
            await session.flush()
            session.add(MembershipDocument(
                application_id=application.id, document_type=MembershipDocumentType.ID_FRONT,
                status=MembershipDocumentStatus.CONFIRMED,
                object_key="membership-documents/verified/isolated-old.pdf", content_type="application/pdf",
                size_bytes=100, confirmed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            ))
            await session.commit()
        await engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0014_invoice_account_binding")
        snapshot = text("SELECT id, application_id, object_key, status, confirmed_at, deleted_at FROM membership_documents")
        with engine.connect() as connection:
            before = connection.execute(snapshot).all()
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(snapshot).all() == before
            assert connection.execute(text("SELECT expires_at, deletion_attempts, deletion_retry_at, deletion_error FROM membership_documents")).one() == (None, 0, None, None)
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0017_meal_order_pickup_overlap"
    finally:
        engine.dispose()
        get_settings.cache_clear()
