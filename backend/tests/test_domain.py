from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.auth import (
    decode_token,
    hash_password,
    make_token_pair,
    require_admin,
    verify_password,
)
from app.config import Settings
from app.domain import (
    DomainError,
    apply_paid_quantity,
    campaign_available_quantity,
    confirm_campaign,
    live_proposal_status,
    order_available_actions,
    price_for_membership,
    validate_group_join,
)
from app.models import (
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InventoryReservation,
    ExternalEvent,
    FulfillmentMethod,
    FulfillmentState,
    Membership,
    MembershipChargeKind,
    MembershipFeeSchedule,
    MembershipStatus,
    MembershipType,
    MealEvent,
    MealEventStatus,
    Meeting,
    Order,
    OrderFulfillment,
    OrderKind,
    PaymentStatus,
    PaymentAttempt,
    PickupLocation,
    PointTransaction,
    Product,
    ProposalStatus,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
    Shipment,
    ShipmentStatus,
    Supplier,
    SurplusDistribution,
    TargetType,
    ReservationStatus,
    SalesChannel,
    User,
    UserRole,
    VoteProposal,
    Wish,
)
from app.seed import DEMO_PASSWORD, reset_demo_data, seed_demo_data
from app.routers.orders import request_order_refund
from app.schemas import BundleCreate, BundleItemInput, CampaignCreate


NOW = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)


def make_user() -> User:
    user = User(
        id="user-1",
        email="member@example.com",
        display_name="社員",
        password_hash=hash_password("password123"),
        user_role=UserRole.CUSTOMER,
        membership_type=MembershipType.MEMBER,
    )
    user.membership = Membership(
        id="membership-user-1",
        user_id=user.id,
        status=MembershipStatus.ACTIVE,
    )
    return user


def make_campaign(**overrides) -> GroupCampaign:
    values = {
        "id": "campaign-1",
        "target_type": TargetType.PRODUCT,
        "target_id": "product-1",
        "title": "放牧雞蛋團購",
        "member_price": 100,
        "nonmember_price": 120,
        "min_paid_quantity": 10,
        "supply_cap": 20,
        "per_user_cap": 5,
        "paid_quantity": 8,
        "reserved_quantity": 1,
        "deadline": NOW + timedelta(days=1),
        "estimated_pickup_start": NOW + timedelta(days=2),
        "estimated_pickup_end": NOW + timedelta(days=3),
        "decision_status": GroupDecisionStatus.RECRUITING,
        "intake_status": GroupIntakeStatus.OPEN,
        "created_by_id": "admin-1",
    }
    values.update(overrides)
    return GroupCampaign(**values)


def test_membership_price_is_server_selected() -> None:
    assert price_for_membership(80, 100, MembershipType.MEMBER) == 80
    assert price_for_membership(80, 100, MembershipType.NONMEMBER) == 100


def test_bundle_and_campaign_reject_member_price_above_public_price() -> None:
    with pytest.raises(ValidationError, match="社員價不可高於非社員價"):
        BundleCreate(
            name="價格錯誤套組",
            member_price=150,
            nonmember_price=120,
            items=[BundleItemInput(product_id="product-1", quantity=1)],
        )

    with pytest.raises(ValidationError, match="社員價不可高於非社員價"):
        CampaignCreate(
            target_type=TargetType.PRODUCT,
            target_id="product-1",
            title="價格錯誤團購",
            member_price=150,
            nonmember_price=120,
            min_paid_quantity=10,
            supply_cap=20,
            deadline=NOW + timedelta(days=1),
            estimated_pickup_start=NOW + timedelta(days=2),
            estimated_pickup_end=NOW + timedelta(days=3),
        )


