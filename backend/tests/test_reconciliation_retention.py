from datetime import timedelta, timezone
import asyncio

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
from app.models import ReconciliationRun, SystemSetting, User
import app.reconciliation_retention as retention
from tests.support import make_test_settings


async def db_now(session):
    value = await session.scalar(select(func.current_timestamp()))
    return value.replace(tzinfo=value.tzinfo or timezone.utc)


def completed_run(identifier, status, finished_at):
    return ReconciliationRun(id=identifier, source="job", status=status,
        started_at=finished_at - timedelta(minutes=1), progress_at=finished_at,
        finished_at=finished_at, phases={}, counts={})


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["internal", "job", "request", "periodic"])
async def test_completed_reconciliation_prunes_only_old_telemetry_for_every_source(database_session, source):
    session = database_session
    now = await db_now(session)
    session.add_all([
        completed_run(1, "succeeded", now - timedelta(days=31)),
        completed_run(2, "partial_failure", now - timedelta(days=45)),
        completed_run(3, "cancelled", now - timedelta(days=45)),
        completed_run(4, "succeeded", now - timedelta(days=29)),
        completed_run(5, "partial_failure", now - timedelta(days=60)),
        completed_run(6, "cancelled", now - timedelta(days=60)),
        ReconciliationRun(id=7, source="internal", status="running", started_at=now - timedelta(days=90),
            progress_at=now - timedelta(days=90), phases={}, counts={}),
        User(id="retention-business-user", email="retention@example.test", display_name="保留使用者", password_hash="unused"),
        SystemSetting(key="retention-business-setting", value={"must_remain": True}),
    ])
    await session.commit()
    report = await jobs.reconcile_once(session, make_test_settings(reconciliation_enabled=False), source=source)
    rows = list(await session.scalars(select(ReconciliationRun).order_by(ReconciliationRun.id)))
    assert 1 not in {row.id for row in rows}, "30-day telemetry cleanup must run even when periodic driver is disabled"
    assert {4, 5, 6, 7} <= {row.id for row in rows}, "recent data, latest failures and every running row must survive"
    assert rows[-1].source == source and rows[-1].status == "succeeded"
    assert len(report.to_dict()) == 9 and all(value == 0 for value in report.to_dict().values())
    assert (await session.get(User, "retention-business-user")).display_name == "保留使用者"
    assert (await session.get(SystemSetting, "retention-business-setting")).value == {"must_remain": True}


@pytest.mark.asyncio
async def test_new_telemetry_control_keeps_recent_record_and_business_data(database_session):
    now = await db_now(database_session)
    database_session.add(completed_run(1, "succeeded", now - timedelta(days=29)))
    await database_session.commit()
    await jobs.reconcile_once(database_session, make_test_settings())
    assert await database_session.get(ReconciliationRun, 1) is not None


async def seed_old_runs(session, total=520):
    now = await db_now(session)
    session.add_all([completed_run(i, "succeeded", now - timedelta(days=31)) for i in range(1, total + 1)])
    session.add_all([
        completed_run(total + 1, "succeeded", now - timedelta(days=60)),
        completed_run(total + 2, "partial_failure", now - timedelta(days=60)),
        completed_run(total + 3, "cancelled", now - timedelta(days=60)),
        ReconciliationRun(id=total + 4, source="job", status="running", started_at=now - timedelta(days=90),
            progress_at=now - timedelta(days=90), phases={}, counts={}),
    ])
    await session.commit()


async def business_snapshot(session):
    from app.database import Base
    result = {}
    for table in Base.metadata.sorted_tables:
        if table.name == "reconciliation_runs":
            continue
        query = select(table)
        if table.name == "system_settings":
            query = query.where(table.c.key != retention.RETENTION_KEY)
        result[table.name] = sorted(repr(dict(row)) for row in (await session.execute(query)).mappings())
    return result


