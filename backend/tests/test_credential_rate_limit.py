import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import threading

import aiosqlite
from fastapi import HTTPException, Request
import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import credential_rate_limit as quota
from app.database import create_engine
from app.models import CredentialRateLimitBucket as Bucket
from app.rate_limit import RateLimitKeys, RateLimitRule, account_key, client_key
from tests.support import make_test_settings


NOW = datetime(2030, 1, 2, tzinfo=timezone.utc)
SETTINGS = make_test_settings()


def keys(ip="192.0.2.1", account="quota@example.com", scope="login"):
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": (ip, 1234)})
    return client_key(request, scope, account)


@asynccontextmanager
async def quota_store(tmp_path, *, memory):
    engine = create_engine("sqlite+aiosqlite:///:memory:" if memory else f"sqlite+aiosqlite:///{tmp_path / 'quota.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Bucket.__table__.create)
        await connection.execute(text("CREATE TABLE caller_data (id INTEGER PRIMARY KEY)"))
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest.fixture(params=["memory", "file"])
async def store(request, tmp_path):
    async with quota_store(tmp_path, memory=request.param == "memory") as factory:
        yield factory


@pytest.fixture
async def memory_store(tmp_path):
    async with quota_store(tmp_path, memory=True) as factory:
        yield factory


@pytest.fixture
async def file_store(tmp_path):
    async with quota_store(tmp_path, memory=False) as factory:
        yield factory


async def buckets(factory):
    async with factory() as session:
        return dict((await session.execute(select(Bucket.key_hash, Bucket.hits))).all())


async def test_two_keys_atomic_and_blocked_request_does_not_charge_new_ip(store):
    rule = RateLimitRule(2, 300)
    async with store() as caller:
        await quota.consume_credential_quota(caller, keys(), rule, SETTINGS, now=NOW)
        await quota.consume_credential_quota(caller, keys(), rule, SETTINGS, now=NOW + timedelta(seconds=1))
        before = await buckets(store)
        with pytest.raises(HTTPException) as error:
            await quota.consume_credential_quota(caller, keys(ip="192.0.2.2"), rule, SETTINGS, now=NOW + timedelta(seconds=2))
        assert error.value.status_code == 429 and error.value.headers["Retry-After"] == "300"
        assert await buckets(store) == before and len(before) == 2
        assert all("quota@" not in key and "192.0.2" not in key and len(key) == 64 for key in before)


async def test_true_sliding_window_releases_only_expired_hits(store):
    rule = RateLimitRule(2, 300)
    async with store() as caller:
        await quota.consume_credential_quota(caller, keys(), rule, SETTINGS, now=NOW)
        await quota.consume_credential_quota(caller, keys(), rule, SETTINGS, now=NOW + timedelta(seconds=299))
        await quota.consume_credential_quota(caller, keys(), rule, SETTINGS, now=NOW + timedelta(seconds=300))
        with pytest.raises(HTTPException) as error:
            await quota.consume_credential_quota(caller, keys(), rule, SETTINGS, now=NOW + timedelta(seconds=301))
        assert error.value.status_code == 429
        assert all(hits == [quota._time_us(NOW + timedelta(seconds=299)), quota._time_us(NOW + timedelta(seconds=300))]
                   for hits in (await buckets(store)).values())


async def test_login_success_resets_only_own_account_and_keeps_ip_budget(store):
    rule = RateLimitRule(2, 300)
    async with store() as caller:
        for _ in range(2):
            await quota.consume_credential_quota(caller, keys(), rule, SETTINGS, now=NOW)
        await caller.execute(text("DELETE FROM caller_data WHERE id=-1"))
        await quota.reset_credential_quota(caller, account_key("login", "quota@example.com"), SETTINGS)
        await caller.commit()
        saved = await buckets(store)
        assert sorted(map(len, saved.values())) == [0, 2]
        await quota.consume_credential_quota(caller, keys(ip="192.0.2.2"), rule, SETTINGS, now=NOW)
        with pytest.raises(HTTPException) as error:
            await quota.consume_credential_quota(caller, keys(account="other@example.com"), rule, SETTINGS, now=NOW)
        assert error.value.status_code == 429


async def test_verification_success_preserves_clearing_both_ip_and_account(store):
    key = keys(scope="verify-email")
    async with store() as caller:
        for _ in range(5):
            await quota.consume_credential_quota(caller, key, RateLimitRule(5, 3600), SETTINGS, now=NOW)
        await caller.execute(text("DELETE FROM caller_data WHERE id=-1"))
        await quota.reset_credential_quota(caller, key, SETTINGS)
        await caller.commit()
        assert all(hits == [] for hits in (await buckets(store)).values())
        await quota.consume_credential_quota(caller, keys(scope="verify-email", account="next@example.com"), RateLimitRule(5, 3600), SETTINGS, now=NOW)


async def test_account_reset_and_business_data_commit_or_rollback_together(store):
    async with store() as caller:
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
        before = await buckets(store)
        await caller.execute(text("INSERT INTO caller_data VALUES (1)"))
        await quota.reset_credential_quota(caller, keys(), SETTINGS)
        await caller.rollback()
        assert await buckets(store) == before
        await caller.execute(text("INSERT INTO caller_data VALUES (2)"))
        await quota.reset_credential_quota(caller, keys(), SETTINGS)
        await caller.commit()
        assert all(hits == [] for hits in (await buckets(store)).values())
        assert list(await caller.scalars(text("SELECT id FROM caller_data"))) == [2]


async def test_busy_caller_is_untouched_and_recovers_after_its_own_commit(store):
    async with store() as caller:
        await caller.execute(text("INSERT INTO caller_data VALUES (1)"))
        with pytest.raises(quota.CredentialQuotaUnavailable) as error:
            await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
        assert error.value.status_code == 503 and error.value.headers["X-Error-Code"] == "credential_quota_unavailable"
        assert caller.in_transaction()
        assert await caller.scalar(text("SELECT count(*) FROM caller_data")) == 1
        await caller.commit()
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
        await caller.execute(text("INSERT INTO caller_data VALUES (2)"))
        await caller.rollback()
    async with store() as observer:
        assert list(await observer.scalars(text("SELECT id FROM caller_data"))) == [1]
    assert all(len(hits) == 1 for hits in (await buckets(store)).values())


async def test_scope_isolation_and_fixed_hmac_order(store):
    forward = keys()
    reverse = RateLimitKeys(tuple(reversed(forward.values)))
    assert quota.hashed_keys(forward, SETTINGS) == quota.hashed_keys(reverse, SETTINGS)
    assert quota.hashed_keys(forward, SETTINGS) != quota.hashed_keys(keys(scope="verify-email"), SETTINGS)
    async with store() as caller:
        await quota.consume_credential_quota(caller, forward, RateLimitRule(1, 300), SETTINGS, now=NOW)
        await quota.consume_credential_quota(caller, keys(scope="verify-email"), RateLimitRule(1, 3600), SETTINGS, now=NOW)
    assert len(await buckets(store)) == 4


async def test_cleanup_is_bounded_and_preserves_active_rows(store):
    async with store() as session:
        await session.execute(insert(Bucket), [dict(key_hash=f"{index:064x}", hits=[1], expires_at=NOW - timedelta(seconds=1)) for index in range(300)])
        session.add(Bucket(key_hash="f" * 64, hits=[1], expires_at=NOW + timedelta(days=1)))
        await session.commit()
    engine = store.kw["bind"]
    assert await quota.prune_credential_quotas(engine, now=NOW) == 128
    assert await quota.prune_credential_quotas(engine, now=NOW) == 128
    assert await quota.prune_credential_quotas(engine, now=NOW) == 44
    assert await buckets(store) == {"f" * 64: [1]}


async def test_file_busy_timeout_is_restored_for_other_business_requests(file_store):
    store = file_store
    engine = store.kw["bind"]
    async with engine.connect() as connection:
        before = await connection.scalar(text("PRAGMA busy_timeout"))
    async with store() as caller:
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
    async with engine.connect() as connection:
        assert await connection.scalar(text("PRAGMA busy_timeout")) == before


async def test_private_queue_atomic_callback_cannot_commit_later_business_dml(memory_store, monkeypatch):
    store = memory_store
    engine = store.kw["bind"]
    assert aiosqlite.__version__ == "0.22.1"
    driver = engine.pool.__dict__["connection"].driver_connection
    started, release, writer_queued = threading.Event(), threading.Event(), asyncio.Event()
    original = driver._execute
    async def observe(function, *args, **kwargs):
        if started.is_set() and function.__name__ != "atomic":
            writer_queued.set()
        return await original(function, *args, **kwargs)
    monkeypatch.setattr(driver, "_execute", observe)
    def operation(connection):
        connection.execute("INSERT INTO credential_rate_limit_buckets VALUES (?,?,?)", ("a" * 64, "[1]", "2030-01-02 00:00:00.000000"))
        started.set()
        if not release.wait(2):
            raise TimeoutError("Controlled worker release missing")
    async with store() as caller:
        quota_task = asyncio.create_task(quota._memory_transaction(engine, operation))
        writer = None
        try:
            assert await asyncio.to_thread(started.wait, 1)
            writer = asyncio.create_task(caller.execute(text("INSERT INTO caller_data VALUES (1)")))
            await asyncio.wait_for(writer_queued.wait(), 1)
        finally:
            release.set()
            await quota_task
            if writer is not None:
                await writer
        await caller.rollback()
    assert await buckets(store) == {"a" * 64: [1]}
    async with store() as session:
        assert await session.scalar(text("SELECT count(*) FROM caller_data")) == 0


async def test_timeout_queued_memory_callback_may_count_once_without_fallback(memory_store, monkeypatch):
    store = memory_store
    engine = store.kw["bind"]
    driver = engine.pool.__dict__["connection"].driver_connection
    started, release = threading.Event(), threading.Event()
    def block():
        started.set()
        release.wait(2)
    blocking = asyncio.create_task(driver._execute(block))
    monkeypatch.setattr(quota, "BUDGET_SECONDS", .03)
    try:
        assert await asyncio.to_thread(started.wait, 1)
        async with store() as caller:
            with pytest.raises(quota.CredentialQuotaUnavailable):
                await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
    finally:
        release.set()
        await blocking
    cursor = await driver.execute("SELECT 1")
    await cursor.close()
    assert all(len(hits) == 1 for hits in (await buckets(store)).values())


async def test_memory_private_capability_missing_fails_closed_without_touching_caller(memory_store, monkeypatch):
    store = memory_store
    engine = store.kw["bind"]
    driver = engine.pool.__dict__["connection"].driver_connection
    async with store() as caller:
        await caller.execute(text("INSERT INTO caller_data VALUES (1)"))
        with monkeypatch.context() as patch:
            patch.setattr(driver, "_execute", None)
            with pytest.raises(quota.CredentialQuotaUnavailable):
                await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS)
        assert await caller.scalar(text("SELECT count(*) FROM caller_data")) == 1
        await caller.commit()


async def test_success_reset_uses_only_remaining_quota_budget(store):
    async with store() as caller:
        await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
        await caller.execute(text("INSERT INTO caller_data VALUES (1)"))
        with pytest.raises(quota.CredentialQuotaUnavailable):
            await quota.reset_credential_quota(caller, keys(), SETTINGS, budget=quota.QuotaBudget(0))
    async with store() as observer:
        assert await observer.scalar(text("SELECT count(*) FROM caller_data")) == 0
    assert all(len(hits) == 1 for hits in (await buckets(store)).values())


async def test_business_elapsed_time_does_not_consume_quota_io_budget(store, monkeypatch):
    monkeypatch.setattr(quota, "BUDGET_SECONDS", .15)
    async with store() as caller:
        budget = await quota.consume_credential_quota(caller, keys(), RateLimitRule(2, 300), SETTINGS, now=NOW)
        assert 0 < budget.remaining_seconds <= .15
        await caller.execute(text("DELETE FROM caller_data WHERE id=-1"))
        await asyncio.sleep(.18)  # More than the whole quota budget; represents unrelated auth work.
        await quota.reset_credential_quota(caller, keys(), SETTINGS, budget=budget)
        await caller.commit()
    assert all(values == [] for values in (await buckets(store)).values())