def test_campaign_shipping_contract_is_normalized_and_validated() -> None:
    base_payload = {
        "target_type": TargetType.PRODUCT,
        "target_id": "product-1",
        "title": "可配送團購",
        "member_price": 100,
        "nonmember_price": 120,
        "min_paid_quantity": 10,
        "supply_cap": 20,
        "deadline": NOW + timedelta(days=1),
        "estimated_pickup_start": NOW + timedelta(days=2),
        "estimated_pickup_end": NOW + timedelta(days=3),
    }

    pickup_only = CampaignCreate(
        **base_payload,
        can_ship=False,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=[ShippingChannel.HOME_DELIVERY],
    )
    assert pickup_only.shipping_temperature is None
    assert pickup_only.allowed_shipping_channels == []

    with pytest.raises(
        ValidationError,
        match="可配送團購必須設定溫層與至少一個物流通路",
    ):
        CampaignCreate(**base_payload, can_ship=True)

    with pytest.raises(
        ValidationError,
        match="冷藏或冷凍團購只支援宅配",
    ):
        CampaignCreate(
            **base_payload,
            can_ship=True,
            shipping_temperature=ShippingTemperature.CHILLED,
            allowed_shipping_channels=[ShippingChannel.SEVEN_ELEVEN],
        )

    chilled_delivery = CampaignCreate(
        **base_payload,
        can_ship=True,
        shipping_temperature=ShippingTemperature.CHILLED,
        allowed_shipping_channels=[ShippingChannel.HOME_DELIVERY],
    )
    assert chilled_delivery.can_ship is True
    assert chilled_delivery.shipping_temperature == ShippingTemperature.CHILLED
    assert chilled_delivery.allowed_shipping_channels == [
        ShippingChannel.HOME_DELIVERY
    ]


def test_vote_status_freezes_at_deadline() -> None:
    proposal = VoteProposal(
        status=ProposalStatus.VOTING,
        threshold=10,
        deadline=NOW,
    )
    assert (
        live_proposal_status(proposal, vote_count=9, now=NOW)
        == ProposalStatus.ENDED_UNMET
    )
    assert (
        live_proposal_status(proposal, vote_count=10, now=NOW)
        == ProposalStatus.CONVERSION_PENDING
    )


def test_join_enforces_person_and_supply_caps() -> None:
    campaign = make_campaign()
    assert campaign_available_quantity(campaign) == 11
    validate_group_join(campaign, quantity=2, user_committed_quantity=2, now=NOW)
    with pytest.raises(DomainError, match="每人最多"):
        validate_group_join(
            campaign, quantity=4, user_committed_quantity=2, now=NOW
        )
    with pytest.raises(DomainError, match="剩餘供應"):
        campaign.per_user_cap = 20
        validate_group_join(
            campaign, quantity=12, user_committed_quantity=0, now=NOW
        )


def test_paid_threshold_pauses_until_admin_confirmation() -> None:
    campaign = make_campaign()
    apply_paid_quantity(campaign, quantity=2, now=NOW)
    assert campaign.paid_quantity == 10
    assert campaign.reserved_quantity == 0
    assert campaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION
    assert campaign.intake_status == GroupIntakeStatus.PAUSED
    confirm_campaign(campaign, NOW + timedelta(days=2), now=NOW)
    assert campaign.decision_status == GroupDecisionStatus.CONFIRMED
    assert campaign.intake_status == GroupIntakeStatus.OPEN


def test_regular_paid_order_can_cancel_only_before_preparing() -> None:
    order = Order(
        id="order-1",
        order_number="TEST001",
        order_kind=OrderKind.REGULAR,
        user_id="user-1",
        membership_type_snapshot=MembershipType.MEMBER,
        amount_total=100,
        contact_email="member@example.com",
        payment_status=PaymentStatus.PAID,
        fulfillment_status="pending_confirmation",
    )
    assert "cancel" in order_available_actions(order, now=NOW)
    order.fulfillment_status = "preparing"
    assert "cancel" not in order_available_actions(order, now=NOW)


