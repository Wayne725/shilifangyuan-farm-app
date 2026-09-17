from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models import (
    ActivityRegistration,
    ActivityRegistrationStatus,
    AdminAudit,
    EmailVerificationToken,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    InventoryReservation,
    Invoice,
    InvoiceCarrierType,
    InvoiceStatus,
    MealEvent,
    MealEventOffering,
    MemberProfile,
    MemberRosterEntry,
    MemberProposal,
    Membership,
    MembershipApplication,
    MembershipApplicationStatus,
    MembershipCharge,
    MembershipChargeKind,
    MembershipChargeStatus,
    MembershipDocument,
    MembershipDocumentStatus,
    MembershipDocumentType,
    MembershipFeeSchedule,
    MembershipStatus,
    MembershipType,
    Notification,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderItemOption,
    OrderKind,
    OutboxEvent,
    PaymentStatus,
    Product,
    ProductCategory,
    Refund,
    RefreshSession,
    ReservationStatus,
    SalesChannel,
    Shipment,
    ShipmentStatus,
    ShippingChannel,
    ShippingTemperature,
    TaxType,
    User,
    UserRole,
)
from app.routers.auth import auth_router
from app.routers.community import community_router
from app.routers.invoices import invoices_router
from app.routers.meals import meals_router
from app.routers.membership import membership_router
from app.routers.orders import orders_router
from app.schemas import InvoicePreferenceInput
from tests.support import (
    fanyu_test_context,
    api_test_context,
    auth_headers,
    fast_password_hash,
    make_test_settings,
    prepare_test_invoice,
)


def settings():
    return make_test_settings(
        environment="development",
        pii_encryption_keys_json=json.dumps(
            {
                "v1": base64.b64encode(b"p" * 32).decode("ascii"),
            }
        ),
    )


def test_company_invoice_accepts_mobile_barcode_carrier() -> None:
    preference = InvoicePreferenceInput(
        invoice_buyer_type="company",
        invoice_buyer_tax_id="12345675",
        invoice_buyer_name="測試股份有限公司",
        invoice_buyer_email="accounting@example.com",
        invoice_carrier_type="mobile_barcode",
        invoice_carrier_value="/AB12+-.",
    )

    assert preference.invoice_carrier_type == InvoiceCarrierType.MOBILE_BARCODE
    assert preference.invoice_carrier_value == "/AB12+-."


def test_personal_invoice_accepts_member_carrier_email() -> None:
    preference = InvoicePreferenceInput(
        invoice_buyer_type="personal",
        invoice_buyer_email="buyer@example.com",
        invoice_carrier_type="cloud",
    )

    assert str(preference.invoice_buyer_email) == "buyer@example.com"


def test_personal_member_carrier_rejects_email_over_64_characters() -> None:
    with pytest.raises(ValueError, match="64"):
        InvoicePreferenceInput(
            invoice_buyer_type="personal",
            invoice_buyer_email=f"{'a' * 53}@example.com",
            invoice_carrier_type="cloud",
        )


def test_company_member_carrier_rejects_email_over_64_characters() -> None:
    with pytest.raises(ValueError, match="64"):
        InvoicePreferenceInput(
            invoice_buyer_type="company",
            invoice_buyer_tax_id="12345675",
            invoice_buyer_name="測試股份有限公司",
            invoice_buyer_email=f"{'a' * 53}@example.com",
            invoice_carrier_type="cloud",
        )


@pytest.fixture
async def v2_context(database_session):
    async with api_test_context(
        database_session,
        [
            auth_router,
            membership_router,
            community_router,
            meals_router,
            orders_router,
            invoices_router,
        ],
        settings=settings(),
    ) as client:
        session = database_session
        now = datetime.now(timezone.utc)
        admin = User(
            email="admin@example.com",
            display_name="管理員",
            password_hash=fast_password_hash("admin-pass-123"),
            user_role=UserRole.ADMIN,
            email_verified_at=now,
        )
        applicant = User(
            email="applicant@example.com",
            display_name="申請人",
            password_hash=fast_password_hash("applicant-pass-123"),
            email_verified_at=now,
        )
        member_a = User(
            email="member-a@example.com",
            display_name="社員甲",
            password_hash=fast_password_hash("member-a-pass-123"),
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
            password_hash=fast_password_hash("member-b-pass-123"),
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
            password_hash=fast_password_hash("customer-b-pass-123"),
            email_verified_at=now,
        )
        session.add_all(
            [admin, applicant, member_a, member_b, customer_b]
        )
        await session.commit()

        yield {
            "client": client,
            "session": session,
            "admin": admin,
            "applicant": applicant,
            "member_a": member_a,
            "member_b": member_b,
            "customer_b": customer_b,
        }


