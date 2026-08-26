from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .auth import hash_password, verify_password
from .config import Settings, get_settings
from .database import SessionLocal
from .integrations.common import IntegrationError
from .integrations.pii_crypto import (
    VersionedPIICipher,
    pii_cipher_from_settings,
)
from .integrations.r2_storage import r2_document_storage_from_settings
from .member_claims import roster_aad
from .models import (
    Activity,
    ActivityRegistration,
    ActivityRegistrationStatus,
    ActivityStatus,
    AdminAudit,
    EducationLecture,
    EducationQuestion,
    EducationAttempt,
    BadgeDefinition,
    FiscalYear,
    EmailVerificationToken,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    GroupBundle,
    GroupBundleItem,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InventoryReservation,
    Invoice,
    InvoiceStatus,
    Meal,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    MealOption,
    MealOptionGroup,
    MeetingType,
    MemberDirectoryEntry,
    MemberBadge,
    MemberProfile,
    MemberRosterEntry,
    MemberProposal,
    MemberProposalComment,
    MemberProposalStatus,
    MemberProposalVote,
    MemberVoteChoice,
    Meeting,
    MeetingAttendance,
    MeetingResolution,
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
    PasswordResetToken,
    PaymentAttempt,
    PaymentStatus,
    PickupLocation,
    PointAccount,
    PointSourceType,
    PointTransaction,
    Product,
    ProposalStatus,
    ReservationStatus,
    Refund,
    RefundStatus,
    SalesChannel,
    Shipment,
    ShipmentStatus,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
    Supplier,
    SupplierAccreditation,
    SupplierAccreditationStatus,
    SupplierDocument,
    SurplusDistribution,
    SurplusLedger,
    SystemSetting,
    TargetType,
    TaxType,
    User,
    UserRole,
    Vote,
    VoteProposal,
    Wish,
    WishSupport,
    WishStatus,
    ExternalEvent,
)
from .official_catalog import (
    DINNER_MEAL_SLUGS,
    LUNCH_MEAL_SLUGS,
    OFFICIAL_MEALS,
    OFFICIAL_PRODUCTS,
    MealSpec,
    build_meal,
    build_option_groups,
    product_record,
)


DEMO_PASSWORD = "member123"
DEMO_ROSTER_ID = "member-roster-existing-demo"
DEMO_ROSTER_NUMBER = "SLF-2018-0099"
DEMO_PREORDER_EVENT_ID = "meal-event-preorder-demo"
DEMO_PREORDER_OFFERING_ID = "meal-offering-preorder-demo"
DEMO_DINNER_EVENT_ID = "meal-event-dinner-demo"


def _build_demo_roster_entry(
    cipher: VersionedPIICipher,
) -> MemberRosterEntry:
    aad = roster_aad(DEMO_ROSTER_ID)
    return MemberRosterEntry(
        id=DEMO_ROSTER_ID,
        member_number=DEMO_ROSTER_NUMBER,
        legal_name_encrypted=cipher.encrypt_text(
            "既有社員展示",
            associated_data=aad,
        ),
        email_encrypted=cipher.encrypt_text(
            "existing@shilifangyuan.tw",
            associated_data=aad,
        ),
        phone_encrypted=cipher.encrypt_text(
            "0911888777",
            associated_data=aad,
        ),
        encryption_key_version=cipher.current_version,
        share_certificate_number="DEMO-SHARE-0099",
        share_capital_amount=3000,
        share_count=3,
        share_subscribed_on=date(2018, 5, 10),
        share_paid_on=date(2018, 5, 12),
    )


def _build_demo_preorder_event(
    now: datetime,
    meals: list[Meal],
    admin_id: str,
    *,
    dinner: bool = False,
) -> MealEvent:
    event_id = DEMO_DINNER_EVENT_ID if dinner else DEMO_PREORDER_EVENT_ID
    ordering_ends_at = now + timedelta(hours=24 if dinner else 18)
    pickup_starts_at = ordering_ends_at + timedelta(hours=3 if dinner else 2)
    return MealEvent(
        id=event_id,
        title="每日晚餐預訂" if dinner else "每日午餐預訂",
        location="啟碁科技" if dinner else "清華大學",
        ordering_starts_at=now - timedelta(hours=2),
        ordering_ends_at=ordering_ends_at,
        pickup_starts_at=pickup_starts_at,
        pickup_ends_at=pickup_starts_at + timedelta(hours=2),
        status=MealEventStatus.PUBLISHED,
        created_by_id=admin_id,
        offerings=[
            MealEventOffering(
                id=(
                    DEMO_PREORDER_OFFERING_ID
                    if index == 1 and not dinner
                    else f"meal-offering-{'dinner' if dinner else 'lunch'}-{index:02d}"
                ),
                meal_id=meal.id,
                price=meal.price,
                capacity=50,
                paid_quantity=0,
                position=index,
            )
            for index, meal in enumerate(meals, start=1)
        ],
    )


