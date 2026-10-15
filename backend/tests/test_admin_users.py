from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.meal_schedules import TAIPEI
from app.models import (
    AdminAudit, MemberRosterEntry, Membership, MembershipApplication,
    MembershipApplicationStatus, MembershipCharge, MembershipChargeKind,
    MembershipChargeStatus, MembershipFeeSchedule, MembershipStatus,
    PaymentAttempt, PaymentStatus, Refund, User, UserRole,
)
from app.routers.admin_users import admin_users_router
from app.routers.membership import membership_router
from tests.support import api_test_context, auth_headers, make_test_settings


@pytest.fixture
async def users_context(database_session):
    admin = User(email="users-admin@example.com", display_name="會員管理員", password_hash="private-password-hash", user_role=UserRole.ADMIN)
    buyer = User(email="users-buyer@example.com", display_name="王小明", password_hash="private-password-hash")
    other = User(email="users-other@example.com", display_name="其他使用者", password_hash="private-password-hash")
    database_session.add_all([admin, buyer, other])
    await database_session.commit()
    async with api_test_context(database_session, [admin_users_router, membership_router], settings=make_test_settings()) as client:
        yield SimpleNamespace(session=database_session, client=client, admin=admin, buyer=buyer, other=other)


def confirmation(**overrides):
    return {"confirmed_offline": True, "confirmed_on": datetime.now(TAIPEI).date().isoformat(), "reason": "已核對線下入社及繳費紀錄", **overrides}


async def grant(c, target=None, **overrides):
    return await c.client.post(f"/v1/admin/users/{(target or c.buyer).id}/offline-membership", json=confirmation(**overrides), headers=auth_headers(c.admin))


def roster(number, **overrides):
    return MemberRosterEntry(member_number=number, legal_name_encrypted="private-name", email_encrypted="private-email", phone_encrypted="private-phone", **overrides)


@pytest.mark.asyncio
async def test_email_less_line_customer_can_be_found_and_linked_to_roster_by_admin(users_context):
    c = users_context
    c.buyer.email = None
    c.buyer.notification_email = "line-notices@example.com"
    entry = roster("LINE-100")
    c.session.add(entry)
    await c.session.commit()
    found = await c.client.get("/v1/admin/users", params={"search": "line-notices@example.com"}, headers=auth_headers(c.admin))
    assert found.status_code == 200 and found.json()["total"] == 1
    assert found.json()["items"][0]["email"] is None
    assert found.json()["items"][0]["membership_type"] == "nonmember"
    promoted = await grant(c, member_number="LINE-100")
    assert promoted.status_code == 200, promoted.text
    await c.session.refresh(entry)
    assert entry.claimed_user_id == c.buyer.id
    assert promoted.json()["membership_type"] == "member"


@pytest.mark.asyncio
async def test_remove_membership_updates_registered_user_without_deleting_records(users_context):
    c = users_context
    granted = await grant(c)
    assert granted.status_code == 200
    membership_id = granted.json()["membership_id"]
    path = f"/v1/admin/members/{membership_id}/suspend"
    assert (await c.client.post(path, json={"reason": "社員資料誤設"}, headers=auth_headers(c.other))).status_code == 403
    removed = await c.client.post(path, json={"reason": "社員資料誤設"}, headers=auth_headers(c.admin))
    assert removed.status_code == 200, removed.text
    listing = await c.client.get("/v1/admin/users", params={"search": c.buyer.email, "membership_type": "nonmember"}, headers=auth_headers(c.admin))
    item = listing.json()["items"][0]
    assert item["membership_type"] == "nonmember"
    assert item["membership_status"] == "suspended"
    assert item["membership_id"] == membership_id
    assert item["member_number"] == granted.json()["member_number"]
    summary = await c.client.get("/v1/members/me", headers=auth_headers(c.buyer))
    assert summary.json()["membership_type"] == "nonmember"
    assert await c.session.scalar(select(User.id).where(User.id == c.buyer.id)) == c.buyer.id
    audit = await c.session.scalar(select(AdminAudit).where(AdminAudit.action == "membership.suspended"))
    assert audit.reason == "社員資料誤設" and audit.actor_id == c.admin.id
    assert await c.session.scalar(select(func.count()).select_from(Refund)) == 0


@pytest.mark.asyncio
async def test_all_registered_users_search_and_membership_filter_are_admin_only(users_context):
    c = users_context
    c.session.add(Membership(user=c.other, status=MembershipStatus.ACTIVE, member_number="ROSTER_100%"))
    await c.session.commit()
    assert (await c.client.get("/v1/admin/users")).status_code == 401
    assert (await c.client.get("/v1/admin/users", headers=auth_headers(c.buyer))).status_code == 403
    all_users = await c.client.get("/v1/admin/users", headers=auth_headers(c.admin))
    assert all_users.status_code == 200, all_users.text
    assert all_users.json()["total"] == 3
    assert "password_hash" not in all_users.text and "private-password-hash" not in all_users.text
    for params, expected in [({"search": "王小明"}, {c.buyer.id}), ({"search": "ROSTER_100%"}, {c.other.id}), ({"membership_type": "member"}, {c.other.id}), ({"membership_type": "nonmember"}, {c.buyer.id, c.admin.id})]:
        result = await c.client.get("/v1/admin/users", params=params, headers=auth_headers(c.admin))
        assert {item["id"] for item in result.json()["items"]} == expected
    first = (await c.client.get("/v1/admin/users", params={"limit": 1}, headers=auth_headers(c.admin))).json()
    second = (await c.client.get("/v1/admin/users", params={"limit": 1, "offset": 1}, headers=auth_headers(c.admin))).json()
    assert first["items"][0]["id"] != second["items"][0]["id"]