def test_admin_fulfillment_actions_match_route_guards() -> None:
    order = Order(
        id="order-actions",
        order_number="TEST-ACTIONS",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user_id="user-1",
        membership_type_snapshot=MembershipType.MEMBER,
        amount_total=100,
        contact_email="member@example.com",
        payment_status=PaymentStatus.PAID,
        fulfillment_status="pending_confirmation",
    )
    assert "start_preparing" in order_available_actions(order, True, NOW)

    order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    assert "start_preparing" in order_available_actions(order, True, NOW)
    order.fulfillment_status = "preparing"
    order.fulfillment = OrderFulfillment(
        id="fulfillment-actions",
        order_id=order.id,
        method=FulfillmentMethod.ECPAY_LOGISTICS,
        status=FulfillmentState.PREPARING,
    )
    order.fulfillment.shipment = Shipment(
        id="shipment-actions",
        order_fulfillment_id=order.fulfillment.id,
        channel=ShippingChannel.HOME_DELIVERY,
        temperature=ShippingTemperature.AMBIENT,
        status=ShipmentStatus.READY_TO_CREATE,
        shipping_fee=160,
    )
    assert "create_shipment" in order_available_actions(order, True, NOW)
    order.fulfillment.shipment.status = ShipmentStatus.CREATED
    assert "advance_shipment" in order_available_actions(order, True, NOW)

    order.fulfillment_method = FulfillmentMethod.EVENT_PICKUP
    order.fulfillment_status = "pending_confirmation"
    order.order_kind = OrderKind.REGULAR
    order.sales_channel = SalesChannel.MEAL_PREORDER
    assert "start_preparing" not in order_available_actions(order, True, NOW)

    order.fulfillment_method = FulfillmentMethod.COOPERATIVE_PICKUP
    order.order_kind = OrderKind.GROUP
    order.sales_channel = SalesChannel.GROUP
    order.group_campaign = make_campaign(
        decision_status=GroupDecisionStatus.PENDING_CONFIRMATION,
    )
    assert "start_preparing" not in order_available_actions(order, True, NOW)

    order.group_campaign.decision_status = GroupDecisionStatus.CONFIRMED
    assert "start_preparing" in order_available_actions(order, True, NOW)


def test_password_hash_and_jwt_round_trip() -> None:
    user = make_user()
    assert user.password_hash.startswith("pbkdf2_sha256$210000$")
    assert verify_password("password123", user.password_hash)
    assert not verify_password("wrong-password", user.password_hash)
    tokens = make_token_pair(user)
    payload = decode_token(tokens["access_token"], "access")
    assert payload["sub"] == user.id
    assert payload["membership"] == MembershipType.MEMBER.value


@pytest.mark.asyncio
async def test_customer_cannot_use_admin_dependency() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(make_user())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_resettable(database_session) -> None:
    first = await seed_demo_data(database_session)
    second = await seed_demo_data(database_session)
    product_count = await database_session.scalar(select(func.count(Product.id)))
    assert first["products"] == 12
    assert first["suppliers"] == 3
    assert first["pickup_locations"] == 5
    assert second["products"] == 0
    assert product_count == 12
    assert await database_session.scalar(select(func.count(Supplier.id))) == 3
    assert await database_session.scalar(
        select(func.count(PickupLocation.id))
    ) == 5
    assert await database_session.scalar(
        select(func.count(Product.id)).where(
            Product.product_number.is_(None)
        )
    ) == 0
    assert await database_session.scalar(
        select(func.count(Product.id)).where(Product.supplier_id.is_(None))
    ) == 0
    assert await database_session.scalar(
        select(func.count(PointTransaction.id))
    ) == 2
    assert await database_session.scalar(select(func.count(Wish.id))) == 1
    assert await database_session.scalar(select(func.count(Meeting.id))) == 1
    assert await database_session.scalar(select(func.count(MealEvent.id))) == 2
    assert await database_session.scalar(
        select(func.count(MealEvent.id)).where(
            MealEvent.status == MealEventStatus.PUBLISHED
        )
    ) == 1
    assert await database_session.scalar(
        select(func.count(SurplusDistribution.id))
    ) == 1
    seeded_user = await database_session.scalar(
        select(User).where(User.email == "member@shilifangyuan.tw")
    )
    assert verify_password(DEMO_PASSWORD, seeded_user.password_hash)
    assert seeded_user.customer_number.startswith("SLF-C-")
    seeded_membership = await database_session.scalar(
        select(Membership).where(Membership.user_id == seeded_user.id)
    )
    assert seeded_membership.share_capital_amount == 1000
    assert seeded_membership.share_count == 10
    reset = await reset_demo_data(database_session)
    assert reset["products"] == 12
    assert reset["suppliers"] == 3
    assert reset["pickup_locations"] == 5
    assert await database_session.scalar(select(func.count(Product.id))) == 12