PRODUCTS = [
    {
        "slug": "bok-choy",
        "name": "有機小白菜",
        "description": "合作農場當日採收，口感清甜。",
        "category": "當季蔬果",
        "unit": "把",
        "member_price": 45,
        "nonmember_price": 55,
        "stock_quantity": 60,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": "/assets/products/bok-choy.jpg",
    },
    {
        "slug": "spinach",
        "name": "有機菠菜",
        "description": "無農藥栽培，適合清炒或煮湯。",
        "category": "當季蔬果",
        "unit": "把",
        "member_price": 50,
        "nonmember_price": 60,
        "stock_quantity": 45,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": None,
    },
    {
        "slug": "fruit-corn",
        "name": "水果玉米",
        "description": "香甜多汁，可直接蒸煮。",
        "category": "當季蔬果",
        "unit": "包",
        "member_price": 90,
        "nonmember_price": 105,
        "stock_quantity": 50,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": "/assets/products/fruit-corn.jpg",
    },
    {
        "slug": "sweet-potato",
        "name": "台農地瓜",
        "description": "綿密香甜，產地直送。",
        "category": "當季蔬果",
        "unit": "袋",
        "member_price": 80,
        "nonmember_price": 95,
        "stock_quantity": 40,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": "/assets/products/sweet-potato.jpg",
    },
    {
        "slug": "tomatoes",
        "name": "牛番茄",
        "description": "酸甜平衡，適合生食與料理。",
        "category": "當季蔬果",
        "unit": "盒",
        "member_price": 85,
        "nonmember_price": 100,
        "stock_quantity": 36,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": "/assets/products/tomatoes.jpg",
    },
    {
        "slug": "carrot",
        "name": "產銷履歷紅蘿蔔",
        "description": "自然栽種，甜度高。",
        "category": "當季蔬果",
        "unit": "袋",
        "member_price": 65,
        "nonmember_price": 75,
        "stock_quantity": 52,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": None,
    },
    {
        "slug": "rice",
        "name": "友善耕作白米",
        "description": "小農契作白米，口感飽滿。",
        "category": "米・雜糧",
        "unit": "包",
        "member_price": 220,
        "nonmember_price": 250,
        "stock_quantity": 30,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": "/assets/products/rice.jpg",
    },
    {
        "slug": "eggs",
        "name": "放牧雞蛋",
        "description": "人道飼養放牧雞蛋，每盒十入。",
        "category": "蛋品",
        "unit": "盒",
        "member_price": 120,
        "nonmember_price": 140,
        "stock_quantity": 80,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": "/assets/products/eggs.jpg",
    },
    {
        "slug": "pineapple",
        "name": "金鑽鳳梨",
        "description": "產地熟成，果肉細緻。",
        "category": "當季蔬果",
        "unit": "顆",
        "member_price": 75,
        "nonmember_price": 90,
        "stock_quantity": 25,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": None,
    },
    {
        "slug": "banana",
        "name": "友善香蕉",
        "description": "減藥栽培，自然熟成。",
        "category": "當季蔬果",
        "unit": "把",
        "member_price": 70,
        "nonmember_price": 85,
        "stock_quantity": 35,
        "tax_type": TaxType.TAX_EXEMPT,
        "image_url": None,
    },
    {
        "slug": "black-bean-soy-sauce",
        "name": "黑豆醬油",
        "description": "純釀黑豆醬油，無添加防腐劑。",
        "category": "加工品",
        "unit": "瓶",
        "member_price": 180,
        "nonmember_price": 210,
        "stock_quantity": 24,
        "tax_type": TaxType.TAXABLE,
        "image_url": "/assets/products/black-bean-soy-sauce.jpg",
    },
    {
        "slug": "pineapple-jam",
        "name": "手作鳳梨果醬",
        "description": "以契作鳳梨小量熬煮。",
        "category": "加工品",
        "unit": "罐",
        "member_price": 160,
        "nonmember_price": 190,
        "stock_quantity": 20,
        "tax_type": TaxType.TAXABLE,
        "image_url": "/assets/products/pineapple-jam.jpg",
    },
    *(product_record(spec) for spec in OFFICIAL_PRODUCTS),
]


PICKUP_LOCATIONS = [
    {
        "id": "pickup-coop-store",
        "code": "coop-store",
        "name": "合作社門市（水木書苑內左側）",
        "address": "",
        "instructions": "",
        "sort_order": 10,
    },
    {
        "id": "pickup-tsmc-building",
        "code": "tsmc-building",
        "name": "台積館",
        "address": "",
        "instructions": "",
        "sort_order": 20,
    },
    {
        "id": "pickup-education-building",
        "code": "education-building",
        "name": "教育學院大樓",
        "address": "",
        "instructions": "",
        "sort_order": 30,
    },
    {
        "id": "pickup-humanities-building",
        "code": "humanities-building",
        "name": "人社院",
        "address": "",
        "instructions": "",
        "sort_order": 40,
    },
    {
        "id": "pickup-incubation-center",
        "code": "incubation-center",
        "name": "創新育成大樓",
        "address": "",
        "instructions": "",
        "sort_order": 50,
    },
]


SUPPLIER_DEMOS = [
    {
        "id": "supplier-demo-produce",
        "supplier_number": "SUP-2026-0001",
        "business_name": "田野共好農場（展示）",
        "contact_person": "測試接洽人甲",
        "email": "produce@example.test",
        "reviewed_days_ago": 90,
    },
    {
        "id": "supplier-demo-eggs",
        "supplier_number": "SUP-2026-0002",
        "business_name": "共好蛋場（展示）",
        "contact_person": "測試接洽人乙",
        "email": "eggs@example.test",
        "reviewed_days_ago": 70,
    },
    {
        "id": "supplier-demo-pantry",
        "supplier_number": "SUP-2026-0003",
        "business_name": "日常加工室（展示）",
        "contact_person": "測試接洽人丙",
        "email": "pantry@example.test",
        "reviewed_days_ago": 45,
    },
]


PRODUCT_SUPPLIERS = {
    "eggs": "supplier-demo-eggs",
    "black-bean-soy-sauce": "supplier-demo-pantry",
    "pineapple-jam": "supplier-demo-pantry",
}


def _product_supplier_id(data: dict[str, object]) -> str:
    slug = str(data["slug"])
    category = getattr(data["category"], "value", data["category"])
    return PRODUCT_SUPPLIERS.get(
        slug,
        (
            "supplier-demo-pantry"
            if category in {"加工品", "飲品", "生活用品"}
            else "supplier-demo-produce"
        ),
    )


def _build_demo_supplier(
    supplier_data: dict[str, object],
    seed_cipher: VersionedPIICipher,
    reviewer_id: str,
    now: datetime,
) -> Supplier:
    supplier_id = str(supplier_data["id"])
    aad = f"supplier:{supplier_id}"
    reviewed_on = (
        now - timedelta(days=int(supplier_data["reviewed_days_ago"]))
    ).date()
    return Supplier(
        id=supplier_id,
        supplier_number=str(supplier_data["supplier_number"]),
        business_name=str(supplier_data["business_name"]),
        tax_id=None,
        responsible_person_encrypted=seed_cipher.encrypt_text(
            "測試負責人",
            associated_data=aad,
        ),
        contact_person_encrypted=seed_cipher.encrypt_text(
            str(supplier_data["contact_person"]),
            associated_data=aad,
        ),
        phone_encrypted=seed_cipher.encrypt_text(
            "0900000000",
            associated_data=aad,
        ),
        email_encrypted=seed_cipher.encrypt_text(
            str(supplier_data["email"]),
            associated_data=aad,
        ),
        line_id_encrypted=None,
        settlement_terms="每月彙整一次，實際條件待合作社確認。",
        bank_account_encrypted=seed_cipher.encrypt_text(
            "SANDBOX-DEMO",
            associated_data=aad,
        ),
        encryption_key_version=seed_cipher.current_version,
        accredited_on=reviewed_on,
        is_active=True,
        accreditations=[
            SupplierAccreditation(
                reviewed_on=reviewed_on,
                reviewer_id=reviewer_id,
                process_notes="展示用審認紀錄；正式文件與訪查結果待匯入。",
                status=SupplierAccreditationStatus.APPROVED,
                result_notes="展示資料通過。",
            )
        ],
    )


