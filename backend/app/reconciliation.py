from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker

from .models import Activity, ActivityStatus, MealEvent, MealEventStatus, MemberProposal, MemberProposalStatus, OutboxEvent, OutboxStatus, PaymentAttempt, ReconciliationRun
from .failed_jobs import JOB_KINDS
from .reconciliation_schemas import ReconciliationBacklogRead, ReconciliationRunRead, ReconciliationStatusRead


class ReconciliationPartialFailure(RuntimeError):
    def __init__(self, failures: dict[str, str], counts: dict[str, int]):
        super().__init__("One or more reconciliation stages did not complete")
        self.failures = dict(failures)
        self.counts = dict(counts)


async def _sqlite_transaction_open(session) -> bool:
    if session.get_bind().dialect.name != "sqlite":
        return False
    connection = await session.connection()
    raw = await connection.get_raw_connection()
    return raw.driver_connection.in_transaction


class ReconciliationMonitor:
    def __init__(self, session, counts, first_phase, source):
        bind = session.bind
        self.factory = async_sessionmaker(bind.engine if isinstance(bind, AsyncConnection) else bind,
            expire_on_commit=False, autoflush=False)
        self.session = session
        self.run_id = None
        self.started_at = None
        self.deferred_reason = None
        self.source = source
        self.counts = dict(counts)
        self.phases = {first_phase: {"status": "running"}}
        self.current_phase = first_phase
        self.phase_started = time.monotonic()

    async def start(self):
        if await _sqlite_transaction_open(self.session):
            self.deferred_reason = "sqlite_existing_transaction"
            with self.session.no_autoflush:
                self.started_at = await self.session.scalar(select(func.current_timestamp()))
            return
        await self._insert()

    def record_unavailable(self, error_type):
        self.phases["monitoring"] = {"status": "failed", "error_type": error_type[:80], "error_code": "monitor_unavailable"}

    async def _insert(self):
        values = dict(source=self.source, status="running", current_phase=self.current_phase,
            phases=dict(self.phases), counts=dict(self.counts), deferred_reason=self.deferred_reason)
        if self.started_at is not None:
            # Match SQLite CURRENT_TIMESTAMP precision for its textual date CHECK.
            values["started_at"] = func.datetime(self.started_at)
        async with self.factory() as session:
            run = ReconciliationRun(**values)
            session.add(run)
            await session.flush()
            run_id = run.id
            await session.commit()
        self.run_id = run_id

    async def advance(self, phase, counts, *, next_phase=None, error_type=None, cancelled=False, failed=False):
        status = "cancelled" if cancelled else "failed" if error_type else "succeeded"
        self.phases[phase] = {"status": status, "duration_ms": max(0, round((time.monotonic() - self.phase_started) * 1000))}
        if error_type:
            self.phases[phase].update(error_type=error_type[:80], error_code="stage_failed")
        self.counts = dict(counts)
        self.current_phase = next_phase
        if next_phase:
            self.phases[next_phase] = {"status": "running"}
            self.phase_started = time.monotonic()
        if self.run_id is None:
            if await _sqlite_transaction_open(self.session):
                return
            await self._insert()
        values = dict(phases=dict(self.phases), counts=dict(self.counts), current_phase=next_phase, progress_at=func.now())
        if next_phase is None:
            values.update(status="cancelled" if cancelled else "partial_failure" if failed else "succeeded", finished_at=func.now())
        async with self.factory() as session:
            await session.execute(update(ReconciliationRun).where(
                ReconciliationRun.id == self.run_id, ReconciliationRun.status == "running").values(**values))
            await session.commit()


