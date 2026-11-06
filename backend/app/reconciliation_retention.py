from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
import logging
import time
from uuid import uuid4
from weakref import WeakKeyDictionary

from sqlalchemy import delete, func, literal_column, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import ReconciliationRun, SystemSetting


RETENTION_KEY = "reconciliation_runs_retention"
TERMINAL_STATUSES = ("succeeded", "partial_failure", "cancelled")
_checks = WeakKeyDictionary()
logger = logging.getLogger("uvicorn.error").getChild(__name__)


async def _db_now(session):
    clock = func.clock_timestamp() if session.get_bind().dialect.name == "postgresql" else func.current_timestamp()
    value = await session.scalar(select(clock))
    return value.replace(tzinfo=value.tzinfo or timezone.utc)


def retention_candidates(cutoff, current_run_id=None):
    # Literal fixed statuses let PostgreSQL use the partial index in generic plans.
    terminal = ReconciliationRun.status.in_([literal_column(f"'{status}'") for status in TERMINAL_STATUSES])
    latest = [select(ReconciliationRun.id).order_by(ReconciliationRun.id.desc()).limit(1).subquery()]
    latest.extend(select(ReconciliationRun.id).where(ReconciliationRun.status == status)
                  .order_by(ReconciliationRun.id.desc()).limit(1).subquery() for status in TERMINAL_STATUSES)
    protected = union_all(*(select(query.c.id) for query in latest)).subquery()
    conditions = [terminal, ReconciliationRun.finished_at.is_not(None), ReconciliationRun.finished_at < cutoff,
                  ReconciliationRun.id.not_in(select(protected.c.id).where(protected.c.id.is_not(None)))]
    if current_run_id is not None:
        conditions.append(ReconciliationRun.id != current_run_id)
    return conditions


async def _locked_state(session, *, create=False):
    if create:
        insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
        statement = insert(SystemSetting).values(key=RETENTION_KEY, value={}, updated_at=func.now())
        await session.execute(statement.on_conflict_do_update(index_elements=[SystemSetting.key], set_={"key": statement.excluded.key}))
    else:
        # SQLite ignores FOR UPDATE; this private-row DML also takes its writer lock.
        await session.execute(update(SystemSetting).where(SystemSetting.key == RETENTION_KEY)
                              .values(key=RETENTION_KEY, updated_at=SystemSetting.updated_at))
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == RETENTION_KEY).with_for_update())
    return dict(row.value) if row is not None else None


async def _write_state(session, state):
    await session.execute(update(SystemSetting).where(SystemSetting.key == RETENTION_KEY)
                          .values(value=state, updated_at=func.now()))


async def _claim(factory, settings, owner):
    async with factory() as session:
        state = await _locked_state(session, create=True)
        now = await _db_now(session)
        timestamp = now.timestamp()
        if float(state.get("lease_until", 0)) > timestamp or float(state.get("next_allowed_at", 0)) > timestamp:
            await session.rollback()
            return False
        state.update(owner=owner, status="running", last_started_at=now.isoformat(), last_finished_at=None,
                     deleted_count=0, error_type=None, lease_until=timestamp + settings.reconciliation_retention_lease_seconds,
                     next_allowed_at=timestamp + settings.reconciliation_retention_interval_seconds)
        await _write_state(session, state)
        await session.commit()
        return True


async def _delete_batch(factory, settings, owner, current_run_id):
    async with factory() as session:
        state = await _locked_state(session)
        now = await _db_now(session)
        if not state or state.get("owner") != owner or float(state.get("lease_until", 0)) <= now.timestamp():
            await session.rollback()
            return None
        cutoff = now - timedelta(days=settings.reconciliation_retention_days)
        conditions = retention_candidates(cutoff, current_run_id)
        ids = list(await session.scalars(select(ReconciliationRun.id).where(*conditions)
                                        .order_by(ReconciliationRun.finished_at, ReconciliationRun.id)
                                        .limit(settings.reconciliation_retention_batch_size)))
        deleted = 0
        if ids:
            result = await session.execute(delete(ReconciliationRun).where(ReconciliationRun.id.in_(ids), *conditions))
            deleted = result.rowcount
        state["deleted_count"] += deleted
        state["lease_until"] = now.timestamp() + settings.reconciliation_retention_lease_seconds
        await _write_state(session, state)
        await session.commit()
        return deleted


async def _finish(factory, owner, *, error_type=None):
    async with factory() as session:
        state = await _locked_state(session)
        if not state or state.get("owner") != owner:
            await session.rollback()
            return
        now = await _db_now(session)
        state.update(status="failed" if error_type else "succeeded", last_finished_at=now.isoformat(),
                     error_type=error_type, lease_until=0)
        if error_type is None:
            state["last_success_at"] = now.isoformat()
        await _write_state(session, state)
        await session.commit()


async def maybe_prune_reconciliation_runs(factory, settings, current_run_id=None):
    if not settings.reconciliation_retention_enabled:
        return
    bind = factory.kw["bind"]
    current = time.monotonic()
    previous = _checks.get(bind)
    if previous is not None and current - previous < settings.reconciliation_retention_interval_seconds:
        return
    _checks[bind] = current
    owner = uuid4().hex
    claimed = False
    try:
        claimed = await _claim(factory, settings, owner)
        if not claimed:
            return
        for _ in range(settings.reconciliation_retention_max_batches):
            deleted = await _delete_batch(factory, settings, owner, current_run_id)
            if deleted is None:
                await _finish(factory, owner, error_type="RetentionLeaseLost")
                return
            if deleted < settings.reconciliation_retention_batch_size:
                break
        await _finish(factory, owner)
    except (Exception, asyncio.CancelledError) as error:
        error_type = type(error).__name__
        logger.error("reconciliation_retention_failed error_type=%s", error_type)
        if claimed:
            try:
                await _finish(factory, owner, error_type=error_type)
            except Exception as state_error:
                logger.error("reconciliation_retention_status_failed error_type=%s", type(state_error).__name__)
        if isinstance(error, asyncio.CancelledError):
            raise


async def read_retention_status(session, settings, now=None):
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == RETENTION_KEY).execution_options(populate_existing=True))
    state = row.value if row is not None else {}
    current = now if now is not None else await _db_now(session)
    oldest = await session.scalar(select(ReconciliationRun.finished_at).where(*retention_candidates(
        current - timedelta(days=settings.reconciliation_retention_days)))
        .order_by(ReconciliationRun.finished_at, ReconciliationRun.id).limit(1))
    return dict(enabled=settings.reconciliation_retention_enabled, retention_days=settings.reconciliation_retention_days,
                batch_size=settings.reconciliation_retention_batch_size, max_batches=settings.reconciliation_retention_max_batches,
                interval_seconds=settings.reconciliation_retention_interval_seconds, status=state.get("status", "never_run"),
                last_started_at=state.get("last_started_at"), last_finished_at=state.get("last_finished_at"),
                last_success_at=state.get("last_success_at"), deleted_count=state.get("deleted_count", 0),
                error_type=state.get("error_type"), oldest_eligible_at=oldest)
