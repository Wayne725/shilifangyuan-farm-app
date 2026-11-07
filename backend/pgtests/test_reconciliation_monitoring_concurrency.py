import asyncio

import pytest
from sqlalchemy import func, select

import app.jobs as jobs
from app.models import ReconciliationRun, SystemSetting
from app.reconciliation import ReconciliationPartialFailure, read_reconciliation_status
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import make_test_settings


@pytest.mark.asyncio
async def test_older_worker_finishing_last_cannot_replace_newer_current_or_success(postgres_sessions, monkeypatch):
    factory = postgres_sessions
    old_started, release_old = asyncio.Event(), asyncio.Event()

    async def first_stage(session, *args):
        if asyncio.current_task().get_name() == "older-reconcile":
            session.add(SystemSetting(key="older-business-uncommitted", value={"synthetic": True}))
            await session.flush()
            old_started.set()
            await release_old.wait()
        await session.commit()
        return 0

    monkeypatch.setattr(jobs, "_reconcile_expired_payments", first_stage)
    settings = make_test_settings()

    async def run():
        async with factory() as session:
            return await jobs.reconcile_once(session, settings)

    older = asyncio.create_task(run(), name="older-reconcile")
    try:
        await asyncio.wait_for(old_started.wait(), 5)
        async with factory() as reader:
            current = await reader.scalar(select(ReconciliationRun))
            assert current.status == "running" and current.current_phase == "expired_payments"
            assert await reader.get(SystemSetting, "older-business-uncommitted") is None
            older_id = current.id
        await asyncio.wait_for(run(), 15)
        async with factory() as reader:
            status = await read_reconciliation_status(reader, settings)
            newer_id = status.current.id
            assert newer_id > older_id and status.last_success.id == newer_id
        release_old.set()
        await asyncio.wait_for(older, 15)
        async with factory() as reader:
            status = await read_reconciliation_status(reader, settings)
            assert status.current.id == status.last_success.id == newer_id
            assert (await reader.get(ReconciliationRun, older_id)).status == "succeeded"
            assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 2
    finally:
        release_old.set()
        if not older.done():
            older.cancel()
            await asyncio.gather(older, return_exceptions=True)


@pytest.mark.asyncio
async def test_pg_monitor_start_is_committed_without_committing_callers_pending_write(postgres_sessions, monkeypatch):
    factory = postgres_sessions
    async with factory() as session:
        session.add(SystemSetting(key="caller-pending", value={"must_rollback": True}))
        await session.flush()

        async def fail(worker, *args):
            async with factory() as reader:
                assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 1
                assert await reader.get(SystemSetting, "caller-pending") is None
            raise RuntimeError("controlled first-stage failure")

        monkeypatch.setattr(jobs, "_reconcile_expired_payments", fail)
        monkeypatch.setattr(jobs, "SessionLocal", lambda: pytest.fail("must use the supplied test database bind"))
        with pytest.raises(ReconciliationPartialFailure):
            await jobs.reconcile_once(session, make_test_settings())
    async with factory() as reader:
        assert await reader.get(SystemSetting, "caller-pending") is None
        run = await reader.scalar(select(ReconciliationRun))
        assert run.status == "partial_failure" and run.deferred_reason is None
        assert run.phases["outbox"]["status"] == "succeeded"
