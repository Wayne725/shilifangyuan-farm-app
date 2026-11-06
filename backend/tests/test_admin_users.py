import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.integrations.pii_crypto import pii_cipher_from_settings
from app.member_claims import normalize_phone, roster_aad
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
    entry = roster("OFFLINE-001")
    database_session.add_all([admin, buyer, other, entry])
    await database_session.commit()
    async with api_test_context(database_session, [admin_users_router, membership_router], settings=offline_settings()) as client:
        yield SimpleNamespace(session=database_session, client=client, admin=admin, buyer=buyer, other=other, entry=entry)


def confirmation(**overrides):
    return {"confirmed_offline": True, "confirmed_on": datetime.now(TAIPEI).date().isoformat(), "reason": "已核對線下入社及繳費紀錄", "phone": "0912345678", "roster_entry_id": "missing-roster", **overrides}


async def grant(c, target=None, **overrides):
    return await c.client.post(f"/v1/admin/users/{(target or c.buyer).id}/offline-membership", json=confirmation(**{"roster_entry_id": c.entry.id, **overrides}), headers=auth_headers(c.admin))


def offline_settings():
    return make_test_settings(pii_encryption_keys_json=json.dumps({"v1": base64.b64encode(b"p" * 32).decode()}))


def roster(number, *, phone="0912345678", legal_name="王小明", settings=None, **overrides):
    entry_id = overrides.pop("id", str(uuid4()))
    cipher = pii_cipher_from_settings(settings or offline_settings())
    aad = roster_aad(entry_id)
    return MemberRosterEntry(
        id=entry_id, member_number=number,
        legal_name_encrypted=cipher.encrypt_text(legal_name, associated_data=aad),
        email_encrypted=cipher.encrypt_text("roster@example.com", associated_data=aad),
        phone_encrypted=cipher.encrypt_text(normalize_phone(phone), associated_data=aad),
        **overrides,
    )


async def check_phone(c, target=None, phone="0912345678", headers=None):
    return await c.client.post(f"/v1/admin/users/{(target or c.buyer).id}/membership-phone-check",
        json={"phone": phone}, headers=headers if headers is not None else auth_headers(c.admin))


@pytest.mark.asyncio
async def test_email_less_line_customer_can_be_found_and_linked_to_roster_by_admin(users_context):
    c = users_context
    c.buyer.email = None
    c.buyer.notification_email = "line-notices@example.com"
    entry = c.entry
    entry.member_number = "LINE-100"
    await c.session.commit()
    found = await c.client.get("/v1/admin/users", params={"search": "line-notices@example.com"}, headers=auth_headers(c.admin))
    assert found.status_code == 200 and found.json()["total"] == 1
    assert found.json()["items"][0]["email"] is None
    assert found.json()["items"][0]["membership_type"] == "nonmember"
    promoted = await grant(c)
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
    entry = c.entry
    entry.member_number, entry.share_capital_amount, entry.share_count = "EXISTING-001", 3000, 3
    await c.session.commit()
    result = await grant(c, phone="+886 912-345-678")
    assert result.status_code == 200, result.text
    member = await c.session.scalar(select(Membership).where(Membership.user_id == c.buyer.id))
    assert (member.member_number, member.share_capital_amount, member.share_count) == ("EXISTING-001", 3000, 3)
    assert entry.claimed_user_id == c.buyer.id and entry.pending_claim_user_id is None
    assert (await grant(c, target=c.other)).status_code == 409


@pytest.mark.asyncio
async def test_missing_roster_phone_cannot_create_an_automatic_member_number(users_context):
    c = users_context
    result = await grant(c, phone="0987654321")
    assert result.status_code == 409, result.text
    assert "查無" in result.json()["detail"]
    assert await c.session.scalar(select(func.count(Membership.id))) == 0
    assert await c.session.scalar(select(func.count(AdminAudit.id))) == 0
    assert c.entry.claimed_user_id is None


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


@pytest.mark.asyncio
async def test_phone_preview_normalizes_contact_and_does_not_grant_or_disclose_it(users_context):
    c = users_context
    response = await check_phone(c, phone="＋８８６ ９１２－３４５－６７８")
    assert response.status_code == 200, response.text
    assert response.json() == {"roster_entry_id": c.entry.id, "member_number": "OFFLINE-001",
        "legal_name": "王小明", "phone_masked": "******5678"}
    assert "0912345678" not in response.text
    assert "roster@example.com" not in response.text
    assert c.entry.claimed_user_id is None and c.entry.pending_claim_user_id is None
    for model in (Membership, MembershipCharge, PaymentAttempt, Refund, AdminAudit):
        assert await c.session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_phone_preview_and_grant_are_admin_only(users_context):
    c = users_context
    assert (await check_phone(c, headers={})).status_code == 401
    assert (await check_phone(c, headers=auth_headers(c.buyer))).status_code == 403
    c.buyer.is_active = False
    await c.session.commit()
    assert (await check_phone(c)).status_code == 409


@pytest.mark.parametrize("phone", ["", "0912", "a" * 10, "1" * 16])
@pytest.mark.asyncio
async def test_invalid_phone_rejected_by_preview_and_grant(users_context, phone):
    assert (await check_phone(users_context, phone=phone)).status_code == 422
    assert (await grant(users_context, phone=phone)).status_code == 422


@pytest.mark.asyncio
async def test_phone_and_confirmed_roster_are_required_and_number_cannot_bypass_them(users_context):
    c = users_context
    path = f"/v1/admin/users/{c.buyer.id}/offline-membership"
    for missing in ("phone", "roster_entry_id"):
        body = confirmation(roster_entry_id=c.entry.id)
        del body[missing]
        response = await c.client.post(path, json=body, headers=auth_headers(c.admin))
        assert response.status_code == 422
    assert (await grant(c, member_number="ARBITRARY-NUMBER")).status_code == 422
    assert await c.session.scalar(select(func.count(Membership.id))) == 0


