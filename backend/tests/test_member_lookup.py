from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.jobs as jobs_module
from app.config import get_settings
from app.database import get_session
from app.integrations.common import IntegrationResponseError
from app.integrations.email_sender import EmailSendResult
from app.integrations.pii_crypto import decrypt_auth_outbox_credential, encrypt_auth_outbox_credential, pii_cipher_from_settings
from app.member_claims import roster_aad
from app.member_lookup import MEMBER_LOOKUP_EVENT, MEMBER_LOOKUP_MESSAGE
from app.models import MemberRosterEntry, Membership, MembershipStatus, OutboxEvent, OutboxStatus, User
from app.rate_limit import reset_all
from app.routers.member_lookup import member_lookup_router
from tests.support import make_test_settings


PHONE = "0912345678"
EMAIL = "lookup@example.com"


@pytest.fixture
async def lookup_app(database_session, monkeypatch):
    settings = make_test_settings(
        pii_encryption_keys_json=json.dumps({"v1": base64.b64encode(b"m" * 32).decode()}),
        web_base_url="https://coop.example.com",
    )
    application = FastAPI()
    application.include_router(member_lookup_router)

    async def session_override():
        yield database_session

    application.dependency_overrides[get_session] = session_override
    application.dependency_overrides[get_settings] = lambda: settings
    sent = []

    class AcceptedSender:
        async def send(self, message):
            sent.append(message)
            return EmailSendResult(accepted=True, provider_message_id="lookup-mail", status_code=200)

    monkeypatch.setattr(jobs_module, "email_sender_from_settings", lambda _: AcceptedSender())
    reset_all()
    yield application, settings, sent
    reset_all()


def client_for(application, ip="192.0.2.1"):
    return AsyncClient(transport=ASGITransport(app=application, client=(ip, 12345)), base_url="http://test")


async def add_roster(session, settings, *, number="LOOKUP-001", phone=PHONE, email=EMAIL, active=True):
    entry_id = str(uuid4())
    cipher = pii_cipher_from_settings(settings)
    aad = roster_aad(entry_id)
    entry = MemberRosterEntry(
        id=entry_id, member_number=number, is_active=active,
        legal_name_encrypted=cipher.encrypt_text("不可公開的社員姓名", associated_data=aad),
        phone_encrypted=cipher.encrypt_text(phone, associated_data=aad),
        email_encrypted=cipher.encrypt_text(email, associated_data=aad),
    )
    session.add(entry)
    await session.flush()
    return entry


async def add_account(session, *, active=True):
    user = User(email=f"account-{uuid4()}@example.com", display_name="既有網站帳號", password_hash="!not-a-password", is_active=active)
    session.add(user)
    await session.flush()
    return user


async def identity_snapshot(session):
    return {
        "users": [tuple(row) for row in await session.execute(select(User.id, User.is_active, User.membership_type, User.pending_member_claim).order_by(User.id))],
        "memberships": [tuple(row) for row in await session.execute(select(Membership.id, Membership.user_id, Membership.member_number, Membership.status).order_by(Membership.id))],
        "roster": [tuple(row) for row in await session.execute(select(MemberRosterEntry.id, MemberRosterEntry.is_active, MemberRosterEntry.claimed_user_id, MemberRosterEntry.pending_claim_user_id, MemberRosterEntry.pending_claim_expires_at).order_by(MemberRosterEntry.id))],
    }


