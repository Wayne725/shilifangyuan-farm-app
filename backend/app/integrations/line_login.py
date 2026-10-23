from __future__ import annotations

import base64
import hashlib
import math
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from email_validator import EmailNotValidError, validate_email

from ..config import Settings
from .common import IntegrationConfigurationError, IntegrationError, IntegrationResponseError
from .tls import create_tls_context


@dataclass(frozen=True)
class LineProfile:
    subject: str
    display_name: str
    email: str | None


class LineLoginClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def validate_settings(self) -> None:
        settings = self.settings
        redirect = urlsplit(settings.line_login_redirect_uri)
        web = urlsplit(settings.web_base_url)
        if (not settings.line_login_channel_id.strip()
            or not settings.line_login_channel_secret.strip()
            or not redirect.netloc or redirect.username or redirect.password
            or redirect.query or redirect.fragment
            or redirect.path != "/auth/line/callback"
            or (redirect.scheme, redirect.netloc) != (web.scheme, web.netloc)
            or redirect.scheme not in {"http", "https"}
            or (settings.environment not in {"development", "test"} and redirect.scheme != "https")
            or "openid" not in settings.line_login_scope.split()
            or not set(settings.line_login_scope.split()) <= {"openid", "profile", "email"}):
            raise IntegrationConfigurationError("LINE Login settings are incomplete")

    def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        self.validate_settings()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return "https://access.line.me/oauth2/v2.1/authorize?" + urlencode({
            "response_type": "code", "client_id": self.settings.line_login_channel_id,
            "redirect_uri": self.settings.line_login_redirect_uri,
            "state": state, "nonce": nonce, "scope": self.settings.line_login_scope,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })

    async def _post(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=False, verify=create_tls_context(),
            ) as client:
                response = await client.post(f"https://api.line.me/oauth2/v2.1/{path}", data=data)
                if response.status_code != 200:
                    raise IntegrationResponseError("LINE authorization could not be verified")
                result = response.json()
                if not isinstance(result, dict):
                    raise IntegrationResponseError("LINE returned an invalid response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            raise IntegrationError("LINE authorization service is unavailable") from exc

    async def exchange(self, code: str, verifier: str, nonce: str) -> LineProfile:
        self.validate_settings()
        token = await self._post("token", {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self.settings.line_login_redirect_uri,
            "client_id": self.settings.line_login_channel_id,
            "client_secret": self.settings.line_login_channel_secret,
            "code_verifier": verifier,
        })
        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise IntegrationResponseError("LINE did not return an ID token")
        profile = await self._post("verify", {
            "id_token": id_token, "client_id": self.settings.line_login_channel_id, "nonce": nonce,
        })
        now = time.time()
        expires_at = profile.get("exp")
        issued_at = profile.get("iat")
        valid_times = all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in (expires_at, issued_at)
        )
        if (profile.get("iss") != "https://access.line.me"
            or profile.get("aud") != self.settings.line_login_channel_id
            or profile.get("nonce") != nonce
            or not isinstance(profile.get("sub"), str) or not 0 < len(profile["sub"]) <= 255
            or not valid_times or expires_at <= now or issued_at > now + 60 or issued_at >= expires_at):
            raise IntegrationResponseError("LINE ID token claims were rejected")
        email = None
        if isinstance(profile.get("email"), str):
            try:
                email = validate_email(profile["email"], check_deliverability=False).normalized.lower()
            except EmailNotValidError:
                pass
        name = str(profile.get("name") or "LINE使用者").strip()[:80] or "LINE使用者"
        return LineProfile(profile["sub"], name, email)
