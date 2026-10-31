from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from app import credential_rate_limit as quota
from app.auth import make_token_pair
from app.models import CredentialRateLimitBucket, EmailVerificationToken, User
from app.routers.auth import _email_token_hash, auth_router
from app.routers.line_auth import line_auth_router
from tests.support import api_test_context, make_test_settings


SETTINGS = make_test_settings(line_login_channel_id="1234567890", line_login_channel_secret="synthetic-line-key",
    line_login_redirect_uri="https://app.example.test/auth/line/callback")
ENDPOINTS = [
    ("login", {"email": "missing@example.com", "password": "wrong-password"}, 10),
    ("register", {"email": "missing@example.com", "password": "test-password", "display_name": "合成註冊"}, 5),
    ("register-existing-member", {"email": "missing@example.com", "password": "test-password", "member_number": "UNCLAIMED", "legal_name": "合成姓名", "display_name": "合成註冊"}, 5),
    ("verify-email", {"email": "missing@example.com", "token": "123456"}, 5),
    ("resend-verification", {"email": "missing@example.com"}, 5),
    ("forgot-password", {"email": "missing@example.com"}, 5),
    ("line/start", {"data_consent_version": "line-data-v1"}, 10),
]


@pytest.mark.parametrize("path,payload,cap", ENDPOINTS)
async def test_each_existing_credential_gate_preserves_its_original_cap(database_session, path, payload, cap):
    async with api_test_context(database_session, [auth_router, line_auth_router], settings=SETTINGS) as client:
        replies = [await client.post(f"/v1/auth/{path}", json=payload) for _ in range(cap + 1)]
    assert all(reply.status_code not in {422, 429, 500} for reply in replies[:-1])
    assert replies[-1].status_code == 429, replies[-1].text
    assert replies[-1].headers["Retry-After"] == ("300" if cap == 10 else "3600")


@pytest.mark.parametrize("path,payload,cap", ENDPOINTS)
async def test_quota_failure_is_typed_503_before_user_or_business_queries(database_session, monkeypatch, path, payload, cap):
    user = User(email="valid-bearer@example.com", display_name="合成帳號", password_hash="!synthetic", email_verified_at=datetime.now(timezone.utc))
    database_session.add(user)
    await database_session.commit()
    headers = {"Authorization": "Bearer " + make_token_pair(user, SETTINGS)["access_token"]}
    statements = []
    def record(_connection, _cursor, statement, *_args):
        statements.append(statement)
    async def unavailable(*args):
        raise quota.CredentialQuotaUnavailable()
    monkeypatch.setattr(quota, "_consume_memory", unavailable)
    engine = database_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        async with api_test_context(database_session, [auth_router, line_auth_router], settings=SETTINGS) as client:
            reply = await client.post(f"/v1/auth/{path}", json=payload, headers=headers)
        assert reply.status_code == 503 and reply.headers["X-Error-Code"] == "credential_quota_unavailable"
        assert reply.headers["Retry-After"] == "2"
        assert statements == []
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_temporary_quota_fault_recovers_without_local_fallback(database_session, monkeypatch):
    async def unavailable(*args):
        raise OSError("synthetic sensitive error must not be exposed")
    async with api_test_context(database_session, [auth_router], settings=SETTINGS) as client:
        with monkeypatch.context() as patch:
            patch.setattr(quota, "_consume_memory", unavailable)
            failure = await client.post("/v1/auth/login", json={"email": "missing@example.com", "password": "wrong-password"})
        assert failure.status_code == 503 and "sensitive" not in failure.text
        recovered = await client.post("/v1/auth/login", json={"email": "missing@example.com", "password": "wrong-password"})
        assert recovered.status_code == 401
    hits = list(await database_session.scalars(select(CredentialRateLimitBucket.hits)))
    assert len(hits) == 2 and all(len(values) == 1 for values in hits)


async def test_successful_verifications_keep_same_nat_compatibility(database_session):
    now = datetime.now(timezone.utc)
    users = [User(email=f"verify-same-nat-{index}@example.com", display_name="合成驗證", password_hash="!synthetic") for index in range(6)]
    database_session.add_all(users)
    await database_session.flush()
    for user in users:
        database_session.add(EmailVerificationToken(user_id=user.id, token_hash=_email_token_hash(user.id, "123456", SETTINGS), expires_at=now + timedelta(minutes=10)))
    await database_session.commit()
    async with api_test_context(database_session, [auth_router], settings=SETTINGS) as client:
        for user in users:
            reply = await client.post("/v1/auth/verify-email", json={"email": user.email, "token": "123456"})
            assert reply.status_code == 200, reply.text
    assert all(values == [] for values in await database_session.scalars(select(CredentialRateLimitBucket.hits)))
