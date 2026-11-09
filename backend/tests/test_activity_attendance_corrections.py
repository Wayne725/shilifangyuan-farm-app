from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import (
    Activity, ActivityRegistration, ActivityRegistrationStatus, ActivityStatus,
    AdminAudit, PointAccount, PointSourceType, PointTransaction,
)
from tests.support import auth_headers
from tests.test_v2_routers import v2_context


@pytest.fixture
async def attendance_context(v2_context):
    context = v2_context
    session, now = context["session"], datetime.now(timezone.utc)
    activity = Activity(title="已結束活動補登", location="合作社", created_by_id=context["member_a"].id,
        starts_at=now - timedelta(hours=3), ends_at=now - timedelta(hours=1),
        registration_deadline=now - timedelta(days=1), capacity=10, status=ActivityStatus.COMPLETED)
    session.add(activity)
    await session.flush()
    registration = ActivityRegistration(activity_id=activity.id, user_id=context["member_a"].id,
        status=ActivityRegistrationStatus.NO_SHOW, queue_position=1)
    session.add(registration)
    await session.commit()
    return {**context, "activity": activity, "registration": registration,
            "path": f"/v1/admin/activities/{activity.id}/registrations/{registration.id}"}


async def points(session):
    return int(await session.scalar(select(func.coalesce(func.sum(PointTransaction.amount), 0))) or 0)


@pytest.mark.asyncio
async def test_late_attendance_is_correctable_with_optional_reason_and_append_only_points(attendance_context):
    c = attendance_context
    client, session, registration = c["client"], c["session"], c["registration"]
    original_revision = registration.revision
    for expected, target, total in [("no_show", "attended", 5), ("attended", "no_show", 0), ("no_show", "attended", 5)]:
        response = await client.post(f"{c['path']}/{target}", headers=auth_headers(c["admin"]), json={
            "expected_status": expected, "reason": "  ",
        })
        assert response.status_code == 200, response.text
        assert response.json()["status"] == target
        assert response.json()["revision"] == original_revision
        assert await points(session) == total
    entries = list(await session.scalars(select(PointTransaction).order_by(PointTransaction.created_at, PointTransaction.id)))
    assert [entry.amount for entry in entries] == [5, -5, 5]
    assert all(entry.source_type == PointSourceType.ADMIN_ADJUSTMENT for entry in entries)
    audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.action == "activity.attendance_corrected")))
    assert len(audits) == 3
    assert all(audit.reason == "" for audit in audits)
    assert {entry.reference_id for entry in entries} == {audit.id for audit in audits}
    replay = await client.post(f"{c['path']}/attended", headers=auth_headers(c["admin"]), json={
        "expected_status": "no_show", "reason": "同一次回應遺失的重送",
    })
    assert replay.status_code == 409
    assert await points(session) == 5
    assert await session.scalar(select(func.count(PointTransaction.id))) == 3


@pytest.mark.asyncio
async def test_initial_attendance_points_are_reversed_without_rewriting_original(attendance_context):
    c = attendance_context
    c["registration"].status = ActivityRegistrationStatus.REGISTERED
    await c["session"].commit()
    initial = await c["client"].post(f"{c['path']}/attended", headers=auth_headers(c["admin"]))
    assert initial.status_code == 200, initial.text
    original = await c["session"].scalar(select(PointTransaction))
    original_id = original.id
    corrected = await c["client"].post(f"{c['path']}/no_show", headers=auth_headers(c["admin"]), json={
        "expected_status": "attended", "reason": "誤點出席，核對後更正",
    })
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["checked_in_at"] is None
    assert await points(c["session"]) == 0
    await c["session"].refresh(original)
    assert original.id == original_id and original.amount == 5 and original.source_type == PointSourceType.ACTIVITY


@pytest.mark.asyncio
async def test_restoring_historical_attendance_restores_original_credit_not_current_rate(attendance_context):
    c = attendance_context
    c["registration"].status = ActivityRegistrationStatus.ATTENDED
    account = PointAccount(user_id=c["member_a"].id)
    c["session"].add(account)
    await c["session"].flush()
    original = PointTransaction(account_id=account.id, amount=80, source_type=PointSourceType.ACTIVITY,
        reference_id=c["activity"].id, note="歷史活動原給80點")
    c["session"].add(original)
    await c["session"].commit()
    for expected, target, total in [("attended", "no_show", 0), ("no_show", "attended", 80)]:
        response = await c["client"].post(f"{c['path']}/{target}", headers=auth_headers(c["admin"]),
            json={"expected_status": expected, "reason": "更正並保留歷史點額"})
        assert response.status_code == 200, response.text
        assert await points(c["session"]) == total
    await c["session"].refresh(original)
    assert original.amount == 80 and original.note == "歷史活動原給80點"


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [({}, 422), ({"expected_status": "no_show", "reason": "x" * 1001}, 422),
    ({"expected_status": "attended", "reason": "過期畫面"}, 409)])
async def test_correction_requires_current_status_and_bounded_reason(attendance_context, body, status):
    c = attendance_context
    rejected = await c["client"].post(f"{c['path']}/attended", headers=auth_headers(c["admin"]), json=body)
    assert rejected.status_code == status
    assert await points(c["session"]) == 0
    assert c["registration"].status == ActivityRegistrationStatus.NO_SHOW
    assert await c["session"].scalar(select(func.count(AdminAudit.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("registration_status", ["cancelled", "waitlisted", "pending_payment"])
async def test_cancelled_waitlisted_or_unpaid_registration_cannot_be_marked_attended(attendance_context, registration_status):
    c = attendance_context
    c["registration"].status = ActivityRegistrationStatus(registration_status)
    await c["session"].commit()
    response = await c["client"].post(f"{c['path']}/attended", headers=auth_headers(c["admin"]),
        json={"expected_status": "no_show", "reason": "不可繞過正常報名付款"})
    assert response.status_code == 409
    assert await points(c["session"]) == 0


@pytest.mark.asyncio
async def test_correction_does_not_grant_nonmember_points_and_requires_admin(attendance_context):
    c = attendance_context
    c["registration"].user_id = c["customer_b"].id
    await c["session"].commit()
    body = {"expected_status": "no_show", "reason": "核對完成"}
    assert (await c["client"].post(f"{c['path']}/attended", json=body)).status_code == 401
    assert (await c["client"].post(f"{c['path']}/attended", headers=auth_headers(c["member_b"]), json=body)).status_code == 403
    response = await c["client"].post(f"{c['path']}/attended", headers=auth_headers(c["admin"]), json=body)
    assert response.status_code == 200, response.text
    assert await points(c["session"]) == 0
    assert await c["session"].scalar(select(func.count(PointAccount.id))) == 0
