import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest
from sqlalchemy import func, insert, select, text
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import credential_rate_limit as quota
from app.models import CredentialRateLimitBucket as Bucket, EmailVerificationToken, RefreshSession, User
from app.config import get_settings
from app.database import get_session
from app.rate_limit import RateLimitKeys, RateLimitRule
from app.routers import auth
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.test_credential_rate_limit import NOW, SETTINGS, keys
from tests.support import fast_password_hash


async def snapshots(factory):
    async with factory() as session:
        return dict((await session.execute(select(Bucket.key_hash, Bucket.hits))).all())


@pytest.mark.asyncio
async def test_concurrent_quota_admission_is_shared_and_reverse_keys_do_not_deadlock(postgres_sessions):
    key = keys()
    async def attempt(index):
        async with postgres_sessions() as caller:
            try:
                selected = key if index % 2 else RateLimitKeys(tuple(reversed(key.values)))
                await quota.consume_credential_quota(caller, selected, RateLimitRule(2, 300), SETTINGS, now=NOW)
                return 200
            except HTTPException as error:
                return error.status_code
    result = await asyncio.wait_for(asyncio.gather(*(attempt(index) for index in range(8))), 10)
    assert sorted(result) == [200, 200, 429, 429, 429, 429, 429, 429]
    assert all(len(hits) == 2 for hits in (await snapshots(postgres_sessions)).values())


@pytest.mark.asyncio
async def test_blocked_account_does_not_create_or_charge_companion_ip(postgres_sessions):
    async with postgres_sessions() as caller:
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(1, 300), SETTINGS, now=NOW)
        before = await snapshots(postgres_sessions)
        with pytest.raises(HTTPException) as error:
            await quota.consume_credential_quota(caller, keys(ip="192.0.2.99"), RateLimitRule(1, 300), SETTINGS, now=NOW)
        assert error.value.status_code == 429
        assert await snapshots(postgres_sessions) == before


@pytest.mark.asyncio
async def test_held_quota_lock_returns_503_then_recovers_without_counting_a_known_rollback(postgres_sessions):
    async with postgres_sessions() as caller:
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
    before = await snapshots(postgres_sessions)
    async with postgres_sessions() as locker:
        await locker.execute(select(Bucket).order_by(Bucket.key_hash).with_for_update())
        async with postgres_sessions() as caller:
            with pytest.raises(quota.CredentialQuotaUnavailable):
                await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
        assert await snapshots(postgres_sessions) == before
        await locker.rollback()
    async with postgres_sessions() as caller:
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
    assert all(len(hits) == 2 for hits in (await snapshots(postgres_sessions)).values())


@pytest.mark.asyncio
async def test_cleanup_skips_locked_expired_rows_and_preserves_active_budgets(postgres_sessions):
    async with postgres_sessions() as session:
        await session.execute(insert(Bucket), [dict(key_hash=f"{index:064x}", hits=[1], expires_at=NOW - timedelta(seconds=1)) for index in range(130)])
        session.add(Bucket(key_hash="f" * 64, hits=[1], expires_at=NOW + timedelta(days=1)))
        await session.commit()
    async with postgres_sessions() as locker:
        locked = list(await locker.scalars(select(Bucket.key_hash).order_by(Bucket.key_hash).limit(2).with_for_update()))
        assert await quota.prune_credential_quotas(postgres_sessions.kw["bind"], now=NOW) == 128
        assert set(await snapshots(postgres_sessions)) == {*locked, "f" * 64}
        await locker.rollback()
    assert await quota.prune_credential_quotas(postgres_sessions.kw["bind"], now=NOW) == 2
    assert await snapshots(postgres_sessions) == {"f" * 64: [1]}


@pytest.mark.asyncio
async def test_quota_commit_is_independent_but_success_reset_rolls_back_with_caller(postgres_sessions):
    async with postgres_sessions() as caller:
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
        await caller.execute(text("SELECT 1"))
        await caller.rollback()
        before = await snapshots(postgres_sessions)
        assert len(before) == 2
        await quota.reset_credential_quota(caller, keys(), SETTINGS)
        await caller.rollback()
        assert await snapshots(postgres_sessions) == before
        await quota.reset_credential_quota(caller, keys(), SETTINGS)
        await caller.commit()
    assert all(values == [] for values in (await snapshots(postgres_sessions)).values())


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", ["login", "verify"])
async def test_reset_lock_failure_rolls_back_real_auth_and_recovers(postgres_sessions, monkeypatch, flow):
    postgres_sessions.configure(autoflush=False)
    original_hash = fast_password_hash("correct-password")
    async with postgres_sessions() as seed:
        user = User(email="reset-lock@example.com", display_name="合成驗證", password_hash=original_hash,
            email_verified_at=datetime.now(timezone.utc) if flow == "login" else None)
        seed.add(user)
        await seed.flush()
        if flow == "verify":
            seed.add(EmailVerificationToken(user_id=user.id, token_hash=auth._email_token_hash(user.id, "123456", SETTINGS),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        await seed.commit()
        identity = user.id
    app = FastAPI()
    app.include_router(auth.auth_router)
    async def sessions():
        async with postgres_sessions() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    original_consume = auth.consume_credential_quota
    locker = postgres_sessions()
    async def consume_then_lock(session, key, rule, settings):
        budget = await original_consume(session, key, rule, settings)
        key_hash = quota.hashed_keys(key, settings)[-1]
        if flow == "login":
            key_hash = quota.hashed_keys("login:account:reset-lock@example.com", settings)[0]
        await locker.execute(select(Bucket).where(Bucket.key_hash == key_hash).with_for_update())
        return budget
    path = "/v1/auth/login" if flow == "login" else "/v1/auth/verify-email"
    body = {"email": "reset-lock@example.com", **({"password": "correct-password"} if flow == "login" else {"token": "123456"})}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated", trust_env=False) as client:
            with monkeypatch.context() as patch:
                patch.setattr(auth, "consume_credential_quota", consume_then_lock)
                failure = await client.post(path, json=body)
            assert failure.status_code == 503 and failure.headers["X-Error-Code"] == "credential_quota_unavailable"
            assert "set-cookie" not in failure.headers
            await locker.rollback()
            async with postgres_sessions() as observer:
                saved = await observer.get(User, identity)
                assert saved.password_hash == original_hash
                assert await observer.scalar(select(func.count(RefreshSession.id))) == 0
                if flow == "verify":
                    assert saved.email_verified_at is None
                    assert await observer.scalar(select(EmailVerificationToken.used_at)) is None
            assert all(len(values) == 1 for values in (await snapshots(postgres_sessions)).values())
            recovered = await client.post(path, json=body)
            assert recovered.status_code == 200, recovered.text
    finally:
        await locker.close()
    async with postgres_sessions() as observer:
        saved = await observer.get(User, identity)
        assert saved.email_verified_at is not None
        if flow == "login":
            assert saved.password_hash != original_hash
            assert await observer.scalar(select(func.count(RefreshSession.id))) == 1
        else:
            assert await observer.scalar(select(EmailVerificationToken.used_at)) is not None
