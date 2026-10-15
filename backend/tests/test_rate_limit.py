from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from app.rate_limit import (
    RateLimitRule,
    cleanup_expired,
    client_key,
    enforce,
    reset_all,
)


def make_request(
    ip_address: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/login",
            "headers": headers or [],
            "client": (ip_address, 1234),
        }
    )


def test_client_key_ignores_spoofed_forwarded_header() -> None:
    plain = client_key(make_request("192.0.2.30"), "register")
    spoofed = client_key(
        make_request(
            "192.0.2.30",
            [(b"x-forwarded-for", b"203.0.113.99")],
        ),
        "register",
    )

    assert spoofed == plain


@pytest.fixture(autouse=True)
def clear_rate_limits():
    reset_all()
    yield
    reset_all()


def test_login_limit_follows_account_across_ip_addresses() -> None:
    rule = RateLimitRule(max_attempts=2, window_seconds=60)
    enforce(
        client_key(make_request("192.0.2.1"), "login", "USER@example.com"),
        rule,
        now=1,
    )
    enforce(
        client_key(make_request("192.0.2.2"), "login", "user@example.com"),
        rule,
        now=2,
    )

    with pytest.raises(HTTPException) as error:
        enforce(
            client_key(
                make_request("192.0.2.3"),
                "login",
                "user@example.com",
            ),
            rule,
            now=3,
        )

    assert error.value.status_code == 429


def test_login_limit_follows_ip_across_accounts() -> None:
    rule = RateLimitRule(max_attempts=2, window_seconds=60)
    request = make_request("192.0.2.10")
    enforce(client_key(request, "login", "one@example.com"), rule, now=1)
    enforce(client_key(request, "login", "two@example.com"), rule, now=2)

    with pytest.raises(HTTPException) as error:
        enforce(
            client_key(request, "login", "three@example.com"),
            rule,
            now=3,
        )

    assert error.value.status_code == 429


def test_expired_rate_limit_buckets_are_removed() -> None:
    rule = RateLimitRule(max_attempts=2, window_seconds=10)
    enforce(
        client_key(
            make_request("192.0.2.20"),
            "login",
            "old@example.com",
        ),
        rule,
        now=1,
    )

    assert cleanup_expired(now=12) == 2
    assert cleanup_expired(now=12) == 0
