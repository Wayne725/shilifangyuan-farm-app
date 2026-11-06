import asyncio
from datetime import timedelta, timezone
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
from app.config import get_settings
from app.database import Base, create_engine, get_session
from app.models import OutboxEvent, OutboxStatus, ReconciliationRun, SystemSetting, User, UserRole
from app.reconciliation import ReconciliationPartialFailure
from tests.support import api_test_context, auth_headers, make_test_settings


@pytest.mark.asyncio
async def test_normal_reconciliation_persists_complete_run_with_original_counts(database_session):
    report = await jobs.reconcile_once(database_session, make_test_settings())
    assert report.to_dict() == {"payment_attempts": 0, "proposals": 0, "campaigns": 0, "member_proposals": 0,
        "activities": 0, "meal_events": 0, "meal_events_created": 0, "outbox_completed": 0, "outbox_failed": 0}
    run = await database_session.scalar(select(ReconciliationRun))
    assert run is not None
    assert run.status == "succeeded" and run.finished_at >= run.started_at
    assert run.counts == report.to_dict()
    assert len(run.phases) == 10 and all(phase["status"] == "succeeded" for phase in run.phases.values())


@pytest.mark.asyncio
async def test_admin_status_requires_admin_without_exposing_private_work_payload(database_session):
    admin = User(email="monitor-admin@example.test", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    buyer = User(email="monitor-buyer@example.test", display_name="買家", password_hash="unused")
    database_session.add_all([admin, buyer])
    await database_session.commit()
    async with api_test_context(database_session, [jobs.jobs_router], settings=make_test_settings()) as client:
        response = await client.get("/v1/admin/reconciliation-status", headers=auth_headers(admin))
        assert response.status_code == 200
        assert response.json()["current"] is None and response.json()["last_success"] is None
        assert (await client.get("/v1/admin/reconciliation-status")).status_code == 401
        assert (await client.get("/v1/admin/reconciliation-status", headers=auth_headers(buyer))).status_code == 403
        assert "example.test" not in response.text
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("file_database", [False, True])
@pytest.mark.parametrize("preflushed", [False, True])
@pytest.mark.parametrize("fail_first", [False, True])
async def test_monitor_never_commits_or_rolls_back_callers_sqlite_work_before_original_stage(
    tmp_path, monkeypatch, file_database, preflushed, fail_first,
):
    url = f"sqlite+aiosqlite:///{tmp_path / 'caller.db'}" if file_database else "sqlite+aiosqlite:///:memory:"
    engine = create_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            pending = SystemSetting(key="caller-pending", value={"original": True})
            session.add(pending)
            if preflushed:
                await session.flush()

            async def original_first_stage(worker, *args):
                assert worker is session
                assert pending not in session.deleted
                if preflushed:
                    raw = await (await session.connection()).get_raw_connection()
                    assert raw.driver_connection.in_transaction
                else:
                    assert pending in session.new
                with session.no_autoflush:
                    runs = await session.scalar(select(func.count(ReconciliationRun.id)))
                    assert runs == (0 if preflushed else 1)
                if fail_first:
                    raise RuntimeError("synthetic failure after preserving caller transaction")
                await session.commit()
                return 0

            monkeypatch.setattr(jobs, "_reconcile_expired_payments", original_first_stage)
            monkeypatch.setattr(jobs, "SessionLocal", lambda: pytest.fail("must bind monitoring to the supplied database"))
            if fail_first:
                with pytest.raises(ReconciliationPartialFailure):
                    await jobs.reconcile_once(session, make_test_settings())
            else:
                await jobs.reconcile_once(session, make_test_settings())
        async with factory() as reader:
            assert (await reader.get(SystemSetting, "caller-pending") is None) is fail_first
            run = await reader.scalar(select(ReconciliationRun))
            assert run.status == ("partial_failure" if fail_first else "succeeded")
            assert run.deferred_reason == ("sqlite_existing_transaction" if preflushed else None)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_internal_partial_failure_runs_later_stages_then_returns_safe_503(database_session, monkeypatch):
    async def fail(session, *args):
        session.add(SystemSetting(key="bad-stage-pending", value={"must_rollback": True}))
        await session.flush()
        raise RuntimeError("synthetic-private-email@example.test bearer-secret-must-not-leak")

    monkeypatch.setattr(jobs, "_reconcile_member_proposals", fail)
    settings = make_test_settings()
    async with api_test_context(database_session, [jobs.jobs_router], settings=settings) as client:
        response = await client.post("/internal/reconcile", headers={"X-Reconcile-Secret": settings.internal_reconcile_secret})
    assert response.status_code == 503
    run = await database_session.scalar(select(ReconciliationRun))
    assert run.source == "internal" and run.status == "partial_failure"
    assert run.phases["member_proposals"]["error_type"] == "RuntimeError"
    assert run.phases["activities"]["status"] == run.phases["outbox"]["status"] == "succeeded"
    assert await database_session.get(SystemSetting, "bad-stage-pending") is None
    assert "bearer-secret" not in response.text + json.dumps(run.phases)
    assert "private-email" not in response.text + json.dumps(run.phases)


@pytest.mark.asyncio
async def test_cancellation_rolls_back_current_stage_and_remains_cancellation(database_session, monkeypatch):
    started = asyncio.Event()

    async def pause(session, *args):
        session.add(SystemSetting(key="cancelled-stage-pending", value={"must_rollback": True}))
        await session.flush()
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs, "_reconcile_expired_payments", pause)
    task = asyncio.create_task(jobs.reconcile_once(database_session, make_test_settings()))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await database_session.get(SystemSetting, "cancelled-stage-pending") is None
    run = await database_session.scalar(select(ReconciliationRun))
    assert run.status == "cancelled" and run.finished_at is not None
    assert run.phases["expired_payments"]["status"] == "cancelled"
    assert "outbox" not in run.phases


