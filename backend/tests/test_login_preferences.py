from datetime import datetime, timezone

import pytest

from app.auth import decode_token, hash_password
from app.models import User
from app.routers.auth import auth_router
from tests.support import api_test_context, make_test_settings


@pytest.mark.parametrize("remember", [True, False])
@pytest.mark.asyncio
async def test_remember_login_cookie_preference_survives_refresh_rotation(database_session, remember):
    settings = make_test_settings(refresh_cookie_secure=False, refresh_cookie_samesite="lax")
    user = User(email="remember-login@example.com", display_name="登入測試", password_hash=hash_password("password-for-test"), email_verified_at=datetime.now(timezone.utc))
    database_session.add(user)
    await database_session.commit()
    async with api_test_context(database_session, [auth_router], settings=settings) as client:
        login = await client.post("/v1/auth/login", json={"email": user.email, "password": "password-for-test", "remember_me": remember})
        assert login.status_code == 200, login.text
        assert ("Max-Age=" in login.headers["set-cookie"]) == remember
        assert "HttpOnly" in login.headers["set-cookie"] and "SameSite=lax" in login.headers["set-cookie"]
        refresh_token = client.cookies.get(settings.refresh_cookie_name)
        assert decode_token(refresh_token, "refresh", settings)["remember_me"] == remember
        renewed = await client.post("/v1/auth/refresh")
        assert renewed.status_code == 200, renewed.text
        assert ("Max-Age=" in renewed.headers["set-cookie"]) == remember
        assert decode_token(client.cookies.get(settings.refresh_cookie_name), "refresh", settings)["remember_me"] == remember
