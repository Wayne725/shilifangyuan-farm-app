from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import hash_password
from .config import get_settings
from .database import SessionLocal
from .integrations.common import IntegrationError
from .integrations.pii_crypto import (
    VersionedPIICipher,
    pii_cipher_from_settings,
)
from .integrations.r2_storage import r2_document_storage_from_settings
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
    MemberDirectoryEntry,
    MemberBadge,
    MemberProfile,
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
    OrderKind,
    OutboxEvent,
    PasswordResetToken,
    PaymentAttempt,
    PaymentStatus,
    PointAccount,
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
    ExternalEvent,
)


DEMO_PASSWORD = "member123"


EDUCATION_CONTENT = [
    ("一人一票，營業而不以營利為目的", "合作社以共同需要為核心。社員不因出資較多而取得更多表決權，每位社員均為一票。", "合作社社員的表決權如何計算？", ["依持股比例", "一人一票", "依消費金額"], 1),
    ("社員是主人，不只是顧客", "社員共同擁有、參與治理並監督合作社，也以消費支持共同事業。", "社員與一般顧客最大的不同是？", ["可參與治理並共同負責", "永遠享有最低價", "不需要遵守章程"], 0),
    ("認購社股與學生保護", "社股是合作事業的共同資本，不等同購物金；學生社員的權益與負擔須依章程及適用規範保護。", "社股最接近下列何者？", ["購物折價券", "共同事業的出資", "訂單退款"], 1),
    ("結餘提撥與消費回饋", "年度結餘先提撥合作資本或公積金，剩餘部分依社員消費貢獻度分配，而非依持股比例。", "可分配結餘的分配基礎是？", ["持股比例", "年齡", "消費貢獻度"], 2),
    ("社員與免稅農產品", "一級農產品與加工食品的稅務分類不同；社員福利不會改變商品本身的稅務分類。", "商品稅別主要依據什麼？", ["社員身分", "商品性質", "付款方式"], 1),
]


async def seed_cooperative_data(session: AsyncSession) -> None:
    if await session.scalar(select(EducationLecture.id).limit(1)) is not None:
        return
    for position, (title, body, prompt, options, correct) in enumerate(EDUCATION_CONTENT, start=1):
        lecture = EducationLecture(title=title, body=body, position=position)
        session.add(lecture)
        await session.flush()
        session.add(EducationQuestion(lecture_id=lecture.id, prompt=prompt, options=options, correct_option=correct))
    await session.commit()


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
        "image_url": "/assets/products/bok-choy.png",
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
        "image_url": "/assets/products/fruit-corn.png",
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
        "image_url": "/assets/products/sweet-potato.png",
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
        "image_url": "/assets/products/tomatoes.png",
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
        "image_url": "/assets/products/rice.png",
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
        "image_url": "/assets/products/eggs.png",
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
        "image_url": "/assets/products/black-bean-soy-sauce.png",
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
        "image_url": "/assets/products/pineapple-jam.png",
    },
]


async def seed_demo_data(session: AsyncSession) -> Dict[str, int]:
    await seed_cooperative_data(session)
    existing = await session.scalar(select(User.id).limit(1))
    if existing is not None:
        return {"users": 0, "products": 0, "campaigns": 0}

    settings = get_settings()
    now = datetime.now(timezone.utc)
    admin_password_hash = hash_password(settings.demo_admin_password)
    member_password_hash = hash_password(settings.demo_member_password)
    nonmember_password_hash = hash_password(settings.demo_nonmember_password)
    users: List[User] = [
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
    session.add_all(users)
    await session.flush()

    try:
        seed_cipher = pii_cipher_from_settings(settings)
    except IntegrationError:
        seed_cipher = VersionedPIICipher({"v1": bytes(32)}, "v1")

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

    products = [Product(**data) for data in PRODUCTS]
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
        image_url="/assets/products/bok-choy.png",
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
        image_url="/assets/products/rice.png",
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

    meals = [
        Meal(
            id="meal-seasonal-demo",
            slug="seasonal-coop-lunchbox",
            name="時蔬合作便當",
            description="白飯、當季時蔬、豆腐與友善契作主菜。",
            image_url="/assets/meals/taiwanese-lunchbox.png",
            price=120,
            tax_type=TaxType.TAXABLE,
        ),
        Meal(
            id="meal-veggie-demo",
            slug="vegetarian-coop-lunchbox",
            name="田園蔬食便當",
            description="五色蔬菜與黑豆時蔬，清爽不含肉類。",
            image_url="/assets/meals/taiwanese-lunchbox.png",
            price=110,
            tax_type=TaxType.TAXABLE,
        ),
    ]
    session.add_all(meals)
    await session.flush()
    meal_event = MealEvent(
        id="meal-event-pickup-demo",
        title="校園週四便當預購",
        location="學校圖書館前合作社攤位",
        ordering_starts_at=now - timedelta(days=2),
        ordering_ends_at=now - timedelta(hours=1),
        pickup_starts_at=now - timedelta(minutes=30),
        pickup_ends_at=now + timedelta(hours=2),
        status=MealEventStatus.PICKUP_OPEN,
        created_by_id=users[0].id,
        offerings=[
            MealEventOffering(
                id="meal-offering-seasonal-demo",
                meal_id=meals[0].id,
                price=120,
                capacity=30,
                paid_quantity=2,
                position=1,
            ),
            MealEventOffering(
                id="meal-offering-veggie-demo",
                meal_id=meals[1].id,
                price=110,
                capacity=20,
                paid_quantity=0,
                position=2,
            ),
        ],
    )
    session.add(meal_event)
    await session.flush()
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
                source_meal_offering_id="meal-offering-seasonal-demo",
                product_name=meals[0].name,
                unit_label="份",
                quantity=2,
                unit_price=120,
                subtotal=240,
                tax_type=TaxType.TAXABLE,
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
    }


async def reset_demo_data(session: AsyncSession) -> Dict[str, int]:
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
        OrderItem,
        Order,
        MealEventOffering,
        MealEvent,
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
        "campaigns={campaigns}".format(**counts)
    )


if __name__ == "__main__":
    asyncio.run(main())