@pytest.mark.parametrize("active", [True, False])
@pytest.mark.asyncio
async def test_duplicate_phone_does_not_pick_a_roster_entry(users_context, active):
    c = users_context
    c.session.add(roster("DUPLICATE-002", phone="+886912345678", is_active=active))
    await c.session.commit()
    for response in (await check_phone(c), await grant(c)):
        assert response.status_code == 409
        assert "多筆" in response.json()["detail"]
    assert await c.session.scalar(select(func.count(Membership.id))) == 0


@pytest.mark.parametrize("change", ["inactive", "claimed", "pending", "changed_phone", "duplicate", "deleted"])
@pytest.mark.asyncio
async def test_phone_preview_cannot_be_reused_after_roster_changes(users_context, change):
    c = users_context
    checked = await check_phone(c)
    assert checked.status_code == 200
    if change == "inactive":
        c.entry.is_active = False
    elif change == "claimed":
        c.entry.claimed_user_id = c.other.id
    elif change == "pending":
        c.entry.pending_claim_user_id = c.other.id
        c.entry.pending_claim_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    elif change == "changed_phone":
        c.entry.phone_encrypted = pii_cipher_from_settings(offline_settings()).encrypt_text("0987654321", associated_data=roster_aad(c.entry.id))
    elif change == "duplicate":
        c.session.add(roster("DUPLICATE-002"))
    else:
        await c.session.delete(c.entry)
    await c.session.commit()
    response = await grant(c, roster_entry_id=checked.json()["roster_entry_id"])
    assert response.status_code == 409, response.text
    assert await c.session.scalar(select(func.count(Membership.id))) == 0
    assert await c.session.scalar(select(func.count(AdminAudit.id))) == 0


@pytest.mark.asyncio
async def test_confirmed_roster_id_must_match_current_phone_result(users_context):
    c = users_context
    other_entry = roster("OTHER-PHONE", phone="0987654321")
    c.session.add(other_entry)
    await c.session.commit()
    response = await grant(c, roster_entry_id=other_entry.id)
    assert response.status_code == 409 and "核對結果已變更" in response.json()["detail"]
    assert await c.session.scalar(select(func.count(Membership.id))) == 0


@pytest.mark.asyncio
async def test_suspended_membership_restores_same_number_and_shares_by_phone(users_context):
    c = users_context
    c.entry.member_number, c.entry.share_count, c.entry.share_capital_amount = "RESTORE-001", 10, 1000
    c.entry.claimed_user_id = c.buyer.id
    member = Membership(user=c.buyer, status=MembershipStatus.SUSPENDED, member_number="RESTORE-001", share_count=10, share_capital_amount=1000)
    c.session.add(member)
    await c.session.commit()
    assert (await check_phone(c)).status_code == 200
    first = await grant(c)
    second = await grant(c)
    assert first.status_code == second.status_code == 200
    assert first.json()["membership_id"] == second.json()["membership_id"] == member.id
    assert (member.member_number, member.share_count, member.share_capital_amount) == ("RESTORE-001", 10, 1000)
    audit = await c.session.scalar(select(AdminAudit).where(AdminAudit.action == "membership.offline_confirmed"))
    assert audit.data["phone_verified"] is True and audit.data["roster_entry_id"] == c.entry.id
    assert "0912345678" not in json.dumps(audit.data)
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.offline_confirmed")) == 1


@pytest.mark.parametrize("conflict", ["number", "linked_roster", "shares", "number_owner", "resigned", "terminated"])
@pytest.mark.asyncio
async def test_phone_match_does_not_override_existing_membership_conflicts(users_context, conflict):
    c = users_context
    member = Membership(user=c.buyer, status=MembershipStatus.SUSPENDED, member_number="OFFLINE-001")
    if conflict == "number":
        member.member_number = "OLD-001"
    elif conflict == "linked_roster":
        c.session.add(roster("OTHER-001", phone="0987654321", claimed_user_id=c.buyer.id))
    elif conflict == "shares":
        member.share_count, member.share_capital_amount = 10, 1000
    elif conflict == "number_owner":
        member.member_number = None
        c.session.add(Membership(user=c.other, status=MembershipStatus.ACTIVE, member_number="OFFLINE-001"))
    else:
        member.status = MembershipStatus(conflict)
    c.session.add(member)
    await c.session.commit()
    response = await grant(c)
    assert response.status_code == 409, response.text
    assert member.status != MembershipStatus.ACTIVE
    assert c.entry.claimed_user_id is None
    assert await c.session.scalar(select(func.count(AdminAudit.id))) == 0


@pytest.mark.asyncio
async def test_expired_other_claim_can_be_reassigned_after_phone_check(users_context):
    c = users_context
    c.entry.pending_claim_user_id = c.other.id
    c.entry.pending_claim_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await c.session.commit()
    assert (await check_phone(c)).status_code == 200
    assert c.entry.pending_claim_user_id == c.other.id
    response = await grant(c)
    assert response.status_code == 200, response.text
    assert c.entry.claimed_user_id == c.buyer.id and c.entry.pending_claim_user_id is None


@pytest.mark.asyncio
async def test_unreadable_roster_does_not_silently_bypass_phone_uniqueness(users_context):
    c = users_context
    c.entry.phone_encrypted = "invalid-ciphertext"
    await c.session.commit()
    assert (await check_phone(c)).status_code == 503
    assert (await grant(c)).status_code == 503
    assert await c.session.scalar(select(func.count(Membership.id))) == 0
