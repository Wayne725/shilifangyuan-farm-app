"""Exercises offline membership only in the disposable local PostgreSQL schema."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.integrations.payment_service import PaymentApplicationError, SQLAlchemyPaymentCallbackRepository, create_membership_payment_attempt
from app.meal_schedules import TAIPEI
from app.models import AdminAudit, MemberRosterEntry, Membership, MembershipCharge, MembershipChargeStatus, MembershipStatus, PaymentAttempt, PaymentStatus, Refund, User, UserRole
from app.routers.admin_users import OfflineMembershipConfirmation, admin_users_router, confirm_offline_membership
from app.routers.membership import return_share_capital
from app.schemas import MembershipApplicationReview
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers
from tests.test_admin_users import offline_settings, roster
from tests.test_v2_payment_jobs import fake_payment, make_pending_membership, payment_settings, successful_callback


def confirmation(entry_id, phone="0912345678"):
    return OfflineMembershipConfirmation(
        confirmed_offline=True, confirmed_on=datetime.now(TAIPEI).date(),
        reason="隔離並行核對線下入社紀錄", phone=phone, roster_entry_id=entry_id,
    )


async def seed_users(factory):
    async with factory() as session:
        admin = User(email="pg-offline-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        users = [User(email=f"pg-offline-user-{index}@example.com", display_name=f"使用者{index}", password_hash="test") for index in range(3)]
        session.add_all([admin, *users])
        await session.commit()
        return admin.id, [user.id for user in users]


async def seed_roster(factory, number):
    async with factory() as session:
        entry = roster(number)
        session.add(entry)
        await session.commit()
        return entry.id


async def grant_via_api(factory, admin_id, user_id, entry_id, phone="0912345678"):
    async with factory() as session:
        admin = await session.get(User, admin_id)
        async with api_test_context(session, [admin_users_router], settings=offline_settings()) as client:
            response = await client.post(
                f"/v1/admin/users/{user_id}/offline-membership",
                json=confirmation(entry_id, phone).model_dump(mode="json"), headers=auth_headers(admin),
            )
            return response.status_code, response.json()


@pytest.mark.asyncio
async def test_concurrent_offline_confirmations_create_one_membership_and_one_audit(postgres_sessions):
    admin_id, user_ids = await seed_users(postgres_sessions)
    entry_id = await seed_roster(postgres_sessions, "PG-OFFLINE-001")
    results = await asyncio.wait_for(asyncio.gather(*(
        grant_via_api(postgres_sessions, admin_id, user_ids[0], entry_id) for _ in range(5)
    )), timeout=15)
    assert all(status == 200 for status, _ in results)
    assert len({body["membership_id"] for _, body in results}) == 1
    assert {body["member_number"] for _, body in results} == {"PG-OFFLINE-001"}
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Membership.id))) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.offline_confirmed")) == 1
        for model in (MembershipCharge, PaymentAttempt, Refund):
            assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_two_people_matching_same_phone_roster_only_one_succeeds(postgres_sessions):
    admin_id, user_ids = await seed_users(postgres_sessions)
    entry_id = await seed_roster(postgres_sessions, "PG-OFFLINE-SAME")
    results = await asyncio.wait_for(asyncio.gather(*(
        grant_via_api(postgres_sessions, admin_id, user_id, entry_id) for user_id in user_ids[:2]
    )), timeout=15)
    assert sorted(status for status, _ in results) == [200, 409]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Membership.id))) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.offline_confirmed")) == 1


@pytest.mark.asyncio
async def test_concurrent_unknown_phones_do_not_create_membership_or_number(postgres_sessions):
    admin_id, user_ids = await seed_users(postgres_sessions)
    entry_id = await seed_roster(postgres_sessions, "PG-RESERVED-001")
    results = await asyncio.wait_for(asyncio.gather(*(
        grant_via_api(postgres_sessions, admin_id, user_id, entry_id, phone="0987654321") for user_id in user_ids
    )), timeout=15)
    assert all(status == 409 for status, _ in results)
    async with postgres_sessions() as session:
        entry = await session.get(MemberRosterEntry, entry_id)
        assert entry.member_number == "PG-RESERVED-001" and entry.claimed_user_id is None
        assert await session.scalar(select(func.count(MemberRosterEntry.id))) == 1
        assert await session.scalar(select(func.count(Membership.id))) == 0
        assert await session.scalar(select(func.count(AdminAudit.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("first_action", ["offline", "callback"])
async def test_offline_confirmation_and_membership_payment_callback_serialize_without_fk_deadlock(postgres_sessions, fake_payment, first_action):
    async with postgres_sessions() as session:
        buyer, membership, admission, _capital = await make_pending_membership(session)
        admin = User(email="pg-offline-payment-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        entry = roster("PG-OFFLINE-PAYMENT")
        session.add_all([admin, entry])
        await session.commit()
        now = datetime.now(timezone.utc)
        attempt = await create_membership_payment_attempt(session, admission.id, buyer, payment_settings(), now=now)
        state = {"user_id": buyer.id, "admin_id": admin.id, "membership_id": membership.id, "charge_id": admission.id, "attempt_id": attempt.id}
        payload = successful_callback(attempt, now, "PG-OFFLINE-ONLINE-TRADE")
    first_locked = asyncio.Event()
    second_started = asyncio.Event()
    allow_commit = asyncio.Event()

    async def perform(action, first):
        if not first:
            await first_locked.wait()
        async with postgres_sessions() as session:
            if first:
                if action == "offline":
                    await session.scalar(select(User.id).where(User.id == state["user_id"]).with_for_update(key_share=True))
                await session.scalar(select(PaymentAttempt.id).where(PaymentAttempt.id == state["attempt_id"]).with_for_update())
                first_locked.set()
                await allow_commit.wait()
            else:
                second_started.set()
            if action == "callback":
                return await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback("pg-offline-callback-race", payload)
            admin = await session.get(User, state["admin_id"])
            try:
                await confirm_offline_membership(state["user_id"], confirmation(entry.id), admin, session, offline_settings())
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    first = asyncio.create_task(perform(first_action, True))
    second = asyncio.create_task(perform("callback" if first_action == "offline" else "offline", False))
    try:
        await asyncio.wait_for(second_started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not second.done()
        allow_commit.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=15)
        assert results == ([200, "late_paid_refund_required"] if first_action == "offline" else ["paid", 409])
    finally:
        allow_commit.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
    async with postgres_sessions() as session:
        member = await session.get(Membership, state["membership_id"])
        charge = await session.get(MembershipCharge, state["charge_id"])
        assert await session.scalar(select(func.count(Membership.id))) == 1
        assert member.status == (MembershipStatus.ACTIVE if first_action == "offline" else MembershipStatus.PENDING_PAYMENT)
        assert charge.status == (MembershipChargeStatus.REFUNDED if first_action == "offline" else MembershipChargeStatus.PAID)
        assert await session.scalar(select(func.count(Refund.id))) == (1 if first_action == "offline" else 0)
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.offline_confirmed")) == (1 if first_action == "offline" else 0)


@pytest.mark.asyncio
async def test_new_membership_payment_cannot_start_after_offline_confirmation_takes_user_guard(postgres_sessions, fake_payment):
    async with postgres_sessions() as session:
        buyer, _membership, admission, _capital = await make_pending_membership(session)
        admin = User(email="pg-offline-start-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        entry = roster("PG-OFFLINE-START")
        session.add_all([admin, entry])
        await session.commit()
        user_id, admin_id, charge_id = buyer.id, admin.id, admission.id
    locked = asyncio.Event()
    payment_started = asyncio.Event()
    finish = asyncio.Event()

    async def offline():
        async with postgres_sessions() as session:
            await session.scalar(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
            locked.set()
            await finish.wait()
            admin = await session.get(User, admin_id)
            await confirm_offline_membership(user_id, confirmation(entry.id), admin, session, offline_settings())
            return 200

    async def start_payment():
        await locked.wait()
        async with postgres_sessions() as session:
            buyer = await session.get(User, user_id)
            payment_started.set()
            try:
                await create_membership_payment_attempt(session, charge_id, buyer, payment_settings())
                return 200
            except PaymentApplicationError:
                await session.rollback()
                return 409

    first, second = asyncio.create_task(offline()), asyncio.create_task(start_payment())
    try:
        await asyncio.wait_for(payment_started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not second.done()
        finish.set()
        assert await asyncio.wait_for(asyncio.gather(first, second), timeout=15) == [200, 409]
    finally:
        finish.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(PaymentAttempt.id))) == 0
        assert await session.scalar(select(func.count(Refund.id))) == 0


@pytest.mark.asyncio
async def test_offline_confirmation_and_resigned_member_capital_return_do_not_deadlock(postgres_sessions, fake_payment):
    async with postgres_sessions() as session:
        buyer, membership, _admission, capital = await make_pending_membership(session)
        admin = User(email="pg-capital-return-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        entry = roster("PG-RESIGNED-CAPITAL")
        session.add_all([admin, entry])
        await session.commit()
        now = datetime.now(timezone.utc)
        attempt = await create_membership_payment_attempt(session, capital.id, buyer, payment_settings(), now=now)
        membership.status = MembershipStatus.RESIGNED
        membership.member_number = "PG-RESIGNED-CAPITAL"
        membership.share_capital_amount = capital.amount
        membership.share_count = 1
        membership.ended_at = now
        capital.status = MembershipChargeStatus.PAID
        capital.paid_at = now
        attempt.status = PaymentStatus.PAID
        attempt.paid_at = now
        attempt.provider_trade_no = "PG-CAPITAL-RETURN-TRADE"
        await session.commit()
        user_id, admin_id, member_id, charge_id, attempt_id = buyer.id, admin.id, membership.id, capital.id, attempt.id

    locked = asyncio.Event()
    refund_started = asyncio.Event()
    finish = asyncio.Event()

    async def offline():
        async with postgres_sessions() as session:
            await session.scalar(select(User.id).where(User.id == user_id).with_for_update(key_share=True))
            await session.scalar(select(PaymentAttempt.id).where(PaymentAttempt.id == attempt_id).with_for_update())
            locked.set()
            await finish.wait()
            admin = await session.get(User, admin_id)
            try:
                await confirm_offline_membership(user_id, confirmation(entry.id), admin, session, offline_settings())
                return 200
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    async def return_capital():
        await locked.wait()
        async with postgres_sessions() as session:
            admin = await session.get(User, admin_id)
            refund_started.set()
            return await return_share_capital(
                member_id, MembershipApplicationReview(reason="核對退社股金並返還"),
                admin, session, payment_settings(),
            )

    first, second = asyncio.create_task(offline()), asyncio.create_task(return_capital())
    try:
        await asyncio.wait_for(refund_started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        assert not second.done()
        finish.set()
        rejected, result = await asyncio.wait_for(asyncio.gather(first, second), timeout=15)
        assert rejected == 409
        assert result["amount"] == 1000 and result["status"] == "completed"
    finally:
        finish.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)

    async with postgres_sessions() as session:
        membership = await session.get(Membership, member_id)
        charge = await session.get(MembershipCharge, charge_id)
        assert membership.status == MembershipStatus.RESIGNED
        assert (membership.share_capital_amount, membership.share_count) == (1000, 1)
        assert charge.status == MembershipChargeStatus.REFUNDED
        assert await session.scalar(select(func.count(Refund.id))) == 1
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.offline_confirmed")) == 0
        assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "membership.share_capital_return")) == 1
