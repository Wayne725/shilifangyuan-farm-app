import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    Membership, MembershipCharge, MembershipAdmissionQuizAttempt,
    MembershipAdmissionReview, MembershipAdmissionRound, Notification, OutboxEvent,
    User, UserRole,
)
from app.routers.membership_admission import membership_admission_router
from app.routers.admin_users import admin_users_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers
from tests.test_membership_admission import (
    ADMIN_URL, URL, admission_settings, profile_body, quiz_body, ready, submitted,
)


@pytest.fixture
async def admission_pg(postgres_sessions):
    postgres_sessions.configure(autoflush=False)
    async with postgres_sessions() as session:
        owner = User(email="admission-pg@example.com", display_name="隔離申請人", password_hash="test")
        admins = [User(email=f"admission-pg-admin-{i}@example.com", display_name=f"管理員{i}",
            user_role=UserRole.ADMIN, password_hash="test") for i in range(2)]
        session.add_all([owner, *admins])
        await session.commit()
        headers = {"owner_id": owner.id, "owner_headers": auth_headers(owner), "admin_headers": auth_headers(admins[0]),
            "second_admin_headers": auth_headers(admins[1])}
    return postgres_sessions, headers


async def call(factory, method, path, headers, body):
    async with factory() as session:
        async with api_test_context(session, [membership_admission_router, admin_users_router], settings=admission_settings()) as client:
            return await client.request(method, path, headers=headers, json=body)


async def prepare(factory, headers, helper=ready):
    async with factory() as session:
        async with api_test_context(session, [membership_admission_router], settings=admission_settings()) as client:
            return await helper({"session": session, "client": client, **headers})


async def race(*operations):
    gate = asyncio.Event()

    async def run(operation):
        await gate.wait()
        return await operation

    tasks = [asyncio.create_task(run(operation)) for operation in operations]
    gate.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_starts_create_one_round(admission_pg, same_key):
    factory, headers = admission_pg
    key = str(uuid4())
    results = await race(*(call(factory, "POST", f"{URL}/rounds", headers["owner_headers"],
        {"idempotency_key": key if same_key else str(uuid4())}) for _ in range(3)))
    assert [response.status_code for response in results] == [200, 200, 200]
    assert len({response.json()["id"] for response in results}) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count(MembershipAdmissionRound.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_quiz_cannot_consume_extra_attempt(admission_pg, same_key):
    factory, headers = admission_pg
    row = await prepare(factory, headers)
    first = quiz_body(row, score=70)
    second = first if same_key else quiz_body(row, score=80)
    results = await race(*(call(factory, "POST", f"{URL}/rounds/{row['id']}/quiz-attempts",
        headers["owner_headers"], body) for body in (first, second)))
    assert sorted(response.status_code for response in results) == ([200, 200] if same_key else [200, 409]), [response.text for response in results]
    async with factory() as session:
        assert await session.scalar(select(func.count(MembershipAdmissionQuizAttempt.id))) == 1
        stored = await session.get(MembershipAdmissionRound, row["id"])
        assert stored.version == row["version"] + 1


@pytest.mark.asyncio
async def test_parallel_profile_save_keeps_single_version(admission_pg):
    factory, headers = admission_pg
    row = await prepare(factory, headers)
    results = await race(*(call(factory, "PUT", f"{URL}/rounds/{row['id']}", headers["owner_headers"],
        profile_body(row, requested_share_count=shares)) for shares in (10, 20)))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(response.json() for response in results if response.status_code == 200)
    async with factory() as session:
        stored = await session.get(MembershipAdmissionRound, row["id"])
        assert stored.version == row["version"] + 1
        assert stored.requested_share_count == winner["requested_share_count"]


@pytest.mark.asyncio
@pytest.mark.parametrize("competing_action", ["review", "withdraw"])
async def test_review_competition_does_not_duplicate_or_activate(admission_pg, competing_action):
    factory, headers = admission_pg
    row = await prepare(factory, headers, submitted)
    approval = {"expected_version": row["version"], "decision": "approve", "reason": "隔離核准",
        "decision_reference": "TEST-決議"}
    first = call(factory, "POST", f"{ADMIN_URL}/{row['id']}/review", headers["admin_headers"], approval)
    if competing_action == "review":
        second = call(factory, "POST", f"{ADMIN_URL}/{row['id']}/review", headers["second_admin_headers"],
            {**approval, "decision": "reject", "reason": "隔離駁回"})
    else:
        second = call(factory, "POST", f"{URL}/rounds/{row['id']}/withdraw", headers["owner_headers"],
            {"expected_version": row["version"], "reason": "隔離撤回"})
    results = await race(first, second)
    assert sorted(response.status_code for response in results) == [200, 409], [response.text for response in results]
    winner = next(response.json() for response in results if response.status_code == 200)
    async with factory() as session:
        stored = await session.get(MembershipAdmissionRound, row["id"])
        assert stored.version == row["version"] + 1 and stored.status == winner["status"]
        expected_reviews = 0 if stored.status == "withdrawn" else 1
        assert await session.scalar(select(func.count(MembershipAdmissionReview.id))) == expected_reviews
        for model in (Membership, MembershipCharge, Notification, OutboxEvent):
            assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_new_admission_and_legacy_offline_grant_cannot_both_succeed(admission_pg):
    factory, headers = admission_pg
    results = await race(
        call(factory, "POST", f"{URL}/rounds", headers["owner_headers"], {"idempotency_key": str(uuid4())}),
        call(factory, "POST", f"/v1/admin/users/{headers['owner_id']}/offline-membership", headers["admin_headers"], {
            "confirmed_offline": True, "confirmed_on": datetime.now(timezone.utc).date().isoformat(),
            "reason": "隔離測試既有線下資料與新版申請互斥", "member_number": "ISOLATED-PG-001",
        }),
    )
    assert sorted(response.status_code for response in results) == [200, 409], [response.text for response in results]
    async with factory() as session:
        count_rounds = await session.scalar(select(func.count(MembershipAdmissionRound.id)))
        count_memberships = await session.scalar(select(func.count(Membership.id)))
        assert count_rounds + count_memberships == 1
        assert await session.scalar(select(func.count(MembershipCharge.id))) == 0
