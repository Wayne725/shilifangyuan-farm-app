from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.membership_admission_guards import LEGACY_ADMISSION_BLOCKED
from app.models import (
    AdminAudit,
    Membership,
    MembershipAdmissionRound,
    MembershipApplication,
    MembershipApplicationStatus,
    MembershipCharge,
    MembershipChargeKind,
    MembershipChargeStatus,
    MembershipFeeSchedule,
    MembershipStatus,
    OutboxEvent,
)
from app.routers.membership import _ensure_membership_charges, start_traineeship_if_fully_paid
from tests.support import auth_headers
from tests.test_admin_users import check_phone, grant, users_context
from tests.test_v2_routers import v2_context


pytestmark = pytest.mark.asyncio


async def admission_round(session, user, **overrides):
    record = MembershipAdmissionRound(user_id=user.id, round_number=1, idempotency_key=str(uuid4()), **overrides)
    session.add(record)
    await session.commit()
    return record


@pytest.mark.parametrize("round_status", ["draft", "submitted", "needs_supplement", "approved", "rejected", "withdrawn"])
async def test_new_admission_cannot_use_legacy_approval_or_charge_service(v2_context, round_status):
    c = v2_context
    application = MembershipApplication(
        user_id=c["applicant"].id, status=MembershipApplicationStatus.SUBMITTED,
    )
    c["session"].add(application)
    await admission_round(c["session"], c["applicant"], status=round_status)
    response = await c["client"].post(
        f"/v1/admin/membership-applications/{application.id}/approve",
        headers=auth_headers(c["admin"]), json={},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == LEGACY_ADMISSION_BLOCKED
    with pytest.raises(HTTPException) as error:
        await _ensure_membership_charges(c["session"], application)
    assert error.value.status_code == 409
    await c["session"].refresh(application)
    assert application.status == MembershipApplicationStatus.SUBMITTED
    assert application.reviewed_at is None
    assert await c["session"].scalar(select(func.count(Membership.id)).where(Membership.user_id == c["applicant"].id)) == 0
    assert await c["session"].scalar(select(func.count(MembershipCharge.id))) == 0
    assert await c["session"].scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "membership.application_approved")) == 0


async def test_new_admission_cannot_be_activated_by_offline_confirmation(users_context):
    c = users_context
    await admission_round(c.session, c.buyer)
    preview = await check_phone(c)
    assert preview.status_code == 409
    assert preview.json()["detail"] == LEGACY_ADMISSION_BLOCKED
    response = await grant(c)
    assert response.status_code == 409
    assert response.json()["detail"] == LEGACY_ADMISSION_BLOCKED
    assert await c.session.scalar(select(func.count(Membership.id))) == 0
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.offline_confirmed")) == 0


async def test_new_admission_cannot_use_legacy_trainee_activation(v2_context):
    c = v2_context
    membership = Membership(user_id=c["applicant"].id, status=MembershipStatus.TRAINEE, trainee_number="TEST-TRAINEE")
    c["session"].add(membership)
    await admission_round(c["session"], c["applicant"])
    response = await c["client"].post(
        f"/v1/admin/members/{membership.id}/activate",
        headers=auth_headers(c["admin"]), json={"reason": "旁路回歸驗證"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == LEGACY_ADMISSION_BLOCKED
    await c["session"].refresh(membership)
    assert membership.status == MembershipStatus.TRAINEE
    assert membership.member_number is None
    assert membership.activated_at is None


async def test_legacy_payment_record_is_preserved_without_promoting_new_admission(v2_context):
    c = v2_context
    application = MembershipApplication(user_id=c["applicant"].id, status=MembershipApplicationStatus.APPROVED)
    c["session"].add(application)
    await c["session"].flush()
    membership = Membership(user_id=c["applicant"].id, application_id=application.id, status=MembershipStatus.PENDING_PAYMENT)
    c["session"].add(membership)
    await c["session"].flush()
    for kind in MembershipChargeKind:
        schedule = MembershipFeeSchedule(charge_kind=kind, amount=100, effective_from=date(2020, 1, 1))
        c["session"].add(schedule)
        await c["session"].flush()
        c["session"].add(MembershipCharge(
            user_id=c["applicant"].id, application_id=application.id,
            membership_id=membership.id, fee_schedule_id=schedule.id,
            charge_kind=kind, amount=100, status=MembershipChargeStatus.PAID,
        ))
    await admission_round(c["session"], c["applicant"])
    await start_traineeship_if_fully_paid(c["session"], membership.id)
    await c["session"].commit()
    assert membership.status == MembershipStatus.PENDING_PAYMENT
    assert membership.trainee_number is None
    charges = list(await c["session"].scalars(select(MembershipCharge)))
    assert len(charges) == 2
    assert all(charge.status == MembershipChargeStatus.PAID for charge in charges)


async def test_authoritative_roster_claim_remains_available_for_new_admission(v2_context):
    c = v2_context
    record = await admission_round(c["session"], c["applicant"])
    created = await c["client"].post(
        "/v1/admin/member-roster", headers=auth_headers(c["admin"]),
        json={"member_number": "EXISTING-001", "legal_name": "申請人", "email": c["applicant"].email, "phone": "0900000000"},
    )
    assert created.status_code == 201, created.text
    claimed = await c["client"].post(
        "/v1/membership/claim-existing", headers=auth_headers(c["applicant"]),
        json={"member_number": "EXISTING-001", "legal_name": "申請人", "phone": "0900000000"},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "active"
    assert claimed.json()["member_number"] == "EXISTING-001"
    assert await c["session"].scalar(select(func.count(MembershipCharge.id))) == 0
    await c["session"].refresh(record)
    assert record.user_id == c["applicant"].id
