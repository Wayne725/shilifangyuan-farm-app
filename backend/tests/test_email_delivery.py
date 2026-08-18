from __future__ import annotations

import pytest

import app.jobs as jobs_module
from app.integrations.email_sender import EmailSendResult
from app.integrations.common import IntegrationResponseError
from app.jobs import jobs_router
from app.rate_limit import reset_all
from app.routers.auth import auth_router
from tests.support import api_test_context, make_test_settings


@pytest.fixture
async def email_app(database_session, monkeypatch: pytest.MonkeyPatch):
    settings = make_test_settings(
        environment="development",
        internal_reconcile_secret="email-reconcile-secret",
    )

    class AcceptedSender:
        async def send(self, message):
            return EmailSendResult(
                accepted=True,
                provider_message_id="accepted-message",
                status_code=200,
                provider="resend",
            )

    monkeypatch.setattr(
        jobs_module,
        "email_sender_from_settings",
        lambda _settings: AcceptedSender(),
    )
    async with api_test_context(
        database_session,
        [auth_router, jobs_router],
        settings=settings,
    ) as client:
        reset_all()
        yield client, settings
        reset_all()


@pytest.mark.asyncio
async def test_provider_accepted_replacement_keeps_previous_code_valid(
    email_app,
) -> None:
    client, settings = email_app
    registered = await client.post(
        "/v1/auth/register",
        json={
            "email": "delivery@example.com",
            "display_name": "寄信測試",
            "password": "initial-pass-123",
        },
    )
    previous_code = registered.json()["development_token"]
    first_delivery = await client.post(
        "/internal/reconcile",
        headers={"X-Reconcile-Secret": settings.internal_reconcile_secret},
    )
    assert first_delivery.json()["outbox_completed"] == 1

    resent = await client.post(
        "/v1/auth/resend-verification",
        json={"email": "delivery@example.com"},
    )
    replacement_code = resent.json()["development_token"]
    replacement_delivery = await client.post(
        "/internal/reconcile",
        headers={"X-Reconcile-Secret": settings.internal_reconcile_secret},
    )
    assert replacement_delivery.json()["outbox_completed"] == 1

    verified_with_previous = await client.post(
        "/v1/auth/verify-email",
        json={"token": previous_code},
    )
    replacement_after_verification = await client.post(
        "/v1/auth/verify-email",
        json={"token": replacement_code},
    )

    assert verified_with_previous.status_code == 200
    assert replacement_after_verification.status_code == 400


@pytest.mark.asyncio
async def test_failed_replacement_delivery_keeps_previous_code_valid(
    email_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, settings = email_app
    registered = await client.post(
        "/v1/auth/register",
        json={
            "email": "failed-delivery@example.com",
            "display_name": "失敗寄信測試",
            "password": "initial-pass-123",
        },
    )
    previous_code = registered.json()["development_token"]
    await client.post(
        "/internal/reconcile",
        headers={"X-Reconcile-Secret": settings.internal_reconcile_secret},
    )

    class RejectedSender:
        async def send(self, message):
            raise IntegrationResponseError("providers unavailable")

    monkeypatch.setattr(
        jobs_module,
        "email_sender_from_settings",
        lambda _settings: RejectedSender(),
    )
    await client.post(
        "/v1/auth/resend-verification",
        json={"email": "failed-delivery@example.com"},
    )
    failed_delivery = await client.post(
        "/internal/reconcile",
        headers={"X-Reconcile-Secret": settings.internal_reconcile_secret},
    )
    verified = await client.post(
        "/v1/auth/verify-email",
        json={"token": previous_code},
    )

    assert failed_delivery.json()["outbox_failed"] == 1
    assert verified.status_code == 200


@pytest.mark.asyncio
async def test_verify_email_is_rate_limited_after_five_failed_codes(
    email_app,
) -> None:
    client, _settings = email_app

    for _ in range(5):
        response = await client.post(
            "/v1/auth/verify-email",
            json={"token": "999999"},
        )
        assert response.status_code == 400

    limited = await client.post(
        "/v1/auth/verify-email",
        json={"token": "999999"},
    )

    assert limited.status_code == 429


@pytest.mark.asyncio
async def test_successful_email_verification_resets_rate_limit(
    email_app,
) -> None:
    client, _settings = email_app
    registered = await client.post(
        "/v1/auth/register",
        json={
            "email": "rate-limit-reset@example.com",
            "display_name": "驗證限流測試",
            "password": "initial-pass-123",
        },
    )
    valid_code = registered.json()["development_token"]
    invalid_code = "000000" if valid_code != "000000" else "000001"

    for _ in range(4):
        failed = await client.post(
            "/v1/auth/verify-email",
            json={"token": invalid_code},
        )
        assert failed.status_code == 400

    verified = await client.post(
        "/v1/auth/verify-email",
        json={"token": valid_code},
    )
    assert verified.status_code == 200

    for _ in range(5):
        failed_after_success = await client.post(
            "/v1/auth/verify-email",
            json={"token": invalid_code},
        )
        assert failed_after_success.status_code == 400

    limited = await client.post(
        "/v1/auth/verify-email",
        json={"token": invalid_code},
    )
    assert limited.status_code == 429
