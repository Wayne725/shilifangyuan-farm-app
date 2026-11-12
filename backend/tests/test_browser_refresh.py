from datetime import datetime, timedelta, timezone

import pytest
import time_machine
from httpx import Cookies
from sqlalchemy import delete, func, select

from app.auth import decode_token
from app.browser_refresh import (
    BROWSER_REFRESH_TYPE,
    OPERATION_HEADER,
    PROTOCOL_HEADER,
    RECOVERY_WINDOW,
    REFRESH_COOKIE_RACE,
)
from app.models import BrowserRefreshSelection, RefreshSession, User
from app.routers.auth import auth_router
from tests.support import api_test_context, fast_password_hash, make_test_settings

FIRST_OP = {OPERATION_HEADER: "1" * 32}
SECOND_OP = {OPERATION_HEADER: "2" * 32}


@pytest.fixture
async def browser(database_session):
    settings = make_test_settings()
    now = datetime.now(timezone.utc)
    database_session.add_all([
        User(email=email, display_name=email, password_hash=fast_password_hash("test-password"),
             email_verified_at=now)
        for email in ("a@example.com", "b@example.com")
    ])
    await database_session.commit()
    async with api_test_context(database_session, [auth_router], settings=settings) as client:
        yield client, settings


async def login(client, account="a", *, remember=True, v2=True):
    result = await client.post(
        "/v1/auth/login",
        json={"email": f"{account}@example.com", "password": "test-password", "remember_me": remember},
        headers={PROTOCOL_HEADER: "2"} if v2 else {},
    )
    assert result.status_code == 200, result.text
    return result


async def refresh(client, headers=None):
    return await client.post("/v1/auth/refresh", headers=headers)


def snapshot(client):
    return Cookies(client.cookies)


def generation(response, settings):
    prefix = f"{settings.refresh_cookie_name}_v2_"
    issued = [(name, value) for name, value in response.cookies.items() if name.startswith(prefix)]
    assert len(issued) == 1, issued
    name, value = issued[0]
    return name, value, decode_token(value, BROWSER_REFRESH_TYPE, settings)


def access(result):
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


@pytest.mark.parametrize("remember", [True, False])
@pytest.mark.asyncio
async def test_lost_refresh_cookie_replays_only_the_same_bounded_operation(browser, database_session, remember):
    client, settings = browser
    first = await login(client, remember=remember)
    assert first.headers[PROTOCOL_HEADER] == "2"
    assert first.headers["Cache-Control"] == "no-store"
    first_name, _, first_claims = generation(first, settings)
    assert first_claims["remember_me"] is remember
    first_cookie_header = next(value for value in first.headers.get_list("set-cookie") if value.startswith(first_name))
    assert ("Max-Age=" in first_cookie_header) is remember
    assert "HttpOnly" in first_cookie_header and "SameSite=lax" in first_cookie_header

    predecessor = snapshot(client)
    lost = await refresh(client, FIRST_OP)
    assert lost.status_code == 200, lost.text
    client.cookies = predecessor
    for headers in (None, {OPERATION_HEADER: "invalid"}):
        denied = await refresh(client, headers)
        assert denied.status_code == 401
        assert "X-Error-Code" not in denied.headers
    contended = await refresh(client, SECOND_OP)
    assert contended.status_code == 401
    assert contended.headers["X-Error-Code"] == REFRESH_COOKIE_RACE
    assert "Set-Cookie" not in contended.headers
    assert "access_token" not in contended.json()
    recovered = await refresh(client, FIRST_OP)
    assert recovered.status_code == 200, recovered.text
    assert generation(recovered, settings) == generation(lost, settings)
    repeated_with_delivered_cookie = await refresh(client, FIRST_OP)
    assert repeated_with_delivered_cookie.status_code == 200
    assert generation(repeated_with_delivered_cookie, settings) == generation(lost, settings)
    assert await database_session.scalar(select(func.count()).select_from(RefreshSession)) == 2

    # Older deployed browser clients can still rotate an already opted-in session.
    pre_rotation = snapshot(client)
    older_client_rotation = await refresh(client)
    assert older_client_rotation.status_code == 200
    assert generation(older_client_rotation, settings) != generation(lost, settings)
    client.cookies = pre_rotation
    assert (await refresh(client, FIRST_OP)).status_code == 401
    selection = await database_session.scalar(select(BrowserRefreshSelection))
    assert selection.recovery_proof_hash is None