async def seed_demo_data(session: AsyncSession) -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    existing = await session.scalar(select(User.id).limit(1))
    if existing is not None:
        await sync_preview_demo_data(session, settings, now)
        return {
            "users": 0,
            "products": 0,
            "campaigns": 0,
            "suppliers": 0,
            "pickup_locations": 0,
        }

    admin_password_hash = hash_password(settings.demo_admin_password)
    member_password_hash = hash_password(settings.demo_member_password)
    nonmember_password_hash = hash_password(settings.demo_nonmember_password)
    users: list[User] = [
        User(
            id="user-admin",
            email="admin@shilifangyuan.tw",
            display_name="十里方圓管理員",
            password_hash=admin_password_hash,
            user_role=UserRole.ADMIN,
            membership_type=MembershipType.MEMBER,
            email_verified_at=now - timedelta(days=60),
        ),
        User(
            id="user-member",
            email="member@shilifangyuan.tw",
            display_name="社員小方",
            password_hash=member_password_hash,
            membership_type=MembershipType.MEMBER,
            email_verified_at=now - timedelta(days=45),
        ),
        User(
            id="user-customer",
            email="customer@shilifangyuan.tw",
            display_name="一般消費者",
            password_hash=nonmember_password_hash,
            membership_type=MembershipType.NONMEMBER,
            email_verified_at=now - timedelta(days=30),
        ),
    ]
    users.extend(
        User(
            id=f"user-voter-{number:02d}",
            email=f"voter{number}@shilifangyuan.tw",
            display_name=f"投票者 {number}",
            password_hash=(
                member_password_hash
                if number % 2
                else nonmember_password_hash
            ),
            membership_type=(
                MembershipType.MEMBER
                if number % 2
                else MembershipType.NONMEMBER
            ),
            email_verified_at=now - timedelta(days=20),
        )
        for number in range(1, 9)
    )
    users.extend(
        [
            User(
                id="user-applicant-supplement",
                email="supplement@shilifangyuan.tw",
                display_name="補件申請人",
                password_hash=nonmember_password_hash,
                membership_type=MembershipType.NONMEMBER,
                email_verified_at=now - timedelta(days=5),
            ),
            User(
                id="user-applicant-payment",
                email="pending@shilifangyuan.tw",
                display_name="待付款申請人",
                password_hash=nonmember_password_hash,
                membership_type=MembershipType.NONMEMBER,
                email_verified_at=now - timedelta(days=8),
            ),
        ]
    )
    customer_sequence = 0
    for user in users:
        if user.user_role == UserRole.ADMIN:
            continue
        customer_sequence += 1
        user.customer_number = (
            f"SLF-C-{now.year}-{customer_sequence:04d}"
        )
    session.add_all(users)
    await session.flush()

    try:
        seed_cipher = pii_cipher_from_settings(settings)
    except IntegrationError:
        seed_cipher = VersionedPIICipher({"v1": bytes(32)}, "v1")

    session.add(_build_demo_roster_entry(seed_cipher))

    pickup_locations = []
    for location_data in PICKUP_LOCATIONS:
        existing_location = await session.scalar(
            select(PickupLocation).where(
                PickupLocation.code == location_data["code"]
            )
        )
        if existing_location is None:
            pickup_locations.append(PickupLocation(**location_data))
    session.add_all(pickup_locations)

    suppliers = [
        _build_demo_supplier(
            supplier_data,
            seed_cipher,
            users[0].id,
            now,
        )
        for supplier_data in SUPPLIER_DEMOS
    ]
    session.add_all(suppliers)
    await session.flush()

    effective_from = date(now.year, 1, 1)
    fee_schedules = []
    for schedule_id, charge_kind, amount in (
        ("fee-admission-demo", MembershipChargeKind.ADMISSION_FEE, 500),
        ("fee-share-demo", MembershipChargeKind.SHARE_CAPITAL, 1000),
    ):
        schedule = await session.scalar(
            select(MembershipFeeSchedule).where(
                MembershipFeeSchedule.charge_kind == charge_kind,
                MembershipFeeSchedule.effective_from == effective_from,
            )
        )
        if schedule is None:
            schedule = MembershipFeeSchedule(
                id=schedule_id,
                charge_kind=charge_kind,
                amount=amount,
                effective_from=effective_from,
            )
            session.add(schedule)
        fee_schedules.append(schedule)

    active_member_users = [
        users[0],
        users[1],
        *users[3:11],
    ]
    active_memberships = [
        Membership(
            id=f"membership-{index:02d}",
            user_id=user.id,
            member_number=f"SLF-{now.year}-{index:04d}",
            share_certificate_number=f"SHARE-{now.year}-{index:04d}",
            share_capital_amount=1000,
            share_count=10,
            share_subscribed_on=(now - timedelta(days=120 - index)).date(),
            share_paid_on=(now - timedelta(days=115 - index)).date(),
            status=MembershipStatus.ACTIVE,
            activated_at=now - timedelta(days=90 - index),
        )
        for index, user in enumerate(active_member_users, start=1)
    ]
    session.add_all(active_memberships)
    session.add(
        MemberDirectoryEntry(
            user_id=users[1].id,
            is_public=True,
            nickname="小方",
            avatar_url=None,
            expertise="友善耕作、共煮",
            bio="喜歡把產地故事帶回日常餐桌。",
        )
    )

    supplement_user = next(
        user for user in users if user.id == "user-applicant-supplement"
    )
    payment_user = next(
        user for user in users if user.id == "user-applicant-payment"
    )

    def encrypted_profile(user: User, legal_name: str) -> MemberProfile:
        aad = f"member-profile:{user.id}"
        return MemberProfile(
            user_id=user.id,
            legal_name_encrypted=seed_cipher.encrypt_text(
                legal_name,
                associated_data=aad,
            ),
            phone_encrypted=seed_cipher.encrypt_text(
                "0912345678",
                associated_data=aad,
            ),
            birth_date_encrypted=seed_cipher.encrypt_text(
                "1990-01-01",
                associated_data=aad,
            ),
            address_encrypted=seed_cipher.encrypt_text(
                "Sandbox 測試地址",
                associated_data=aad,
            ),
            emergency_contact_encrypted=seed_cipher.encrypt_text(
                "測試聯絡人 0900000000",
                associated_data=aad,
            ),
            encryption_key_version=seed_cipher.current_version,
            consent_version="sandbox-v1",
            consented_at=now - timedelta(days=3),
        )

    supplement_application = MembershipApplication(
        id="application-supplement",
        user_id=supplement_user.id,
        status=MembershipApplicationStatus.NEEDS_SUPPLEMENT,
        submitted_at=now - timedelta(days=3),
        reviewed_by_id=users[0].id,
        reviewed_at=now - timedelta(days=2),
        review_reason="第二證件影像需重新上傳（僅使用測試素材）",
        documents=[
            MembershipDocument(
                document_type=MembershipDocumentType.ID_FRONT,
                status=MembershipDocumentStatus.CONFIRMED,
                object_key="membership-documents/demo/supplement-front.jpg",
                content_type="image/jpeg",
                size_bytes=120000,
                checksum_sha256="a" * 64,
                confirmed_at=now - timedelta(days=3),
            ),
            MembershipDocument(
                document_type=MembershipDocumentType.ID_BACK,
                status=MembershipDocumentStatus.CONFIRMED,
                object_key="membership-documents/demo/supplement-back.jpg",
                content_type="image/jpeg",
                size_bytes=118000,
                checksum_sha256="b" * 64,
                confirmed_at=now - timedelta(days=3),
            ),
        ],
    )
    payment_application = MembershipApplication(
        id="application-payment",
        user_id=payment_user.id,
        status=MembershipApplicationStatus.APPROVED,
        submitted_at=now - timedelta(days=6),
        reviewed_by_id=users[0].id,
        reviewed_at=now - timedelta(days=5),
        documents=[
            MembershipDocument(
                document_type=document_type,
                status=MembershipDocumentStatus.CONFIRMED,
                object_key=(
                    "membership-documents/demo/"
                    f"payment-{document_type.value}.jpg"
                ),
                content_type="image/jpeg",
                size_bytes=125000,
                checksum_sha256=f"{index}" * 64,
                confirmed_at=now - timedelta(days=6),
            )
            for index, document_type in enumerate(
                (
                    MembershipDocumentType.ID_FRONT,
                    MembershipDocumentType.ID_BACK,
                    MembershipDocumentType.SECONDARY,
                ),
                start=1,
            )
        ],
    )
    session.add_all(
        [
            encrypted_profile(supplement_user, "測試補件者"),
            encrypted_profile(payment_user, "測試待付款者"),
            supplement_application,
            payment_application,
        ]
    )
    await session.flush()
    pending_membership = Membership(
        id="membership-pending-payment",
        user_id=payment_user.id,
        application_id=payment_application.id,
        status=MembershipStatus.PENDING_PAYMENT,
    )
    session.add(pending_membership)
    await session.flush()
    session.add_all(
        [
            MembershipCharge(
                id="charge-admission-paid",
                user_id=payment_user.id,
                application_id=payment_application.id,
                membership_id=pending_membership.id,
                fee_schedule_id=fee_schedules[0].id,
                charge_kind=MembershipChargeKind.ADMISSION_FEE,
                amount=500,
                status=MembershipChargeStatus.PAID,
                receipt_number=f"SLFR-{now:%Y%m%d}-DEMO0001",
                paid_at=now - timedelta(days=4),
            ),
            MembershipCharge(
                id="charge-share-pending",
                user_id=payment_user.id,
                application_id=payment_application.id,
                membership_id=pending_membership.id,
                fee_schedule_id=fee_schedules[1].id,
                charge_kind=MembershipChargeKind.SHARE_CAPITAL,
                amount=1000,
                status=MembershipChargeStatus.PENDING,
            ),
        ]
    )

    products = [
        Product(
            **data,
            product_number=f"P-{index:04d}",
            sku=data["slug"].upper(),
            supplier_id=_product_supplier_id(data),
        )
        for index, data in enumerate(PRODUCTS, start=1)
    ]
    session.add_all(products)
    await session.flush()
    by_slug = {product.slug: product for product in products}
    for slug in {
        "rice",
        "black-bean-soy-sauce",
        "pineapple-jam",
        "sweet-potato",
    }:
        by_slug[slug].can_ship = True
        by_slug[slug].shipping_temperature = ShippingTemperature.AMBIENT
        by_slug[slug].allowed_shipping_channels = [
            ShippingChannel.HOME_DELIVERY.value,
            ShippingChannel.SEVEN_ELEVEN.value,
            ShippingChannel.FAMILY_MART.value,
            ShippingChannel.HILIFE.value,
        ]

    bundle = GroupBundle(
        name="家庭友善蔬果箱",
        description="小白菜、玉米、地瓜與紅蘿蔔的固定團購套組。",
        member_price=320,
        nonmember_price=370,
        image_url="/assets/products/bok-choy.jpg",
        items=[
            GroupBundleItem(product_id=by_slug["bok-choy"].id, quantity=2),
            GroupBundleItem(product_id=by_slug["fruit-corn"].id, quantity=2),
            GroupBundleItem(product_id=by_slug["sweet-potato"].id, quantity=1),
            GroupBundleItem(product_id=by_slug["carrot"].id, quantity=1),
        ],
    )
    pantry_bundle = GroupBundle(
        name="安心常備食材組",
        description="白米、黑豆醬油與鳳梨果醬組合。",
        member_price=520,
        nonmember_price=600,
        image_url="/assets/products/rice.jpg",
        items=[
            GroupBundleItem(product_id=by_slug["rice"].id, quantity=1),
            GroupBundleItem(
                product_id=by_slug["black-bean-soy-sauce"].id, quantity=1
            ),
            GroupBundleItem(
                product_id=by_slug["pineapple-jam"].id, quantity=1
            ),
        ],
    )
    session.add_all([bundle, pantry_bundle])
    await session.flush()

    proposal = VoteProposal(
        proposer_id=users[1].id,
        target_type=TargetType.PRODUCT,
        target_id=by_slug["eggs"].id,
        target_name_snapshot=by_slug["eggs"].name,
        status=ProposalStatus.VOTING,
        threshold=10,
        deadline=now + timedelta(days=4),
        reviewed_by_id=users[0].id,
        reviewed_at=now - timedelta(days=1),
    )
    session.add(proposal)
    await session.flush()
    voters = users[1:10]
    session.add_all(
        Vote(
            proposal_id=proposal.id,
            user_id=user.id,
            estimated_quantity=2 if index < 3 else 1,
        )
        for index, user in enumerate(voters)
    )

    campaign = GroupCampaign(
        target_type=TargetType.BUNDLE,
        target_id=bundle.id,
        title="家庭友善蔬果箱共同購買",
        description="達 10 箱後由管理員確認成團。",
        image_url=bundle.image_url,
        member_price=320,
        nonmember_price=370,
        min_paid_quantity=10,
        supply_cap=30,
        per_user_cap=5,
        paid_quantity=9,
        deadline=now + timedelta(days=5),
        estimated_pickup_start=now + timedelta(days=7),
        estimated_pickup_end=now + timedelta(days=9),
        decision_status=GroupDecisionStatus.RECRUITING,
        intake_status=GroupIntakeStatus.OPEN,
        core_locked_at=now - timedelta(hours=2),
        threshold_version=3,
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=[
            ShippingChannel.HOME_DELIVERY.value,
            ShippingChannel.SEVEN_ELEVEN.value,
            ShippingChannel.FAMILY_MART.value,
            ShippingChannel.HILIFE.value,
        ],
        created_by_id=users[0].id,
    )
    session.add(campaign)
    await session.flush()

    seeded_orders = [
        Order(
            order_number="DEMO-GRP-001",
            order_kind=OrderKind.GROUP,
            sales_channel=SalesChannel.GROUP,
            fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
            user_id=users[1].id,
            group_campaign_id=campaign.id,
            membership_type_snapshot=MembershipType.MEMBER,
            amount_total=320 * 5,
            contact_email=users[1].email,
            payment_status=PaymentStatus.PAID,
            invoice_status=InvoiceStatus.NOT_ELIGIBLE,
            paid_at=now - timedelta(hours=2),
            items=[
                OrderItem(
                    source_bundle_id=bundle.id,
                    product_name=bundle.name,
                    unit_label="組",
                    quantity=5,
                    unit_price=320,
                    subtotal=1600,
                    tax_type=TaxType.TAX_EXEMPT,
                )
            ],
            fulfillment=OrderFulfillment(
                method=FulfillmentMethod.COOPERATIVE_PICKUP,
                status=FulfillmentState.PENDING_CONFIRMATION,
            ),
        ),
        Order(
            order_number="DEMO-GRP-002",
            order_kind=OrderKind.GROUP,
            sales_channel=SalesChannel.GROUP,
            fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
            user_id=users[2].id,
            group_campaign_id=campaign.id,
            membership_type_snapshot=MembershipType.NONMEMBER,
            amount_total=370 * 4,
            contact_email=users[2].email,
            payment_status=PaymentStatus.PAID,
            invoice_status=InvoiceStatus.NOT_ELIGIBLE,
            paid_at=now - timedelta(hours=1),
            items=[
                OrderItem(
                    source_bundle_id=bundle.id,
                    product_name=bundle.name,
                    unit_label="組",
                    quantity=4,
                    unit_price=370,
                    subtotal=1480,
                    tax_type=TaxType.TAX_EXEMPT,
                )
            ],
            fulfillment=OrderFulfillment(
                method=FulfillmentMethod.COOPERATIVE_PICKUP,
                status=FulfillmentState.PENDING_CONFIRMATION,
            ),
        ),
    ]
    session.add_all(seeded_orders)
    await session.flush()
    session.add_all(
        [
            InventoryReservation(
                order_id=seeded_orders[0].id,
                group_campaign_id=campaign.id,
                quantity=5,
                status=ReservationStatus.CONSUMED,
                expires_at=seeded_orders[0].paid_at,
                created_at=seeded_orders[0].paid_at,
            ),
            InventoryReservation(
                order_id=seeded_orders[1].id,
                group_campaign_id=campaign.id,
                quantity=4,
                status=ReservationStatus.CONSUMED,
                expires_at=seeded_orders[1].paid_at,
                created_at=seeded_orders[1].paid_at,
            ),
        ]
    )

    activity = Activity(
        id="activity-hike-demo",
        created_by_id=users[1].id,
        title="觀音山社員健行",
        description="社員一起走步道、分享沿途生態與合作社近況。",
        image_url="/assets/community/member-hike.png",
        location="觀音山遊客中心",
        starts_at=now + timedelta(days=6),
        ends_at=now + timedelta(days=6, hours=4),
        registration_deadline=now + timedelta(days=4),
        capacity=5,
        waitlist_enabled=True,
        status=ActivityStatus.PUBLISHED,
        reviewed_by_id=users[0].id,
        reviewed_at=now - timedelta(days=1),
        registrations=[
            ActivityRegistration(
                user_id=user.id,
                status=ActivityRegistrationStatus.REGISTERED,
                queue_position=index,
                registered_at=now - timedelta(hours=12 - index),
            )
            for index, user in enumerate(active_member_users[1:5], start=1)
        ],
    )
    member_proposal = MemberProposal(
        id="member-proposal-demo",
        created_by_id=users[1].id,
        title="每月安排一次產地共學日",
        body="建議每月由社員輪流提案一處合作農場，安排半日交流。",
        status=MemberProposalStatus.VOTING,
        minimum_voters=10,
        discussion_ends_at=now - timedelta(days=1),
        voting_ends_at=now + timedelta(days=5),
        reviewed_by_id=users[0].id,
        reviewed_at=now - timedelta(days=4),
        votes=[
            MemberProposalVote(
                user_id=user.id,
                choice=(
                    MemberVoteChoice.YES
                    if index < 7
                    else (
                        MemberVoteChoice.NO
                        if index < 9
                        else MemberVoteChoice.ABSTAIN
                    )
                ),
            )
            for index, user in enumerate(active_member_users)
        ],
    )
    session.add_all([activity, member_proposal])

    point_account = PointAccount(
        id="point-account-member-demo",
        user_id=users[1].id,
    )
    wish = Wish(
        id="wish-local-grains-demo",
        proposer_id=users[1].id,
        name="在地雜糧早餐組",
        description="希望一起尋找可定期供應的燕麥、芝麻與無糖豆粉組合。",
        expected_price=280,
        status=WishStatus.SOURCING,
        admin_note="已聯繫兩位友善耕作供應者，等待樣品與報價。",
        created_at=now - timedelta(days=5),
    )
    meeting = Meeting(
        id="meeting-affairs-demo",
        meeting_type=MeetingType.AFFAIRS,
        title="八月社務會議（展示）",
        agenda=[
            {"title": "秋季共同購買排程"},
            {"title": "社員活動與取貨站輪值"},
            {"title": "供應者審認進度"},
        ],
        starts_at=now - timedelta(days=12),
        location="合作社門市",
        created_by_id=users[0].id,
    )
    fiscal_year = FiscalYear(
        id="fiscal-year-demo",
        label=f"{now.year - 1} 年度（展示）",
        starts_on=date(now.year - 1, 1, 1),
        ends_on=date(now.year - 1, 12, 31),
        reserve_percentage=50,
        confirmed_at=now - timedelta(days=45),
        confirmed_by_id=users[0].id,
    )
    session.add_all([point_account, wish, meeting, fiscal_year])
    await session.flush()
    session.add_all(
        [
            PointTransaction(
                id="point-activity-demo",
                account_id=point_account.id,
                amount=80,
                source_type=PointSourceType.ACTIVITY,
                reference_id=activity.id,
                note="社員活動參與",
            ),
            PointTransaction(
                id="point-vote-demo",
                account_id=point_account.id,
                amount=120,
                source_type=PointSourceType.VOTE,
                reference_id=member_proposal.id,
                note="社員提案參與",
            ),
            *[
                WishSupport(
                    wish_id=wish.id,
                    user_id=user.id,
                    created_at=now - timedelta(days=4, hours=index),
                )
                for index, user in enumerate(active_member_users[:6])
            ],
            *[
                MeetingAttendance(
                    meeting_id=meeting.id,
                    member_id=user.id,
                    attended=True,
                    checked_in_at=meeting.starts_at,
                )
                for user in active_member_users[:7]
            ],
            MeetingResolution(
                meeting_id=meeting.id,
                title="九月試辦產地共學",
                resolution_text="由社務小組整理場地與交通方案，下次會議確認。",
            ),
            SurplusLedger(
                id="surplus-ledger-demo",
                fiscal_year_id=fiscal_year.id,
                total_revenue=480000,
                total_cost=420000,
                total_surplus=60000,
                reserve_amount=30000,
                distributable_surplus=30000,
            ),
            SurplusDistribution(
                fiscal_year_id=fiscal_year.id,
                member_id=users[1].id,
                contribution_amount=1600,
                contribution_basis_points=1067,
                distribution_amount=320,
            ),
        ]
    )

    meals = [build_meal(spec) for spec in OFFICIAL_MEALS]
    session.add_all(meals)
    await session.flush()
    meals_by_slug = {meal.slug: meal for meal in meals}
    pickup_meal = meals_by_slug[LUNCH_MEAL_SLUGS[0]]
    meal_event = MealEvent(
        id="meal-event-pickup-demo",
        title="今日午餐取餐",
        location="清華大學",
        ordering_starts_at=now - timedelta(days=2),
        ordering_ends_at=now - timedelta(hours=1),
        pickup_starts_at=now - timedelta(minutes=30),
        pickup_ends_at=now + timedelta(hours=2),
        status=MealEventStatus.PICKUP_OPEN,
        created_by_id=users[0].id,
        offerings=[
            MealEventOffering(
                id="meal-offering-pickup-demo",
                meal_id=pickup_meal.id,
                price=pickup_meal.price,
                capacity=50,
                paid_quantity=2,
                position=1,
            )
        ],
    )
    lunch_event = _build_demo_preorder_event(
        now,
        [meals_by_slug[slug] for slug in LUNCH_MEAL_SLUGS],
        users[0].id,
    )
    dinner_event = _build_demo_preorder_event(
        now,
        [meals_by_slug[slug] for slug in DINNER_MEAL_SLUGS],
        users[0].id,
        dinner=True,
    )
    session.add_all([meal_event, lunch_event, dinner_event])
    await session.flush()
    main_group = next(
        group for group in pickup_meal.option_groups if group.name == "主食選擇"
    )
    rice_option = next(
        option for option in main_group.options if option.name == "紫米飯"
    )
    meal_order = Order(
        id="order-meal-pickup-demo",
        order_number="DEMO-MEAL-001",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user_id=users[1].id,
        meal_event_id=meal_event.id,
        membership_type_snapshot=MembershipType.MEMBER,
        amount_total=240,
        contact_email=users[1].email,
        fulfillment_status=FulfillmentStatus.READY_FOR_PICKUP,
        payment_status=PaymentStatus.PAID,
        invoice_status=InvoiceStatus.NOT_ELIGIBLE,
        paid_at=now - timedelta(hours=3),
        items=[
            OrderItem(
                source_meal_offering_id="meal-offering-pickup-demo",
                product_name=pickup_meal.name,
                unit_label="份",
                quantity=2,
                unit_price=pickup_meal.price,
                subtotal=pickup_meal.price * 2,
                tax_type=TaxType.TAXABLE,
                selected_options=[
                    OrderItemOption(
                        source_meal_option_id=rice_option.id,
                        group_name=main_group.name,
                        option_name=rice_option.name,
                        price_delta=rice_option.price_delta,
                        position=0,
                    )
                ],
            )
        ],
        fulfillment=OrderFulfillment(
            method=FulfillmentMethod.EVENT_PICKUP,
            status=FulfillmentState.READY_FOR_PICKUP,
            pickup_location=meal_event.location,
            pickup_starts_at=meal_event.pickup_starts_at,
            pickup_ends_at=meal_event.pickup_ends_at,
            pickup_code="381642",
            pickup_qr_token_hash="c" * 64,
        ),
    )
    session.add(meal_order)

    shipping_rates = [
        ShippingRate(
            channel=channel,
            temperature=ShippingTemperature.AMBIENT,
            fee=(
                160
                if channel == ShippingChannel.HOME_DELIVERY
                else 70
            ),
            free_shipping_threshold=1500,
            effective_from=date(now.year, 1, 1),
        )
        for channel in (
            ShippingChannel.HOME_DELIVERY,
            ShippingChannel.SEVEN_ELEVEN,
            ShippingChannel.FAMILY_MART,
            ShippingChannel.HILIFE,
        )
    ]
    shipping_rates.extend(
        [
            ShippingRate(
                channel=ShippingChannel.HOME_DELIVERY,
                temperature=ShippingTemperature.CHILLED,
                fee=220,
                free_shipping_threshold=1500,
                effective_from=date(now.year, 1, 1),
            ),
            ShippingRate(
                channel=ShippingChannel.HOME_DELIVERY,
                temperature=ShippingTemperature.FROZEN,
                fee=260,
                free_shipping_threshold=1500,
                effective_from=date(now.year, 1, 1),
            ),
        ]
    )
    new_shipping_rates = []
    for shipping_rate in shipping_rates:
        existing_rate = await session.scalar(
            select(ShippingRate.id).where(
                ShippingRate.channel == shipping_rate.channel,
                ShippingRate.temperature == shipping_rate.temperature,
                ShippingRate.effective_from == shipping_rate.effective_from,
            )
        )
        if existing_rate is None:
            new_shipping_rates.append(shipping_rate)
    session.add_all(new_shipping_rates)

    fulfillment_id = "fulfillment-shipping-demo"
    shipping_aad_prefix = f"order-fulfillment:{fulfillment_id}"
    shipping_order = Order(
        id="order-shipping-demo",
        order_number="DEMO-SHIP-001",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.ECPAY_LOGISTICS,
        user_id=users[2].id,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=620,
        contact_email=users[2].email,
        fulfillment_status=FulfillmentStatus.PREPARING,
        payment_status=PaymentStatus.PAID,
        invoice_status=InvoiceStatus.NOT_ELIGIBLE,
        paid_at=now - timedelta(days=1),
        items=[
            OrderItem(
                source_product_id=by_slug["rice"].id,
                product_name=by_slug["rice"].name,
                unit_label=by_slug["rice"].unit,
                quantity=1,
                unit_price=250,
                subtotal=250,
                tax_type=TaxType.TAX_EXEMPT,
            ),
            OrderItem(
                source_product_id=by_slug["black-bean-soy-sauce"].id,
                product_name=by_slug["black-bean-soy-sauce"].name,
                unit_label=by_slug["black-bean-soy-sauce"].unit,
                quantity=1,
                unit_price=210,
                subtotal=210,
                tax_type=TaxType.TAXABLE,
            ),
        ],
        fulfillment=OrderFulfillment(
            id=fulfillment_id,
            method=FulfillmentMethod.ECPAY_LOGISTICS,
            status=FulfillmentState.SHIPPED,
            recipient_name_encrypted=seed_cipher.encrypt_text(
                "測試收件人",
                associated_data=f"{shipping_aad_prefix}:recipient_name",
            ),
            recipient_phone_encrypted=seed_cipher.encrypt_text(
                "0912345678",
                associated_data=f"{shipping_aad_prefix}:recipient_phone",
            ),
            shipping_address_encrypted=seed_cipher.encrypt_text(
                "Sandbox 測試配送地址",
                associated_data=f"{shipping_aad_prefix}:shipping_address",
            ),
            encryption_key_version=seed_cipher.current_version,
            shipment=Shipment(
                channel=ShippingChannel.HOME_DELIVERY,
                temperature=ShippingTemperature.AMBIENT,
                status=ShipmentStatus.IN_TRANSIT,
                shipping_fee=160,
                ecpay_logistics_id="DEMO-STAGE-LOGISTICS-001",
                tracking_number="STAGE-DEMO-0001",
                provider_payload={
                    "LogisticsStatusName": "配送中（Sandbox 展示）"
                },
            ),
        ),
    )
    session.add(shipping_order)

    session.add(
        Notification(
            user_id=users[1].id,
            event_type="proposal.approved",
            title="團購投票已通過審核",
            body="放牧雞蛋已開始投票，目前 9/10 人。",
            data={"proposal_id": proposal.id},
        )
    )
    await session.commit()
    return {
        "users": len(users),
        "products": len(products),
        "campaigns": 1,
        "suppliers": len(suppliers),
        "pickup_locations": len(pickup_locations),
    }


