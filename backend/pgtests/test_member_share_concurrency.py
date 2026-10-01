"""Uses only the disposable localhost PostgreSQL schema fixture."""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.member_claims import attach_roster_membership
from app.member_shares import MemberSharesError, update_member_shares
from app.models import AdminAudit, MemberRosterEntry, Membership, User, UserRole
from app.schemas import MemberSharesUpdate
from pgtests.test_transaction_concurrency import postgres_sessions


async def seed_share_records(factory, *, claimed: bool):
    async with factory() as session:
        admin = User(email="pg-share-admin@example.com", display_name="管理員甲", password_hash="test", user_role=UserRole.ADMIN)
        other_admin = User(email="pg-share-admin-other@example.com", display_name="管理員乙", password_hash="test", user_role=UserRole.ADMIN)
        owner = User(email="pg-share-owner@example.com", display_name="社員", password_hash="test", email_verified_at=datetime.now(timezone.utc))
        roster = MemberRosterEntry(member_number="PG-SHARES-001", legal_name_encrypted="synthetic", email_encrypted="synthetic", phone_encrypted="synthetic", share_capital_amount=0, share_count=0)
        session.add_all([admin, other_admin, owner, roster])
        await session.flush()
        membership = await attach_roster_membership(session, roster, owner) if claimed else None
        await session.commit()
        return {
            "admin_id": admin.id, "other_admin_id": other_admin.id, "owner_id": owner.id,
            "roster_id": roster.id, "roster_version": roster.updated_at,
            "membership_id": membership.id if membership else None,
            "membership_version": membership.updated_at if membership else None,
        }


def changes(version, amount=5000, count=5):
    return MemberSharesUpdate(share_capital_amount=amount, share_count=count, reason="隔離並行驗證", expected_updated_at=version)


@pytest.mark.asyncio
async def test_roster_and_membership_editors_serialize_and_stale_editor_cannot_overwrite(postgres_sessions):
    state = await seed_share_records(postgres_sessions, claimed=True)
    first_locked = asyncio.Event()
    second_started = asyncio.Event()
    allow_commit = asyncio.Event()

    async def first_editor():
        async with postgres_sessions() as session:
            await update_member_shares(session, target_kind="roster", target_id=state["roster_id"], actor_id=state["admin_id"], changes=changes(state["roster_version"]))
            first_locked.set()
            await allow_commit.wait()
            await session.commit()
            return 200

    async def second_editor():
        await first_locked.wait()
        async with postgres_sessions() as session:
            second_started.set()
            try:
                await update_member_shares(session, target_kind="membership", target_id=state["membership_id"], actor_id=state["other_admin_id"], changes=changes(state["membership_version"], 9000, 9))
                await session.commit()
                return 200
            except MemberSharesError as exc:
                await session.rollback()
                return exc.status_code

    first, second = asyncio.create_task(first_editor()), asyncio.create_task(second_editor())
    try:
        await asyncio.wait_for(second_started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not second.done()
        allow_commit.set()
        assert await asyncio.wait_for(asyncio.gather(first, second), timeout=5) == [200, 409]
    finally:
        allow_commit.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
    async with postgres_sessions() as session:
        roster = await session.get(MemberRosterEntry, state["roster_id"])
        member = await session.get(Membership, state["membership_id"])
        assert (roster.share_capital_amount, roster.share_count) == (5000, 5)
        assert (member.share_capital_amount, member.share_count) == (5000, 5)
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "member_shares.updated")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first_action", ["claim", "edit"])
async def test_claim_racing_with_share_edit_never_creates_a_stale_membership(postgres_sessions, first_action):
    state = await seed_share_records(postgres_sessions, claimed=False)
    first_locked = asyncio.Event()
    second_started = asyncio.Event()
    allow_commit = asyncio.Event()

    async def execute(action, first):
        if not first:
            await first_locked.wait()
        async with postgres_sessions() as session:
            if not first:
                second_started.set()
            try:
                if action == "claim":
                    roster = await session.scalar(select(MemberRosterEntry).where(MemberRosterEntry.id == state["roster_id"]).with_for_update())
                    owner = await session.get(User, state["owner_id"])
                    await attach_roster_membership(session, roster, owner)
                else:
                    await update_member_shares(session, target_kind="roster", target_id=state["roster_id"], actor_id=state["admin_id"], changes=changes(state["roster_version"]))
                if first:
                    first_locked.set()
                    await allow_commit.wait()
                await session.commit()
                return 200
            except MemberSharesError as exc:
                await session.rollback()
                return exc.status_code

    first = asyncio.create_task(execute(first_action, True))
    second = asyncio.create_task(execute("edit" if first_action == "claim" else "claim", False))
    try:
        await asyncio.wait_for(second_started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not second.done()
        allow_commit.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        assert results == ([200, 409] if first_action == "claim" else [200, 200])
    finally:
        allow_commit.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
    async with postgres_sessions() as session:
        roster = await session.get(MemberRosterEntry, state["roster_id"])
        member = await session.scalar(select(Membership).where(Membership.user_id == state["owner_id"]))
        assert member is not None
        expected = (0, 0) if first_action == "claim" else (5000, 5)
        assert (roster.share_capital_amount, roster.share_count) == expected
        assert (member.share_capital_amount, member.share_count) == expected
        assert roster.claimed_user_id == member.user_id