@pytest.mark.asyncio
async def test_receipt_expiry_and_v2_refresh_cannot_be_used_as_legacy_cookie(browser):
    client, settings = browser
    legacy = await login(client, v2=False)
    assert PROTOCOL_HEADER not in legacy.headers
    legacy_refresh = await refresh(client, FIRST_OP)
    assert legacy_refresh.status_code == 200
    old_cookie = legacy_refresh.cookies[settings.refresh_cookie_name]

    current = await login(client)
    first_name, first_token, _ = generation(current, settings)
    predecessor = snapshot(client)
    lost = await refresh(client, FIRST_OP)
    successor = snapshot(client)
    assert lost.status_code == 200, lost.text
    client.cookies = predecessor
    with time_machine.travel(datetime.now(timezone.utc) + RECOVERY_WINDOW + timedelta(seconds=1), tick=False):
        denied = await refresh(client, FIRST_OP)
        assert denied.status_code == 401
        assert "X-Error-Code" not in denied.headers
        client.cookies = successor
        assert (await refresh(client, SECOND_OP)).status_code == 200

    client.cookies.clear()
    client.cookies.set(settings.refresh_cookie_name, first_token, path="/v1/auth")
    assert (await refresh(client, FIRST_OP)).status_code == 401
    client.cookies.set(settings.refresh_cookie_name, old_cookie, path="/v1/auth")
    client.cookies.set(first_name, first_token, path="/v1/auth")
    assert (await refresh(client, FIRST_OP)).status_code == 401
    client.cookies.delete(first_name, path="/v1/auth")
    assert (await refresh(client, FIRST_OP)).status_code == 200


@pytest.mark.asyncio
async def test_late_refresh_and_logout_cookies_cannot_replace_new_account(browser):
    client, settings = browser
    old = await login(client)
    old_identity = snapshot(client)
    late_refresh = await refresh(client, FIRST_OP)
    assert late_refresh.status_code == 200, late_refresh.text
    late_logout = await client.post("/v1/auth/logout", headers=access(old))
    assert late_logout.status_code == 204, late_logout.text

    client.cookies = old_identity
    new = await login(client, "b", v2=False)
    assert new.headers[PROTOCOL_HEADER] == "2"
    assert new.cookies[f"{settings.refresh_cookie_name}_device"] == old.cookies[f"{settings.refresh_cookie_name}_device"]
    client.cookies.extract_cookies(late_refresh)
    client.cookies.extract_cookies(late_logout)
    restored = await refresh(client, SECOND_OP)
    assert restored.status_code == 200, restored.text
    assert restored.json()["user"]["email"] == "b@example.com"
    assert (await client.get("/v1/auth/me", headers=access(old))).status_code == 401


@pytest.mark.asyncio
async def test_old_same_account_logout_cannot_invalidate_new_sign_in(browser):
    client, settings = browser
    stale = await login(client)
    current = await login(client)
    assert generation(stale, settings)[0] != generation(current, settings)[0]
    assert (await client.get("/v1/auth/me", headers=access(stale))).status_code == 401
    assert (await client.patch("/v1/auth/preferences", headers=access(stale), json={})).status_code == 401
    assert (await client.post("/v1/auth/logout", headers=access(stale))).status_code == 204
    assert (await client.get("/v1/auth/me", headers=access(current))).status_code == 200
    refreshed = await refresh(client, FIRST_OP)
    assert refreshed.status_code == 200, refreshed.text
    assert (await client.post("/v1/auth/logout", headers=access(refreshed))).status_code == 204
    assert (await refresh(client, FIRST_OP)).status_code == 401
    assert (await client.get("/v1/auth/me", headers=access(current))).status_code == 401


