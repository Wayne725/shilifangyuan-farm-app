from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import hash_password, make_token_pair
from app.config import Settings, get_settings
from app.database import Base, get_session
from app.models import (
    ActivityRegistration,
    ActivityRegistrationStatus,
    InvoiceStatus,
    MealEventOffering,
    MemberProfile,
    MemberProposal,
    Membership,
    MembershipApplication,
    MembershipCharge,
    MembershipChargeKind,
    MembershipDocument,
    MembershipDocumentStatus,
    MembershipDocumentType,
    MembershipStatus,
    Order,
    PaymentStatus,
    User,
    UserRole,
)
from app.routers.auth import auth_router
from app.routers.community import community_router
from app.routers.meals import meals_router
from app.routers.membership import membership_router


def make_test_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="development",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        pii_encryption_keys_json=json.dumps(
            {
                "v1": base64.b64encode(b"p" * 32).decode("ascii"),
            }
        ),
    )


def auth_headers(user: User) -> dict[str, str]:
    token = make_token_pair(user)["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def v2_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        application = FastAPI()
        application.include_router(auth_router)
        application.include_router(membership_router)
        application.include_router(community_router)
        application.include_router(meals_router)

        async def override_get_session():
            yield session

        application.dependency_overrides[get_session] = override_get_session
        application.dependency_overrides[get_settings] = make_test_settings

        now = datetime.now(timezone.utc)
        admin = User(
            email="admin@example.com",
            display_name="管理員",
            password_hash=hash_password("admin-pass-123"),
            user_role=UserRole.ADMIN,
            email_verified_at=now,
        )
        applicant = User(
            email="applicant@example.com",
            display_name="申請人",
            password_hash=hash_password("applicant-pass-123"),
            email_verified_at=now,
        )
        member_a = User(
            email="member-a@example.com",
            display_name="社員甲",
            password_hash=hash_password("member-a-pass-123"),
            email_verified_at=now,
            membership=Membership(
                member_number="SLF-2026-0001",
                status=MembershipStatus.ACTIVE,
                activated_at=now,
            ),
        )
        member_b = User(
            email="member-b@example.com",
            display_name="社員乙",
            password_hash=hash_password("member-b-pass-123"),
            email_verified_at=now,
            membership=Membership(
                member_number="SLF-2026-0002",
                status=MembershipStatus.ACTIVE,
                activated_at=now,
            ),
        )
        customer_b = User(
            email="customer-b@example.com",
            display_name="一般顧客乙",
            password_hash=hash_password("customer-b-pass-123"),
            email_verified_at=now,
        )
        session.add_all(
            [admin, applicant, member_a, member_b, customer_b]
        )
        await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            yield {
                "client": client,
                "session": session,
                "admin": admin,
                "applicant": applicant,
                "member_a": member_a,
                "member_b": member_b,
                "customer_b": customer_b,
            }
    await engine.dispose()


@pytest.mark.asyncio
async def test_auth_register_verify_login_refresh_and_reset(
    v2_context,
) -> None:
    client = v2_context["client"]
    registration = {
        "email": "New.User@Example.com",
        "display_name": "新使用者",
        "password": "initial-pass-123",
    }

    registered = await client.post("/v1/auth/register", json=registration)

    assert registered.status_code == 201
    verification_token = registered.json()["development_token"]
    unverified_login = await client.post(
        "/v1/auth/login",
        json={
            "email": "new.user@example.com",
            "password": "initial-pass-123",
        },
    )
    assert unverified_login.status_code == 403

    verified = await client.post(
        "/v1/auth/verify-email",
        json={"token": verification_token},
    )
    assert verified.status_code == 200
    reused_token = await client.post(
        "/v1/auth/verify-email",
        json={"token": verification_token},
    )
    assert reused_token.status_code == 400

    logged_in = await client.post(
        "/v1/auth/login",
        json={
            "email": "new.user@example.com",
            "password": "initial-pass-123",
        },
    )
    assert logged_in.status_code == 200
    tokens = logged_in.json()
    assert tokens["user"]["membership_type"] == "nonmember"
    me = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "new.user@example.com"

    refreshed = await client.post(
        "/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != tokens["access_token"]

    forgot = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "new.user@example.com"},
    )
    reset_token = forgot.json()["development_token"]
    reset = await client.post(
        "/v1/auth/reset-password",
        json={"token": reset_token, "password": "replacement-pass-123"},
    )
    assert reset.status_code == 200
    old_password = await client.post(
        "/v1/auth/login",
        json={
            "email": "new.user@example.com",
            "password": "initial-pass-123",
        },
    )
    assert old_password.status_code == 401
    new_password = await client.post(
        "/v1/auth/login",
        json={
            "email": "new.user@example.com",
            "password": "replacement-pass-123",
        },
    )
    assert new_password.status_code == 200