async def sync_preview_demo_data(
    session: AsyncSession,
    settings: Settings,
    now: datetime,
) -> None:
    if settings.environment.strip().lower() not in {"development", "preview"}:
        return

    demo_passwords = {
        "admin@shilifangyuan.tw": settings.demo_admin_password,
        "member@shilifangyuan.tw": settings.demo_member_password,
        "customer@shilifangyuan.tw": settings.demo_nonmember_password,
    }
    demo_users = list(
        await session.scalars(
            select(User).where(User.email.in_(demo_passwords))
        )
    )
    for user in demo_users:
        password = demo_passwords[user.email]
        if not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password)

    await _sync_preview_suppliers(session, settings, now)
    await _sync_preview_products(session)
    meals_by_slug = await _sync_preview_meals(session)

    existing_roster = await session.scalar(
        select(MemberRosterEntry.id).where(
            MemberRosterEntry.member_number == DEMO_ROSTER_NUMBER
        )
    )
    if existing_roster is None:
        session.add(
            _build_demo_roster_entry(pii_cipher_from_settings(settings))
        )

    admin_id = await session.scalar(
        select(User.id).where(User.user_role == UserRole.ADMIN)
    )
    if admin_id is not None:
        await _sync_preview_meal_event(
            session,
            now,
            admin_id,
            [meals_by_slug[slug] for slug in LUNCH_MEAL_SLUGS],
        )
        await _sync_preview_meal_event(
            session,
            now,
            admin_id,
            [meals_by_slug[slug] for slug in DINNER_MEAL_SLUGS],
            dinner=True,
        )
    await session.commit()


