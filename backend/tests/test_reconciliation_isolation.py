from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.jobs as jobs
from app.integrations.email_sender import EmailSendResult
from app.models import Activity, ActivityStatus, OutboxEvent, OutboxStatus, SystemSetting, User
from tests.support import make_test_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_phase", [None, "_reconcile_expired_payments", "_reconcile_member_proposals"])
async def test_phase_failure_does_not_starve_other_due_domains(database_session, monkeypatch, failed_phase):
    session = database_session
    now = datetime.now(timezone.utc)
    user = User(email="runner-isolation@example.test", display_name="隔離背景測試", password_hash="unused")
    session.add(user)
    await session.flush()
    activity = Activity(created_by_id=user.id, title="失敗階段後仍應結算", location="隔離",
        starts_at=now - timedelta(hours=3), ends_at=now - timedelta(hours=1),
        registration_deadline=now - timedelta(days=1), capacity=2, status=ActivityStatus.PUBLISHED)
    event = OutboxEvent(event_type="send_email", aggregate_type="user", aggregate_id=user.id,
        available_at=now - timedelta(seconds=1), payload={"to_email": user.email, "subject": "隔離假通知", "text_content": "不可寄到外網"})
    session.add_all([activity, event])
    await session.commit()
    activity_id, event_id = activity.id, event.id
    factory = async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)
    calls = []
    delivered = []
    dirty_stage_visible_to_tail = []
    original_activity = jobs._reconcile_activities
    original_outbox = jobs._process_outbox
    fault_key = "audit:ops01:failed-stage-write"

    async def activities(worker, *args, **kwargs):
        calls.append("activities")
        dirty_stage_visible_to_tail.append(await worker.get(SystemSetting, fault_key) is not None)
        return await original_activity(worker, *args, **kwargs)

    async def outbox(*args, **kwargs):
        calls.append("outbox")
        return await original_outbox(*args, **kwargs)

    class FakeEmailSender:
        async def send(self, message):
            delivered.append(message.subject)
            return EmailSendResult(True, "isolated-runner-receipt", 202, "mock")

    monkeypatch.setattr(jobs, "_reconcile_activities", activities)
    monkeypatch.setattr(jobs, "_process_outbox", outbox)
    monkeypatch.setattr(jobs, "email_sender_from_settings", lambda _: FakeEmailSender())
    if failed_phase:
        async def fail(worker, *args, **kwargs):
            calls.append(failed_phase)
            worker.add(SystemSetting(key=fault_key, value={"synthetic_uncommitted": True}))
            await worker.flush()
            raise RuntimeError("controlled stage failure, no external integration")
        monkeypatch.setattr(jobs, failed_phase, fail)
    failure = None
    report = None
    async with factory() as worker:
        try:
            report = await jobs.reconcile_once(worker, make_test_settings(), now=now)
        except Exception as exc:
            failure = type(exc).__name__
            # Cleanup is the caller's current behavior, not evidence of isolation inside reconcile_once.
            await worker.rollback()
    async with factory() as reader:
        status = (await reader.get(Activity, activity_id)).status.value
        event_status = (await reader.get(OutboxEvent, event_id)).status.value
        bad_write_exists = await reader.get(SystemSetting, fault_key) is not None
    result = {"phase": failed_phase or "normal-control", "exception_type": failure, "calls": calls,
        "activity_status": status, "outbox_status": event_status, "fake_delivered": delivered,
        "failed_stage_write_visible_to_tail": dirty_stage_visible_to_tail,
        "failed_stage_write_committed": bad_write_exists, "report": report.to_dict() if report else None,
        "external_requests": 0}
    assert status == ActivityStatus.COMPLETED.value, result
    assert event_status == OutboxStatus.COMPLETED.value, result
    assert calls[-2:] == ["activities", "outbox"], result
    assert delivered == ["隔離假通知"], result
    assert dirty_stage_visible_to_tail == [False] and not bad_write_exists, result
    assert failure in ({None} if failed_phase is None else {None, "ReconciliationPartialFailure"}), result
