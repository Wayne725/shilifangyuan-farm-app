import base64
import hashlib
import math
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select

from app.auth import decode_token, make_token_pair, verify_password
from app.integrations.common import IntegrationConfigurationError, IntegrationError, IntegrationResponseError
from app.integrations.line_login import LineLoginClient, LineProfile
from app.models import ExternalIdentity, LineOAuthState, Membership, MembershipStatus, User, UserRole
import app.routers.line_auth as line_auth_module
from app.routers.auth import auth_router
from app.routers.line_auth import FLOW_COOKIE, REGISTRATION_COOKIE, line_auth_router
from tests.support import api_test_context, fast_password_hash, make_test_settings


@pytest.fixture
def settings():
    return make_test_settings(
        line_login_channel_id="1234567890",
        line_login_channel_secret="test-channel-secret",
        line_login_redirect_uri="https://app.example.test/auth/line/callback",
        refresh_cookie_secure=False,
        refresh_cookie_samesite="lax",
    )


@pytest.fixture(autouse=True)
def reset_line_rate_limits():
    from app import rate_limit

    rate_limit._hits.clear()
    rate_limit._bucket_windows.clear()
    yield
    rate_limit._hits.clear()
    rate_limit._bucket_windows.clear()


@pytest.fixture
def profile(monkeypatch):
    calls = []
    response = {"subject": "U-test-line-subject", "display_name": "LINE測試使用者", "email": "line-user@example.com"}

    async def exchange(self, code, verifier, nonce):
        calls.append((code, verifier, nonce))
        return LineProfile(**response)

    monkeypatch.setattr(LineLoginClient, "exchange", exchange)
    return response, calls


async def start_flow(client, **body):
    response = await client.post("/v1/auth/line/start", json={"return_to": "/shop", **body})
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return query["state"][0], query


async def callback(client, state, **body):
    return await client.post("/v1/auth/line/callback", json={"state": state, "code": "test-code", **body})


async def seed_user(session, **values):
    user = User(
        email=values.pop("email", "existing@example.com"),
        display_name="既有帳號",
        password_hash=fast_password_hash("test-password"),
        email_verified_at=datetime.now(timezone.utc),
        **values,
    )
    session.add(user)
    await session.commit()
    return user


def credentials(user, settings):
    return {"Authorization": f"Bearer {make_token_pair(user, settings)['access_token']}"}