async def _sync_preview_suppliers(
    session: AsyncSession,
    settings: Settings,
    now: datetime,
) -> None:
    supplier_ids = [str(item["id"]) for item in SUPPLIER_DEMOS]
    existing_ids = set(
        await session.scalars(
            select(Supplier.id).where(Supplier.id.in_(supplier_ids))
        )
    )
    missing = [
        item for item in SUPPLIER_DEMOS if str(item["id"]) not in existing_ids
    ]
    if not missing:
        return

    reviewer_id = await session.scalar(
        select(User.id).where(User.user_role == UserRole.ADMIN).limit(1)
    )
    if reviewer_id is None:
        reviewer_id = await session.scalar(select(User.id).limit(1))
    if reviewer_id is None:
        return

    seed_cipher = pii_cipher_from_settings(settings)
    session.add_all(
        _build_demo_supplier(item, seed_cipher, reviewer_id, now)
        for item in missing
    )
    await session.flush()


async def _sync_preview_products(session: AsyncSession) -> None:
    existing = {
        product.slug: product
        for product in await session.scalars(
            select(Product).where(
                Product.slug.in_([item["slug"] for item in PRODUCTS])
            )
        )
    }
    for index, data in enumerate(PRODUCTS, start=1):
        slug = str(data["slug"])
        product = existing.get(slug)
        if product is None:
            product = Product(
                **data,
                product_number=f"P-{index:04d}",
                sku=slug.upper(),
                supplier_id=_product_supplier_id(data),
            )
            session.add(product)
            continue
        for field, value in data.items():
            if field != "slug":
                setattr(product, field, value)
    bundle_images = {
        "家庭友善蔬果箱": "/assets/products/bok-choy.jpg",
        "安心常備食材組": "/assets/products/rice.jpg",
    }
    bundles = await session.scalars(
        select(GroupBundle).where(GroupBundle.name.in_(bundle_images))
    )
    for bundle in bundles:
        bundle.image_url = bundle_images[bundle.name]


