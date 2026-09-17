from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.member_shares import update_member_shares
from app.models import AdminAudit, MemberRosterEntry, Membership, MembershipCharge, MembershipChargeStatus, MembershipStatus, PaymentAttempt, Refund, User, UserRole
from app.routers.membership import membership_router
from app.schemas import MemberSharesUpdate
from tests.support import api_test_context, auth_headers
from tests.test_v2_routers import settings


@pytest.fixture
async def shares_context(database_session):
    session = database_session
    admin = User(email="shares-admin@example.com", display_name="股籍管理員", password_hash="test", user_role=UserRole.ADMIN)
    owner = User(email="shares-owner@example.com", display_name="既有社員", password_hash="test", email_verified_at=datetime.now(timezone.utc))
    session.add_all([admin, owner])
    await session.commit()
    async with api_test_context(session, [membership_router], settings=settings()) as client:
        created = await client.post("/v1/admin/member-roster", json={
            "member_number": "SHARES-001", "legal_name": "股籍測試人", "email": owner.email, "phone": "0912345678",
            "share_capital_amount": 0, "share_count": 0, "share_certificate_number": "LEGACY-SHARES-001",
            "share_subscribed_on": "2020-01-02", "share_paid_on": "2020-01-03",
        }, headers=auth_headers(admin))
        assert created.status_code == 201, created.text
        yield {
            "session": session, "client": client, "admin_id": admin.id, "owner_id": owner.id,
            "admin_headers": auth_headers(admin), "owner_headers": auth_headers(owner),
            "roster": created.json(),
        }


def change_body(record, **overrides):
    return {
        "share_capital_amount": 5000, "share_count": 5,
        "expected_updated_at": record["updated_at"], "reason": "依合作社線下紀錄補正",
        **overrides,
    }


async def claim_roster(context):
    result = await context["client"].post("/v1/membership/claim-existing", json={
        "member_number": "SHARES-001", "legal_name": "股籍測試人", "phone": "0912345678",
    }, headers=context["owner_headers"])
    assert result.status_code == 200, result.text
    return result.json()["id"]


@pytest.mark.asyncio
async def test_unclaimed_roster_edit_is_admin_only_and_does_not_create_money_or_accounts(shares_context):
    ctx = shares_context
    client, session, roster = ctx["client"], ctx["session"], ctx["roster"]
    path = f"/v1/admin/member-roster/{roster['id']}/shares"
    assert (await client.patch(path, json=change_body(roster))).status_code == 401
    assert (await client.patch(path, json=change_body(roster), headers=ctx["owner_headers"])).status_code == 403
    result = await client.patch(path, json=change_body(roster), headers=ctx["admin_headers"])
    assert result.status_code == 200, result.text
    assert result.json()["share_capital_amount"] == 5000
    assert result.json()["share_count"] == 5
    assert result.json()["updated_at"].endswith("Z")
    assert result.json()["updated_at"] != roster["updated_at"]
    assert result.json()["claimed"] is False
    for model in (Membership, MembershipCharge, PaymentAttempt, Refund):
        assert await session.scalar(select(func.count(model.id))) == 0
    assert await session.scalar(select(func.count(User.id))) == 2
    stored = await session.get(MemberRosterEntry, roster["id"])
    assert stored.share_certificate_number == "LEGACY-SHARES-001"
    assert stored.share_paid_on.isoformat() == "2020-01-03"
    assert stored.share_subscribed_on.isoformat() == "2020-01-02"


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["roster", "membership"])
async def test_claimed_edit_synchronizes_both_tables_with_one_private_audit(shares_context, target_kind):
    ctx = shares_context
    client, session = ctx["client"], ctx["session"]
    membership_id = await claim_roster(ctx)
    records = (await client.get("/v1/admin/members" if target_kind == "membership" else "/v1/admin/member-roster", headers=ctx["admin_headers"])).json()
    record = records[0]
    path = f"/v1/admin/members/{membership_id}/shares" if target_kind == "membership" else f"/v1/admin/member-roster/{ctx['roster']['id']}/shares"
    if target_kind == "membership":
        assert (await client.patch(path, json=change_body(record), headers=ctx["owner_headers"])).status_code == 403
    result = await client.patch(path, json=change_body(record), headers=ctx["admin_headers"])
    assert result.status_code == 200, result.text
    roster = await session.get(MemberRosterEntry, ctx["roster"]["id"])
    member = await session.get(Membership, membership_id)
    assert (roster.share_capital_amount, roster.share_count) == (5000, 5)
    assert (member.share_capital_amount, member.share_count) == (5000, 5)
    assert member.status == MembershipStatus.ACTIVE
    assert member.share_paid_on.isoformat() == "2020-01-03"
    assert member.share_certificate_number == "LEGACY-SHARES-001"
    audit = (await session.scalars(select(AdminAudit).where(AdminAudit.action == "member_shares.updated"))).one()
    assert audit.actor_id == ctx["admin_id"]
    assert audit.reason == "依合作社線下紀錄補正"
    assert audit.data["before"]["roster"]["share_capital_amount"] == 0
    assert audit.data["before"]["membership"]["share_count"] == 0
    assert audit.data["after"]["roster"]["share_capital_amount"] == 5000
    assert audit.data["after"]["membership"]["share_count"] == 5
    assert "股籍測試人" not in str(audit.data)
    assert "0912345678" not in str(audit.data)
    public = (await client.get("/v1/members/me", headers=ctx["owner_headers"])).json()["membership"]
    for key in ("share_capital_amount", "share_count", "share_certificate_number", "share_paid_on", "share_subscribed_on", "updated_at"):
        assert key not in public
    for model in (MembershipCharge, PaymentAttempt, Refund):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_new_claim_reads_previously_corrected_shares(shares_context):
    ctx = shares_context
    result = await ctx["client"].patch(f"/v1/admin/member-roster/{ctx['roster']['id']}/shares", json=change_body(ctx["roster"]), headers=ctx["admin_headers"])
    assert result.status_code == 200
    membership_id = await claim_roster(ctx)
    member = await ctx["session"].get(Membership, membership_id)
    assert (member.share_capital_amount, member.share_count) == (5000, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("share_capital_amount", -1), ("share_count", -1), ("share_count", True),
    ("share_capital_amount", 5.0), ("share_capital_amount", "5000"), ("share_count", "5"),
    ("share_capital_amount", 2147483648), ("share_count", 2147483648),
    ("reason", "  "), ("reason", "x" * 1001), ("expected_updated_at", "2026-09-17T11:00:00"),
    ("expected_updated_at", 0),
    ("share_paid_on", "2026-09-17"), ("status", "active"),
])
async def test_invalid_or_out_of_scope_share_changes_are_rejected(shares_context, field, value):
    ctx = shares_context
    result = await ctx["client"].patch(f"/v1/admin/member-roster/{ctx['roster']['id']}/shares", json=change_body(ctx["roster"], **{field: value}), headers=ctx["admin_headers"])
    assert result.status_code == 422, result.text
    assert await ctx["session"].scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "member_shares.updated")) == 0