@pytest.mark.asyncio
async def test_start_binds_one_time_state_to_browser_and_pkce(database_session, settings):
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        state, query = await start_flow(client, intent="member", remember_me=False, return_to="/checkout?from=meal")
        row = await database_session.scalar(select(LineOAuthState))
        assert row.state_hash == hashlib.sha256(state.encode()).hexdigest()
        assert row.browser_hash == hashlib.sha256(client.cookies.get(FLOW_COOKIE).encode()).hexdigest()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(row.code_verifier.encode()).digest()).rstrip(b"=").decode()
        assert query["code_challenge"] == [challenge]
        assert query["code_challenge_method"] == ["S256"]
        assert query["nonce"] == [row.nonce]
        assert query["redirect_uri"] == [settings.line_login_redirect_uri]
        assert row.registration_intent == "member" and row.remember_me is False
        assert row.return_to == "/checkout?from=meal"
        assert row.consumed_at is None
        assert (row.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds() <= 600
        assert row.browser_hash != client.cookies.get(FLOW_COOKIE)
        response = await client.get("/v1/auth/line/status")
        assert response.json() == {"ready": True, "linked": False, "identity_verified": False, "message": ""}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("return_to", ["https://evil.example/shop", "//evil.example/shop", "/unknown-screen", "/shop\\evil", "/shop\n", "/meals/../admin", "/groups/../admin"])
@pytest.mark.asyncio
async def test_start_rejects_open_redirects_and_unsupported_return_paths(database_session, settings, return_to):
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        response = await client.post("/v1/auth/line/start", json={"return_to": return_to})
        assert response.status_code == 422
        assert await database_session.scalar(select(func.count()).select_from(LineOAuthState)) == 0


@pytest.mark.parametrize("return_to", [
    "/social", "/admin?section=orders&q=SLF-1", "/admin/meals?tab=cancellations&q=SLF-1",
    "/groups/campaign-1", "/products/product-1", "/proposals/proposal-1",
    "/group-votes/proposal-1", "/membership", "/register",
])
@pytest.mark.asyncio
async def test_login_keeps_supported_work_page_without_granting_admin_role(database_session, settings, profile, return_to):
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        state, _ = await start_flow(client, return_to=return_to)
        response = await callback(client, state)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["return_to"] == ("/account" if return_to == "/register" else return_to)
        assert result["session"]["user"]["user_role"] == "customer"
        assert result["session"]["user"]["membership_type"] == "nonmember"


@pytest.mark.asyncio
async def test_status_fallback_and_cross_origin_start(database_session, settings):
    unconfigured = settings.model_copy(update={"line_login_channel_secret": ""})
    async with api_test_context(database_session, [line_auth_router], settings=unconfigured) as client:
        status = await client.get("/v1/auth/line/status")
        assert status.json()["ready"] is False
        assert (await client.post("/v1/auth/line/start", json={})).status_code == 503
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        response = await client.post("/v1/auth/line/start", json={}, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        assert await database_session.scalar(select(func.count()).select_from(LineOAuthState)) == 0


@pytest.mark.parametrize("remember", [True, False])
@pytest.mark.asyncio
async def test_new_line_account_member_intent_does_not_grant_membership_or_email_verification(database_session, settings, profile, remember):
    async with api_test_context(database_session, [line_auth_router, auth_router], settings=settings) as client:
        state, _ = await start_flow(client, intent="member", remember_me=remember)
        response = await callback(client, state)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "authenticated" and result["intent"] == "member"
        user = result["session"]["user"]
        assert user["membership_type"] == "nonmember" and user["user_role"] == "customer"
        assert user["email_verified_at"] is None
        saved = await database_session.get(User, user["id"])
        assert saved.password_hash == "!line-only" and not verify_password("anything", saved.password_hash)
        assert saved.customer_number.startswith("SLF-C-")
        identity = await database_session.scalar(select(ExternalIdentity))
        assert identity.registration_intent == "member" and identity.user_id == saved.id
        assert await database_session.scalar(select(func.count()).select_from(Membership)) == 0
        refresh_cookie = next(value for value in response.headers.get_list("set-cookie") if value.startswith(f"{settings.refresh_cookie_name}="))
        assert "HttpOnly" in refresh_cookie and "SameSite=lax" in refresh_cookie
        assert ("Max-Age=" in refresh_cookie) == remember
        refresh = await client.post("/v1/auth/refresh")
        assert refresh.status_code == 200, refresh.text
        assert decode_token(client.cookies.get(settings.refresh_cookie_name), "refresh", settings)["remember_me"] is remember
        client.headers.update({"Authorization": f"Bearer {result['session']['access_token']}"})
        status = await client.get("/v1/auth/line/status")
        assert status.json()["identity_verified"] is True


@pytest.mark.parametrize("invalid", ["wrong_browser", "missing_cookie", "expired", "consumed", "unknown"])
@pytest.mark.asyncio
async def test_invalid_authorization_never_exchanges_code(database_session, settings, profile, invalid, monkeypatch):
    diagnostics = []
    monkeypatch.setattr(
        line_auth_module.logger,
        "warning",
        lambda message, *args: diagnostics.append(message % args),
    )
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        state, _ = await start_flow(client)
        row = await database_session.scalar(select(LineOAuthState))
        if invalid in {"wrong_browser", "missing_cookie"}:
            client.cookies.clear()
        if invalid == "wrong_browser":
            client.cookies.set(FLOW_COOKIE, "different-browser", path="/v1/auth/line")
        elif invalid == "expired":
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif invalid == "consumed":
            row.consumed_at = datetime.now(timezone.utc)
        else:
            state = "unknown-state-with-sufficient-length"
        await database_session.commit()
        response = await callback(client, state)
        assert response.status_code == 400
        if invalid == "wrong_browser":
            assert "state_found=True cookie_present=True browser_match=False" in diagnostics
        elif invalid == "missing_cookie":
            assert "state_found=True cookie_present=False browser_match=False" in diagnostics
        elif invalid == "unknown":
            assert "state_found=False cookie_present=True browser_match=False" in diagnostics
        else:
            assert diagnostics == []
        assert all(
            "different-browser" not in line
            and "unknown-state-with-sufficient-length" not in line
            for line in diagnostics
        )
        assert profile[1] == []
        assert await database_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 0


@pytest.mark.asyncio
async def test_callback_replay_and_cancel_clear_flow_without_new_account(database_session, settings, profile):
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        state, _ = await start_flow(client)
        browser_cookie = next(cookie for cookie in client.cookies.jar if cookie.name == FLOW_COOKIE)
        browser = browser_cookie.value
        cancelled = await callback(client, state, error="access_denied", code="")
        assert cancelled.json()["status"] == "cancelled"
        assert client.cookies.get(FLOW_COOKIE) is None
        client.cookies.set(FLOW_COOKIE, browser, domain=browser_cookie.domain, path=browser_cookie.path)
        assert (await callback(client, state)).status_code == 400
        assert profile[1] == []
        assert await database_session.scalar(select(func.count()).select_from(User)) == 0
        next_state, _ = await start_flow(client)
        next_browser = client.cookies.get(FLOW_COOKIE)
        assert (await callback(client, next_state)).status_code == 200
        client.cookies.set(FLOW_COOKIE, next_browser, domain=browser_cookie.domain, path=browser_cookie.path)
        assert (await callback(client, next_state)).status_code == 400
        assert len(profile[1]) == 1


@pytest.mark.asyncio
async def test_missing_code_and_provider_failure_consume_authorization(database_session, settings, monkeypatch):
    calls = []

    async def fail(self, code, verifier, nonce):
        calls.append(code)
        raise IntegrationError("provider unavailable")

    monkeypatch.setattr(LineLoginClient, "exchange", fail)
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        state, _ = await start_flow(client)
        assert (await callback(client, state, code="")).status_code == 400
        assert calls == []
        state, _ = await start_flow(client)
        failure = await callback(client, state)
        assert failure.status_code == 502
        assert (await callback(client, state)).status_code == 400
        assert calls == ["test-code"]
        assert await database_session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.asyncio
async def test_existing_email_is_not_silently_merged(database_session, settings, profile):
    existing = await seed_user(database_session, email=profile[0]["email"])
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        state, _ = await start_flow(client)
        result = await callback(client, state)
        assert result.status_code == 200
        created = result.json()["session"]["user"]
        assert created["id"] != existing.id and created["email"] is None
        assert created["membership_type"] == "nonmember" and created["user_role"] == "customer"
        assert await database_session.scalar(select(func.count()).select_from(User)) == 2
        assert await database_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 1
        assert (await database_session.get(User, existing.id)).password_hash != "!line-only"


@pytest.mark.asyncio
async def test_link_preserves_original_admin_and_membership_then_signs_in_same_identity(database_session, settings, profile):
    user = await seed_user(database_session, user_role=UserRole.ADMIN)
    membership = Membership(user_id=user.id, member_number="OLD-001", status=MembershipStatus.ACTIVE, activated_at=datetime.now(timezone.utc))
    database_session.add(membership)
    await database_session.commit()
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        client.headers.update(credentials(user, settings))
        state, _ = await start_flow(client, mode="link", return_to="/account")
        linked = await callback(client, state)
        assert linked.status_code == 200 and linked.json()["status"] == "linked"
        assert await database_session.scalar(select(func.count()).select_from(User)) == 1
        assert (await database_session.get(User, user.id)).email == "existing@example.com"
        client.headers.clear()
        state, _ = await start_flow(client)
        logged_in = await callback(client, state)
        assert logged_in.json()["session"]["user"]["id"] == user.id
        assert logged_in.json()["session"]["user"]["user_role"] == "admin"
        assert logged_in.json()["session"]["user"]["membership_type"] == "member"
        assert (await database_session.get(Membership, membership.id)).member_number == "OLD-001"


@pytest.mark.asyncio
async def test_start_mode_and_account_changes_are_rejected(database_session, settings, profile):
    first = await seed_user(database_session)
    second = await seed_user(database_session, email="another@example.com")
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        assert (await client.post("/v1/auth/line/start", json={"mode": "link"})).status_code == 401
        client.headers.update(credentials(first, settings))
        assert (await client.post("/v1/auth/line/start", json={"mode": "login"})).status_code == 409
        state, _ = await start_flow(client, mode="link")
        client.headers.update(credentials(second, settings))
        assert (await callback(client, state)).status_code == 409
        assert profile[1] == []
        client.headers.clear()
        state, _ = await start_flow(client)
        client.headers.update(credentials(first, settings))
        assert (await callback(client, state)).status_code == 409
        assert profile[1] == []


@pytest.mark.parametrize("change", ["logout", "deactivate"])
@pytest.mark.asyncio
async def test_link_rechecks_original_account_after_provider_exchange(database_session, settings, monkeypatch, change):
    user = await seed_user(database_session)

    async def exchange(self, code, verifier, nonce):
        if change == "logout":
            user.token_version += 1
        else:
            user.is_active = False
        await database_session.commit()
        return LineProfile("U-test-line-subject", "LINE測試", None)

    monkeypatch.setattr(LineLoginClient, "exchange", exchange)
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        client.headers.update(credentials(user, settings))
        state, _ = await start_flow(client, mode="link")
        result = await callback(client, state)
        assert result.status_code == 409
        assert await database_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 0


@pytest.mark.parametrize("conflict", ["line_owned_elsewhere", "user_has_other_line"])
@pytest.mark.asyncio
async def test_link_does_not_replace_existing_identity(database_session, settings, profile, conflict):
    first = await seed_user(database_session)
    second = await seed_user(database_session, email="other-owner@example.com")
    owner = second if conflict == "line_owned_elsewhere" else first
    subject = profile[0]["subject"] if conflict == "line_owned_elsewhere" else "U-other-subject"
    identity = ExternalIdentity(user_id=owner.id, provider="line", subject=subject)
    database_session.add(identity)
    await database_session.commit()
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        client.headers.update(credentials(first, settings))
        state, _ = await start_flow(client, mode="link")
        assert (await callback(client, state)).status_code == 409
        assert await database_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 1
        saved = await database_session.get(ExternalIdentity, identity.id)
        assert saved.subject == subject and saved.user_id == owner.id


@pytest.mark.asyncio
async def test_disabled_linked_account_cannot_sign_in(database_session, settings, profile):
    user = await seed_user(database_session, is_active=False)
    database_session.add(ExternalIdentity(user_id=user.id, provider="line", subject=profile[0]["subject"]))
    await database_session.commit()
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        state, _ = await start_flow(client)
        assert (await callback(client, state)).status_code == 403
        assert client.cookies.get(settings.refresh_cookie_name) is None


@pytest.mark.asyncio
async def test_no_line_email_automatically_creates_customer_once(database_session, settings, profile):
    profile[0]["email"] = None
    async with api_test_context(database_session, [line_auth_router, auth_router], settings=settings) as client:
        state, _ = await start_flow(client, intent="member", remember_me=False, return_to="/checkout")
        result = await callback(client, state)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "authenticated" and result.json()["return_to"] == "/checkout"
        user = result.json()["session"]["user"]
        assert user["email"] is None and user["email_verified_at"] is None
        assert user["notification_email"] is None
        assert user["membership_type"] == "nonmember"
        assert client.cookies.get(FLOW_COOKIE) is None and client.cookies.get(REGISTRATION_COOKIE) is None
        assert (await callback(client, state)).status_code == 400
        assert (await client.post("/v1/auth/line/register", json={})).status_code == 400
        refresh = await client.post("/v1/auth/refresh")
        assert refresh.status_code == 200 and refresh.json()["user"]["id"] == user["id"]
        assert await database_session.scalar(select(func.count()).select_from(User)) == 1
        assert len(profile[1]) == 1


async def legacy_pending_flow(client, session, subject):
    state, _ = await start_flow(client)
    record = await session.scalar(select(LineOAuthState).where(LineOAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()))
    record.consumed_at = datetime.now(timezone.utc)
    record.pending_subject = subject
    record.pending_display_name = "舊版待完成LINE使用者"
    registration = "legacy-registration-token"
    record.registration_hash = hashlib.sha256(registration.encode()).hexdigest()
    await session.commit()
    client.cookies.set(REGISTRATION_COOKIE, registration, path="/v1/auth/line")


@pytest.mark.asyncio
async def test_legacy_pending_flow_can_finish_without_email(database_session, settings, profile):
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        await legacy_pending_flow(client, database_session, profile[0]["subject"])
        assert (await client.get("/v1/auth/line/pending")).json()["status"] == "needs_email"
        result = await client.post("/v1/auth/line/register", json={})
        assert result.status_code == 200, result.text
        assert result.json()["session"]["user"]["email"] is None
        assert (await client.post("/v1/auth/line/register", json={})).status_code == 400


@pytest.mark.parametrize("failure", ["expired", "wrong_browser", "existing_email", "already_logged_in"])
@pytest.mark.asyncio
async def test_pending_registration_failures_do_not_create_or_merge_accounts(database_session, settings, profile, failure):
    profile[0]["email"] = None
    existing = await seed_user(database_session)
    async with api_test_context(database_session, [line_auth_router], settings=settings) as client:
        await legacy_pending_flow(client, database_session, profile[0]["subject"])
        email = "new@example.com"
        if failure == "expired":
            row = await database_session.scalar(select(LineOAuthState))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await database_session.commit()
        elif failure == "wrong_browser":
            registration = client.cookies.get(REGISTRATION_COOKIE)
            client.cookies.clear()
            client.cookies.set(FLOW_COOKIE, "wrong-browser", path="/v1/auth/line")
            client.cookies.set(REGISTRATION_COOKIE, registration, path="/v1/auth/line")
        elif failure == "existing_email":
            email = existing.email
        else:
            client.headers.update(credentials(existing, settings))
        response = await client.post("/v1/auth/line/register", json={"email": email})
        assert response.status_code == (409 if failure in {"existing_email", "already_logged_in"} else 400)
        assert await database_session.scalar(select(func.count()).select_from(User)) == 1
        assert await database_session.scalar(select(func.count()).select_from(ExternalIdentity)) == 0


@pytest.mark.parametrize("updates", [
    {"line_login_channel_id": ""},
    {"line_login_channel_secret": ""},
    {"line_login_scope": "profile"},
    {"line_login_scope": "openid profile invalid"},
    {"line_login_redirect_uri": "https://evil.example/auth/line/callback"},
    {"line_login_redirect_uri": "https://app.example.test/other"},
    {"line_login_redirect_uri": "https://app.example.test/auth/line/callback?next=evil"},
    {"line_login_redirect_uri": "https://user:password@app.example.test/auth/line/callback"},
    {"environment": "production", "web_base_url": "http://app.example.test", "line_login_redirect_uri": "http://app.example.test/auth/line/callback"},
])
def test_client_rejects_incomplete_or_unsafe_configuration(settings, updates):
    with pytest.raises(IntegrationConfigurationError):
        LineLoginClient(settings.model_copy(update=updates)).validate_settings()


@pytest.mark.asyncio
async def test_client_exchanges_code_with_verifier_and_verifies_id_token_and_nonce(settings, monkeypatch):
    calls = []
    now = time.time()

    async def post(self, path, data):
        calls.append((path, data))
        if path == "token":
            return {"id_token": "fake-id-token", "access_token": "unused-access-token"}
        return {"iss": "https://access.line.me", "aud": settings.line_login_channel_id,
            "nonce": "fake-nonce", "sub": "U-verified-subject", "exp": now + 3600,
            "iat": now - 1, "name": "LINE使用者", "email": "Notify@EXAMPLE.com"}

    monkeypatch.setattr(LineLoginClient, "_post", post)
    result = await LineLoginClient(settings).exchange("fake-code", "fake-verifier", "fake-nonce")
    assert result == LineProfile("U-verified-subject", "LINE使用者", "notify@example.com")
    assert calls[0][0] == "token" and calls[0][1]["code_verifier"] == "fake-verifier"
    assert calls[0][1]["redirect_uri"] == settings.line_login_redirect_uri
    assert calls[1] == ("verify", {"id_token": "fake-id-token", "client_id": settings.line_login_channel_id, "nonce": "fake-nonce"})


@pytest.mark.parametrize("bad_claims", [
    {"iss": "https://evil.example"}, {"aud": "different-channel"}, {"nonce": "wrong-nonce"},
    {"sub": ""}, {"sub": 123}, {"sub": "U" * 256}, {"exp": 0},
    {"exp": math.nan}, {"exp": math.inf}, {"iat": math.nan}, {"iat": True},
    {"iat": time.time() + 3600},
])
@pytest.mark.asyncio
async def test_client_rejects_invalid_verified_token_claims(settings, monkeypatch, bad_claims):
    now = time.time()

    async def post(self, path, data):
        if path == "token":
            return {"id_token": "fake-id-token"}
        return {"iss": "https://access.line.me", "aud": settings.line_login_channel_id,
            "nonce": "fake-nonce", "sub": "U-subject", "exp": now + 600,
            "iat": now - 1, **bad_claims}

    monkeypatch.setattr(LineLoginClient, "_post", post)
    with pytest.raises(IntegrationResponseError):
        await LineLoginClient(settings).exchange("fake-code", "fake-verifier", "fake-nonce")


@pytest.mark.parametrize("response", [{}, {"id_token": None}, {"id_token": 123}])
@pytest.mark.asyncio
async def test_client_requires_id_token(settings, monkeypatch, response):
    async def post(self, path, data):
        assert path == "token"
        return response

    monkeypatch.setattr(LineLoginClient, "_post", post)
    with pytest.raises(IntegrationResponseError):
        await LineLoginClient(settings).exchange("fake-code", "fake-verifier", "fake-nonce")


@pytest.mark.parametrize("kind", ["http_error", "invalid_json", "non_object", "timeout"])
@pytest.mark.asyncio
async def test_client_handles_provider_failures_without_exposing_response_or_secret(settings, monkeypatch, kind):
    async def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        if kind == "timeout":
            raise httpx.ReadTimeout("provider timeout with sensitive content", request=request)
        if kind == "http_error":
            return httpx.Response(401, json={"error_description": "sensitive-provider-details"}, request=request)
        if kind == "invalid_json":
            return httpx.Response(200, text="invalid-sensitive-provider-details", request=request)
        return httpx.Response(200, json=["sensitive-provider-details"], request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(IntegrationError) as failure:
        await LineLoginClient(settings).exchange("fake-code", "fake-verifier", "fake-nonce")
    assert "sensitive" not in str(failure.value)
    assert settings.line_login_channel_secret not in str(failure.value)