async def _sync_preview_meals(session: AsyncSession) -> dict[str, Meal]:
    official_slugs = [spec.slug for spec in OFFICIAL_MEALS]
    existing = {
        meal.slug: meal
        for meal in await session.scalars(
            select(Meal)
            .where(Meal.slug.in_(official_slugs))
            .options(
                selectinload(Meal.option_groups).selectinload(
                    MealOptionGroup.options
                )
            )
        )
    }
    for spec in OFFICIAL_MEALS:
        meal = existing.get(spec.slug)
        if meal is None:
            meal = build_meal(spec)
            session.add(meal)
            existing[spec.slug] = meal
            continue
        meal.name = spec.name
        meal.description = spec.description
        meal.image_url = spec.image_url
        meal.price = spec.price
        meal.tax_type = TaxType.TAXABLE
        meal.is_active = True
        _sync_meal_options(meal, spec)

    old_meals = await session.scalars(
        select(Meal).where(
            Meal.slug.in_(
                ["seasonal-coop-lunchbox", "vegetarian-coop-lunchbox"]
            )
        )
    )
    for meal in old_meals:
        meal.is_active = False
    legacy_events = await session.scalars(
        select(MealEvent).where(
            MealEvent.title.in_(
                ["今日展示便當（虛擬資料）", "校園週四便當預購"]
            ),
            MealEvent.id.notin_(
                [DEMO_PREORDER_EVENT_ID, DEMO_DINNER_EVENT_ID]
            ),
        )
    )
    for event in legacy_events:
        event.status = MealEventStatus.COMPLETED
    await session.flush()
    return existing