@pytest.mark.asyncio
async def test_admin_invoice_query_requires_reason_and_records_audit(
    v2_context,
    monkeypatch,
) -> None:
    import app.routers.invoices as invoice_router_module

    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    customer = v2_context["customer_b"]
    order = Order(
        order_number="SLFINVOICEQUERY01",
        invoice_provider_context=fanyu_test_context(),
        order_kind=OrderKind.REGULAR,
        user_id=customer.id,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=120,
        contact_email=customer.email,
        invoice_carrier_type=InvoiceCarrierType.CLOUD,
        payment_status=PaymentStatus.PAID,
        invoice_status=InvoiceStatus.FAILED,
        items=[
            OrderItem(
                product_name="測試蔬菜箱",
                unit_label="箱",
                quantity=1,
                unit_price=120,
                subtotal=120,
                tax_type=TaxType.TAXABLE,
            )
        ],
    )
    session.add(order)
    await session.commit()

    class InvoiceAdapter:
        provider_name = "fanyu"
        binding_context = fanyu_test_context()

        def prepare_invoice(self, request):
            return prepare_test_invoice(request, provider="fanyu")

        async def query_invoice(self, relate_number, *, buyer_type):
            assert buyer_type == "personal"
            return {
                "RtnCode": 1,
                "InvoiceNo": "AB12345672",
                "InvoiceDate": "2026-08-26 214500",
                "RandomNumber": "7788",
                "ProviderStatus": "0",
            }

    monkeypatch.setattr(
        invoice_router_module,
        "invoice_adapter_from_settings",
        lambda _settings: InvoiceAdapter(),
    )

    missing_reason = await client.post(
        f"/v1/admin/orders/{order.id}/invoice/query",
        json={"reason": "   "},
        headers=auth_headers(admin),
    )
    queried = await client.post(
        f"/v1/admin/orders/{order.id}/invoice/query",
        json={"reason": "客服確認逾時訂單"},
        headers=auth_headers(admin),
    )

    assert missing_reason.status_code == 422
    assert queried.status_code == 200, queried.text
    assert queried.json()["found"] is True
    assert queried.json()["invoice_number"] == "AB12345672"
    invoice = await session.scalar(
        select(Invoice).where(Invoice.order_id == order.id)
    )
    audit = await session.scalar(
        select(AdminAudit).where(
            AdminAudit.action == "invoice.provider_queried",
            AdminAudit.aggregate_id == order.id,
        )
    )
    assert invoice is not None
    assert invoice.status == InvoiceStatus.ISSUED
    assert audit is not None
    assert audit.actor_id == admin.id
    assert audit.reason == "客服確認逾時訂單"
    assert audit.data == {
        "found": True,
        "provider": "fanyu",
        "invoice_status": "issued",
    }


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
    assert registered.json()["delivery_status"] == "queued"
    assert "寄送佇列" in registered.json()["message"]
    registered_user = await v2_context["session"].scalar(
        select(User).where(User.email == "new.user@example.com")
    )
    assert registered_user is not None
    assert registered_user.customer_number.startswith(
        f"SLF-C-{datetime.now(timezone.utc).year}-"
    )
    assert len(registered_user.customer_number.rsplit("-", 1)[1]) == 4
    verification_token = registered.json()["development_token"]
    assert verification_token.isdigit()
    assert len(verification_token) == 6
    stored_verification = await v2_context["session"].scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == registered_user.id
        )
    )
    assert stored_verification is not None
    assert stored_verification.token_hash != hashlib.sha256(
        verification_token.encode("utf-8")
    ).hexdigest()
    unverified_login = await client.post(
        "/v1/auth/login",
        json={
            "email": "new.user@example.com",
            "password": "initial-pass-123",
        },
    )
    assert unverified_login.status_code == 403

    resent = await client.post(
        "/v1/auth/resend-verification",
        json={"email": "new.user@example.com"},
    )
    assert resent.status_code == 202
    assert resent.json()["delivery_status"] == "queued"
    assert "已排入寄送佇列" in resent.json()["message"]
    assert "10 分鐘效期內" in resent.json()["message"]
    assert "任一驗證成功後全部失效" in resent.json()["message"]
    resent_token = resent.json()["development_token"]
    assert resent_token != verification_token
    previous_code_before_delivery = await client.post(
        "/v1/auth/verify-email",
        json={
            "email": "new.user@example.com",
            "token": verification_token,
        },
    )
    assert previous_code_before_delivery.status_code == 200

    replacement_after_verification = await client.post(
        "/v1/auth/verify-email",
        json={"email": "new.user@example.com", "token": resent_token},
    )
    assert replacement_after_verification.status_code == 400
    reused_token = await client.post(
        "/v1/auth/verify-email",
        json={
            "email": "new.user@example.com",
            "token": verification_token,
        },
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
    assert "refresh_token" not in tokens
    assert "slf_refresh=" in logged_in.headers["set-cookie"]
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    assert tokens["user"]["membership_type"] == "nonmember"
    assert tokens["user"]["customer_number"] == registered_user.customer_number
    original_refresh_token = client.cookies.get("slf_refresh")
    assert original_refresh_token
    me = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "new.user@example.com"

    refreshed = await client.post(
        "/v1/auth/refresh",
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != tokens["access_token"]
    replayed_refresh = await client.post(
        "/v1/auth/refresh",
        headers={"Cookie": f"slf_refresh={original_refresh_token}"},
    )
    assert replayed_refresh.status_code == 401
    refresh_sessions = list(
        await v2_context["session"].scalars(
            select(RefreshSession).where(
                RefreshSession.user_id == registered_user.id
            )
        )
    )
    assert len(refresh_sessions) == 2
    assert sum(item.revoked_at is None for item in refresh_sessions) == 1

    forgot = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "new.user@example.com"},
    )
    assert forgot.status_code == 202
    assert forgot.json()["delivery_status"] == "queued"
    assert "已受理" in forgot.json()["message"]
    reset_token = forgot.json()["development_token"]
    second_forgot = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "new.user@example.com"},
    )
    second_reset_token = second_forgot.json()["development_token"]
    reset = await client.post(
        "/v1/auth/reset-password",
        json={"token": reset_token, "password": "replacement-pass-123"},
    )
    assert reset.status_code == 200
    stale_reset = await client.post(
        "/v1/auth/reset-password",
        json={
            "token": second_reset_token,
            "password": "attacker-pass-123",
        },
    )
    assert stale_reset.status_code == 400
    revoked_access = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    revoked_refresh = await client.post("/v1/auth/refresh")
    assert revoked_access.status_code == 401
    assert revoked_refresh.status_code == 401
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
    logged_out = await client.post(
        "/v1/auth/logout",
        headers={
            "Authorization": (
                f"Bearer {new_password.json()['access_token']}"
            )
        },
    )
    assert logged_out.status_code == 204
    assert "slf_refresh=" in logged_out.headers["set-cookie"]
    assert "Max-Age=0" in logged_out.headers["set-cookie"]
    after_logout = await client.get(
        "/v1/auth/me",
        headers={
            "Authorization": (
                f"Bearer {new_password.json()['access_token']}"
            )
        },
    )
    assert after_logout.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation_respects_replacement_foreign_key(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    await session.execute(text("PRAGMA foreign_keys=ON"))

    logged_in = await client.post(
        "/v1/auth/login",
        json={
            "email": "customer-b@example.com",
            "password": "customer-b-pass-123",
        },
    )
    assert logged_in.status_code == 200

    refreshed = await client.post("/v1/auth/refresh")

    assert refreshed.status_code == 200, refreshed.text


@pytest.mark.asyncio
async def test_login_upgrades_legacy_password_hash(v2_context) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    customer = v2_context["customer_b"]
    assert customer.password_hash.startswith("pbkdf2_sha256$")

    logged_in = await client.post(
        "/v1/auth/login",
        json={
            "email": customer.email,
            "password": "customer-b-pass-123",
        },
    )

    assert logged_in.status_code == 200
    await session.refresh(customer)
    assert customer.password_hash.startswith("$argon2id$")


@pytest.mark.asyncio
async def test_login_cookie_rejects_untrusted_browser_origin(v2_context) -> None:
    response = await v2_context["client"].post(
        "/v1/auth/login",
        json={
            "email": "customer-b@example.com",
            "password": "customer-b-pass-123",
        },
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_existing_member_registers_from_roster_and_gets_active_membership(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    roster_payload = {
        "member_number": "SLF-2019-0042",
        "legal_name": "王社員",
        "email": "existing.member@example.com",
        "phone": "0912-345-678",
        "share_certificate_number": "SHARE-0042",
        "share_capital_amount": 3000,
        "share_count": 3,
        "share_subscribed_on": "2019-04-10",
        "share_paid_on": "2019-04-12",
    }
    created = await client.post(
        "/v1/admin/member-roster",
        json=roster_payload,
        headers=auth_headers(admin),
    )

    assert created.status_code == 201
    assert created.json()["member_number"] == "SLF-2019-0042"
    assert created.json()["email_masked"].endswith("@example.com")
    assert created.json()["phone_masked"].endswith("5678")
    assert created.json()["claimed"] is False
    stored_roster = await session.scalar(
        select(MemberRosterEntry).where(
            MemberRosterEntry.member_number == "SLF-2019-0042"
        )
    )
    assert stored_roster is not None
    assert "王社員" not in stored_roster.legal_name_encrypted
    assert "existing.member" not in stored_roster.email_encrypted

    mismatch = await client.post(
        "/v1/auth/register-existing-member",
        json={
            "member_number": "SLF-2019-0042",
            "legal_name": "王社員",
            "display_name": "社員小王",
            "email": "existing.member@example.com",
            "phone": "0900000000",
            "password": "existing-member-pass-123",
        },
    )
    assert mismatch.status_code == 400

    registered = await client.post(
        "/v1/auth/register-existing-member",
        json={
            "member_number": "slf-2019-0042",
            "legal_name": "王 社員",
            "display_name": "社員小王",
            "email": "Existing.Member@example.com",
            "phone": "0912345678",
            "password": "existing-member-pass-123",
        },
    )
    assert registered.status_code == 201
    verification_token = registered.json()["development_token"]
    claimed_user = await session.scalar(
        select(User).where(User.email == "existing.member@example.com")
    )
    assert claimed_user is not None
    claimed_membership_before_verification = await session.scalar(
        select(Membership).where(Membership.user_id == claimed_user.id)
    )
    assert claimed_user.display_name == "社員小王"
    assert claimed_user.pending_member_claim is True
    assert claimed_membership_before_verification is None
    await session.refresh(stored_roster)
    assert stored_roster.claimed_user_id is None
    assert stored_roster.pending_claim_user_id == claimed_user.id

    duplicate_pending_claim = await client.post(
        "/v1/auth/register-existing-member",
        json={
            "member_number": "SLF-2019-0042",
            "legal_name": "王社員",
            "display_name": "另一個顯示名稱",
            "email": "existing.member@example.com",
            "phone": "0912345678",
            "password": "existing-member-pass-123",
        },
    )
    assert duplicate_pending_claim.status_code == 409
    assert "等待 Email 驗證" in duplicate_pending_claim.json()["detail"]

    hidden_claim_status = await client.post(
        "/v1/auth/register-existing-member",
        json={
            "member_number": "SLF-2019-0042",
            "legal_name": "錯誤姓名",
            "display_name": "錯誤帳號",
            "email": "wrong@example.com",
            "phone": "0900000000",
            "password": "existing-member-pass-123",
        },
    )
    assert hidden_claim_status.status_code == 400

    before_verification = await client.post(
        "/v1/auth/login",
        json={
            "email": "existing.member@example.com",
            "password": "existing-member-pass-123",
        },
    )
    assert before_verification.status_code == 403
    verified = await client.post(
        "/v1/auth/verify-email",
        json={
            "email": "existing.member@example.com",
            "token": verification_token,
        },
    )
    assert verified.status_code == 200
    claimed_membership = await session.scalar(
        select(Membership).where(Membership.user_id == claimed_user.id)
    )
    assert claimed_membership is not None
    assert claimed_membership.status == MembershipStatus.ACTIVE
    assert claimed_membership.member_number == "SLF-2019-0042"
    assert claimed_membership.share_capital_amount == 3000
    await session.refresh(stored_roster)
    assert stored_roster.claimed_user_id == claimed_user.id
    assert stored_roster.pending_claim_user_id is None
    logged_in = await client.post(
        "/v1/auth/login",
        json={
            "email": "existing.member@example.com",
            "password": "existing-member-pass-123",
        },
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["membership_type"] == "member"


@pytest.mark.asyncio
async def test_existing_customer_account_can_claim_matching_member_roster(
    v2_context,
) -> None:
    client = v2_context["client"]
    admin = v2_context["admin"]
    customer = v2_context["customer_b"]
    created = await client.post(
        "/v1/admin/member-roster",
        json={
            "member_number": "SLF-2020-0077",
            "legal_name": "一般顧客乙",
            "email": customer.email,
            "phone": "0987654321",
        },
        headers=auth_headers(admin),
    )
    assert created.status_code == 201

    claimed = await client.post(
        "/v1/membership/claim-existing",
        json={
            "member_number": "SLF-2020-0077",
            "legal_name": "一般顧客乙",
            "phone": "0987-654-321",
        },
        headers=auth_headers(customer),
    )

    assert claimed.status_code == 200
    assert claimed.json()["status"] == "active"
    assert claimed.json()["member_number"] == "SLF-2020-0077"
    assert not any(key.startswith("share_") for key in claimed.json())
    mine = await client.get(
        "/v1/members/me",
        headers=auth_headers(customer),
    )
    assert mine.status_code == 200
    assert mine.json()["membership_type"] == "member"


@pytest.mark.asyncio
async def test_membership_me_wrapper_includes_separate_directory_entry(
    v2_context,
) -> None:
    client = v2_context["client"]
    member = v2_context["member_a"]
    applicant = v2_context["applicant"]

    before_directory = await client.get(
        "/v1/members/me",
        headers=auth_headers(member),
    )

    assert before_directory.status_code == 200
    assert before_directory.json()["membership_type"] == "member"
    assert before_directory.json()["membership"]["status"] == "active"
    assert before_directory.json()["directory"] is None

    updated = await client.put(
        "/v1/members/me/directory",
        json={
            "is_public": True,
            "nickname": "山林社員",
            "avatar_url": "https://example.test/avatar.png",
            "expertise": "友善耕作",
            "bio": "願意分享田間觀察。",
        },
        headers=auth_headers(member),
    )
    assert updated.status_code == 200

    after_directory = await client.get(
        "/v1/members/me",
        headers=auth_headers(member),
    )
    assert after_directory.status_code == 200
    assert after_directory.json()["directory"] == updated.json()

    nonmember = await client.get(
        "/v1/members/me",
        headers=auth_headers(applicant),
    )
    assert nonmember.status_code == 200
    assert nonmember.json() == {
        "membership_type": "nonmember",
        "membership": None,
        "directory": None,
    }


@pytest.mark.asyncio
async def test_trainee_order_uses_member_price_and_trainee_snapshot(
    v2_context,
) -> None:
    session = v2_context["session"]
    now = datetime.now(timezone.utc)
    trainee = User(
        customer_number="SLF-C-2026-0088",
        email="trainee-buyer@example.com",
        display_name="實習社員買家",
        password_hash=fast_password_hash("trainee-buyer-pass-123"),
        email_verified_at=now,
        membership=Membership(
            trainee_number="SLF-T-2026-0088",
            status=MembershipStatus.TRAINEE,
        ),
    )
    product = Product(
        slug="trainee-priced-product",
        name="社員價測試品",
        description="",
        category=ProductCategory.PROCESSED,
        unit="份",
        member_price=80,
        nonmember_price=120,
        stock_quantity=10,
        tax_type=TaxType.TAXABLE,
    )
    session.add_all([trainee, product])
    await session.commit()
    payload = {
        "items": [{"product_id": product.id, "quantity": 2}],
    }

    quote = await v2_context["client"].post(
        "/v1/orders/quote",
        json=payload,
        headers=auth_headers(trainee),
    )
    created = await v2_context["client"].post(
        "/v1/orders",
        json={**payload, "contact_email": trainee.email},
        headers=auth_headers(trainee),
    )

    assert quote.status_code == 200
    assert quote.json()["membership_type"] == "trainee"
    assert quote.json()["amount_total"] == 160
    assert created.status_code == 201
    assert created.json()["membership_type_snapshot"] == "trainee"
    assert created.json()["items"][0]["product_id"] == product.id
    assert created.json()["items"][0]["unit_price"] == 80


@pytest.mark.asyncio
async def test_admin_cannot_refund_after_logistics_shipment_is_created(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    buyer = v2_context["applicant"]
    now = datetime.now(timezone.utc)
    product = Product(
        id="created-shipment-refund-guard-product",
        slug="created-shipment-refund-guard",
        name="已建立物流退款防護商品",
        description="",
        category=ProductCategory.PROCESSED,
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=4,
        tax_type=TaxType.TAXABLE,
    )
    order = Order(
        order_number="CREATED-SHIPMENT-REFUND-GUARD",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.ECPAY_LOGISTICS,
        user=buyer,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=450,
        contact_email=buyer.email,
        payment_status=PaymentStatus.PAID,
        fulfillment_status=FulfillmentStatus.PREPARING,
        paid_at=now,
        items=[
            OrderItem(
                product_name="已建立物流退款防護商品",
                unit_label="份",
                quantity=1,
                unit_price=450,
                subtotal=450,
                tax_type=TaxType.TAXABLE,
                source_product_id=product.id,
            )
        ],
    )
    fulfillment = OrderFulfillment(
        order=order,
        method=FulfillmentMethod.ECPAY_LOGISTICS,
        status=FulfillmentState.AWAITING_SHIPMENT,
        shipment=Shipment(
            channel=ShippingChannel.HOME_DELIVERY,
            temperature=ShippingTemperature.AMBIENT,
            status=ShipmentStatus.CREATED,
            shipping_fee=160,
        ),
    )
    reservation = InventoryReservation(
        order=order,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.CONSUMED,
        expires_at=now + timedelta(minutes=15),
    )
    session.add_all([product, order, fulfillment, reservation])
    await session.commit()

    response = await client.post(
        f"/v1/orders/{order.id}/admin/refund",
        json={"reason": "測試物流建立後退款"},
        headers=auth_headers(admin),
    )

    await session.refresh(order)
    await session.refresh(product)
    await session.refresh(reservation)
    refunds = list(
        await session.scalars(select(Refund).where(Refund.order_id == order.id))
    )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "物流已建立或商品已交付，請先完成人工攔截／退回再退款"
    )
    assert order.payment_status == PaymentStatus.PAID
    assert order.fulfillment_status == FulfillmentStatus.PREPARING
    assert reservation.status == ReservationStatus.CONSUMED
    assert product.stock_quantity == 4
    assert refunds == []


@pytest.mark.asyncio
async def test_membership_application_read_includes_private_profile_and_documents(
    v2_context,
) -> None:
    client = v2_context["client"]
    applicant = v2_context["applicant"]
    admin = v2_context["admin"]
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
        headers=auth_headers(applicant),
    )

    assert saved.status_code == 200
    saved_body = saved.json()
    assert {
        key: saved_body["profile"][key]
        for key in profile
    } == profile
    assert saved_body["profile"]["consented_at"] is not None
    assert saved_body["documents"] == []

    mine = await client.get(
        "/v1/membership/application",
        headers=auth_headers(applicant),
    )
    assert mine.status_code == 200
    assert mine.json()["id"] == saved_body["id"]
    assert mine.json()["profile"] == saved_body["profile"]
    assert mine.json()["documents"] == []

    admin_list = await client.get(
        "/v1/admin/membership-applications",
        headers=auth_headers(admin),
    )
    assert admin_list.status_code == 200
    assert len(admin_list.json()) == 1
    assert admin_list.json()[0]["id"] == saved_body["id"]
    assert admin_list.json()[0]["profile"] == saved_body["profile"]
    assert admin_list.json()[0]["documents"] == []

    admin_detail = await client.get(
        f"/v1/admin/membership-applications/{saved_body['id']}",
        headers=auth_headers(admin),
    )
    assert admin_detail.status_code == 200
    assert admin_detail.json()["id"] == saved_body["id"]
    assert admin_detail.json()["profile"] == saved_body["profile"]
    assert admin_detail.json()["documents"] == []


@pytest.mark.asyncio
async def test_active_member_cannot_open_duplicate_membership_application(
    v2_context,
) -> None:
    response = await v2_context["client"].put(
        "/v1/membership/application",
        json={
            "legal_name": "社員甲",
            "phone": "0912345678",
            "birth_date": "1995-05-16",
            "address": "臺北市測試路一號",
            "emergency_contact": "測試聯絡人 0987654321",
            "consent_version": "sandbox-v1",
        },
        headers=auth_headers(v2_context["member_a"]),
    )

    assert response.status_code == 409
    assert "無法重複" in response.json()["detail"]


@pytest.mark.asyncio
async def test_membership_submission_adds_charges_before_admin_approval(
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

    application = await session.scalar(
        select(MembershipApplication).where(
            MembershipApplication.user_id == applicant.id
        )
    )
    assert application is not None
    submitted = await client.post(
        "/v1/membership/application/submit",
        headers=headers,
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"
    assert submitted.json()["documents"] == []
    pending_membership = await session.scalar(
        select(Membership).where(Membership.user_id == applicant.id)
    )
    assert pending_membership is not None
    assert pending_membership.status == MembershipStatus.PENDING_PAYMENT
    charges_before_approval = (
        await session.scalars(
            select(MembershipCharge).where(
                MembershipCharge.user_id == applicant.id
            )
        )
    ).all()
    assert {
        charge.charge_kind: (charge.amount, charge.status)
        for charge in charges_before_approval
    } == {
        MembershipChargeKind.ADMISSION_FEE: (
            500,
            MembershipChargeStatus.PENDING,
        ),
        MembershipChargeKind.SHARE_CAPITAL: (
            1000,
            MembershipChargeStatus.PENDING,
        ),
    }

    supplement_requested = await client.post(
        f"/v1/admin/membership-applications/{application.id}/request-supplement",
        json={"reason": "請更新聯絡地址"},
        headers=auth_headers(admin),
    )
    assert supplement_requested.status_code == 200
    assert supplement_requested.json()["status"] == "needs_supplement"
    updated_profile = {
        **profile,
        "address": "臺北市測試區補件路二號",
    }
    updated = await client.put(
        "/v1/membership/application",
        json=updated_profile,
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["profile"]["address"] == updated_profile["address"]
    supplemented = await client.post(
        "/v1/membership/application/supplement",
        headers=headers,
    )
    assert supplemented.status_code == 200
    assert supplemented.json()["status"] == "submitted"
    assert supplemented.json()["profile"]["address"] == updated_profile["address"]

    approved = await client.post(
        f"/v1/admin/membership-applications/{application.id}/approve",
        json={"reason": "Sandbox 基本資料齊全"},
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

    pending_charge.status = MembershipChargeStatus.PAID
    pending_charge.paid_at = datetime.now(timezone.utc)
    pending_charge.receipt_number = "RCPT-WITHDRAW-001"
    await session.commit()
    withdrawn = await client.post(
        "/v1/membership/application/withdraw",
        json={"reason": "啟用前撤回"},
        headers=headers,
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "withdrawn"

    pending_membership = await session.scalar(
        select(Membership).where(Membership.user_id == applicant.id)
    )
    assert pending_membership is not None
    await session.refresh(pending_membership)
    assert pending_membership.status == MembershipStatus.TERMINATED
    saved_charges = (
        await session.scalars(
            select(MembershipCharge).where(
                MembershipCharge.user_id == applicant.id
            )
        )
    ).all()
    assert {
        charge.charge_kind: charge.status for charge in saved_charges
    } == {
        MembershipChargeKind.ADMISSION_FEE:
            MembershipChargeStatus.REFUNDED,
        MembershipChargeKind.SHARE_CAPITAL: MembershipChargeStatus.WAIVED,
    }
    refunds = (
        await session.scalars(
            select(Refund).where(
                Refund.membership_charge_id == pending_charge.id
            )
        )
    ).all()
    assert len(refunds) == 1


@pytest.mark.asyncio
async def test_activated_membership_cannot_withdraw_application(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    member = v2_context["member_a"]
    membership = member.membership
    application = MembershipApplication(
        user_id=member.id,
        status=MembershipApplicationStatus.APPROVED,
    )
    session.add(application)
    await session.flush()
    membership.application_id = application.id
    await session.commit()

    for membership_status in (
        MembershipStatus.ACTIVE,
        MembershipStatus.SUSPENDED,
        MembershipStatus.RESIGNED,
        MembershipStatus.TERMINATED,
    ):
        membership.status = membership_status
        await session.commit()
        response = await client.post(
            "/v1/membership/application/withdraw",
            json={"reason": "不應退回已啟用款項"},
            headers=auth_headers(member),
        )
        assert response.status_code == 409

    await session.refresh(application)
    assert application.status == MembershipApplicationStatus.APPROVED


@pytest.mark.asyncio
async def test_admin_cannot_reject_application_after_any_membership_charge_is_paid(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    application = MembershipApplication(
        user_id=applicant.id,
        status=MembershipApplicationStatus.SUBMITTED,
    )
    schedule = MembershipFeeSchedule(
        charge_kind=MembershipChargeKind.ADMISSION_FEE,
        amount=500,
        effective_from=date.today(),
    )
    session.add_all([application, schedule])
    await session.flush()
    membership = Membership(
        user_id=applicant.id,
        application_id=application.id,
        status=MembershipStatus.PENDING_PAYMENT,
    )
    session.add(membership)
    await session.flush()
    session.add(
        MembershipCharge(
            user_id=applicant.id,
            application_id=application.id,
            membership_id=membership.id,
            fee_schedule_id=schedule.id,
            charge_kind=MembershipChargeKind.ADMISSION_FEE,
            amount=500,
            status=MembershipChargeStatus.PAID,
            receipt_number="RCPT-REJECT-BLOCKED-001",
            paid_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    rejected = await client.post(
        f"/v1/admin/membership-applications/{application.id}/reject",
        json={"reason": "資料未通過"},
        headers=auth_headers(admin),
    )
    application_after = await client.get(
        f"/v1/admin/membership-applications/{application.id}",
        headers=auth_headers(admin),
    )
    memberships_after = await client.get(
        "/v1/admin/members",
        headers=auth_headers(admin),
    )

    assert rejected.status_code == 409
    assert application_after.json()["status"] == "submitted"
    saved_membership = next(
        item
        for item in memberships_after.json()
        if item["id"] == membership.id
    )
    assert saved_membership["status"] == "pending_payment"


@pytest.mark.asyncio
async def test_admin_rejects_unpaid_application_and_closes_membership_charges(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    application = MembershipApplication(
        user_id=applicant.id,
        status=MembershipApplicationStatus.SUBMITTED,
    )
    schedule = MembershipFeeSchedule(
        charge_kind=MembershipChargeKind.ADMISSION_FEE,
        amount=500,
        effective_from=date.today(),
    )
    session.add_all([application, schedule])
    await session.flush()
    membership = Membership(
        user_id=applicant.id,
        application_id=application.id,
        status=MembershipStatus.PENDING_PAYMENT,
    )
    session.add(membership)
    await session.flush()
    session.add(
        MembershipCharge(
            user_id=applicant.id,
            application_id=application.id,
            membership_id=membership.id,
            fee_schedule_id=schedule.id,
            charge_kind=MembershipChargeKind.ADMISSION_FEE,
            amount=500,
        )
    )
    await session.commit()

    rejected = await client.post(
        f"/v1/admin/membership-applications/{application.id}/reject",
        json={"reason": "資料未通過"},
        headers=auth_headers(admin),
    )
    memberships_after = await client.get(
        "/v1/admin/members",
        headers=auth_headers(admin),
    )
    charges_after = await client.get(
        "/v1/membership/charges",
        headers=auth_headers(applicant),
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    saved_membership = next(
        item
        for item in memberships_after.json()
        if item["id"] == membership.id
    )
    assert saved_membership["status"] == "terminated"
    assert charges_after.json()[0]["status"] == "waived"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("membership_status", "trainee_number", "member_number"),
    [
        (MembershipStatus.TRAINEE, "SLF-T-2026-0042", None),
        (MembershipStatus.ACTIVE, "SLF-T-2026-0042", "SLF-2026-0042"),
    ],
)
async def test_admin_cannot_reject_application_after_membership_rights_begin(
    v2_context,
    membership_status: MembershipStatus,
    trainee_number: str,
    member_number: str | None,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    application = MembershipApplication(
        user_id=applicant.id,
        status=MembershipApplicationStatus.SUBMITTED,
    )
    membership = Membership(
        user_id=applicant.id,
        application=application,
        status=membership_status,
        trainee_number=trainee_number,
        member_number=member_number,
    )
    session.add(membership)
    await session.commit()

    rejected = await client.post(
        f"/v1/admin/membership-applications/{application.id}/reject",
        json={"reason": "資料未通過"},
        headers=auth_headers(admin),
    )

    assert rejected.status_code == 409


@pytest.mark.asyncio
async def test_admin_membership_actions_enforce_valid_transitions(
    v2_context,
) -> None:
    client = v2_context["client"]
    admin = v2_context["admin"]
    membership = v2_context["member_a"].membership
    headers = auth_headers(admin)

    suspended = await client.post(
        f"/v1/admin/members/{membership.id}/suspend",
        json={"reason": "測試停權"},
        headers=headers,
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    repeated = await client.post(
        f"/v1/admin/members/{membership.id}/suspend",
        json={"reason": "不可重複停權"},
        headers=headers,
    )
    assert repeated.status_code == 409

    resigned = await client.post(
        f"/v1/admin/members/{membership.id}/resign",
        json={"reason": "社員申請退社"},
        headers=headers,
    )
    assert resigned.status_code == 200
    assert resigned.json()["status"] == "resigned"

    invalid_termination = await client.post(
        f"/v1/admin/members/{membership.id}/terminate",
        json={"reason": "已退社不可再終止"},
        headers=headers,
    )
    assert invalid_termination.status_code == 409


@pytest.mark.asyncio
async def test_admin_promotes_trainee_and_preserves_both_numbers(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    now = datetime.now(timezone.utc)
    trainee = User(
        customer_number="SLF-C-2026-0099",
        email="trainee@example.com",
        display_name="實習社員",
        password_hash=fast_password_hash("trainee-pass-123"),
        email_verified_at=now,
        membership=Membership(
            trainee_number="SLF-T-2026-0001",
            status=MembershipStatus.TRAINEE,
        ),
    )
    session.add(trainee)
    await session.flush()
    application = MembershipApplication(
        user_id=trainee.id,
        status=MembershipApplicationStatus.APPROVED,
    )
    session.add(application)
    await session.flush()
    trainee.membership.application_id = application.id
    await session.commit()

    trainee_headers = auth_headers(trainee)
    directory_before_activation = await client.get(
        "/v1/members/directory",
        headers=trainee_headers,
    )
    proposal_before_activation = await client.post(
        "/v1/member-proposals",
        json={
            "title": "實習社員不可發起治理提案",
            "body": "此請求應被正式社員權限擋下。",
        },
        headers=trainee_headers,
    )
    assert directory_before_activation.status_code == 403
    assert proposal_before_activation.status_code == 403

    withdrawn = await client.post(
        "/v1/membership/application/withdraw",
        json={"reason": "實習社員不可撤回成一般申請人"},
        headers=auth_headers(trainee),
    )
    assert withdrawn.status_code == 409

    promoted = await client.post(
        f"/v1/admin/members/{trainee.membership.id}/activate",
        json={"reason": "線下訓練、審核與面試完成"},
        headers=auth_headers(admin),
    )

    assert promoted.status_code == 200
    body = promoted.json()
    assert body["status"] == "active"
    assert body["trainee_number"] == "SLF-T-2026-0001"
    assert body["member_number"].startswith(f"SLF-{now.year}-")
    assert body["activated_at"] is not None
    directory_after_activation = await client.get(
        "/v1/members/directory",
        headers=trainee_headers,
    )
    assert directory_after_activation.status_code == 200

    repeated = await client.post(
        f"/v1/admin/members/{trainee.membership.id}/activate",
        json={"reason": "不可重複轉正"},
        headers=auth_headers(admin),
    )
    assert repeated.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "application_status",
    [
        MembershipApplicationStatus.REJECTED,
        MembershipApplicationStatus.WITHDRAWN,
    ],
)
async def test_admin_cannot_promote_trainee_with_closed_application(
    v2_context,
    application_status: MembershipApplicationStatus,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    application = MembershipApplication(
        user_id=applicant.id,
        status=application_status,
    )
    membership = Membership(
        user_id=applicant.id,
        application=application,
        trainee_number="SLF-T-2026-0098",
        status=MembershipStatus.TRAINEE,
    )
    session.add(membership)
    await session.commit()

    promoted = await client.post(
        f"/v1/admin/members/{membership.id}/activate",
        json={"reason": "線下流程完成"},
        headers=auth_headers(admin),
    )

    assert promoted.status_code == 409


@pytest.mark.asyncio
async def test_member_proposal_creator_can_read_admin_rejection_reason(
    v2_context,
) -> None:
    client = v2_context["client"]
    admin = v2_context["admin"]
    creator = v2_context["member_a"]
    created = await client.post(
        "/v1/member-proposals",
        json={
            "title": "增設社員工具共享櫃",
            "body": "讓社員登記借用園藝與修繕工具。",
        },
        headers=auth_headers(creator),
    )
    proposal_id = created.json()["id"]
    submitted = await client.post(
        f"/v1/member-proposals/{proposal_id}/submit",
        headers=auth_headers(creator),
    )
    assert submitted.status_code == 200

    rejected = await client.post(
        f"/v1/admin/member-proposals/{proposal_id}/reject",
        json={"reason": "請先補上工具保管與責任規則"},
        headers=auth_headers(admin),
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["review_reason"] == "請先補上工具保管與責任規則"
    detail = await client.get(
        f"/v1/member-proposals/{proposal_id}",
        headers=auth_headers(creator),
    )
    assert detail.status_code == 200
    assert detail.json()["review_reason"] == "請先補上工具保管與責任規則"


@pytest.mark.asyncio
async def test_activity_creator_can_read_admin_review_reason(
    v2_context,
) -> None:
    client = v2_context["client"]
    admin = v2_context["admin"]
    creator = v2_context["member_a"]
    now = datetime.now(timezone.utc)
    created = await client.post(
        "/v1/activities",
        json={
            "title": "社員夜間走讀",
            "description": "認識社區夜間生態。",
            "location": "合作社門口",
            "starts_at": (now + timedelta(days=3)).isoformat(),
            "ends_at": (now + timedelta(days=3, hours=2)).isoformat(),
            "registration_deadline": (now + timedelta(days=2)).isoformat(),
            "capacity": 12,
            "waitlist_enabled": True,
        },
        headers=auth_headers(creator),
    )
    assert created.status_code == 201
    activity_id = created.json()["id"]

    rejected = await client.post(
        f"/v1/admin/activities/{activity_id}/reject",
        json={"reason": "活動安全計畫需要補充"},
        headers=auth_headers(admin),
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["review_reason"] == "活動安全計畫需要補充"
    assert rejected.json()["reviewed_at"] is not None
    detail = await client.get(
        f"/v1/activities/{activity_id}",
        headers=auth_headers(creator),
    )
    assert detail.status_code == 200
    assert detail.json()["review_reason"] == "活動安全計畫需要補充"


@pytest.mark.asyncio
async def test_named_member_vote_list_has_explicit_openapi_contract(
    v2_context,
) -> None:
    schema = (await v2_context["client"].get("/openapi.json")).json()

    response_schema = schema["paths"][
        "/v1/member-proposals/{proposal_id}/votes"
    ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema == {
        "type": "array",
        "items": {
            "$ref": "#/components/schemas/MemberProposalNamedVoteRead",
        },
        "title": "Response List Named Member Proposal Votes V1 Member Proposals  Proposal Id  Votes Get",
    }


@pytest.mark.asyncio
async def test_pending_member_proposal_is_private_until_admin_approval(
    v2_context,
) -> None:
    client = v2_context["client"]
    admin = v2_context["admin"]
    proposer = v2_context["member_a"]
    other_member = v2_context["member_b"]
    now = datetime.now(timezone.utc)
    created = await client.post(
        "/v1/member-proposals",
        json={
            "title": "建立社員共學時段",
            "body": "每月安排一次社員交流與共學。",
        },
        headers=auth_headers(proposer),
    )
    assert created.status_code == 201
    proposal_id = created.json()["id"]
    submitted = await client.post(
        f"/v1/member-proposals/{proposal_id}/submit",
        headers=auth_headers(proposer),
    )
    assert submitted.status_code == 200

    hidden_list = await client.get(
        "/v1/member-proposals",
        headers=auth_headers(other_member),
    )
    assert hidden_list.status_code == 200
    assert hidden_list.json() == []
    hidden_detail = await client.get(
        f"/v1/member-proposals/{proposal_id}",
        headers=auth_headers(other_member),
    )
    assert hidden_detail.status_code == 404
    hidden_votes = await client.get(
        f"/v1/member-proposals/{proposal_id}/votes",
        headers=auth_headers(other_member),
    )
    assert hidden_votes.status_code == 404

    approved = await client.post(
        f"/v1/admin/member-proposals/{proposal_id}/approve",
        json={
            "minimum_voters": 2,
            "discussion_ends_at": (now + timedelta(days=3)).isoformat(),
            "voting_ends_at": (now + timedelta(days=10)).isoformat(),
        },
        headers=auth_headers(admin),
    )
    assert approved.status_code == 200
    published_list = await client.get(
        "/v1/member-proposals",
        headers=auth_headers(other_member),
    )
    assert [item["id"] for item in published_list.json()] == [proposal_id]


@pytest.mark.asyncio
async def test_admin_can_complete_finished_activity(
    v2_context,
) -> None:
    client = v2_context["client"]
    admin = v2_context["admin"]
    member = v2_context["member_a"]
    now = datetime.now(timezone.utc)
    created = await client.post(
        "/v1/activities",
        json={
            "title": "已結束的社員走讀",
            "description": "供結案流程驗證。",
            "location": "合作社集合點",
            "starts_at": (now - timedelta(hours=3)).isoformat(),
            "ends_at": (now - timedelta(hours=1)).isoformat(),
            "registration_deadline": (now - timedelta(hours=4)).isoformat(),
            "capacity": 10,
            "waitlist_enabled": True,
        },
        headers=auth_headers(member),
    )
    assert created.status_code == 201
    activity_id = created.json()["id"]
    approved = await client.post(
        f"/v1/admin/activities/{activity_id}/approve",
        json={},
        headers=auth_headers(admin),
    )
    assert approved.status_code == 200
    completed = await client.post(
        f"/v1/admin/activities/{activity_id}/complete",
        json={"reason": "活動時間已結束"},
        headers=auth_headers(admin),
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    detail = await client.get(
        f"/v1/activities/{activity_id}",
        headers=auth_headers(member),
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_admin_lists_activity_registrations_and_marks_attendance(
    v2_context,
) -> None:
    client = v2_context["client"]
    admin = v2_context["admin"]
    member_a = v2_context["member_a"]
    member_b = v2_context["member_b"]
    now = datetime.now(timezone.utc)
    created = await client.post(
        "/v1/activities",
        json={
            "title": "社員出席管理測試",
            "description": "管理端可查看名單並記錄出席狀態。",
            "location": "合作社集合點",
            "starts_at": (now + timedelta(days=2)).isoformat(),
            "ends_at": (now + timedelta(days=2, hours=2)).isoformat(),
            "registration_deadline": (now + timedelta(days=1)).isoformat(),
            "capacity": 2,
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
    first = await client.post(
        f"/v1/activities/{activity_id}/register",
        headers=auth_headers(member_a),
    )
    second = await client.post(
        f"/v1/activities/{activity_id}/register",
        headers=auth_headers(member_b),
    )
    assert first.status_code == 200
    assert second.status_code == 200

    registrations = await client.get(
        f"/v1/admin/activities/{activity_id}/registrations",
        headers=auth_headers(admin),
    )
    assert registrations.status_code == 200
    assert [item["display_name"] for item in registrations.json()] == [
        "社員甲",
        "社員乙",
    ]
    assert [item["email"] for item in registrations.json()] == [
        member_a.email,
        member_b.email,
    ]

    attended = await client.post(
        f"/v1/admin/activities/{activity_id}/registrations/"
        f"{first.json()['id']}/attended",
        headers=auth_headers(admin),
    )
    no_show = await client.post(
        f"/v1/admin/activities/{activity_id}/registrations/"
        f"{second.json()['id']}/no_show",
        headers=auth_headers(admin),
    )
    assert attended.status_code == 200
    assert attended.json()["status"] == "attended"
    assert attended.json()["checked_in_at"] is not None
    assert no_show.status_code == 200
    assert no_show.json()["status"] == "no_show"

    updated = await client.get(
        f"/v1/admin/activities/{activity_id}/registrations",
        headers=auth_headers(admin),
    )
    assert [item["status"] for item in updated.json()] == [
        "attended",
        "no_show",
    ]
    invalid_repeat = await client.post(
        f"/v1/admin/activities/{activity_id}/registrations/"
        f"{first.json()['id']}/no_show",
        headers=auth_headers(admin),
    )
    assert invalid_repeat.status_code == 409


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
            "pickup_starts_at": (now - timedelta(minutes=10)).isoformat(),
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

    event_list = await client.get("/v1/meal-events")
    event_detail = await client.get(f"/v1/meal-events/{event_id}")
    assert event_list.status_code == 200
    assert event_detail.status_code == 200
    assert event_list.json() == [event_detail.json()]
    assert event_detail.json()["offerings"][0] == {
        "id": offering_id,
        "meal_id": meal.json()["id"],
        "meal_name": "時蔬豆腐便當",
        "description": "當季蔬菜、豆腐與糙米飯。",
        "image_url": None,
        "price": 125,
        "capacity": 1,
        "reserved_quantity": 0,
        "paid_quantity": 0,
        "available_quantity": 1,
        "position": 0,
        "is_active": True,
        "option_groups": [],
    }

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
    assert quote.json()["items"] == [
        {
            "offering_id": offering_id,
            "meal_id": meal.json()["id"],
            "meal_name": "時蔬豆腐便當",
            "quantity": 1,
            "base_price": 125,
            "option_price": 0,
            "unit_price": 125,
            "subtotal": 125,
            "tax_type": "taxable",
            "selections": [],
        }
    ]

    created_order = await client.post(
        f"/v1/meal-events/{event_id}/orders",
        json=order_payload,
        headers=auth_headers(applicant),
    )
    assert created_order.status_code == 201
    assert created_order.json()["sales_channel"] == "meal_preorder"
    assert created_order.json()["fulfillment_method"] == "event_pickup"
    assert created_order.json()["payment_status"] == "pending"
    assert created_order.json()["invoice_status"] == "not_eligible"
    assert created_order.json()["paid_at"] is None
    assert created_order.json()["pickup_code"] is None
    assert created_order.json()["pickup_qr_payload"] is None
    assert created_order.json()["available_actions"] == ["pay", "cancel"]

    pending_order = await client.get(
        f"/v1/meal-orders/{created_order.json()['id']}",
        headers=auth_headers(applicant),
    )
    assert pending_order.status_code == 200
    assert pending_order.json() == created_order.json()
    pending_credential = await client.get(
        f"/v1/meal-orders/{created_order.json()['id']}/pickup-credential",
        headers=auth_headers(applicant),
    )
    assert pending_credential.status_code == 409

    stored_order = await session.scalar(
        select(Order)
        .where(Order.id == created_order.json()["id"])
        .options(selectinload(Order.items))
        .execution_options(populate_existing=True)
    )
    assert stored_order is not None
    assert stored_order.meal_event_id == event_id
    assert stored_order.items[0].source_meal_offering_id == offering_id

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

    reservation = InventoryReservation(
        order_id=created_order.json()["id"],
        source_meal_offering_id=offering_id,
        quantity=1,
        status=ReservationStatus.ACTIVE,
        expires_at=now + timedelta(minutes=15),
    )
    session.add(reservation)
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
    await session.refresh(offering)
    await session.refresh(reservation)
    assert offering.reserved_quantity == 0
    assert reservation.status == ReservationStatus.RELEASED
    assert reservation.released_at is not None

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
    offering.capacity = 2
    await session.commit()
    no_show_order_response = await client.post(
        f"/v1/meal-events/{event_id}/orders",
        json=order_payload,
        headers=auth_headers(applicant),
    )
    assert no_show_order_response.status_code == 201
    redeem_order = await session.get(
        Order,
        redeem_order_response.json()["id"],
    )
    no_show_order = await session.get(
        Order,
        no_show_order_response.json()["id"],
    )
    assert redeem_order is not None
    assert no_show_order is not None
    redeem_order.payment_status = PaymentStatus.PAID
    redeem_order.paid_at = now
    no_show_order.payment_status = PaymentStatus.PAID
    no_show_order.paid_at = now
    offering.paid_quantity = 2
    await session.commit()

    paid_order = await client.get(
        f"/v1/meal-orders/{redeem_order.id}",
        headers=auth_headers(customer_b),
    )
    assert paid_order.status_code == 200
    assert paid_order.json()["payment_status"] == "paid"
    assert paid_order.json()["fulfillment_status"] == "pending"
    assert len(paid_order.json()["pickup_code"]) == 6
    assert paid_order.json()["pickup_qr_payload"].startswith("slf-meal:")

    pickup_credential = await client.get(
        f"/v1/meal-orders/{redeem_order.id}/pickup-credential",
        headers=auth_headers(customer_b),
    )
    assert pickup_credential.status_code == 200
    assert pickup_credential.json() == {
        "order_id": redeem_order.id,
        "pickup_code": paid_order.json()["pickup_code"],
        "qr_token": paid_order.json()["pickup_qr_payload"],
    }
    assert pickup_credential.json()["pickup_code"].isdigit()
    other_user_credential = await client.get(
        f"/v1/meal-orders/{redeem_order.id}/pickup-credential",
        headers=auth_headers(applicant),
    )
    assert other_user_credential.status_code == 404

    invalid_cancel = await client.post(
        f"/v1/admin/meal-events/{event_id}/cancel",
        json={},
        headers=auth_headers(admin),
    )
    assert invalid_cancel.status_code == 422

    pickup_open = await client.post(
        f"/v1/admin/meal-events/{event_id}/open-pickup",
        headers=auth_headers(admin),
    )
    assert pickup_open.status_code == 200
    ready_order = await client.get(
        f"/v1/meal-orders/{redeem_order.id}",
        headers=auth_headers(customer_b),
    )
    assert ready_order.json()["fulfillment_status"] == "ready"
    ambiguous_redeem = await client.post(
        f"/v1/admin/meal-events/{event_id}/redeem",
        json={
            "pickup_code": pickup_credential.json()["pickup_code"],
            "qr_token": pickup_credential.json()["qr_token"],
        },
        headers=auth_headers(admin),
    )
    assert ambiguous_redeem.status_code == 422
    invalid_qr = await client.post(
        f"/v1/admin/meal-events/{event_id}/redeem",
        json={"qr_token": f"{pickup_credential.json()['qr_token']}x"},
        headers=auth_headers(admin),
    )
    assert invalid_qr.status_code == 404
    redeemed = await client.post(
        f"/v1/admin/meal-events/{event_id}/redeem",
        json={"qr_token": pickup_credential.json()["qr_token"]},
        headers=auth_headers(admin),
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["status"] == "picked_up"
    duplicate_redeem = await client.post(
        f"/v1/admin/meal-events/{event_id}/redeem",
        json={"pickup_code": pickup_credential.json()["pickup_code"]},
        headers=auth_headers(admin),
    )
    assert duplicate_redeem.status_code == 409
    await session.refresh(redeem_order)
    assert redeem_order.invoice_status == InvoiceStatus.PENDING

    rejected_event_cancel = await client.post(
        f"/v1/admin/meal-events/{event_id}/cancel",
        json={"reason": "臨時停辦"},
        headers=auth_headers(admin),
    )
    assert rejected_event_cancel.status_code == 409
    assert rejected_event_cancel.json()["detail"] == (
        "已有訂單完成取餐，不可取消整場"
    )
    stored_event = await session.get(MealEvent, event_id)
    assert stored_event is not None
    await session.refresh(stored_event)
    assert stored_event.status.value == "pickup_open"

    stored_event.ordering_ends_at = now - timedelta(minutes=30)
    stored_event.pickup_starts_at = now - timedelta(minutes=20)
    stored_event.pickup_ends_at = now - timedelta(minutes=10)
    await session.commit()
    completed = await client.post(
        f"/v1/admin/meal-events/{event_id}/complete",
        json={"reason": "本場次取餐結束"},
        headers=auth_headers(admin),
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    no_show_read = await client.get(
        f"/v1/meal-orders/{no_show_order.id}",
        headers=auth_headers(applicant),
    )
    assert no_show_read.status_code == 200
    assert no_show_read.json()["fulfillment_status"] == "no_show"
    assert no_show_read.json()["invoice_status"] == "pending"
    assert no_show_read.json()["pickup_code"] is None
    await session.refresh(no_show_order)
    assert no_show_order.fulfillment_status == FulfillmentStatus.PICKED_UP
    assert no_show_order.invoice_status == InvoiceStatus.PENDING

    duplicate_complete = await client.post(
        f"/v1/admin/meal-events/{event_id}/complete",
        headers=auth_headers(admin),
    )
    assert duplicate_complete.status_code == 409


@pytest.mark.asyncio
async def test_meal_refunds_enqueue_email_for_buyer_and_event_cancel(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    customer_b = v2_context["customer_b"]
    now = datetime.now(timezone.utc)

    async def create_paid_order(user: User, label: str):
        meal = await client.post(
            "/v1/admin/meals",
            json={
                "name": f"{label}便當",
                "description": "退款通知回歸測試",
                "price": 130,
                "tax_type": "taxable",
            },
            headers=auth_headers(admin),
        )
        assert meal.status_code == 201
        event = await client.post(
            "/v1/admin/meal-events",
            json={
                "title": f"{label}場次",
                "location": "校門口攤位",
                "ordering_starts_at": (now - timedelta(hours=1)).isoformat(),
                "ordering_ends_at": (now + timedelta(hours=1)).isoformat(),
                "pickup_starts_at": (now + timedelta(hours=2)).isoformat(),
                "pickup_ends_at": (now + timedelta(hours=3)).isoformat(),
                "offerings": [
                    {
                        "meal_id": meal.json()["id"],
                        "price": 130,
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
        created = await client.post(
            f"/v1/meal-events/{event_id}/orders",
            json={
                "items": [{"offering_id": offering_id, "quantity": 1}],
                "contact_email": user.email,
                "invoice_carrier_type": "ecpay",
            },
            headers=auth_headers(user),
        )
        assert created.status_code == 201
        order = await session.get(Order, created.json()["id"])
        offering = await session.get(MealEventOffering, offering_id)
        assert order is not None
        assert offering is not None
        order.payment_status = PaymentStatus.PAID
        order.paid_at = now
        offering.paid_quantity = 1
        reservation = InventoryReservation(
            order_id=order.id,
            source_meal_offering_id=offering.id,
            quantity=1,
            status=ReservationStatus.CONSUMED,
            expires_at=now + timedelta(minutes=15),
        )
        session.add(reservation)
        await session.commit()
        return event_id, order, offering, reservation

    _, buyer_order, buyer_offering, buyer_reservation = (
        await create_paid_order(applicant, "買家取消")
    )
    buyer_cancel = await client.post(
        f"/v1/meal-orders/{buyer_order.id}/cancel",
        json={"reason": "臨時有事"},
        headers=auth_headers(applicant),
    )
    assert buyer_cancel.status_code == 200
    assert buyer_cancel.json()["payment_status"] == "refunded"
    await session.refresh(buyer_offering)
    await session.refresh(buyer_reservation)
    assert buyer_offering.paid_quantity == 0
    assert buyer_reservation.status == ReservationStatus.RELEASED
    buyer_refund = await session.scalar(
        select(Refund).where(Refund.order_id == buyer_order.id)
    )
    buyer_notification = await session.scalar(
        select(Notification).where(
            Notification.user_id == applicant.id,
            Notification.event_type == "refund_completed",
        )
    )
    buyer_email = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == applicant.id,
            OutboxEvent.event_type == "send_email",
        )
    )
    assert buyer_refund is not None
    assert buyer_refund.status.value == "completed"
    assert buyer_notification is not None
    assert buyer_notification.data["order_id"] == buyer_order.id
    assert buyer_email is not None
    assert buyer_email.payload["to_email"] == applicant.email
    assert buyer_email.payload["event_type"] == "refund_completed"

    event_id, event_order, event_offering, event_reservation = (
        await create_paid_order(customer_b, "整場取消")
    )
    event_offering.capacity = 2
    await session.commit()
    pending_created = await client.post(
        f"/v1/meal-events/{event_id}/orders",
        json={
            "items": [
                {"offering_id": event_offering.id, "quantity": 1}
            ],
            "contact_email": applicant.email,
            "invoice_carrier_type": "ecpay",
        },
        headers=auth_headers(applicant),
    )
    assert pending_created.status_code == 201
    pending_event_order = await session.get(
        Order,
        pending_created.json()["id"],
    )
    assert pending_event_order is not None
    pending_event_reservation = InventoryReservation(
        order_id=pending_event_order.id,
        source_meal_offering_id=event_offering.id,
        quantity=1,
        status=ReservationStatus.ACTIVE,
        expires_at=now + timedelta(minutes=15),
    )
    event_offering.reserved_quantity = 1
    session.add(pending_event_reservation)
    await session.commit()
    event_cancel = await client.post(
        f"/v1/admin/meal-events/{event_id}/cancel",
        json={"reason": "供餐單位臨時停辦"},
        headers=auth_headers(admin),
    )
    assert event_cancel.status_code == 200
    assert event_cancel.json()["status"] == "cancelled"
    await session.refresh(event_order)
    await session.refresh(event_offering)
    await session.refresh(event_reservation)
    await session.refresh(pending_event_order)
    await session.refresh(pending_event_reservation)
    assert event_order.payment_status == PaymentStatus.REFUNDED
    assert event_order.fulfillment_status == FulfillmentStatus.CANCELLED
    assert pending_event_order.payment_status == PaymentStatus.EXPIRED
    assert pending_event_order.fulfillment_status == FulfillmentStatus.CANCELLED
    assert event_offering.paid_quantity == 0
    assert event_offering.reserved_quantity == 0
    assert event_reservation.status == ReservationStatus.RELEASED
    assert pending_event_reservation.status == ReservationStatus.RELEASED
    event_refund = await session.scalar(
        select(Refund).where(Refund.order_id == event_order.id)
    )
    event_notification = await session.scalar(
        select(Notification).where(
            Notification.user_id == customer_b.id,
            Notification.event_type == "refund_completed",
        )
    )
    event_email = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == customer_b.id,
            OutboxEvent.event_type == "send_email",
        )
    )
    assert event_refund is not None
    assert event_notification is not None
    assert event_notification.data["meal_event_id"] == event_id
    assert event_email is not None
    assert event_email.payload["to_email"] == customer_b.email
    assert event_email.payload["event_type"] == "refund_completed"


class RecordingDocumentStorage:
    """Captures what the router asks R2 to sign, then replays it as a HEAD."""

    def __init__(self) -> None:
        self.tickets: list[dict] = []
        self.stored: dict[str, dict] = {}
        self.downloaded: list[str] = []

    def create_upload_ticket(
        self,
        *,
        content_type: str,
        content_length: int,
        sha256: str | None = None,
    ):
        from app.integrations.r2_storage import DocumentUploadTicket

        object_key = (
            f"membership-documents/pending/ab/{len(self.tickets)}.png"
        )
        required_headers = {
            "Content-Type": content_type,
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
        verified_key = object_key.replace(
            "membership-documents/pending/",
            "membership-documents/verified/",
            1,
        )
        self.stored[verified_key] = self.stored.pop(object_key)
        return DocumentHead(
            object_key=verified_key,
            content_type=actual["content_type"],
            content_length=actual["content_length"],
            sha256=actual["sha256"] or None,
            etag="etag-1",
        )

    async def delete_document(self, object_key: str) -> None:
        self.stored.pop(object_key, None)

    def create_download_url(self, object_key: str, *, expires_in_seconds: int | None = None) -> str:
        self.downloaded.append(object_key)
        return f"https://r2.example.test/{object_key}?download-signed=1"


@pytest.mark.asyncio
async def test_membership_document_upload_and_confirm_disabled(v2_context) -> None:
    from app.routers.membership import get_document_storage

    client = v2_context["client"]
    storage = RecordingDocumentStorage()
    client._transport.app.dependency_overrides[get_document_storage] = lambda: storage
    headers = auth_headers(v2_context["applicant"])
    upload = await client.post("/v1/membership/documents/upload-url", headers=headers, json={
        "document_type": "id_front", "content_type": "image/png",
        "size_bytes": 2048, "checksum_sha256": "a" * 64,
    })
    confirm = await client.post("/v1/membership/documents/legacy/confirm", headers=headers, json={"checksum_sha256": "a" * 64})
    assert upload.status_code == confirm.status_code == 410
    assert storage.tickets == []
    assert (await client.get("/v1/membership/application", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_legacy_membership_document_review_and_delete_are_audited(v2_context) -> None:
    from tests.membership_support import seed_legacy_document

    client, session = v2_context["client"], v2_context["session"]
    applicant, admin = v2_context["applicant"], v2_context["admin"]
    storage = RecordingDocumentStorage()
    document = await seed_legacy_document(v2_context, storage)
    original_key = document.object_key
    download_path = f"/v1/admin/membership-applications/{document.application_id}/documents/{document.id}/download-url"
    denied = await client.get(download_path, headers=auth_headers(v2_context["customer_b"]))
    assert denied.status_code == 403
    download = await client.get(download_path, headers=auth_headers(admin))
    assert download.status_code == 200, download.text
    assert download.json()["download_url"] == f"https://r2.example.test/{original_key}?download-signed=1"
    view_audit = await session.scalar(select(AdminAudit).where(
        AdminAudit.action == "membership.view_document", AdminAudit.aggregate_id == document.id,
    ))
    assert view_audit is not None and view_audit.actor_id == admin.id
    assert view_audit.data["document_type"] == "id_front"
    assert original_key not in str(view_audit.data)
    replacement = await client.post("/v1/membership/documents/upload-url", headers=auth_headers(applicant), json={
        "document_type": "id_front", "content_type": "image/png", "size_bytes": 3072, "checksum_sha256": "b" * 64,
    })
    assert replacement.status_code == 410
    assert original_key in storage.stored
    forbidden = await client.delete(f"/v1/membership/documents/{document.id}", headers=auth_headers(v2_context["customer_b"]))
    assert forbidden.status_code == 404
    deleted = await client.delete(f"/v1/membership/documents/{document.id}", headers=auth_headers(applicant))
    assert deleted.status_code == 204, deleted.text
    assert original_key not in storage.stored
    await session.refresh(document)
    assert document.status == MembershipDocumentStatus.DELETED
    delete_audit = await session.scalar(select(AdminAudit).where(
        AdminAudit.action == "membership.document_deleted", AdminAudit.aggregate_id == document.id,
    ))
    assert delete_audit is not None and delete_audit.actor_id == applicant.id
    assert delete_audit.data == {"actor_role": "applicant", "application_id": document.application_id, "document_type": "id_front"}
    assert original_key not in str(delete_audit.data)
    assert (await client.get("/v1/membership/documents", headers=auth_headers(applicant))).json() == []


@pytest.mark.asyncio
async def test_meal_options_validate_price_and_persist_snapshots(
    v2_context,
) -> None:
    client = v2_context["client"]
    session = v2_context["session"]
    admin = v2_context["admin"]
    applicant = v2_context["applicant"]
    now = datetime.now(timezone.utc)

    meal = await client.post(
        "/v1/admin/meals",
        json={
            "name": "客製餐盒",
            "price": 130,
            "tax_type": "taxable",
            "option_groups": [
                {
                    "name": "主食選擇",
                    "min_selections": 1,
                    "max_selections": 1,
                    "options": [
                        {"name": "紫米飯"},
                        {"name": "壽司", "price_delta": 10},
                    ],
                },
                {
                    "name": "飲品加購",
                    "min_selections": 0,
                    "max_selections": 1,
                    "options": [
                        {"name": "茂谷汽水", "price_delta": 20},
                    ],
                },
            ],
        },
        headers=auth_headers(admin),
    )
    assert meal.status_code == 201, meal.text
    groups = meal.json()["option_groups"]
    sushi_id = groups[0]["options"][1]["id"]
    soda_id = groups[1]["options"][0]["id"]

    event = await client.post(
        "/v1/admin/meal-events",
        json={
            "title": "每日客製餐盒",
            "location": "清華大學",
            "ordering_starts_at": (now - timedelta(hours=1)).isoformat(),
            "ordering_ends_at": (now + timedelta(hours=1)).isoformat(),
            "pickup_starts_at": (now + timedelta(hours=2)).isoformat(),
            "pickup_ends_at": (now + timedelta(hours=3)).isoformat(),
            "offerings": [
                {
                    "meal_id": meal.json()["id"],
                    "price": 130,
                    "capacity": 5,
                }
            ],
        },
        headers=auth_headers(admin),
    )
    assert event.status_code == 201, event.text
    event_id = event.json()["id"]
    offering_id = event.json()["offerings"][0]["id"]
    published = await client.post(
        f"/v1/admin/meal-events/{event_id}/publish",
        headers=auth_headers(admin),
    )
    assert published.status_code == 200

    missing_required = await client.post(
        f"/v1/meal-events/{event_id}/quote",
        json={
            "items": [{"offering_id": offering_id, "quantity": 1}],
            "contact_email": applicant.email,
        },
    )
    assert missing_required.status_code == 422
    assert missing_required.json()["detail"] == "主食選擇必須選擇 1 項"

    payload = {
        "items": [
            {
                "offering_id": offering_id,
                "quantity": 2,
                "option_ids": [sushi_id, soda_id],
            }
        ],
        "contact_email": applicant.email,
    }
    quote = await client.post(
        f"/v1/meal-events/{event_id}/quote",
        json=payload,
    )
    assert quote.status_code == 200, quote.text
    line = quote.json()["items"][0]
    assert line["base_price"] == 130
    assert line["option_price"] == 30
    assert line["unit_price"] == 160
    assert line["subtotal"] == 320
    assert [item["option_name"] for item in line["selections"]] == [
        "壽司",
        "茂谷汽水",
    ]

    created = await client.post(
        f"/v1/meal-events/{event_id}/orders",
        json=payload,
        headers=auth_headers(applicant),
    )
    assert created.status_code == 201, created.text
    assert created.json()["amount_total"] == 320
    assert created.json()["items"][0]["option_price"] == 30
    snapshots = list(
        await session.scalars(
            select(OrderItemOption)
            .join(OrderItem)
            .where(OrderItem.order_id == created.json()["id"])
            .order_by(OrderItemOption.position)
        )
    )
    assert [snapshot.option_name for snapshot in snapshots] == [
        "壽司",
        "茂谷汽水",
    ]


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