async def read_reconciliation_status(session, settings, *, payment_candidates=None, meal_candidates=None):
    from .reconciliation_retention import read_retention_status
    current = await session.scalar(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(1).execution_options(populate_existing=True))
    last_success = await session.scalar(select(ReconciliationRun).where(ReconciliationRun.status == "succeeded")
        .order_by(ReconciliationRun.id.desc()).limit(1).execution_options(populate_existing=True))
    now = await session.scalar(select(func.current_timestamp()))
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    stale_after = max(300, round(settings.reconciliation_interval_seconds * 3))
    current_read = ReconciliationRunRead.model_validate(current) if current else None
    delivery_state = func.coalesce(OutboxEvent.payload["email_delivery"]["state"].as_string(), "")
    conditions = [
        ("outbox_due", OutboxEvent, OutboxEvent.available_at, (OutboxEvent.status == OutboxStatus.PENDING) & (OutboxEvent.available_at <= now)),
        ("outbox_delayed", OutboxEvent, OutboxEvent.available_at, (OutboxEvent.status == OutboxStatus.PENDING) & (OutboxEvent.available_at > now)),
        ("outbox_processing", OutboxEvent, OutboxEvent.available_at, OutboxEvent.status == OutboxStatus.PROCESSING),
        ("outbox_stale", OutboxEvent, OutboxEvent.available_at, (OutboxEvent.status == OutboxStatus.PROCESSING) & (OutboxEvent.available_at <= now - timedelta(minutes=10))),
        ("outbox_failed", OutboxEvent, OutboxEvent.available_at, OutboxEvent.status == OutboxStatus.FAILED),
        ("email_unknown", OutboxEvent, OutboxEvent.available_at, OutboxEvent.event_type.in_(JOB_KINDS["email"]) & or_(
            delivery_state == "unknown", (OutboxEvent.status == OutboxStatus.FAILED) & (OutboxEvent.attempts > 0) & (delivery_state != "rejected"))),
        ("activities_due", Activity, Activity.ends_at, (Activity.status == ActivityStatus.PUBLISHED) & (Activity.ends_at <= now)),
        ("member_proposals_due", MemberProposal, case((MemberProposal.status == MemberProposalStatus.DISCUSSION,
            MemberProposal.discussion_ends_at), else_=MemberProposal.voting_ends_at), or_(
            (MemberProposal.status == MemberProposalStatus.DISCUSSION) & (MemberProposal.discussion_ends_at <= now),
            (MemberProposal.status == MemberProposalStatus.VOTING) & (MemberProposal.voting_ends_at <= now))),
    ]
    backlog = []
    if payment_candidates is not None:
        query = payment_candidates(settings, now).subquery()
        count, oldest_at = (await session.execute(select(func.count(PaymentAttempt.id), func.min(
            func.coalesce(PaymentAttempt.next_reconcile_at, PaymentAttempt.created_at))).where(PaymentAttempt.id.in_(select(query.c.id))))).one()
        backlog.append(ReconciliationBacklogRead(kind="payments_due", count=count, oldest_at=oldest_at))
    if meal_candidates is not None:
        query = meal_candidates(now).subquery()
        first_due = case((MealEvent.status == MealEventStatus.PUBLISHED,
            case((MealEvent.ordering_ends_at < MealEvent.pickup_starts_at, MealEvent.ordering_ends_at), else_=MealEvent.pickup_starts_at)),
            (MealEvent.status == MealEventStatus.ORDERING_CLOSED, MealEvent.pickup_starts_at), else_=MealEvent.pickup_ends_at)
        count, oldest_at = (await session.execute(select(func.count(MealEvent.id), func.min(first_due))
            .where(MealEvent.id.in_(select(query.c.id))))).one()
        backlog.append(ReconciliationBacklogRead(kind="meal_events_due", count=count, oldest_at=oldest_at))
    for kind, model, oldest, condition in conditions:
        count, oldest_at = (await session.execute(select(func.count(model.id), func.min(oldest)).where(condition))).one()
        backlog.append(ReconciliationBacklogRead(kind=kind, count=count, oldest_at=oldest_at))
    return ReconciliationStatusRead(current=current_read,
        last_success=ReconciliationRunRead.model_validate(last_success) if last_success else None,
        long_running=bool(current_read and current_read.status == "running" and (now - current_read.progress_at).total_seconds() >= stale_after),
        stale_after_seconds=stale_after, backlog=backlog, cleanup=await read_retention_status(session, settings, now))
