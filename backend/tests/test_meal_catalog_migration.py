import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import ProposalStatus, ProposalTargetType, User, Vote, VoteProposal


def test_catalog_migration_preserves_existing_votes_and_guards_new_requests(tmp_path, monkeypatch):
    database = tmp_path / "catalog-migration.db"
    url = f"sqlite+aiosqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{database}")

    async def seed():
        async_engine = create_async_engine(url)
        try:
            async with async_sessionmaker(async_engine, expire_on_commit=False)() as session:
                user = User(email="legacy@example.com", display_name="既有提案者", password_hash="test")
                session.add(user)
                await session.flush()
                session.add(VoteProposal(proposer_id=user.id, target_type=ProposalTargetType.PRODUCT, target_id="legacy-product",
                    target_name_snapshot="既有商品", status=ProposalStatus.VOTING, votes=[Vote(user_id=user.id, estimated_quantity=3)]))
                await session.commit()
        finally:
            await async_engine.dispose()

    try:
        command.upgrade(config, "head")
        asyncio.run(seed())
        command.downgrade(config, "0030_feie_print_jobs")
        with engine.connect() as connection:
            before = connection.execute(text("SELECT id, proposer_id, target_type, target_id, target_name_snapshot, status FROM vote_proposals")).all()
            votes = connection.execute(text("SELECT id, proposal_id, user_id, estimated_quantity FROM votes")).all()
        command.upgrade(config, "head")
        command.check(config)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT id, proposer_id, target_type, target_id, target_name_snapshot, status FROM vote_proposals")).all() == before
            assert connection.execute(text("SELECT id, proposal_id, user_id, estimated_quantity FROM votes")).all() == votes
            connection.execute(text("UPDATE vote_proposals SET target_type='custom', target_id=NULL, request_details='其他需求內容'"))
        with pytest.raises(RuntimeError, match="已有上傳照片或其他需求"):
            command.downgrade(config, "0030_feie_print_jobs")
    finally:
        engine.dispose()
        get_settings.cache_clear()