def _sync_meal_options(meal: Meal, spec: MealSpec) -> None:
    groups_by_name = {group.name: group for group in meal.option_groups}
    active_group_names = {group.name for group in spec.option_groups}
    for position, group_spec in enumerate(spec.option_groups):
        group = groups_by_name.get(group_spec.name)
        if group is None:
            group = build_option_groups(spec)[position]
            meal.option_groups.append(group)
        group.min_selections = group_spec.min_selections
        group.max_selections = group_spec.max_selections
        group.position = position
        group.is_active = True
        options_by_name = {option.name: option for option in group.options}
        active_option_names = {option.name for option in group_spec.options}
        for option_position, option_spec in enumerate(group_spec.options):
            option = options_by_name.get(option_spec.name)
            if option is None:
                option = MealOption(name=option_spec.name)
                group.options.append(option)
            option.price_delta = option_spec.price_delta
            option.position = option_position
            option.is_active = True
        for option in group.options:
            if option.name not in active_option_names:
                option.is_active = False
    for group in meal.option_groups:
        if group.name not in active_group_names:
            group.is_active = False


async def _sync_preview_meal_event(
    session: AsyncSession,
    now: datetime,
    admin_id: str,
    meals: list[Meal],
    *,
    dinner: bool = False,
) -> None:
    event_id = DEMO_DINNER_EVENT_ID if dinner else DEMO_PREORDER_EVENT_ID
    event = await session.scalar(
        select(MealEvent)
        .where(MealEvent.id == event_id)
        .options(selectinload(MealEvent.offerings))
    )
    template = _build_demo_preorder_event(
        now,
        meals,
        admin_id,
        dinner=dinner,
    )
    if event is None:
        await session.execute(
            delete(MealEventOffering).where(
                MealEventOffering.meal_event_id == event_id
            )
        )
        session.add(template)
        return

    event.title = template.title
    event.location = template.location
    event.created_by_id = admin_id
    pickup_ends_at = event.pickup_ends_at
    if pickup_ends_at.tzinfo is None:
        pickup_ends_at = pickup_ends_at.replace(tzinfo=timezone.utc)
    if pickup_ends_at <= now or event.status in {
        MealEventStatus.CANCELLED,
        MealEventStatus.COMPLETED,
    }:
        event.ordering_starts_at = template.ordering_starts_at
        event.ordering_ends_at = template.ordering_ends_at
        event.pickup_starts_at = template.pickup_starts_at
        event.pickup_ends_at = template.pickup_ends_at
        event.status = MealEventStatus.PUBLISHED
        event.cancelled_at = None
        event.cancellation_reason = None

    offerings_by_meal = {
        offering.meal_id: offering for offering in event.offerings
    }
    active_meal_ids = {meal.id for meal in meals}
    for position, meal in enumerate(meals, start=1):
        offering = offerings_by_meal.get(meal.id)
        if offering is None:
            offering = MealEventOffering(
                meal_id=meal.id,
                price=meal.price,
                capacity=50,
            )
            event.offerings.append(offering)
        offering.price = meal.price
        offering.capacity = max(
            50,
            offering.reserved_quantity + offering.paid_quantity,
        )
        offering.position = position
        offering.is_active = True
    for offering in event.offerings:
        if offering.meal_id not in active_meal_ids:
            offering.is_active = False