@pytest.mark.asyncio
async def test_late_receipt_headers_cannot_replace_a_new_generation(browser):
    client, settings = browser
    await login(client)
    older = snapshot(client)
    late = await refresh(client, FIRST_OP)
    assert late.status_code == 200, late.text
    client.cookies = older
    recovered = await refresh(client, FIRST_OP)
    assert generation(recovered, settings) == generation(late, settings)
    current = await refresh(client, SECOND_OP)
    assert current.status_code == 200, current.text
    client.cookies.extract_cookies(late)
    repeated_current = await refresh(client, SECOND_OP)
    assert repeated_current.status_code == 200, repeated_current.text
    assert generation(repeated_current, settings) == generation(current, settings)


@pytest.mark.asyncio
async def test_expired_generation_cleanup_retains_signed_selector_identity(browser, database_session):
    client, settings = browser
    initial = await login(client)
    device_name = f"{settings.refresh_cookie_name}_device"
    device_id = initial.cookies[device_name]
    _, _, claims = generation(initial, settings)
    await database_session.execute(delete(RefreshSession).where(RefreshSession.id == claims["jti"]))
    await database_session.commit()
    assert await database_session.scalar(select(func.count()).select_from(BrowserRefreshSelection)) == 0
    assert (await refresh(client, FIRST_OP)).status_code == 401
    await database_session.rollback()

    renewed = await login(client)
    assert renewed.cookies[device_name] == device_id
    assert (await refresh(client, FIRST_OP)).status_code == 200


@pytest.mark.asyncio
async def test_receipt_requires_the_same_allowed_origin(browser):
    client, settings = browser
    await login(client)
    predecessor = snapshot(client)
    initial_origin = settings.web_base_url
    other_origin = settings.cors_origins[0]
    assert initial_origin != other_origin
    accepted = await refresh(client, {**FIRST_OP, "Origin": initial_origin})
    assert accepted.status_code == 200, accepted.text
    client.cookies = predecessor
    assert (await refresh(client, {**FIRST_OP, "Origin": other_origin})).status_code == 401
    assert (await refresh(client, FIRST_OP)).status_code == 401
    recovered = await refresh(client, {**FIRST_OP, "Origin": initial_origin})
    assert generation(recovered, settings) == generation(accepted, settings)


@pytest.mark.asyncio
async def test_enrollment_opt_out_keeps_existing_browser_and_legacy_sessions_usable(browser):
    client, settings = browser
    current = await login(client)
    settings.browser_refresh_protocol_enabled = False
    assert (await refresh(client, FIRST_OP)).headers[PROTOCOL_HEADER] == "2"
    switched = await login(client, "b")
    assert switched.headers[PROTOCOL_HEADER] == "2"
    device_name = f"{settings.refresh_cookie_name}_device"
    assert switched.cookies[device_name] == current.cookies[device_name]
    client.cookies.clear()
    legacy = await login(client)
    assert PROTOCOL_HEADER not in legacy.headers
    assert (await refresh(client, FIRST_OP)).status_code == 200


@pytest.mark.asyncio
async def test_race_marker_requires_authentic_immediate_predecessor_for_selected_account(browser, database_session):
    client, settings = browser
    original = await login(client)
    predecessor = snapshot(client)
    old_name, old_token, _ = generation(original, settings)
    assert (await refresh(client, FIRST_OP)).status_code == 200
    current = snapshot(client)
    client.cookies = Cookies(predecessor)
    client.cookies.set(old_name, old_token[:-2] + "!!", domain="test.local", path="/v1/auth")
    forged = await refresh(client, SECOND_OP)
    assert forged.status_code == 401
    assert "X-Error-Code" not in forged.headers
    await database_session.rollback()

    client.cookies = current
    switched = await login(client, "b")
    device = f"{settings.refresh_cookie_name}_device"
    assert switched.cookies[device] == original.cookies[device]
    assert (await refresh(client, FIRST_OP)).status_code == 200
    client.cookies = predecessor
    other_account = await refresh(client, SECOND_OP)
    assert other_account.status_code == 401
    assert "X-Error-Code" not in other_account.headers