@pytest.mark.asyncio
async def test_missing_version_rejected_and_stale_retry_does_not_overwrite(shares_context):
    ctx = shares_context
    path = f"/v1/admin/member-roster/{ctx['roster']['id']}/shares"
    missing = change_body(ctx["roster"])
    missing.pop("expected_updated_at")
    assert (await ctx["client"].patch(path, json=missing, headers=ctx["admin_headers"])).status_code == 422
    first = await ctx["client"].patch(path, json=change_body(ctx["roster"]), headers=ctx["admin_headers"])
    assert first.status_code == 200
    stale = await ctx["client"].patch(path, json=change_body(ctx["roster"], share_capital_amount=9000), headers=ctx["admin_headers"])
    assert stale.status_code == 409
    stored = await ctx["session"].get(MemberRosterEntry, ctx["roster"]["id"])
    assert stored.share_capital_amount == 5000
    assert await ctx["session"].scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "member_shares.updated")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["roster", "membership"])
async def test_invalid_claim_link_is_not_silently_repaired(shares_context, target_kind):
    ctx = shares_context
    membership_id = await claim_roster(ctx)
    member = await ctx["session"].get(Membership, membership_id)
    member.member_number = "MISMATCH"
    await ctx["session"].commit()
    records = (await ctx["client"].get("/v1/admin/members" if target_kind == "membership" else "/v1/admin/member-roster", headers=ctx["admin_headers"])).json()
    prefix = "members" if target_kind == "membership" else "member-roster"
    target_id = membership_id if target_kind == "membership" else ctx["roster"]["id"]
    response = await ctx["client"].patch(f"/v1/admin/{prefix}/{target_id}/shares", json=change_body(records[0]), headers=ctx["admin_headers"])
    assert response.status_code == 409
    assert await ctx["session"].scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "member_shares.updated")) == 0


@pytest.mark.asyncio
async def test_membership_without_roster_can_be_corrected_without_creating_roster(shares_context):
    ctx = shares_context
    user = User(email="new-online-member@example.com", display_name="線上社員", password_hash="test")
    member = Membership(user=user, member_number="ONLINE-001", status=MembershipStatus.ACTIVE)
    ctx["session"].add(member)
    await ctx["session"].commit()
    members = (await ctx["client"].get("/v1/admin/members", headers=ctx["admin_headers"])).json()
    row = next(record for record in members if record["id"] == member.id)
    result = await ctx["client"].patch(f"/v1/admin/members/{member.id}/shares", json=change_body(row), headers=ctx["admin_headers"])
    assert result.status_code == 200
    assert result.json()["share_count"] == 5
    assert await ctx["session"].scalar(select(func.count(MemberRosterEntry.id))) == 1