async def process_lookup(session, settings):
    return await jobs_module._process_outbox(session, settings, datetime.now(timezone.utc) + timedelta(seconds=1), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario, expected", [
    ("unclaimed", "已找到相符的既有社員名冊"),
    ("active", "已確認您目前具有正式社員資格"),
    ("suspended", "目前資格需要合作社核對"),
    ("resigned", "目前資格需要合作社核對"),
    ("terminated", "目前資格需要合作社核對"),
    ("pending_payment", "目前資格需要合作社核對"),
    ("trainee", "目前資格需要合作社核對"),
    ("inactive_account", "目前資格需要合作社核對"),
    ("missing_membership", "目前資格需要合作社核對"),
    ("mismatched_number", "目前資格需要合作社核對"),
    ("unclaimed_number_used", "本次無法確認您的社員資格"),
    ("inactive_roster", "本次無法確認您的社員資格"),
    ("duplicate", "本次無法確認您的社員資格"),
    ("inactive_duplicate", "本次無法確認您的社員資格"),
    ("missing", "本次無法確認您的社員資格"),
    ("crossed_contacts", "本次無法確認您的社員資格"),
    ("pending", "已有帳號正在認領"),
    ("pending_no_expiry", "已有帳號正在認領"),
    ("pending_expired", "已找到相符的既有社員名冊"),
])
async def test_lookup_returns_same_public_result_and_emails_current_status_without_identity_mutation(
    database_session, lookup_app, scenario, expected,
):
    application, settings, sent = lookup_app
    entry = None
    if scenario != "missing":
        entry = await add_roster(database_session, settings, active=scenario != "inactive_roster", phone="0987654321" if scenario == "crossed_contacts" else PHONE)
    if scenario in {"duplicate", "inactive_duplicate", "crossed_contacts"}:
        await add_roster(database_session, settings, number="LOOKUP-002", active=scenario != "inactive_duplicate", email="different@example.com" if scenario == "crossed_contacts" else EMAIL)
    account_scenarios = {"active", "suspended", "resigned", "terminated", "pending_payment", "trainee", "inactive_account", "missing_membership", "mismatched_number", "unclaimed_number_used"}
    if scenario in account_scenarios:
        user = await add_account(database_session, active=scenario != "inactive_account")
        if scenario != "unclaimed_number_used":
            entry.claimed_user_id = user.id
        if scenario != "missing_membership":
            database_session.add(Membership(user_id=user.id, member_number="DIFFERENT-001" if scenario == "mismatched_number" else entry.member_number, status=MembershipStatus(scenario) if scenario in {"suspended", "resigned", "terminated", "pending_payment", "trainee"} else MembershipStatus.ACTIVE))
    if scenario.startswith("pending") and scenario != "pending_payment":
        pending = await add_account(database_session)
        entry.pending_claim_user_id = pending.id
        entry.pending_claim_expires_at = None if scenario == "pending_no_expiry" else datetime.now(timezone.utc) + timedelta(hours=-1 if scenario == "pending_expired" else 1)
    await database_session.commit()
    before = await identity_snapshot(database_session)
    async with client_for(application) as client:
        response = await client.post("/v1/auth/member-lookup", json={"phone": "+886 912-345-678", "email": " LOOKUP@EXAMPLE.COM "})
    assert response.status_code == 202
    assert response.json() == {"message": MEMBER_LOOKUP_MESSAGE}
    assert response.headers["cache-control"] == "no-store"
    event = await database_session.scalar(select(OutboxEvent))
    assert event.event_type == MEMBER_LOOKUP_EVENT
    assert event.aggregate_type == "member_lookup"
    assert set(event.payload) == {"credential_encrypted"}
    serialized = json.dumps(event.payload)
    assert PHONE not in serialized and EMAIL not in serialized and "LOOKUP-001" not in serialized
    decrypted = decrypt_auth_outbox_credential(settings, MEMBER_LOOKUP_EVENT, event.aggregate_id, event.payload["credential_encrypted"])
    assert json.loads(decrypted) == {"phone": PHONE, "email": EMAIL}
    assert sent == []
    assert await identity_snapshot(database_session) == before
    assert await process_lookup(database_session, settings) == (1, 0)
    assert len(sent) == 1
    assert sent[0].to_email == EMAIL
    assert expected in sent[0].text_content
    assert sent[0].idempotency_key == f"outbox-{event.id}"
    assert "https://coop.example.com/line-consent" in sent[0].text_content
    assert all(value not in sent[0].text_content for value in [PHONE, "LOOKUP-001", "不可公開的社員姓名"])
    assert event.status == OutboxStatus.COMPLETED
    assert event.payload == {"credential_redacted": True}
    assert await identity_snapshot(database_session) == before
    assert await process_lookup(database_session, settings) == (0, 0)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_corrupt_roster_is_not_exposed_on_request_and_fails_closed_in_worker(database_session, lookup_app):
    application, settings, sent = lookup_app
    entry = await add_roster(database_session, settings)
    entry.phone_encrypted = "invalid-envelope"
    await database_session.commit()
    async with client_for(application) as client:
        result = await client.post("/v1/auth/member-lookup", json={"phone": PHONE, "email": EMAIL})
    assert result.status_code == 202 and result.json() == {"message": MEMBER_LOOKUP_MESSAGE}
    assert await process_lookup(database_session, settings) == (0, 1)
    assert sent == []
    event = await database_session.scalar(select(OutboxEvent))
    assert event.status == OutboxStatus.PENDING
    assert "credential_encrypted" in event.payload
    assert PHONE not in event.last_error and EMAIL not in event.last_error