@pytest.mark.asyncio
async def test_offline_grant_is_idempotent_audited_and_does_not_create_online_money(users_context):
    c = users_context
    first = await grant(c)
    assert first.status_code == 200, first.text
    second = await grant(c)
    assert second.status_code == 200, second.text
    assert first.json()["member_number"] == second.json()["member_number"]
    assert first.json()["membership_type"] == "member" and first.json()["user_role"] == "customer"
    assert await c.session.scalar(select(func.count(Membership.id))) == 1
    audits = list(await c.session.scalars(select(AdminAudit).where(AdminAudit.action == "membership.offline_confirmed")))
    assert len(audits) == 1 and audits[0].actor_id == c.admin.id
    assert audits[0].data["confirmed_on"] == confirmation()["confirmed_on"]
    assert audits[0].data["source"] == "offline"
    for model in [MembershipCharge, PaymentAttempt, Refund]:
        assert await c.session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_roster_link_preserves_member_number_shares_and_prevents_other_claim(users_context):
    c = users_context
    entry = roster("EXISTING-001", share_capital_amount=3000, share_count=3)
    c.session.add(entry)
    await c.session.commit()
    result = await grant(c, member_number="existing-001")
    assert result.status_code == 200, result.text
    member = await c.session.scalar(select(Membership).where(Membership.user_id == c.buyer.id))
    assert (member.member_number, member.share_capital_amount, member.share_count) == ("EXISTING-001", 3000, 3)
    assert entry.claimed_user_id == c.buyer.id and entry.pending_claim_user_id is None
    assert (await grant(c, target=c.other, member_number="EXISTING-001")).status_code == 409


@pytest.mark.asyncio
async def test_automatic_number_skips_unclaimed_roster_numbers(users_context):
    c = users_context
    number = f"SLF-{datetime.now(timezone.utc).year}-0001"
    c.session.add(roster(number))
    await c.session.commit()
    result = await grant(c)
    assert result.status_code == 200, result.text
    assert result.json()["member_number"] != number


@pytest.mark.parametrize("values", [{"confirmed_offline": False}, {"reason": "  "}, {"confirmed_on": "invalid"}, {"confirmed_on": (datetime.now(TAIPEI).date() + timedelta(days=1)).isoformat()}])
@pytest.mark.asyncio
async def test_grant_requires_explicit_offline_confirmation_and_valid_reason(users_context, values):
    c = users_context
    assert (await grant(c, **values)).status_code == 422
    assert await c.session.scalar(select(func.count(Membership.id))) == 0


async def seed_online_charge(c, status):
    application = MembershipApplication(user_id=c.buyer.id, status=MembershipApplicationStatus.SUBMITTED)
    membership = Membership(user=c.buyer, application=application, status=MembershipStatus.PENDING_PAYMENT)
    fee = MembershipFeeSchedule(charge_kind=MembershipChargeKind.ADMISSION_FEE, amount=500, effective_from=datetime.now(TAIPEI).date())
    charge = MembershipCharge(user=c.buyer, membership=membership, application=application, fee_schedule=fee, charge_kind=MembershipChargeKind.ADMISSION_FEE, amount=500, status=status)
    c.session.add(charge)
    await c.session.commit()
    return charge, membership, application


@pytest.mark.parametrize("status", [MembershipChargeStatus.PAID, MembershipChargeStatus.REFUND_PENDING])
@pytest.mark.asyncio
async def test_existing_online_money_is_not_overwritten_by_offline_grant(users_context, status):
    c = users_context
    charge, membership, _ = await seed_online_charge(c, status)
    result = await grant(c)
    assert result.status_code == 409, result.text
    assert charge.status == status and membership.status == MembershipStatus.PENDING_PAYMENT


@pytest.mark.asyncio
async def test_pending_online_charge_is_closed_without_faking_payment(users_context):
    c = users_context
    charge, membership, application = await seed_online_charge(c, MembershipChargeStatus.PENDING)
    attempt = PaymentAttempt(membership_charge_id=charge.id, provider="raygate", merchant_trade_no="OFFLINE-PENDING", amount=500, status=PaymentStatus.PENDING, expires_at=datetime.now(timezone.utc) + timedelta(minutes=30))
    c.session.add(attempt)
    await c.session.commit()
    result = await grant(c)
    assert result.status_code == 200, result.text
    assert membership.status == MembershipStatus.ACTIVE
    assert application.status == MembershipApplicationStatus.APPROVED
    assert charge.status == MembershipChargeStatus.WAIVED and charge.paid_at is None
    assert attempt.status == PaymentStatus.EXPIRED and attempt.paid_at is None


@pytest.mark.asyncio
async def test_inactive_account_and_other_users_cannot_grant_membership(users_context):
    c = users_context
    path = f"/v1/admin/users/{c.buyer.id}/offline-membership"
    assert (await c.client.post(path, json=confirmation(), headers=auth_headers(c.other))).status_code == 403
    c.buyer.is_active = False
    await c.session.commit()
    assert (await grant(c)).status_code == 409
