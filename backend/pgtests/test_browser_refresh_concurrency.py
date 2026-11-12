import asyncio
from datetime import datetime, timezone

import pytest
from httpx import Cookies
from sqlalchemy import func, select

from app.browser_refresh import OPERATION_HEADER, PROTOCOL_HEADER, REFRESH_COOKIE_RACE
from app.models import BrowserRefreshSelection, RefreshSession, User
from app.routers.auth import auth_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, fast_password_hash, make_test_settings
from tests.test_browser_refresh import FIRST_OP, SECOND_OP, access, generation, login, snapshot


@pytest.fixture
async def browser_api(postgres_sessions):
    settings = make_test_settings()
    async with postgres_sessions() as session:
        session.add_all([
            User(
                email=f"{account}@example.com", display_name=account,
                password_hash=fast_password_hash("test-password"),
                email_verified_at=datetime.now(timezone.utc),
            )
            for account in ("a", "b")
        ])
        await session.commit()
        async with api_test_context(session, [auth_router], settings=settings) as client:
            first = await login(client)
            cookies = snapshot(client)

    async def request(path, headers, body=None):
        async with postgres_sessions() as session:
            async with api_test_context(session, [auth_router], settings=settings) as client:
                client.cookies = Cookies(cookies)
                return await client.post(path, headers=headers, json=body)

    return request, first, settings


async def concurrently(*operations):
    return await asyncio.wait_for(asyncio.gather(*operations), timeout=8)


@pytest.mark.parametrize("same_operation", [True, False])
@pytest.mark.asyncio
async def test_concurrent_browser_refreshes_have_one_successor(browser_api, postgres_sessions, same_operation):
    request, first, settings = browser_api
    first_headers = FIRST_OP
    second_headers = first_headers if same_operation else SECOND_OP
    results = await concurrently(
        request("/v1/auth/refresh", first_headers),
        request("/v1/auth/refresh", second_headers),
    )
    assert sorted(result.status_code for result in results) == ([200, 200] if same_operation else [200, 401])
    if not same_operation:
        loser = next(result for result in results if result.status_code == 401)
        assert loser.headers["X-Error-Code"] == REFRESH_COOKIE_RACE
        assert "Set-Cookie" not in loser.headers
        assert "access_token" not in loser.json()
    issued = [generation(result, settings)[2]["jti"] for result in results if result.status_code == 200]
    assert len(set(issued)) == 1
    assert issued[0] != generation(first, settings)[2]["jti"]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RefreshSession)) == 2


@pytest.mark.parametrize("same_account", [True, False])
@pytest.mark.asyncio
async def test_concurrent_old_logout_and_new_login_preserve_new_session(browser_api, postgres_sessions, same_account):
    request, first, settings = browser_api
    logged_out, new = await concurrently(
        request("/v1/auth/logout", access(first)),
        request("/v1/auth/login", {PROTOCOL_HEADER: "2"}, {
            "email": "a@example.com" if same_account else "b@example.com",
            "password": "test-password",
        }),
    )
    assert logged_out.status_code == 204, logged_out.text
    assert new.status_code == 200, new.text

    async with postgres_sessions() as session:
        selection = await session.scalar(select(BrowserRefreshSelection))
        assert selection.family_id == generation(new, settings)[2]["family"]
        async with api_test_context(session, [auth_router], settings=settings) as client:
            client.cookies.extract_cookies(new)
            active = await client.post("/v1/auth/refresh", headers={OPERATION_HEADER: "f" * 32})
            assert active.status_code == 200, active.text