async def reset_demo_data(session: AsyncSession) -> dict[str, int]:
    document_keys = list(
        await session.scalars(
            select(MembershipDocument.object_key).where(
                MembershipDocument.object_key.like(
                    "membership-documents/%"
                )
            )
        )
    )
    if document_keys:
        try:
            storage = r2_document_storage_from_settings(get_settings())
            await storage.delete_documents(document_keys)
        except IntegrationError:
            if get_settings().environment.strip().lower() in {
                "sandbox",
                "production",
            }:
                raise
    merchant_trade_numbers = list(
        await session.scalars(select(PaymentAttempt.merchant_trade_no))
    )
    existing_tombstones = set(
        await session.scalars(
            select(ExternalEvent.external_event_key).where(
                ExternalEvent.provider == "ecpay_reset",
                ExternalEvent.external_event_key.in_(merchant_trade_numbers),
            )
        )
    )
    tombstone_time = datetime.now(timezone.utc)
    session.add_all(
        ExternalEvent(
            provider="ecpay_reset",
            external_event_key=merchant_trade_no,
            event_type="payment_tombstone",
            payload={"merchant_trade_no": merchant_trade_no},
            processed=True,
            processed_at=tombstone_time,
        )
        for merchant_trade_no in merchant_trade_numbers
        if merchant_trade_no not in existing_tombstones
    )
    await session.flush()
    tables = [
        AdminAudit,
        MemberBadge,
        BadgeDefinition,
        WishSupport,
        Wish,
        MeetingResolution,
        MeetingAttendance,
        Meeting,
        PointTransaction,
        PointAccount,
        EducationAttempt,
        EducationQuestion,
        EducationLecture,
        SurplusDistribution,
        SurplusLedger,
        FiscalYear,
        SystemSetting,
        OutboxEvent,
        Notification,
        Invoice,
        Refund,
        InventoryReservation,
        PaymentAttempt,
        Shipment,
        OrderFulfillment,
        OrderItemOption,
        OrderItem,
        Order,
        MealEventOffering,
        MealEvent,
        MealOption,
        MealOptionGroup,
        Meal,
        ShippingRate,
        ActivityRegistration,
        Activity,
        MemberProposalVote,
        MemberProposalComment,
        MemberProposal,
        GroupCampaign,
        Vote,
        VoteProposal,
        GroupBundleItem,
        GroupBundle,
        Product,
        SupplierDocument,
        SupplierAccreditation,
        Supplier,
        PickupLocation,
        MemberRosterEntry,
        MemberDirectoryEntry,
        MembershipCharge,
        MembershipFeeSchedule,
        MembershipDocument,
        Membership,
        MembershipApplication,
        MemberProfile,
        EmailVerificationToken,
        PasswordResetToken,
        User,
    ]
    for model in tables:
        await session.execute(delete(model))
    await session.commit()
    session.expunge_all()
    return await seed_demo_data(session)


async def main() -> None:
    async with SessionLocal() as session:
        counts = await seed_demo_data(session)
    print(
        "Seed complete: users={users}, products={products}, "
        "campaigns={campaigns}, suppliers={suppliers}, "
        "pickup_locations={pickup_locations}".format(**counts)
    )


if __name__ == "__main__":
    asyncio.run(main())
