from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import hash_password
from .config import get_settings
from .database import SessionLocal
from .models import (
    AdminAudit,
    FulfillmentStatus,
    GroupBundle,
    GroupBundleItem,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InventoryReservation,
    Invoice,
    InvoiceStatus,
    MembershipType,
    Notification,
    Order,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentAttempt,
    PaymentStatus,
    Product,
    ProposalStatus,
    ReservationStatus,
    Refund,
    TargetType,
    TaxType,
    User,
    UserRole,
    Vote,
    VoteProposal,
    ExternalEvent,
)


DEMO_PASSWORD = "member123"


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
    existing = await session.scalar(select(User.id).limit(1))
    if existing is not None:
        return {"users": 0, "products": 0, "campaigns": 0}

    settings = get_settings()
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
        ),
        User(
            id="user-member",
            email="member@shilifangyuan.tw",
            display_name="社員小方",
            password_hash=member_password_hash,
            membership_type=MembershipType.MEMBER,
        ),
        User(
            id="user-customer",
            email="customer@shilifangyuan.tw",
            display_name="一般消費者",
            password_hash=nonmember_password_hash,
            membership_type=MembershipType.NONMEMBER,
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
        )
        for number in range(1, 9)
    )
    session.add_all(users)
    await session.flush()

    products = [Product(**data) for data in PRODUCTS]
    session.add_all(products)
    await session.flush()
    by_slug = {product.slug: product for product in products}

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

    now = datetime.now(timezone.utc)
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
        created_by_id=users[0].id,
    )
    session.add(campaign)
    await session.flush()

    seeded_orders = [
        Order(
            order_number="DEMO-GRP-001",
            order_kind=OrderKind.GROUP,
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
        ),
        Order(
            order_number="DEMO-GRP-002",
            order_kind=OrderKind.GROUP,
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
        OutboxEvent,
        Notification,
        Invoice,
        Refund,
        InventoryReservation,
        PaymentAttempt,
        OrderItem,
        Order,
        GroupCampaign,
        Vote,
        VoteProposal,
        GroupBundleItem,
        GroupBundle,
        Product,
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