@pytest.mark.asyncio
async def test_rollback_reverts_both_share_records_and_audit(shares_context):
    ctx = shares_context
    membership_id = await claim_roster(ctx)
    records = (await ctx["client"].get("/v1/admin/member-roster", headers=ctx["admin_headers"])).json()
    await update_member_shares(ctx["session"], target_kind="roster", target_id=ctx["roster"]["id"], actor_id=ctx["admin_id"], changes=MemberSharesUpdate(**change_body(records[0])))
    await ctx["session"].rollback()
    roster = await ctx["session"].get(MemberRosterEntry, ctx["roster"]["id"])
    member = await ctx["session"].get(Membership, membership_id)
    assert (roster.share_capital_amount, member.share_capital_amount) == (0, 0)
    assert await ctx["session"].scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "member_shares.updated")) == 0


@pytest.mark.asyncio
async def test_missing_targets_return_404(shares_context):
    for path in ("member-roster", "members"):
        response = await shares_context["client"].patch(f"/v1/admin/{path}/missing/shares", json=change_body(shares_context["roster"]), headers=shares_context["admin_headers"])
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_noop_still_rotates_version_and_member_endpoint_rejects_stale_snapshot(shares_context):
    ctx = shares_context
    membership_id = await claim_roster(ctx)
    member = (await ctx["client"].get("/v1/admin/members", headers=ctx["admin_headers"])).json()[0]
    roster = (await ctx["client"].get("/v1/admin/member-roster", headers=ctx["admin_headers"])).json()[0]
    updated = await ctx["client"].patch(f"/v1/admin/member-roster/{roster['id']}/shares", json=change_body(roster, share_capital_amount=0, share_count=0), headers=ctx["admin_headers"])
    assert updated.status_code == 200
    assert updated.json()["updated_at"] != roster["updated_at"]
    stale = await ctx["client"].patch(f"/v1/admin/members/{membership_id}/shares", json=change_body(member), headers=ctx["admin_headers"])
    assert stale.status_code == 409
    stored = await ctx["session"].get(Membership, membership_id)
    assert stored.share_capital_amount == 0


@pytest.mark.asyncio
async def test_missing_claimed_membership_is_rejected_without_changing_roster(shares_context):
    ctx = shares_context
    roster = await ctx["session"].get(MemberRosterEntry, ctx["roster"]["id"])
    roster.claimed_user_id = ctx["owner_id"]
    await ctx["session"].commit()
    record = (await ctx["client"].get("/v1/admin/member-roster", headers=ctx["admin_headers"])).json()[0]
    result = await ctx["client"].patch(f"/v1/admin/member-roster/{record['id']}/shares", json=change_body(record), headers=ctx["admin_headers"])
    assert result.status_code == 409
    stored = await ctx["session"].get(MemberRosterEntry, ctx["roster"]["id"])
    assert stored.share_capital_amount == 0


@pytest.mark.asyncio
async def test_share_correction_does_not_rewrite_existing_paid_charge_or_membership_status(shares_context):
    from tests.test_v2_payment_jobs import make_pending_membership

    ctx = shares_context
    _user, membership, _admission, capital = await make_pending_membership(ctx["session"])
    capital.status = MembershipChargeStatus.PAID
    capital.receipt_number = "PAID-ORIGINAL-001"
    capital.paid_at = datetime(2026, 1, 5, tzinfo=timezone.utc)
    await ctx["session"].commit()
    members = (await ctx["client"].get("/v1/admin/members", headers=ctx["admin_headers"])).json()
    record = next(row for row in members if row["id"] == membership.id)
    result = await ctx["client"].patch(f"/v1/admin/members/{membership.id}/shares", json=change_body(record), headers=ctx["admin_headers"])
    assert result.status_code == 200
    await ctx["session"].refresh(capital)
    await ctx["session"].refresh(membership)
    assert capital.amount == 1000
    assert capital.status == MembershipChargeStatus.PAID
    assert capital.receipt_number == "PAID-ORIGINAL-001"
    assert capital.paid_at.date().isoformat() == "2026-01-05"
    assert membership.status == MembershipStatus.PENDING_PAYMENT
    assert membership.share_paid_on is None
    assert membership.share_capital_amount == 5000
    assert await ctx["session"].scalar(select(func.count(Refund.id))) == 0
