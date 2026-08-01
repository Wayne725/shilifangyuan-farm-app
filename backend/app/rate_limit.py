"""In-process rate limiting for the credential endpoints.

Deliberately dependency-free and per-process: the Sandbox runs a single web
service, and the goal is to blunt credential stuffing and reset-mail flooding,
not to be a distributed quota system. Move this to Redis before running more
than one API instance.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class RateLimitRule:
    max_attempts: int
    window_seconds: float

    @property
    def retry_after(self) -> int:
        return max(1, int(self.window_seconds))


#: Login is the credential-stuffing target; the mail-senders are abuse vectors.
LOGIN_RULE = RateLimitRule(max_attempts=10, window_seconds=300)
REGISTER_RULE = RateLimitRule(max_attempts=5, window_seconds=3600)
PASSWORD_RESET_RULE = RateLimitRule(max_attempts=5, window_seconds=3600)
VERIFICATION_RULE = RateLimitRule(max_attempts=5, window_seconds=3600)

_hits: Dict[str, Deque[float]] = defaultdict(deque)


def client_key(request: Request, scope: str, identifier: str = "") -> str:
    client = request.client.host if request.client else "unknown"
    # X-Forwarded-For is set by Render's proxy; take the original client.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        client = forwarded.split(",")[0].strip() or client
    return f"{scope}:{client}:{identifier.lower()}"


def enforce(key: str, rule: RateLimitRule, *, now: float | None = None) -> None:
    """Raises 429 once `key` exceeds the rule inside the sliding window."""
    current = now if now is not None else time.monotonic()
    bucket = _hits[key]
    cutoff = current - rule.window_seconds
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= rule.max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="嘗試次數過多，請稍後再試",
            headers={"Retry-After": str(rule.retry_after)},
        )
    bucket.append(current)


def reset(key: str) -> None:
    """Clears a bucket, e.g. after a successful login."""
    _hits.pop(key, None)


def reset_all() -> None:
    _hits.clear()
