"""In-process rate limiting for the credential endpoints.

Deliberately dependency-free and per-process: the Sandbox runs a single web
service, and the goal is to blunt credential stuffing and reset-mail flooding,
not to be a distributed quota system. Move this to Redis before running more
than one API instance.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Union

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class RateLimitRule:
    max_attempts: int
    window_seconds: float

    @property
    def retry_after(self) -> int:
        return max(1, int(self.window_seconds))


@dataclass(frozen=True)
class RateLimitKeys:
    values: tuple[str, ...]


#: Login is the credential-stuffing target; the mail-senders are abuse vectors.
LOGIN_RULE = RateLimitRule(max_attempts=10, window_seconds=300)
REGISTER_RULE = RateLimitRule(max_attempts=5, window_seconds=3600)
PASSWORD_RESET_RULE = RateLimitRule(max_attempts=5, window_seconds=3600)
VERIFICATION_RULE = RateLimitRule(max_attempts=5, window_seconds=3600)
DOCUMENT_UPLOAD_RULE = RateLimitRule(max_attempts=12, window_seconds=3600)
INVOICE_QUERY_RULE = RateLimitRule(max_attempts=20, window_seconds=300)
PAYMENT_REFRESH_RULE = RateLimitRule(max_attempts=30, window_seconds=300)

_hits: Dict[str, Deque[float]] = {}
_bucket_windows: Dict[str, float] = {}
RateLimitKey = Union[str, RateLimitKeys]


def client_key(
    request: Request,
    scope: str,
    identifier: str = "",
) -> RateLimitKeys:
    client = request.client.host if request.client else "unknown"
    values = [f"{scope}:ip:{client}"]
    normalized_identifier = identifier.strip().lower()
    if normalized_identifier:
        values.append(f"{scope}:account:{normalized_identifier}")
    return RateLimitKeys(tuple(values))


def _key_values(key: RateLimitKey) -> tuple[str, ...]:
    if isinstance(key, RateLimitKeys):
        return key.values
    return (key,)


def cleanup_expired(*, now: float | None = None) -> int:
    current = now if now is not None else time.monotonic()
    removed = 0
    for key, bucket in list(_hits.items()):
        window_seconds = _bucket_windows.get(key)
        if window_seconds is None:
            continue
        cutoff = current - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if bucket:
            continue
        _hits.pop(key, None)
        _bucket_windows.pop(key, None)
        removed += 1
    return removed


def enforce(
    key: RateLimitKey,
    rule: RateLimitRule,
    *,
    now: float | None = None,
) -> None:
    """Raises 429 once `key` exceeds the rule inside the sliding window."""
    current = now if now is not None else time.monotonic()
    cleanup_expired(now=current)
    cutoff = current - rule.window_seconds
    buckets: list[tuple[str, Deque[float]]] = []
    for value in _key_values(key):
        bucket = _hits.get(value, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= rule.max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="嘗試次數過多，請稍後再試",
                headers={"Retry-After": str(rule.retry_after)},
            )
        buckets.append((value, bucket))
    for value, bucket in buckets:
        bucket.append(current)
        _hits[value] = bucket
        _bucket_windows[value] = rule.window_seconds


def reset(key: RateLimitKey) -> None:
    """Clears a bucket, e.g. after a successful login."""
    for value in _key_values(key):
        _hits.pop(value, None)
        _bucket_windows.pop(value, None)


def reset_all() -> None:
    _hits.clear()
    _bucket_windows.clear()
