from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.common import IntegrationError
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice import BarcodeValidation
from app.models import User
from app.routers import auth as auth_routes
from tests.support import api_test_context, auth_headers, fast_password_hash, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings


@pytest.fixture
async def barcode_users(database_session):
    users = [
        User(
            email=f"carrier-{index}@example.com",
            display_name=f"載具帳號 {index}",
            password_hash=fast_password_hash("password-for-test"),
            email_verified_at=datetime.now(timezone.utc),
        )
        for index in range(2)
    ]
    database_session.add_all(users)
    await database_session.commit()
    return users


@pytest.mark.asyncio
async def test_saved_barcode_survives_new_session_login_refresh_and_logout(
    database_session, barcode_users, monkeypatch,
):
    user, other_user = barcode_users
    user_id, other_id = user.id, other_user.id
    monkeypatch.setattr(
        auth_routes, "invoice_adapter_from_settings",
        lambda _: FanyuInvoiceAdapter(fanyu_settings()),
    )
    settings = make_test_settings(refresh_cookie_secure=False)
    async with api_test_context(database_session, [auth_routes.auth_router], settings=settings) as client:
        saved = await client.patch(
            "/v1/auth/preferences", headers=auth_headers(user),
            json={"saved_mobile_barcode": " /ab12+-. "},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["saved_mobile_barcode"] == "/AB12+-."
        assert saved.json()["id"] == user_id
        other = await client.get("/v1/auth/me", headers=auth_headers(other_user))
        assert other.json()["saved_mobile_barcode"] is None

    async with AsyncSession(database_session.bind, expire_on_commit=False) as fresh_session:
        stored = await fresh_session.get(User, user_id)
        assert stored.saved_mobile_barcode == "/AB12+-."
        assert (await fresh_session.get(User, other_id)).saved_mobile_barcode is None
        async with api_test_context(fresh_session, [auth_routes.auth_router], settings=settings) as client:
            login = await client.post("/v1/auth/login", json={
                "email": stored.email, "password": "password-for-test",
            })
            assert login.status_code == 200, login.text
            assert login.json()["user"]["saved_mobile_barcode"] == "/AB12+-."
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            me = await client.get("/v1/auth/me", headers=headers)
            assert me.json()["saved_mobile_barcode"] == "/AB12+-."
            refreshed = await client.post("/v1/auth/refresh")
            assert refreshed.status_code == 200, refreshed.text
            assert refreshed.json()["user"]["saved_mobile_barcode"] == "/AB12+-."
            logout = await client.post("/v1/auth/logout", headers=headers)
            assert logout.status_code == 204
            relogin = await client.post("/v1/auth/login", json={
                "email": stored.email, "password": "password-for-test",
            })
            assert relogin.json()["user"]["saved_mobile_barcode"] == "/AB12+-."


@pytest.mark.asyncio
async def test_saved_barcode_can_be_replaced_and_cleared_without_contacting_provider(
    database_session, barcode_users, monkeypatch,
):
    user, other_user = barcode_users
    user.saved_mobile_barcode = "/OLD1234"
    other_user.saved_mobile_barcode = "/OTHER12"
    await database_session.commit()
    validator = AsyncMock(return_value=BarcodeValidation("/NEW1234", True, True))
    monkeypatch.setattr(auth_routes, "invoice_adapter_from_settings", lambda _: SimpleNamespace(validate_mobile_barcode=validator))
    async with api_test_context(database_session, [auth_routes.auth_router]) as client:
        replaced = await client.patch(
            "/v1/auth/preferences", headers=auth_headers(user),
            json={"saved_mobile_barcode": "/NEW1234"},
        )
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["saved_mobile_barcode"] == "/NEW1234"
        validator.assert_awaited_once_with("/NEW1234")
        cleared = await client.patch(
            "/v1/auth/preferences", headers=auth_headers(user),
            json={"saved_mobile_barcode": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["saved_mobile_barcode"] is None
        validator.assert_awaited_once()
        other = await client.get("/v1/auth/me", headers=auth_headers(other_user))
        assert other.json()["saved_mobile_barcode"] == "/OTHER12"
    assert await database_session.scalar(select(User.saved_mobile_barcode).where(User.id == user.id)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_rejected_or_unavailable_barcode_validation_preserves_saved_value(
    database_session, barcode_users, monkeypatch, unavailable,
):
    user = barcode_users[0]
    user.saved_mobile_barcode = "/OLD1234"
    await database_session.commit()
    validator = AsyncMock(
        side_effect=IntegrationError("provider unavailable") if unavailable else None,
        return_value=BarcodeValidation("/NEW1234", False, True, "查無此手機條碼"),
    )
    monkeypatch.setattr(auth_routes, "invoice_adapter_from_settings", lambda _: SimpleNamespace(validate_mobile_barcode=validator))
    async with api_test_context(database_session, [auth_routes.auth_router]) as client:
        response = await client.patch(
            "/v1/auth/preferences", headers=auth_headers(user),
            json={"saved_mobile_barcode": "/NEW1234"},
        )
        assert response.status_code == (503 if unavailable else 422), response.text
        me = await client.get("/v1/auth/me", headers=auth_headers(user))
        assert me.json()["saved_mobile_barcode"] == "/OLD1234"


@pytest.mark.asyncio
async def test_preference_update_requires_account_and_rejects_other_user_id(
    database_session, barcode_users,
):
    user, other_user = barcode_users
    async with api_test_context(database_session, [auth_routes.auth_router]) as client:
        anonymous = await client.patch("/v1/auth/preferences", json={"saved_mobile_barcode": None})
        assert anonymous.status_code == 401
        spoofed = await client.patch(
            "/v1/auth/preferences", headers=auth_headers(user),
            json={"saved_mobile_barcode": None, "user_id": other_user.id},
        )
        assert spoofed.status_code == 422
        omitted = await client.patch("/v1/auth/preferences", headers=auth_headers(user), json={})
        assert omitted.status_code == 422


@pytest.mark.asyncio
async def test_refreshed_session_cannot_write_another_accounts_pending_preference(
    database_session, barcode_users,
):
    user, other_user = barcode_users
    other_user.saved_mobile_barcode = "/OTHER12"
    await database_session.commit()
    async with api_test_context(database_session, [auth_routes.auth_router]) as client:
        response = await client.patch(
            "/v1/auth/preferences",
            headers={**auth_headers(other_user), "X-Expected-User-ID": user.id},
            json={"saved_mobile_barcode": None},
        )
    assert response.status_code == 409
    assert other_user.saved_mobile_barcode == "/OTHER12"


@pytest.mark.asyncio
@pytest.mark.parametrize("barcode", ["", "/SHORT", "/ABC123?", "not-a-barcode"])
async def test_malformed_barcode_cannot_overwrite_saved_preference(
    database_session, barcode_users, monkeypatch, barcode,
):
    user = barcode_users[0]
    user.saved_mobile_barcode = "/OLD1234"
    await database_session.commit()
    monkeypatch.setattr(auth_routes, "invoice_adapter_from_settings", lambda _: FanyuInvoiceAdapter(fanyu_settings()))
    async with api_test_context(database_session, [auth_routes.auth_router]) as client:
        response = await client.patch(
            "/v1/auth/preferences", headers=auth_headers(user),
            json={"saved_mobile_barcode": barcode},
        )
    assert response.status_code == 422, response.text
    assert user.saved_mobile_barcode == "/OLD1234"
