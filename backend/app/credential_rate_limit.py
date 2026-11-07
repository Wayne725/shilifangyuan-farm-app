"""Independent, shared credential budgets. Other endpoint limiters keep their existing API."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import sqlite3
import time

import aiosqlite

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
from sqlalchemy.pool import StaticPool

from .config import Settings
from .models import CredentialRateLimitBucket
from .rate_limit import RateLimitKey, RateLimitKeys, RateLimitRule


SCOPES = {"login", "register", "register-existing-member", "verify-email", "resend-verification", "forgot-password", "line-start"}
BUDGET_SECONDS = 2.0
CLEANUP_LIMIT = 128
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
logger = logging.getLogger("uvicorn.error").getChild(__name__)


class CredentialQuotaUnavailable(HTTPException):
    def __init__(self):
        super().__init__(503, "驗證服務暫時忙碌，請稍後再試", headers={"Retry-After": "2", "X-Error-Code": "credential_quota_unavailable"})


@dataclass(frozen=True)
class QuotaBudget:
    remaining_seconds: float


def hashed_keys(key: RateLimitKey, settings: Settings) -> list[str]:
    values = key.values if isinstance(key, RateLimitKeys) else (key,)
    if not 1 <= len(values) <= 2 or any(value.split(":", 1)[0] not in SCOPES for value in values):
        raise ValueError("Only the seven existing credential scopes are supported")
    return sorted({hmac.new(settings.jwt_secret.encode(), b"credential-quota-v1\0" + value.encode(), hashlib.sha256).hexdigest() for value in values})


def _engine(session_or_bind) -> AsyncEngine:
    bind = session_or_bind.bind if isinstance(session_or_bind, AsyncSession) else session_or_bind
    if isinstance(bind, AsyncConnection):
        return bind.engine
    if not isinstance(bind, AsyncEngine):
        raise CredentialQuotaUnavailable()
    return bind


def _private_memory(engine):
    return (engine.dialect.name == "sqlite" and isinstance(engine.pool, StaticPool)
        and engine.url.database in {None, "", ":memory:"})


def _time_us(value):
    return int((value.replace(tzinfo=value.tzinfo or timezone.utc) - EPOCH) // timedelta(microseconds=1))


async def _db_time(connection):
    if connection.dialect.name == "postgresql":
        return _time_us(await connection.scalar(select(func.clock_timestamp())))
    return int(await connection.scalar(text("SELECT CAST((julianday('now') - 2440587.5) * 86400000000 AS INTEGER)")))


def _advance(buckets, rule, now):
    if not 0 < rule.max_attempts <= 10 or not 0 < rule.window_seconds <= 3600:
        raise ValueError("Credential rule exceeds the bounded bucket contract")
    cutoff = now - round(rule.window_seconds * 1_000_000)
    retained = {key: [hit for hit in hits if hit > cutoff] for key, hits in buckets.items()}
    if any(len(hits) >= rule.max_attempts for hits in retained.values()):
        # Keep the existing conservative Retry-After contract; never extend stored hits.
        raise HTTPException(429, "嘗試次數過多，請稍後再試", headers={"Retry-After": str(rule.retry_after)})
    return {key: [*hits, now] for key, hits in retained.items()}


async def _transaction(engine, operation):
    async with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            async with connection.begin():
                await connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await connection.execute(text("SET LOCAL statement_timeout = '1000ms'"))
                return await operation(connection)
        previous = int(await connection.scalar(text("PRAGMA busy_timeout")))
        try:
            await connection.exec_driver_sql("PRAGMA busy_timeout=250")
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            result = await operation(connection)
            await connection.commit()
            return result
        except BaseException:
            await connection.rollback()
            raise
        finally:
            try:
                await connection.exec_driver_sql(f"PRAGMA busy_timeout={previous}")
                await connection.rollback()
            except BaseException:
                await connection.invalidate()
                raise


async def _consume(engine, keys, rule, now):
    table = CredentialRateLimitBucket.__table__
    async def operation(connection):
        insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
        buckets = {}
        for key in keys:
            statement = insert(table).values(key_hash=key, hits=[], expires_at=EPOCH)
            row = (await connection.execute(statement.on_conflict_do_update(index_elements=[table.c.key_hash],
                set_={"key_hash": statement.excluded.key_hash}).returning(table.c.key_hash, table.c.hits))).one()
            buckets[row.key_hash] = row.hits
        current = _time_us(now) if now is not None else await _db_time(connection)
        changed = _advance(buckets, rule, current)
        expiry = EPOCH + timedelta(microseconds=current + round(rule.window_seconds * 1_000_000))
        for key, hits in changed.items():
            await connection.execute(update(table).where(table.c.key_hash == key).values(hits=hits, expires_at=expiry))
    await _transaction(engine, operation)


def _memory_driver(engine):
    record = engine.pool.__dict__.get("connection")
    driver = record.driver_connection if record is not None else None
    if (aiosqlite.__version__ != "0.22.1" or not isinstance(driver, aiosqlite.Connection)
        or not callable(getattr(driver, "_execute", None))):
        raise CredentialQuotaUnavailable()
    return driver


async def _memory_transaction(engine, operation):
    # No checkout/checkin/new Session: those can commit/rollback another StaticPool caller.
    driver = _memory_driver(engine)
    connection = driver._conn
    def atomic():
        if connection.in_transaction:
            raise CredentialQuotaUnavailable()
        owned = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            owned = True
            result = operation(connection)
            connection.commit()
            owned = False
            return result
        except BaseException:
            if owned:
                connection.rollback()
            raise
    # The reviewed pinned worker executes this entire callback before the next queued SQL.
    return await driver._execute(atomic)


def _sqlite_time(connection, now):
    return _time_us(now) if now is not None else int(connection.execute(
        "SELECT CAST((julianday('now') - 2440587.5) * 86400000000 AS INTEGER)").fetchone()[0])


def _sqlite_date(value):
    return (EPOCH + timedelta(microseconds=value)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


async def _consume_memory(engine, keys, rule, now):
    def operation(connection):
        buckets = {}
        for key in keys:
            row = connection.execute("SELECT hits FROM credential_rate_limit_buckets WHERE key_hash=?", (key,)).fetchone()
            buckets[key] = json.loads(row[0]) if row else []
        current = _sqlite_time(connection, now)
        changed = _advance(buckets, rule, current)
        expiry = _sqlite_date(current + round(rule.window_seconds * 1_000_000))
        for key, hits in changed.items():
            connection.execute("INSERT INTO credential_rate_limit_buckets (key_hash,hits,expires_at) VALUES (?,?,?) "
                "ON CONFLICT(key_hash) DO UPDATE SET hits=excluded.hits,expires_at=excluded.expires_at", (key, json.dumps(hits), expiry))
    await _memory_transaction(engine, operation)


async def consume_credential_quota(session, key, rule, settings, *, now=None):
    keys, engine = hashed_keys(key, settings), _engine(session)
    started = time.monotonic()
    try:
        async with asyncio.timeout(BUDGET_SECONDS):
            if _private_memory(engine):
                await _consume_memory(engine, keys, rule, now)
            elif engine.dialect.name == "sqlite" and isinstance(engine.pool, StaticPool):
                raise CredentialQuotaUnavailable()
            else:
                await _consume(engine, keys, rule, now)
        return QuotaBudget(max(0, BUDGET_SECONDS - (time.monotonic() - started)))
    except HTTPException:
        raise
    except (SQLAlchemyError, sqlite3.Error, TimeoutError, OSError, ValueError) as error:
        logger.warning("credential_quota_unavailable error_type=%s", type(error).__name__)
        raise CredentialQuotaUnavailable() from None


async def reset_credential_quota(session, key, settings, *, budget=None):
    keys = hashed_keys(key, settings)
    try:
        async with asyncio.timeout(budget.remaining_seconds if budget is not None else BUDGET_SECONDS):
            engine = _engine(session)
            if _private_memory(engine):
                driver = _memory_driver(engine)
                def reset_in_caller_transaction():
                    if not session.in_transaction() or not driver._conn.in_transaction:
                        raise CredentialQuotaUnavailable()
                    for key_hash in keys:
                        driver._conn.execute("UPDATE credential_rate_limit_buckets SET hits='[]', expires_at=? WHERE key_hash=?",
                            (_sqlite_date(0), key_hash))
                # Auth has already issued its own DML. Never start/commit/close its transaction.
                await driver._execute(reset_in_caller_transaction)
            else:
                for key_hash in keys:
                    await session.execute(update(CredentialRateLimitBucket).where(CredentialRateLimitBucket.key_hash == key_hash)
                        .values(hits=[], expires_at=EPOCH))
    except (CredentialQuotaUnavailable, SQLAlchemyError, sqlite3.Error, TimeoutError, OSError) as error:
        logger.warning("credential_quota_reset_unavailable error_type=%s", type(error).__name__)
        await session.rollback()
        raise CredentialQuotaUnavailable() from None


async def prune_credential_quotas(bind, *, now=None, limit=CLEANUP_LIMIT):
    engine = _engine(bind)
    if not 1 <= limit <= CLEANUP_LIMIT:
        raise ValueError("Cleanup must remain bounded")
    if _private_memory(engine):
        def operation(connection):
            current = _sqlite_time(connection, now)
            keys = [row[0] for row in connection.execute("SELECT key_hash FROM credential_rate_limit_buckets "
                "WHERE expires_at<=? ORDER BY expires_at,key_hash LIMIT ?", (_sqlite_date(current), limit)).fetchall()]
            for key in keys:
                connection.execute("DELETE FROM credential_rate_limit_buckets WHERE key_hash=?", (key,))
            return len(keys)
        async with asyncio.timeout(1):
            return await _memory_transaction(engine, operation)
    if engine.dialect.name == "sqlite" and isinstance(engine.pool, StaticPool):
        raise CredentialQuotaUnavailable()
    table = CredentialRateLimitBucket.__table__
    async def operation(connection):
        current = _time_us(now) if now is not None else await _db_time(connection)
        query = select(table.c.key_hash).where(table.c.expires_at <= EPOCH + timedelta(microseconds=current))
        query = query.order_by(table.c.expires_at, table.c.key_hash).limit(limit)
        if connection.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        keys = list(await connection.scalars(query))
        if keys:
            await connection.execute(delete(table).where(table.c.key_hash.in_(keys)))
        return len(keys)
    async with asyncio.timeout(1):
        return await _transaction(engine, operation)


async def maybe_prune_credential_quotas(factory):
    try:
        await prune_credential_quotas(factory.kw["bind"])
    except (SQLAlchemyError, CredentialQuotaUnavailable, sqlite3.Error, TimeoutError, OSError, ValueError) as error:
        logger.warning("credential_quota_cleanup_deferred error_type=%s", type(error).__name__)