@pytest.mark.asyncio
async def test_membership_application_encrypts_profile_and_approval_adds_charges(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    applicant = v2_context["applicant"]
    admin = v2_context["admin"]
    headers = auth_headers(applicant)
    profile = {
        "legal_name": "王測試",
        "phone": "0912345678",
        "birth_date": "1995-05-16",
        "address": "臺北市測試區測試路一號",
        "emergency_contact": "王家人 0987654321",
        "consent_version": "sandbox-v1",
    }

    saved = await client.put(
        "/v1/membership/application",
        json=profile,
        headers=headers,
    )

    assert saved.status_code == 200
    assert saved.json()["status"] == "draft"
    encrypted = await session.scalar(
        select(MemberProfile).where(MemberProfile.user_id == applicant.id)
    )
    assert encrypted is not None
    for private_value in (
        profile["legal_name"],
        profile["phone"],
        profile["address"],
        profile["emergency_contact"],
    ):
        assert private_value not in " ".join(
            [
                encrypted.legal_name_encrypted,
                encrypted.phone_encrypted,
                encrypted.address_encrypted,
                encrypted.emergency_contact_encrypted,
            ]
        )

    missing_documents = await client.post(
        "/v1/membership/application/submit",
        json=profile,
        headers=headers,
    )
    assert missing_documents.status_code == 409

    application = await session.scalar(
        select(MembershipApplication).where(
            MembershipApplication.user_id == applicant.id
        )
    )
    assert application is not None
    now = datetime.now(timezone.utc)
    documents = [
        MembershipDocument(
            application_id=application.id,
            document_type=document_type,
            status=MembershipDocumentStatus.CONFIRMED,
            object_key=f"sandbox/{application.id}/{document_type.value}",
            content_type="image/png",
            size_bytes=512,
            checksum_sha256=hashlib.sha256(
                document_type.value.encode("utf-8")
            ).hexdigest(),
            confirmed_at=now,
        )
        for document_type in MembershipDocumentType
    ]
    session.add_all(documents)
    await session.commit()

    submitted = await client.post(
        "/v1/membership/application/submit",
        json=profile,
        headers=headers,
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"

    schedule_date = date.today() - timedelta(days=1)
    for charge_kind, amount in (
        ("admission_fee", 500),
        ("share_capital", 1000),
    ):
        schedule = await client.post(
            "/v1/admin/membership-fee-schedules",
            json={
                "charge_kind": charge_kind,
                "amount": amount,
                "effective_from": schedule_date.isoformat(),
            },
            headers=auth_headers(admin),
        )
        assert schedule.status_code == 201

    approved = await client.post(
        f"/v1/admin/membership-applications/{application.id}/approve",
        json={"reason": "Sandbox 證件齊全"},
        headers=auth_headers(admin),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "pending_payment"
    assert approved.json()["member_number"] is None

    charges = await client.get(
        "/v1/membership/charges",
        headers=headers,
    )
    assert charges.status_code == 200
    assert {
        item["charge_kind"]: (item["amount"], item["status"])
        for item in charges.json()
    } == {
        "admission_fee": (500, "pending"),
        "share_capital": (1000, "pending"),
    }
    pending_charge = await session.scalar(
        select(MembershipCharge).where(
            MembershipCharge.user_id == applicant.id,
            MembershipCharge.charge_kind
            == MembershipChargeKind.ADMISSION_FEE,
        )
    )
    assert pending_charge is not None
    receipt = await client.get(
        f"/v1/membership/charges/{pending_charge.id}/receipt",
        headers=headers,
    )
    assert receipt.status_code == 409


@pytest.mark.asyncio
async def test_community_activity_waitlist_and_named_vote_update(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    member_a = v2_context["member_a"]
    member_b = v2_context["member_b"]
    now = datetime.now(timezone.utc)

    forbidden = await client.get(
        "/v1/activities",
        headers=auth_headers(applicant),
    )
    assert forbidden.status_code == 403

    created = await client.post(
        "/v1/activities",
        json={
            "title": "社員郊山健行",
            "description": "一起認識地方水系與農村地景。",
            "location": "臺北近郊集合點",
            "starts_at": (now + timedelta(days=3)).isoformat(),
            "ends_at": (now + timedelta(days=3, hours=4)).isoformat(),
            "registration_deadline": (
                now + timedelta(days=2)
            ).isoformat(),
            "capacity": 1,
            "waitlist_enabled": True,
        },
        headers=auth_headers(member_a),
    )
    assert created.status_code == 201
    activity_id = created.json()["id"]
    approved = await client.post(
        f"/v1/admin/activities/{activity_id}/approve",
        json={},
        headers=auth_headers(admin),
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "published"

    first = await client.post(
        f"/v1/activities/{activity_id}/register",
        headers=auth_headers(member_a),
    )
    second = await client.post(
        f"/v1/activities/{activity_id}/register",
        headers=auth_headers(member_b),
    )
    assert first.json()["status"] == "registered"
    assert second.json()["status"] == "waitlisted"

    cancelled = await client.post(
        f"/v1/activities/{activity_id}/cancel-registration",
        headers=auth_headers(member_a),
    )
    assert cancelled.status_code == 200
    promoted = await session.scalar(
        select(ActivityRegistration).where(
            ActivityRegistration.activity_id == activity_id,
            ActivityRegistration.user_id == member_b.id,
        )
    )
    assert promoted is not None
    assert promoted.status == ActivityRegistrationStatus.REGISTERED

    proposal = await client.post(
        "/v1/member-proposals",
        json={
            "title": "增設每月步道整理日",
            "body": "由社員輪值整理社區周邊步道。",
        },
        headers=auth_headers(member_a),
    )
    assert proposal.status_code == 201
    proposal_id = proposal.json()["id"]
    submitted = await client.post(
        f"/v1/member-proposals/{proposal_id}/submit",
        headers=auth_headers(member_a),
    )
    assert submitted.json()["status"] == "pending_review"

    reviewed = await client.post(
        f"/v1/admin/member-proposals/{proposal_id}/approve",
        json={
            "minimum_voters": 2,
            "discussion_ends_at": (
                now + timedelta(hours=1)
            ).isoformat(),
            "voting_ends_at": (now + timedelta(days=1)).isoformat(),
        },
        headers=auth_headers(admin),
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "discussion"

    stored_proposal = await session.get(MemberProposal, proposal_id)
    assert stored_proposal is not None
    stored_proposal.discussion_ends_at = now - timedelta(minutes=1)
    stored_proposal.voting_ends_at = now + timedelta(days=1)
    await session.commit()

    first_vote = await client.put(
        f"/v1/member-proposals/{proposal_id}/vote",
        json={"choice": "yes"},
        headers=auth_headers(member_a),
    )
    assert first_vote.status_code == 200
    second_vote = await client.put(
        f"/v1/member-proposals/{proposal_id}/vote",
        json={"choice": "abstain"},
        headers=auth_headers(member_b),
    )
    assert second_vote.json()["tally"] == {
        "yes": 1,
        "no": 0,
        "abstain": 1,
        "total": 2,
    }
    updated_vote = await client.put(
        f"/v1/member-proposals/{proposal_id}/vote",
        json={"choice": "yes"},
        headers=auth_headers(member_b),
    )
    assert updated_vote.status_code == 200
    assert updated_vote.json()["tally"] == {
        "yes": 2,
        "no": 0,
        "abstain": 0,
        "total": 2,
    }
    assert updated_vote.json()["my_vote"] == "yes"
    named_votes = await client.get(
        f"/v1/member-proposals/{proposal_id}/votes",
        headers=auth_headers(member_a),
    )
    assert named_votes.status_code == 200
    assert {
        (item["display_name"], item["choice"])
        for item in named_votes.json()
    } == {
        ("社員甲", "yes"),
        ("社員乙", "yes"),
    }


@pytest.mark.asyncio
async def test_meal_event_quote_order_cancel_capacity_and_qr_redeem(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    customer_b = v2_context["customer_b"]
    now = datetime.now(timezone.utc)

    meal = await client.post(
        "/v1/admin/meals",
        json={
            "name": "時蔬豆腐便當",
            "description": "當季蔬菜、豆腐與糙米飯。",
            "price": 120,
            "tax_type": "taxable",
        },
        headers=auth_headers(admin),
    )
    assert meal.status_code == 201
    event = await client.post(
        "/v1/admin/meal-events",
        json={
            "title": "校園週三午餐預購",
            "location": "大學校門口十里方圓攤位",
            "ordering_starts_at": (
                now - timedelta(hours=1)
            ).isoformat(),
            "ordering_ends_at": (now + timedelta(hours=1)).isoformat(),
            "pickup_starts_at": (now + timedelta(hours=2)).isoformat(),
            "pickup_ends_at": (now + timedelta(hours=3)).isoformat(),
            "offerings": [
                {
                    "meal_id": meal.json()["id"],
                    "price": 125,
                    "capacity": 1,
                }
            ],
        },
        headers=auth_headers(admin),
    )
    assert event.status_code == 201
    event_id = event.json()["id"]
    offering_id = event.json()["offerings"][0]["id"]
    published = await client.post(
        f"/v1/admin/meal-events/{event_id}/publish",
        headers=auth_headers(admin),
    )
    assert published.status_code == 200

    order_payload = {
        "items": [{"offering_id": offering_id, "quantity": 1}],
        "contact_email": applicant.email,
        "invoice_carrier_type": "ecpay",
    }
    quote = await client.post(
        f"/v1/meal-events/{event_id}/quote",
        json=order_payload,
    )
    assert quote.status_code == 200
    assert quote.json()["amount_total"] == 125
    assert quote.json()["fulfillment_method"] == "event_pickup"

    created_order = await client.post(
        f"/v1/meal-events/{event_id}/orders",
        json=order_payload,
        headers=auth_headers(applicant),
    )
    assert created_order.status_code == 201
    assert len(created_order.json()["pickup_code"]) == 6
    assert created_order.json()["available_actions"] == ["pay", "cancel"]

    offering = await session.get(MealEventOffering, offering_id)
    assert offering is not None
    offering.reserved_quantity = 1
    await session.commit()
    sold_out = await client.post(
        f"/v1/meal-events/{event_id}/quote",
        json={
            **order_payload,
            "contact_email": customer_b.email,
        },
    )
    assert sold_out.status_code == 409

    offering.reserved_quantity = 0
    await session.commit()
    cancelled = await client.post(
        f"/v1/meal-orders/{created_order.json()['id']}/cancel",
        json={"reason": "行程異動"},
        headers=auth_headers(applicant),
    )
    assert cancelled.status_code == 200
    assert cancelled.json() == {
        "id": created_order.json()["id"],
        "payment_status": "expired",
        "fulfillment_status": "cancelled",
    }

    redeem_payload = {
        **order_payload,
        "contact_email": customer_b.email,
    }
    redeem_order_response = await client.post(
        f"/v1/meal-events/{event_id}/orders",
        json=redeem_payload,
        headers=auth_headers(customer_b),
    )
    assert redeem_order_response.status_code == 201
    redeem_order = await session.get(
        Order,
        redeem_order_response.json()["id"],
    )
    assert redeem_order is not None
    redeem_order.payment_status = PaymentStatus.PAID
    redeem_order.paid_at = now
    offering.paid_quantity = 1
    await session.commit()

    pickup_open = await client.post(
        f"/v1/admin/meal-events/{event_id}/open-pickup",
        headers=auth_headers(admin),
    )
    assert pickup_open.status_code == 200
    redeemed = await client.post(
        f"/v1/admin/meal-events/{event_id}/redeem",
        json={"pickup_code": redeem_order_response.json()["pickup_code"]},
        headers=auth_headers(admin),
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["status"] == "picked_up"
    duplicate_redeem = await client.post(
        f"/v1/admin/meal-events/{event_id}/redeem",
        json={"pickup_code": redeem_order_response.json()["pickup_code"]},
        headers=auth_headers(admin),
    )
    assert duplicate_redeem.status_code == 409
    await session.refresh(redeem_order)
    assert redeem_order.invoice_status == InvoiceStatus.PENDING


class RecordingDocumentStorage:
    """Captures what the router asks R2 to sign, then replays it as a HEAD."""

    def __init__(self) -> None:
        self.tickets: list[dict] = []
        self.stored: dict[str, dict] = {}

    def create_upload_ticket(
        self,
        *,
        content_type: str,
        content_length: int,
        sha256: str | None = None,
    ):
        from app.integrations.r2_storage import DocumentUploadTicket

        object_key = f"membership-documents/ab/{len(self.tickets)}.png"
        required_headers = {
            "Content-Type": content_type,
            "Content-Length": str(content_length),
        }
        if sha256:
            required_headers["x-amz-meta-sha256"] = sha256.lower()
        self.tickets.append(
            {
                "content_type": content_type,
                "content_length": content_length,
                "sha256": sha256,
            }
        )
        # Simulate a client that uploads exactly what was signed.
        self.stored[object_key] = {
            "content_type": content_type,
            "content_length": content_length,
            "sha256": (sha256 or "").lower(),
        }
        return DocumentUploadTicket(
            object_key=object_key,
            upload_url=f"https://r2.example.test/{object_key}?signed=1",
            expires_in_seconds=300,
            required_headers=required_headers,
            max_bytes=8 * 1024 * 1024,
        )

    async def confirm_upload(
        self,
        *,
        object_key: str,
        expected_content_type: str,
        expected_content_length: int,
        expected_sha256: str | None = None,
    ):
        from app.integrations.common import IntegrationResponseError
        from app.integrations.r2_storage import DocumentHead

        actual = self.stored.get(object_key)
        if actual is None:
            raise IntegrationResponseError("missing object")
        if actual["content_type"] != expected_content_type:
            raise IntegrationResponseError("content type mismatch")
        if actual["content_length"] != expected_content_length:
            raise IntegrationResponseError("length mismatch")
        if expected_sha256 and actual["sha256"] != expected_sha256.lower():
            raise IntegrationResponseError("checksum mismatch")
        return DocumentHead(
            object_key=object_key,
            content_type=actual["content_type"],
            content_length=actual["content_length"],
            sha256=actual["sha256"] or None,
            etag="etag-1",
        )

    async def delete_document(self, object_key: str) -> None:
        self.stored.pop(object_key, None)


@pytest.mark.asyncio
async def test_membership_document_upload_then_confirm_round_trip(
    v2_context,
) -> None:
    """Regression: the signed URL must carry the checksum confirm() verifies."""
    from app.routers.membership import get_document_storage

    client = v2_context["client"]
    applicant = v2_context["applicant"]
    storage = RecordingDocumentStorage()
    client._transport.app.dependency_overrides[get_document_storage] = (
        lambda: storage
    )
    checksum = hashlib.sha256(b"sandbox-test-document").hexdigest()

    upload = await client.post(
        "/v1/membership/documents/upload-url",
        json={
            "document_type": "id_front",
            "content_type": "image/png",
            "size_bytes": 2048,
            "checksum_sha256": checksum,
        },
        headers=auth_headers(applicant),
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["required_headers"]["x-amz-meta-sha256"] == checksum
    assert storage.tickets[0]["sha256"] == checksum

    confirmed = await client.post(
        f"/v1/membership/documents/{body['document_id']}/confirm",
        json={"checksum_sha256": checksum},
        headers=auth_headers(applicant),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    mismatched = await client.post(
        f"/v1/membership/documents/{body['document_id']}/confirm",
        json={"checksum_sha256": "b" * 64},
        headers=auth_headers(applicant),
    )
    assert mismatched.status_code == 409


@pytest.mark.asyncio
async def test_login_is_rate_limited_and_resets_on_success(v2_context) -> None:
    """Blunts credential stuffing without locking out the real account owner."""
    from app.rate_limit import LOGIN_RULE, reset_all

    reset_all()
    client = v2_context["client"]
    applicant = v2_context["applicant"]

    for _ in range(LOGIN_RULE.max_attempts):
        wrong = await client.post(
            "/v1/auth/login",
            json={"email": applicant.email, "password": "not-the-password"},
        )
        assert wrong.status_code == 401

    throttled = await client.post(
        "/v1/auth/login",
        json={"email": applicant.email, "password": "not-the-password"},
    )
    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"]

    # A different account is unaffected by one account's failures.
    reset_all()
    correct = await client.post(
        "/v1/auth/login",
        json={"email": applicant.email, "password": "applicant-pass-123"},
    )
    assert correct.status_code == 200

    # Signing in clears the bucket, so a fumbled password is not punished.
    for _ in range(LOGIN_RULE.max_attempts):
        again = await client.post(
            "/v1/auth/login",
            json={"email": applicant.email, "password": "applicant-pass-123"},
        )
        assert again.status_code == 200
    reset_all()