@pytest.mark.asyncio
async def test_lookup_email_retry_preserves_snapshot_and_idempotency_after_membership_changes(database_session, lookup_app, monkeypatch):
    application, settings, sent = lookup_app
    entry = await add_roster(database_session, settings)
    user = await add_account(database_session)
    entry.claimed_user_id = user.id
    membership = Membership(user_id=user.id, member_number=entry.member_number, status=MembershipStatus.ACTIVE)
    database_session.add(membership)
    await database_session.commit()
    attempts = []

    class FailOnce:
        async def send(self, message):
            attempts.append(message)
            if len(attempts) == 1:
                raise IntegrationResponseError("provider temporarily unavailable")
            sent.append(message)
            return EmailSendResult(accepted=True, provider_message_id="retried", status_code=200)

    monkeypatch.setattr(jobs_module, "email_sender_from_settings", lambda _: FailOnce())
    async with client_for(application) as client:
        await client.post("/v1/auth/member-lookup", json={"phone": PHONE, "email": EMAIL})
    assert await process_lookup(database_session, settings) == (0, 1)
    event = await database_session.scalar(select(OutboxEvent))
    assert event.status == OutboxStatus.PENDING and event.attempts == 1
    assert set(event.payload) == {"result_encrypted"}
    assert PHONE not in json.dumps(event.payload) and EMAIL not in json.dumps(event.payload)
    await database_session.refresh(membership)
    membership.status = MembershipStatus.SUSPENDED
    event.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await database_session.commit()
    assert await process_lookup(database_session, settings) == (1, 0)
    assert attempts[0].idempotency_key == attempts[1].idempotency_key
    assert "具有正式社員資格" in attempts[0].text_content
    assert attempts[0] == attempts[1] == sent[0]
    assert event.payload == {"credential_redacted": True}


@pytest.mark.asyncio
async def test_invalid_encrypted_query_does_not_leak_contact_in_worker_error(database_session, lookup_app):
    application, settings, sent = lookup_app
    async with client_for(application) as client:
        await client.post("/v1/auth/member-lookup", json={"phone": PHONE, "email": EMAIL})
    event = await database_session.scalar(select(OutboxEvent))
    event.payload = {"credential_encrypted": encrypt_auth_outbox_credential(
        settings, MEMBER_LOOKUP_EVENT, event.aggregate_id,
        json.dumps({"phone": PHONE, "email": "private-contact-not-an-email"}),
    )}
    await database_session.commit()
    assert await process_lookup(database_session, settings) == (0, 1)
    assert sent == []
    assert event.last_error == "社員查詢資料格式無效"
    assert PHONE not in event.last_error and "private-contact" not in event.last_error


@pytest.mark.asyncio
async def test_terminal_lookup_delivery_failure_removes_contact_envelope(database_session, lookup_app, monkeypatch):
    application, settings, _ = lookup_app
    class Rejected:
        async def send(self, message):
            raise IntegrationResponseError("provider unavailable")
    monkeypatch.setattr(jobs_module, "email_sender_from_settings", lambda _: Rejected())
    async with client_for(application) as client:
        await client.post("/v1/auth/member-lookup", json={"phone": PHONE, "email": EMAIL})
    event = await database_session.scalar(select(OutboxEvent))
    event.attempts = 7
    await database_session.commit()
    assert await process_lookup(database_session, settings) == (0, 1)
    assert event.status == OutboxStatus.FAILED and event.attempts == 8
    assert event.payload == {"credential_redacted": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("same_ip", [True, False])
async def test_lookup_rate_limits_both_ip_and_recipient(database_session, lookup_app, same_ip):
    application, _, _ = lookup_app
    for index in range(6):
        ip = "192.0.2.1" if same_ip else f"192.0.2.{index + 1}"
        email = f"lookup{index}@example.com" if same_ip else "LOOKUP@EXAMPLE.COM"
        async with client_for(application, ip) as client:
            response = await client.post("/v1/auth/member-lookup", json={"phone": PHONE, "email": email})
        assert response.status_code == (202 if index < 5 else 429)
    assert response.headers["retry-after"] == "3600"
    assert len(list(await database_session.scalars(select(OutboxEvent)))) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"phone": "abcdefgh", "email": EMAIL},
    {"phone": "123", "email": EMAIL},
    {"phone": "1234567890123456", "email": EMAIL},
    {"phone": PHONE, "email": "invalid"},
    {"phone": PHONE, "email": EMAIL, "member_number": "LOOKUP-001"},
])
async def test_invalid_lookup_does_not_queue_mail(database_session, lookup_app, body):
    application, _, sent = lookup_app
    async with client_for(application) as client:
        response = await client.post("/v1/auth/member-lookup", json=body)
    assert response.status_code == 422
    assert await database_session.scalar(select(OutboxEvent)) is None
    assert sent == []