@pytest.mark.asyncio
async def test_retention_is_bounded_indexed_and_keeps_all_other_tables_unchanged(database_session):
    from tests.test_integrations import make_regular_order
    await make_regular_order(database_session)
    await seed_old_runs(database_session)
    before = await business_snapshot(database_session)
    await database_session.rollback()
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False, autoflush=False)
    deletes = []

    def statements(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("DELETE"):
            deletes.append((statement, context.compiled_parameters))

    event.listen(database_session.bind.sync_engine, "before_cursor_execute", statements)
    try:
        await retention.maybe_prune_reconciliation_runs(factory, make_test_settings())
    finally:
        event.remove(database_session.bind.sync_engine, "before_cursor_execute", statements)
    assert len(deletes) == 5
    assert all(sql.upper().startswith("DELETE FROM RECONCILIATION_RUNS") for sql, _ in deletes)
    assert all(sum(key.startswith("id_1_") for key in parameters[0]) == 100 for _, parameters in deletes)
    async with factory() as reader:
        ids = set(await reader.scalars(select(ReconciliationRun.id)))
        assert ids == set(range(501, 525))
        state = await retention.read_retention_status(reader, make_test_settings())
        assert state["deleted_count"] == 500 and state["status"] == "succeeded"
        assert state["oldest_eligible_at"] is not None, "bounded cleanup must expose when additional old telemetry remains"
        assert await business_snapshot(reader) == before
        cutoff = await db_now(reader) - timedelta(days=30)
        query = select(ReconciliationRun.id).where(*retention.retention_candidates(cutoff)).order_by(
            ReconciliationRun.finished_at, ReconciliationRun.id).limit(100)
        sql = str(query.compile(reader.bind.sync_engine, compile_kwargs={"literal_binds": True}))
        await reader.execute(text("ANALYZE reconciliation_runs"))
        plan = (await reader.execute(text("EXPLAIN QUERY PLAN " + sql))).all()
        assert any("ix_reconciliation_runs_finished_id" in str(row) for row in plan), plan


@pytest.mark.asyncio
async def test_process_gate_adds_no_sql_on_second_completed_round(database_session):
    await seed_old_runs(database_session, total=3)
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    await retention.maybe_prune_reconciliation_runs(factory, make_test_settings())
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(database_session.bind.sync_engine, "before_cursor_execute", capture)
    try:
        await retention.maybe_prune_reconciliation_runs(factory, make_test_settings())
    finally:
        event.remove(database_session.bind.sync_engine, "before_cursor_execute", capture)
    assert statements == []


@pytest.mark.asyncio
async def test_database_gate_and_owner_fence_prevent_early_or_stale_worker_cleanup(database_session):
    await seed_old_runs(database_session, total=3)
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    settings = make_test_settings()
    assert await retention._claim(factory, settings, "first-owner")
    assert not await retention._claim(factory, settings, "other-owner")
    assert await retention._delete_batch(factory, settings, "other-owner", None) is None
    await retention._finish(factory, "first-owner")
    assert not await retention._claim(factory, settings, "other-owner"), "one hour cooldown survives lease release"
    async with factory() as reader:
        assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 7


@pytest.mark.asyncio
async def test_delete_failure_preserves_committed_batch_and_records_safe_error(database_session):
    await seed_old_runs(database_session, total=250)
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    calls = 0

    def fail_second_delete(connection, cursor, statement, parameters, context, executemany):
        nonlocal calls
        if statement.upper().startswith("DELETE FROM RECONCILIATION_RUNS"):
            calls += 1
            if calls == 2:
                raise RuntimeError("sensitive-recipient@example.test bearer-retention-secret")

    event.listen(database_session.bind.sync_engine, "before_cursor_execute", fail_second_delete)
    try:
        await retention.maybe_prune_reconciliation_runs(factory, make_test_settings())
    finally:
        event.remove(database_session.bind.sync_engine, "before_cursor_execute", fail_second_delete)
    async with factory() as reader:
        assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 154
        state = await retention.read_retention_status(reader, make_test_settings())
        assert state["status"] == "failed" and state["deleted_count"] == 100
        assert state["error_type"] == "RuntimeError" and "secret" not in repr(state) and "recipient" not in repr(state)
    assert not await retention._claim(factory, make_test_settings(), "premature-retry")


@pytest.mark.asyncio
async def test_disabled_retention_never_writes_or_deletes(database_session):
    await seed_old_runs(database_session, total=3)
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    await retention.maybe_prune_reconciliation_runs(factory, make_test_settings(reconciliation_retention_enabled=False))
    assert await database_session.scalar(select(func.count(ReconciliationRun.id))) == 7
    assert await database_session.get(SystemSetting, retention.RETENTION_KEY) is None


@pytest.mark.parametrize("field,value", [("days", 0), ("batch_size", 101), ("max_batches", 6), ("interval_seconds", 3599), ("lease_seconds", 1801)])
def test_retention_config_rejects_unbounded_or_high_frequency_values(field, value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        make_test_settings(**{f"reconciliation_retention_{field}": value})


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size,max_batches", [(1, 1), (2, 2)])
async def test_smaller_batch_limits_are_enforced(database_session, batch_size, max_batches):
    await seed_old_runs(database_session, total=10)
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    settings = make_test_settings(reconciliation_retention_batch_size=batch_size, reconciliation_retention_max_batches=max_batches)
    await retention.maybe_prune_reconciliation_runs(factory, settings, current_run_id=1)
    async with factory() as reader:
        assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 14 - batch_size * max_batches
        assert await reader.get(ReconciliationRun, 1) is not None, "explicit current run stays protected even after another max id exists"


@pytest.mark.asyncio
async def test_cleanup_uses_db_clock_when_host_clock_is_far_in_the_future(database_session):
    import time_machine
    now = await db_now(database_session)
    database_session.add_all([completed_run(1, "succeeded", now - timedelta(days=29)),
                              completed_run(2, "succeeded", now - timedelta(days=60))])
    await database_session.commit()
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    with time_machine.travel("2099-01-01T00:00:00Z"):
        await retention.maybe_prune_reconciliation_runs(factory, make_test_settings())
    async with factory() as reader:
        assert await reader.get(ReconciliationRun, 1) is not None
        state = await retention.read_retention_status(reader, make_test_settings())
        assert state["deleted_count"] == 0 and state["oldest_eligible_at"] is None


@pytest.mark.asyncio
async def test_cleanup_sql_failure_cannot_roll_back_business_or_change_nine_int_contract(database_session):
    await seed_old_runs(database_session, total=3)

    def fail_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith("DELETE FROM RECONCILIATION_RUNS"):
            raise RuntimeError("synthetic-retention-only-secret")

    event.listen(database_session.bind.sync_engine, "before_cursor_execute", fail_delete)
    try:
        report = await jobs.reconcile_once(database_session, make_test_settings())
    finally:
        event.remove(database_session.bind.sync_engine, "before_cursor_execute", fail_delete)
    assert len(report.to_dict()) == 9 and all(value == 0 for value in report.to_dict().values())
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    async with factory() as reader:
        run = await reader.scalar(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(1))
        assert run.status == "succeeded" and all(phase["status"] == "succeeded" for phase in run.phases.values())
        state = await retention.read_retention_status(reader, make_test_settings())
        assert state["status"] == "failed" and state["error_type"] == "RuntimeError"
        assert "secret" not in repr(state)


@pytest.mark.asyncio
async def test_admin_status_exposes_only_safe_cleanup_fields_and_platform_ignores_private_key(database_session):
    from app.models import UserRole
    from app.routers.platform import platform_router
    from tests.support import api_test_context, auth_headers
    admin = User(email="retention-admin@example.test", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    database_session.add(admin)
    await seed_old_runs(database_session, total=3)
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    settings = make_test_settings()
    await retention.maybe_prune_reconciliation_runs(factory, settings)
    async with api_test_context(database_session, [jobs.jobs_router, platform_router], settings=settings) as client:
        response = await client.get("/v1/admin/reconciliation-status", headers=auth_headers(admin))
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        state = response.json()["cleanup"]
        assert state["retention_days"] == 30 and state["status"] == "succeeded"
        assert "owner" not in state and "lease_until" not in state and "next_allowed_at" not in state
        platform = await client.get("/v1/admin/platform", headers=auth_headers(admin))
        assert retention.RETENTION_KEY not in platform.text
        assert (await client.get("/v1/admin/reconciliation-status")).status_code == 401


@pytest.mark.asyncio
async def test_partial_business_round_still_reaches_common_retention_boundary(database_session, monkeypatch):
    from app.reconciliation import ReconciliationPartialFailure
    await seed_old_runs(database_session, total=3)

    async def fail(session, *args):
        session.add(SystemSetting(key="failed-business-write", value={"must_rollback": True}))
        await session.flush()
        raise RuntimeError("controlled business failure")

    monkeypatch.setattr(jobs, "_reconcile_expired_payments", fail)
    with pytest.raises(ReconciliationPartialFailure):
        await jobs.reconcile_once(database_session, make_test_settings(), source="internal")
    assert await database_session.get(ReconciliationRun, 1) is None
    assert await database_session.get(SystemSetting, "failed-business-write") is None
    state = await retention.read_retention_status(database_session, make_test_settings())
    assert state["status"] == "succeeded"


@pytest.mark.asyncio
async def test_cancellation_during_cleanup_remains_cancellation(database_session):
    await seed_old_runs(database_session, total=3)
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)

    def cancel_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith("DELETE FROM RECONCILIATION_RUNS"):
            raise asyncio.CancelledError()

    event.listen(database_session.bind.sync_engine, "before_cursor_execute", cancel_delete)
    try:
        with pytest.raises(asyncio.CancelledError):
            await retention.maybe_prune_reconciliation_runs(factory, make_test_settings())
    finally:
        event.remove(database_session.bind.sync_engine, "before_cursor_execute", cancel_delete)
    async with factory() as reader:
        assert await reader.scalar(select(func.count(ReconciliationRun.id))) == 7
        state = await retention.read_retention_status(reader, make_test_settings())
        assert state["status"] == "failed" and state["error_type"] == "CancelledError"
