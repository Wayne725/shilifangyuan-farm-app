from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.config import get_settings
from app.database import get_session
from app.models import (
    MemberProposal, MemberProposalStatus, Membership, MembershipStatus, User,
)
from app.rate_limit import LOGIN_RULE
from app.routers.auth import auth_router
from app.routers.community import community_router
from tests.support import api_test_context, auth_headers, fast_password_hash, make_test_settings


@pytest.fixture
async def proposal_context(database_session):
    now = datetime.now(timezone.utc)
    author = User(email="proposal-owner@example.test", display_name="提案人", password_hash="unused",
                  membership=Membership(status=MembershipStatus.ACTIVE, member_number="SEC-OWNER", activated_at=now))
    member = User(email="proposal-reader@example.test", display_name="社員", password_hash="unused",
                  membership=Membership(status=MembershipStatus.ACTIVE, member_number="SEC-READER", activated_at=now))
    customer = User(email="proposal-customer@example.test", display_name="一般買家", password_hash="unused")
    database_session.add_all([author, member, customer])
    await database_session.flush()
    proposal = MemberProposal(created_by_id=author.id, title="社員提案", body="討論內容",
                              status=MemberProposalStatus.DISCUSSION, minimum_voters=2,
                              discussion_ends_at=now + timedelta(days=1), voting_ends_at=now + timedelta(days=2))
    database_session.add(proposal)
    await database_session.commit()
    async with api_test_context(database_session, [community_router], settings=make_test_settings()) as client:
        yield client, database_session, proposal, author, member, customer


@pytest.mark.asyncio
async def test_withdrawn_proposal_hides_comments_with_parent_and_named_votes(proposal_context):
    client, _, proposal, author, member, _ = proposal_context
    path = f"/v1/member-proposals/{proposal.id}"
    comment = await client.post(f"{path}/comments", headers=auth_headers(member), json={"body": "公開討論留言"})
    assert comment.status_code == 201, comment.text
    assert (await client.get(f"{path}/comments", headers=auth_headers(member))).json()[0]["body"] == "公開討論留言"
    assert (await client.post(f"{path}/withdraw", headers=auth_headers(author))).status_code == 200
    for suffix in ("", "/votes", "/comments"):
        response = await client.get(f"{path}{suffix}", headers=auth_headers(member))
        assert response.status_code == 404, (suffix, response.text)
    own_comments = await client.get(f"{path}/comments", headers=auth_headers(author))
    assert own_comments.status_code == 200 and own_comments.json()[0]["body"] == "公開討論留言"


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal_status", ["draft", "pending_review", "discussion", "voting", "withdrawn"])
async def test_proposal_read_and_child_write_visibility_is_consistent(proposal_context, proposal_status):
    client, session, proposal, author, member, customer = proposal_context
    proposal.status = MemberProposalStatus(proposal_status)
    await session.commit()
    path = f"/v1/member-proposals/{proposal.id}"
    private = proposal_status in {"draft", "pending_review", "withdrawn"}
    for suffix in ("", "/comments", "/votes"):
        assert (await client.get(f"{path}{suffix}", headers=auth_headers(author))).status_code == 200
        assert (await client.get(f"{path}{suffix}", headers=auth_headers(member))).status_code == (404 if private else 200)
        assert (await client.get(f"{path}{suffix}", headers=auth_headers(customer))).status_code == 403
        assert (await client.get(f"{path}{suffix}")).status_code == 401
    comment = await client.post(f"{path}/comments", headers=auth_headers(member), json={"body": "討論留言"})
    vote = await client.put(f"{path}/vote", headers=auth_headers(member), json={"choice": "yes"})
    assert comment.status_code == (404 if private else 201)
    assert vote.status_code == (404 if private else 200 if proposal_status == "voting" else 409)


@pytest.mark.asyncio
async def test_missing_proposal_and_its_children_return_same_not_found(proposal_context):
    client, _, _, _, member, _ = proposal_context
    path = "/v1/member-proposals/00000000-0000-4000-8000-000000000099"
    for suffix in ("", "/comments", "/votes"):
        response = await client.get(f"{path}{suffix}", headers=auth_headers(member))
        assert response.status_code == 404 and response.json()["detail"] == "找不到社員提案"
    assert (await client.post(f"{path}/comments", headers=auth_headers(member), json={"body": "留言"})).status_code == 404
    assert (await client.put(f"{path}/vote", headers=auth_headers(member), json={"choice": "yes"})).status_code == 404


@asynccontextmanager
async def login_client(session, ip_address):
    application = FastAPI()
    application.include_router(auth_router)

    async def sessions():
        yield session

    application.dependency_overrides[get_session] = sessions
    application.dependency_overrides[get_settings] = lambda: make_test_settings()
    async with AsyncClient(transport=ASGITransport(app=application, client=(ip_address, 1234)), base_url="http://test") as client:
        yield client


@pytest.fixture
async def login_account(database_session):
    user = User(email="successful-login@example.com", display_name="登入測試", password_hash=fast_password_hash("test-password-123"),
                email_verified_at=datetime.now(timezone.utc))
    database_session.add(user)
    await database_session.commit()
    yield {"email": user.email, "password": "test-password-123"}


@pytest.mark.asyncio
async def test_successful_login_does_not_reset_source_ip_quota(database_session, login_account):
    async with login_client(database_session, "192.0.2.101") as client:
        for attempt in range(LOGIN_RULE.max_attempts // 2):
            failure = await client.post("/v1/auth/login", json={"email": f"target-{attempt}@example.com", "password": "wrong-password"})
            assert failure.status_code == 401
            success = await client.post("/v1/auth/login", json=login_account)
            assert success.status_code == 200, success.text
        exhausted = await client.post("/v1/auth/login", json={"email": "new-target@example.com", "password": "wrong-password"})
        assert exhausted.status_code == 429, exhausted.text
        assert exhausted.headers["Retry-After"] == "300"


@pytest.mark.asyncio
async def test_successful_login_clears_only_its_own_account_failures(database_session, login_account):
    async with login_client(database_session, "192.0.2.102") as client:
        for _ in range(LOGIN_RULE.max_attempts - 2):
            response = await client.post("/v1/auth/login", json={**login_account, "password": "wrong-password"})
            assert response.status_code == 401
    async with login_client(database_session, "192.0.2.103") as client:
        assert (await client.post("/v1/auth/login", json=login_account)).status_code == 200
    async with login_client(database_session, "192.0.2.104") as client:
        for _ in range(LOGIN_RULE.max_attempts - 2):
            response = await client.post("/v1/auth/login", json={**login_account, "password": "wrong-password"})
            assert response.status_code == 401