@pytest.mark.asyncio
async def test_seed_reuses_reference_data_created_by_migration(
    database_session,
) -> None:
    database_session.add_all(
        [
            MembershipFeeSchedule(
                id="migration-admission",
                charge_kind=MembershipChargeKind.ADMISSION_FEE,
                amount=500,
                effective_from=date(2026, 1, 1),
            ),
            MembershipFeeSchedule(
                id="migration-share",
                charge_kind=MembershipChargeKind.SHARE_CAPITAL,
                amount=1000,
                effective_from=date(2026, 1, 1),
            ),
            PickupLocation(
                id="pickup-coop-store",
                code="coop-store",
                name="合作社門市（水木書苑內左側）",
                address="",
                instructions="",
                sort_order=10,
            ),
            *[
                ShippingRate(
                    channel=channel,
                    temperature=temperature,
                    fee=70,
                    free_shipping_threshold=1500,
                    effective_from=date(2026, 1, 1),
                )
                for channel, temperature in (
                    (ShippingChannel.HOME_DELIVERY, ShippingTemperature.AMBIENT),
                    (ShippingChannel.SEVEN_ELEVEN, ShippingTemperature.AMBIENT),
                    (ShippingChannel.FAMILY_MART, ShippingTemperature.AMBIENT),
                    (ShippingChannel.HILIFE, ShippingTemperature.AMBIENT),
                    (ShippingChannel.HOME_DELIVERY, ShippingTemperature.CHILLED),
                    (ShippingChannel.HOME_DELIVERY, ShippingTemperature.FROZEN),
                )
            ],
        ]
    )
    await database_session.commit()

    result = await seed_demo_data(database_session)

    assert result["users"] > 0
    assert await database_session.scalar(
        select(func.count(MembershipFeeSchedule.id))
    ) == 2
    assert await database_session.scalar(select(func.count(ShippingRate.id))) == 6
    assert await database_session.scalar(
        select(func.count(PickupLocation.id))
    ) == 5


@pytest.mark.asyncio
async def test_preview_seed_syncs_new_fixture_and_demo_passwords(
    database_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_demo_data(database_session)
    preview_settings = Settings(
        _env_file=None,
        environment="preview",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_admin_password="preview-admin-password",
        demo_member_password="preview-member-password",
        demo_nonmember_password="preview-customer-password",
    )
    monkeypatch.setattr("app.seed.get_settings", lambda: preview_settings)
    await database_session.execute(
        delete(MealEvent).where(MealEvent.id == "meal-event-preorder-demo")
    )
    await database_session.commit()

    result = await seed_demo_data(database_session)

    assert result["users"] == 0
    assert await database_session.scalar(
        select(MealEvent.id).where(
            MealEvent.id == "meal-event-preorder-demo"
        )
    )
    admin = await database_session.scalar(
        select(User).where(User.email == "admin@shilifangyuan.tw")
    )
    assert verify_password("preview-admin-password", admin.password_hash)


@pytest.mark.asyncio
async def test_refund_releases_consumed_reservation_only_once(
    database_session,
) -> None:
    user = make_user()
    product = Product(
        id="product-1",
        slug="refund-rice",
        name="退款白米",
        category="米・雜糧",
        unit="包",
        member_price=100,
        nonmember_price=120,
        stock_quantity=8,
    )
    order = Order(
        id="order-refund",
        order_number="REFUND001",
        order_kind=OrderKind.REGULAR,
        user=user,
        membership_type_snapshot=user.membership_type,
        amount_total=200,
        contact_email=user.email,
        payment_status=PaymentStatus.PAID,
        fulfillment_status="pending_confirmation",
        reservations=[
            InventoryReservation(
                source_product_id=product.id,
                quantity=2,
                status=ReservationStatus.CONSUMED,
                expires_at=NOW,
            )
        ],
    )
    database_session.add_all([product, order])
    await database_session.flush()

    await request_order_refund(database_session, order, user, "測試退款")
    assert product.stock_quantity == 10
    assert order.reservations[0].status == ReservationStatus.RELEASED
    with pytest.raises(HTTPException, match="只有已付款"):
        await request_order_refund(database_session, order, user, "重複退款")
    assert product.stock_quantity == 10


@pytest.mark.asyncio
async def test_demo_reset_preserves_payment_tombstone(database_session) -> None:
    await seed_demo_data(database_session)
    order = await database_session.scalar(select(Order).limit(1))
    database_session.add(
        PaymentAttempt(
            order_id=order.id,
            merchant_trade_no="RESET202607290001",
            amount=order.amount_total,
            expires_at=NOW,
        )
    )
    await database_session.commit()

    await reset_demo_data(database_session)
    tombstone = await database_session.scalar(
        select(ExternalEvent).where(
            ExternalEvent.provider == "ecpay_reset",
            ExternalEvent.external_event_key == "RESET202607290001",
        )
    )
    assert tombstone is not None
    assert tombstone.processed
    assert await database_session.scalar(select(func.count(PaymentAttempt.id))) == 0