@pytest.mark.asyncio
async def test_admin_backlog_separates_due_delayed_stale_and_unknown_without_payload(database_session):
    session = database_session
    admin = User(email="backlog-admin@example.test", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    session.add(admin)
    await session.commit()
    now = await session.scalar(select(func.current_timestamp()))
    now = now.replace(tzinfo=timezone.utc)
    for status, age, attempts, delivery, event_type in [
        (OutboxStatus.PENDING, -60, 0, None, "send_email"),
        (OutboxStatus.PENDING, 1440, 1, {"state": "rejected"}, "send_email"),
        (OutboxStatus.PROCESSING, -1, 1, None, "invoice.issue_requested"),
        (OutboxStatus.PROCESSING, -20, 1, None, "invoice.issue_requested"),
        (OutboxStatus.FAILED, -60, 1, None, "send_email"),
        (OutboxStatus.FAILED, -60, 0, {"state": "unknown"}, "send_email"),
        (OutboxStatus.FAILED, -60, 1, {"state": "rejected"}, "send_email"),
    ]:
        payload = {"to_email": "private-recipient@example.test", "raw_secret": "must-not-expose"}
        if delivery is not None:
            payload["email_delivery"] = delivery
        session.add(OutboxEvent(event_type=event_type, aggregate_type="user", aggregate_id=admin.id,
            status=status, available_at=now + timedelta(minutes=age), attempts=attempts, payload=payload))
    await session.commit()
    async with api_test_context(session, [jobs.jobs_router], settings=make_test_settings()) as client:
        response = await client.get("/v1/admin/reconciliation-status", headers=auth_headers(admin))
    assert response.status_code == 200
    counts = {item["kind"]: item["count"] for item in response.json()["backlog"]}
    assert {key: counts[key] for key in ("outbox_due", "outbox_delayed", "outbox_processing", "outbox_stale", "outbox_failed", "email_unknown")} == {
        "outbox_due": 1, "outbox_delayed": 1, "outbox_processing": 2, "outbox_stale": 1, "outbox_failed": 3, "email_unknown": 2}
    assert "private-recipient" not in response.text and "must-not-expose" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("monitor_path", ["/v1/admin/reconciliation-status", "/v1/admin/reconciliation-status/"])
async def test_monitor_read_does_not_wake_worker_but_existing_business_request_still_does(database_session, monkeypatch, monitor_path):
    import app.main as main_module
    from app.config import get_settings
    from app.database import get_session
    from httpx import ASGITransport, AsyncClient

    settings = make_test_settings()
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    scheduled = []
    monkeypatch.setattr(main_module, "should_reconcile_now", lambda: True)
    monkeypatch.setattr(main_module, "schedule_background_reconcile", lambda *args, **kwargs: scheduled.append(True))
    admin = User(email="monitor-no-wake@example.test", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    database_session.add(admin)
    await database_session.commit()
    application = main_module.create_app()

    async def session_override():
        yield database_session

    application.dependency_overrides[get_session] = session_override
    application.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://localhost") as client:
        response = await client.get(monitor_path, headers=auth_headers(admin), follow_redirects=True)
        assert response.status_code == 200
        assert scheduled == []
        assert (await client.get("/v1/platform")).status_code == 200
        assert scheduled == [True]


@pytest.mark.asyncio
async def test_request_and_periodic_drivers_persist_their_actual_source(database_session, monkeypatch):
    factory = async_sessionmaker(database_session.bind, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    await jobs.lazy_reconcile(database_session, make_test_settings(), minimum_interval_seconds=0)
    completed = asyncio.Event()
    original_outbox = jobs._process_outbox

    async def outbox(*args, **kwargs):
        result = await original_outbox(*args, **kwargs)
        completed.set()
        return result

    monkeypatch.setattr(jobs, "_process_outbox", outbox)
    settings = make_test_settings().model_copy(update={"reconciliation_enabled": True, "reconciliation_interval_seconds": 0.01})
    async with jobs.periodic_reconciliation(settings):
        await asyncio.wait_for(completed.wait(), 5)
    async with factory() as reader:
        runs = list(await reader.scalars(select(ReconciliationRun).order_by(ReconciliationRun.id)))
        assert [run.source for run in runs] == ["request", "periodic"]
        assert all(run.status == "succeeded" for run in runs)


@pytest.mark.asyncio
async def test_heartbeat_write_failure_does_not_starve_work_or_claim_success(database_session):
    failed = False

    def fail_first_monitor_update(connection, cursor, statement, parameters, context, executemany):
        nonlocal failed
        if not failed and statement.lstrip().upper().startswith("UPDATE RECONCILIATION_RUNS"):
            failed = True
            raise RuntimeError("synthetic-sensitive-monitor-secret")

    engine = database_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", fail_first_monitor_update)
    try:
        with pytest.raises(ReconciliationPartialFailure) as error:
            await jobs.reconcile_once(database_session, make_test_settings())
    finally:
        event.remove(engine, "before_cursor_execute", fail_first_monitor_update)
    assert error.value.failures == {"monitoring": "RuntimeError"}
    run = await database_session.scalar(select(ReconciliationRun))
    assert run.status == "partial_failure" and run.phases["outbox"]["status"] == "succeeded"
    assert run.phases["monitoring"]["error_type"] == "RuntimeError"
    assert "sensitive-monitor-secret" not in json.dumps(run.phases)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["direct", "internal"])
@pytest.mark.parametrize("fail_start", [False, True])
async def test_first_monitor_insert_failure_still_runs_every_stage_without_committing_caller_early(
    database_session, monkeypatch, entrypoint, fail_start,
):
    from app import activity_payments

    session = database_session
    pending = SystemSetting(key="caller-before-monitor-start", value={"not_committed_by_monitor": True})
    session.add(pending)
    calls = []
    stages = [
        (jobs, "_reconcile_expired_payments", "expired_payments"),
        (activity_payments, "reconcile_charges", "activity_charges"),
        (jobs, "_reconcile_expired_cash_orders", "cash_orders"),
        (jobs, "_reconcile_proposals", "proposals"),
        (jobs, "_reconcile_campaigns", "campaigns"),
        (jobs, "_reconcile_member_proposals", "member_proposals"),
        (jobs, "_reconcile_activities", "activities"),
        (jobs, "_reconcile_meal_events", "meal_events"),
        (jobs, "generate_scheduled_meal_events", "meal_event_generation"),
        (jobs, "_process_outbox", "outbox"),
    ]

    def track(name, original):
        async def stage(*args, **kwargs):
            calls.append(name)
            if name == "expired_payments":
                assert pending in session.new, "monitor start must not flush or commit caller work"
            return await original(*args, **kwargs)
        return stage

    for module, attribute, name in stages:
        monkeypatch.setattr(module, attribute, track(name, getattr(module, attribute)))
    injected = False

    def fail_first_insert(connection, cursor, statement, parameters, context, executemany):
        nonlocal injected
        if fail_start and not injected and statement.lstrip().upper().startswith("INSERT INTO RECONCILIATION_RUNS"):
            injected = True
            raise RuntimeError("synthetic-private-start@example.test bearer-start-secret")

    settings = make_test_settings()
    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", fail_first_insert)
    failure = None
    try:
        if entrypoint == "direct":
            try:
                await jobs.reconcile_once(session, settings)
            except Exception as error:
                failure = error
        else:
            application = FastAPI()
            application.include_router(jobs.jobs_router)

            async def sessions():
                yield session

            application.dependency_overrides[get_session] = sessions
            application.dependency_overrides[get_settings] = lambda: settings
            async with AsyncClient(transport=ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
                response = await client.post("/internal/reconcile", headers={"X-Reconcile-Secret": settings.internal_reconcile_secret})
    finally:
        event.remove(engine, "before_cursor_execute", fail_first_insert)
    assert injected is fail_start
    assert calls == [stage[2] for stage in stages], "monitor failure must not starve any business stage"
    if entrypoint == "internal":
        assert response.status_code == (503 if fail_start else 200)
        assert "private-start" not in response.text and "bearer-start-secret" not in response.text
    else:
        if fail_start:
            assert isinstance(failure, ReconciliationPartialFailure)
            assert failure.failures == {"monitoring": "RuntimeError"}
        else:
            assert failure is None
    run = await session.scalar(select(ReconciliationRun))
    assert run.status == ("partial_failure" if fail_start else "succeeded")
    assert all(run.phases[name]["status"] == "succeeded" for _, _, name in stages)
    if fail_start:
        assert run.phases["monitoring"]["error_type"] == "RuntimeError"
    assert "private-start" not in json.dumps(run.phases) and "bearer-start-secret" not in json.dumps(run.phases)
    assert await session.get(SystemSetting, "caller-before-monitor-start") is not None


@pytest.mark.asyncio
async def test_failed_middle_stage_cannot_undo_committed_first_stage_or_commit_its_write_with_tail(database_session, monkeypatch):
    async def first(session, *args):
        session.add(SystemSetting(key="committed-first-stage", value={"retained": True}))
        await session.commit()
        return 1

    async def middle(session, *args):
        session.add(SystemSetting(key="uncommitted-middle-stage", value={"must_rollback": True}))
        await session.flush()
        raise RuntimeError("controlled middle failure")

    async def tail(session, *args):
        session.add(SystemSetting(key="committed-tail-stage", value={"retained": True}))
        await session.flush()
        return 1

    monkeypatch.setattr(jobs, "_reconcile_expired_payments", first)
    monkeypatch.setattr(jobs, "_reconcile_member_proposals", middle)
    monkeypatch.setattr(jobs, "_reconcile_activities", tail)
    with pytest.raises(ReconciliationPartialFailure):
        await jobs.reconcile_once(database_session, make_test_settings())
    await database_session.rollback()
    async with async_sessionmaker(database_session.bind, expire_on_commit=False)() as reader:
        assert await reader.get(SystemSetting, "committed-first-stage") is not None
        assert await reader.get(SystemSetting, "uncommitted-middle-stage") is None
        assert await reader.get(SystemSetting, "committed-tail-stage") is not None
        run = await reader.scalar(select(ReconciliationRun))
        assert run.status == "partial_failure" and run.counts["payment_attempts"] == run.counts["activities"] == 1


@pytest.mark.asyncio
async def test_status_aggregate_queries_remain_bounded_when_pending_backlog_grows(database_session):
    session = database_session
    admin = User(email="aggregate-admin@example.test", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    session.add(admin)
    await session.commit()
    headers = auth_headers(admin)
    now = await session.scalar(select(func.current_timestamp()))
    query_counts = []

    async with api_test_context(session, [jobs.jobs_router], settings=make_test_settings()) as client:
        for added, expected in [(1, 1), (100, 101)]:
            session.add_all([OutboxEvent(event_type="send_email", aggregate_type="user", aggregate_id=admin.id,
                available_at=now - timedelta(hours=1), payload={}) for _ in range(added)])
            await session.commit()
            session.expire_all()
            selects = []

            def count_select(connection, cursor, statement, parameters, context, executemany):
                if statement.lstrip().upper().startswith("SELECT"):
                    selects.append(statement)

            event.listen(session.bind.sync_engine, "before_cursor_execute", count_select)
            try:
                response = await client.get("/v1/admin/reconciliation-status", headers=headers)
            finally:
                event.remove(session.bind.sync_engine, "before_cursor_execute", count_select)
            assert response.status_code == 200
            due = next(item for item in response.json()["backlog"] if item["kind"] == "outbox_due")
            assert due["count"] == expected
            query_counts.append(len(selects))
            await session.rollback()
            await session.refresh(admin)
    assert query_counts[1] <= query_counts[0] + 1
    assert max(query_counts) <= 20
