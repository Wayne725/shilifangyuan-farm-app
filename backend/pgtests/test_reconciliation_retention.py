import asyncio
from datetime import timedelta
import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import ReconciliationRun, SystemSetting
import app.reconciliation_retention as retention
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import make_test_settings
from tests.test_integrations import make_regular_order
from tests.test_reconciliation_retention import business_snapshot, db_now


async def seed(session, total=520):
    now = await db_now(session)
    for status in ["succeeded"] * (total + 1) + ["partial_failure", "cancelled"]:
        session.add(ReconciliationRun(source="job", status=status, started_at=now - timedelta(days=40, minutes=1),
            progress_at=now - timedelta(days=40), finished_at=now - timedelta(days=40), phases={}, counts={}))
    session.add(ReconciliationRun(source="request", status="running", started_at=now - timedelta(days=90),
        progress_at=now - timedelta(days=90), phases={}, counts={}))
    await session.commit()


@pytest.mark.asyncio
async def test_separate_pg_worker_engines_share_one_hourly_lease_and_bounded_cleanup(postgres_sessions):
    factory = postgres_sessions
    async with factory() as session:
        await make_regular_order(session)
        await seed(session)
        before = await business_snapshot(session)
        schema = await session.scalar(text("SHOW search_path"))
        assert schema.startswith("acceptance_") and schema.replace("_", "").isalnum()
    engine2 = create_async_engine(factory.kw["bind"].url, connect_args={"server_settings": {"search_path": schema}})
    other = async_sessionmaker(engine2, expire_on_commit=False, autoflush=False)
    try:
        await asyncio.gather(
            retention.maybe_prune_reconciliation_runs(factory, make_test_settings()),
            retention.maybe_prune_reconciliation_runs(other, make_test_settings()),
        )
        async with factory() as reader:
            assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 24
            state = await retention.read_retention_status(reader, make_test_settings())
            assert state["status"] == "succeeded" and state["deleted_count"] == 500
            assert await business_snapshot(reader) == before
            assert not await retention._claim(other, make_test_settings(), "third-worker")
    finally:
        await engine2.dispose()


@pytest.mark.asyncio
async def test_stale_pg_owner_cannot_delete_or_overwrite_newer_cleanup(postgres_sessions):
    factory = postgres_sessions
    async with factory() as session:
        await seed(session, total=120)
    settings = make_test_settings()
    assert await retention._claim(factory, settings, "old-owner")
    async with factory() as session:
        row = await session.get(SystemSetting, retention.RETENTION_KEY)
        row.value = {**row.value, "lease_until": 0, "next_allowed_at": 0}
        await session.commit()
    assert await retention._claim(factory, settings, "new-owner")
    assert await retention._delete_batch(factory, settings, "old-owner", None) is None
    await retention._finish(factory, "old-owner", error_type="must_not_replace_new_owner")
    assert await retention._delete_batch(factory, settings, "new-owner", None) == 100
    await retention._finish(factory, "new-owner")
    async with factory() as reader:
        state = await reader.get(SystemSetting, retention.RETENTION_KEY)
        assert state.value["owner"] == "new-owner" and state.value["status"] == "succeeded"
        assert state.value["deleted_count"] == 100 and state.value["error_type"] is None
        assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 24


@pytest.mark.asyncio
async def test_pg_candidates_use_partial_index_with_bounded_order(postgres_sessions):
    async with postgres_sessions() as session:
        await seed(session, total=1500)
        await session.execute(text("ANALYZE reconciliation_runs"))
        cutoff = await db_now(session) - timedelta(days=30)
        query = select(ReconciliationRun.id).where(*retention.retention_candidates(cutoff)).order_by(
            ReconciliationRun.finished_at, ReconciliationRun.id).limit(100)
        sql = str(query.compile(session.bind.sync_engine, compile_kwargs={"literal_binds": True}))
        plan = await session.scalar(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql))
        assert "ix_reconciliation_runs_finished_id" in json.dumps(plan), plan
        assert len(list(await session.scalars(query))) == 100
