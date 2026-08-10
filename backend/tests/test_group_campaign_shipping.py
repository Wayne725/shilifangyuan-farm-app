from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import make_token_pair
from app.database import Base, get_session
from app.domain import apply_paid_quantity
from app.models import (
    GroupCampaign,
    FulfillmentMethod,
    InventoryReservation,
    MembershipType,
    Order,
    OrderKind,
    Product,
    ProductCategory,
    ProposalStatus,
    ReservationStatus,
    SalesChannel,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
    TargetType,
    TaxType,
    User,
    UserRole,
    Vote,
    VoteProposal,
)
from app.routers.groups import groups_router
from app.routers.proposals import proposals_router


def auth_headers(user: User) -> dict[str, str]:
    token = make_token_pair(user)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def campaign_payload(product_id: str, **overrides) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "target_type": "product",
        "target_id": product_id,
        "title": "冷藏蔬菜箱團購",
        "description": "當季蔬菜組合",
        "member_price": 480,
        "nonmember_price": 520,
        "min_paid_quantity": 5,
        "supply_cap": 20,
        "per_user_cap": 3,
        "deadline": (now + timedelta(days=2)).isoformat(),
        "estimated_pickup_start": (now + timedelta(days=3)).isoformat(),
        "estimated_pickup_end": (now + timedelta(days=4)).isoformat(),
        "can_ship": True,
        "shipping_temperature": "chilled",
        "allowed_shipping_channels": ["home_delivery"],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
async def campaign_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        application = FastAPI()
        application.include_router(groups_router)
        application.include_router(proposals_router)

        async def override_get_session():
            yield session

        application.dependency_overrides[get_session] = override_get_session

        admin = User(
            email="admin@example.test",
            display_name="管理員",
            password_hash="test",
            user_role=UserRole.ADMIN,
        )
        proposer = User(
            email="proposer@example.test",
            display_name="提案人",
            password_hash="test",
        )
        product = Product(
            slug="campaign-vegetables",
            name="當季蔬菜箱",
            description="測試商品",
            category=ProductCategory.SEASONAL_PRODUCE,
            unit="箱",
            member_price=480,
            nonmember_price=520,
            stock_quantity=100,
            tax_type=TaxType.TAX_EXEMPT,
            is_active=True,
        )
        shipping_rate = ShippingRate(
            channel=ShippingChannel.HOME_DELIVERY,
            temperature=ShippingTemperature.CHILLED,
            fee=160,
            free_shipping_threshold=1500,
            effective_from=date.today() - timedelta(days=1),
            is_active=True,
        )
        session.add_all([admin, proposer, product, shipping_rate])
        await session.flush()
        proposal = VoteProposal(
            proposer_id=proposer.id,
            target_type=TargetType.PRODUCT,
            target_id=product.id,
            target_name_snapshot=product.name,
            status=ProposalStatus.VOTING,
            threshold=1,
            deadline=datetime.now(timezone.utc) + timedelta(days=1),
            votes=[
                Vote(
                    user_id=proposer.id,
                    estimated_quantity=1,
                )
            ],
        )
        session.add(proposal)
        await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://test",
        ) as client:
            yield {
                "client": client,
                "session": session,
                "admin": admin,
                "proposer": proposer,
                "product": product,
                "proposal": proposal,
            }
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_create_and_proposal_conversion_preserve_shipping_contract(
    campaign_context,
) -> None:
    client = campaign_context["client"]
    admin = campaign_context["admin"]
    product = campaign_context["product"]
    proposal = campaign_context["proposal"]
    headers = auth_headers(admin)

    created = await client.post(
        "/v1/group-campaigns",
        json=campaign_payload(product.id),
        headers=headers,
    )
    converted = await client.post(
        f"/v1/vote-proposals/{proposal.id}/admin/convert",
        json=campaign_payload(product.id),
        headers=headers,
    )

    assert created.status_code == 201
    assert converted.status_code == 201
    for response in (created, converted):
        body = response.json()
        assert body["can_ship"] is True
        assert body["shipping_temperature"] == "chilled"
        assert body["allowed_shipping_channels"] == [
            ShippingChannel.HOME_DELIVERY.value
        ]


@pytest.mark.asyncio
async def test_group_quote_uses_server_membership_and_shipping_rate(
    campaign_context,
) -> None:
    client = campaign_context["client"]
    admin = campaign_context["admin"]
    proposer = campaign_context["proposer"]
    product = campaign_context["product"]
    created = await client.post(
        "/v1/group-campaigns",
        json=campaign_payload(product.id),
        headers=auth_headers(admin),
    )
    campaign_id = created.json()["id"]

    quoted = await client.post(
        f"/v1/group-campaigns/{campaign_id}/quote",
        json={
            "quantity": 2,
            "fulfillment_method": "ecpay_logistics",
            "shipping_channel": "home_delivery",
        },
        headers=auth_headers(proposer),
    )

    assert quoted.status_code == 200, quoted.text
    assert quoted.json() == {
        "membership_type": "nonmember",
        "quantity": 2,
        "unit_price": 520,
        "product_subtotal": 1040,
        "shipping_fee": 160,
        "amount_total": 1200,
    }


@pytest.mark.asyncio
async def test_group_join_preserves_logistics_intent_before_picker(
    campaign_context,
) -> None:
    client = campaign_context["client"]
    admin = campaign_context["admin"]
    proposer = campaign_context["proposer"]
    product = campaign_context["product"]
    created = await client.post(
        "/v1/group-campaigns",
        json=campaign_payload(product.id),
        headers=auth_headers(admin),
    )
    campaign_id = created.json()["id"]

    joined = await client.post(
        f"/v1/group-campaigns/{campaign_id}/join",
        json={
            "quantity": 2,
            "contact_email": "proposer@example.com",
            "invoice_carrier_type": "ecpay",
            "fulfillment_method": "ecpay_logistics",
            "shipping_channel": "home_delivery",
        },
        headers=auth_headers(proposer),
    )

    assert joined.status_code == 201, joined.text
    body = joined.json()
    assert body["fulfillment"]["method"] == "ecpay_logistics"
    assert body["amount_total"] == 1040
    assert "pay" not in body["available_actions"]


@pytest.mark.asyncio
async def test_group_join_rejects_unrelated_fulfillment_method(
    campaign_context,
) -> None:
    client = campaign_context["client"]
    admin = campaign_context["admin"]
    proposer = campaign_context["proposer"]
    product = campaign_context["product"]
    created = await client.post(
        "/v1/group-campaigns",
        json=campaign_payload(product.id),
        headers=auth_headers(admin),
    )
    campaign_id = created.json()["id"]

    quoted = await client.post(
        f"/v1/group-campaigns/{campaign_id}/quote",
        json={"quantity": 1, "fulfillment_method": "event_pickup"},
        headers=auth_headers(proposer),
    )
    joined = await client.post(
        f"/v1/group-campaigns/{campaign_id}/join",
        json={
            "quantity": 1,
            "contact_email": "proposer@example.com",
            "invoice_carrier_type": "ecpay",
            "fulfillment_method": "event_pickup",
        },
        headers=auth_headers(proposer),
    )

    assert quoted.status_code == 422
    assert joined.status_code == 422


@pytest.mark.asyncio
async def test_first_paid_order_locks_campaign_shipping_conditions(
    campaign_context,
) -> None:
    client = campaign_context["client"]
    session = campaign_context["session"]
    admin = campaign_context["admin"]
    product = campaign_context["product"]
    headers = auth_headers(admin)

    created = await client.post(
        "/v1/group-campaigns",
        json=campaign_payload(
            product.id,
            can_ship=False,
            shipping_temperature="ambient",
            allowed_shipping_channels=["seven_eleven"],
        ),
        headers=headers,
    )
    campaign_id = created.json()["id"]
    assert created.json()["can_ship"] is False
    assert created.json()["shipping_temperature"] is None
    assert created.json()["allowed_shipping_channels"] == []

    before_payment = await client.patch(
        f"/v1/group-campaigns/{campaign_id}",
        json={
            "can_ship": True,
            "shipping_temperature": "ambient",
            "allowed_shipping_channels": ["seven_eleven"],
        },
        headers=headers,
    )
    assert before_payment.status_code == 200, before_payment.text
    assert before_payment.json()["allowed_shipping_channels"] == [
        "seven_eleven"
    ]

    campaign = await session.get(GroupCampaign, campaign_id)
    apply_paid_quantity(campaign, quantity=1, now=datetime.now(timezone.utc))
    await session.commit()

    locked_shipping = await client.patch(
        f"/v1/group-campaigns/{campaign_id}",
        json={"allowed_shipping_channels": ["home_delivery"]},
        headers=headers,
    )
    descriptive_edit = await client.patch(
        f"/v1/group-campaigns/{campaign_id}",
        json={"description": "付款後仍可補充不影響交易的說明"},
        headers=headers,
    )

    assert locked_shipping.status_code == 409
    assert "首筆付款" in locked_shipping.json()["detail"]
    assert descriptive_edit.status_code == 200
    assert descriptive_edit.json()["description"] == (
        "付款後仍可補充不影響交易的說明"
    )


@pytest.mark.asyncio
async def test_active_payment_reservation_temporarily_blocks_shipping_changes(
    campaign_context,
) -> None:
    client = campaign_context["client"]
    session = campaign_context["session"]
    admin = campaign_context["admin"]
    product = campaign_context["product"]
    proposer = campaign_context["proposer"]
    headers = auth_headers(admin)

    created = await client.post(
        "/v1/group-campaigns",
        json=campaign_payload(product.id),
        headers=headers,
    )
    campaign_id = created.json()["id"]
    order = Order(
        order_number="SLF-RESERVE-001",
        order_kind=OrderKind.GROUP,
        sales_channel=SalesChannel.GROUP,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user_id=proposer.id,
        group_campaign_id=campaign_id,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=520,
        contact_email=proposer.email,
    )
    session.add(order)
    await session.flush()
    reservation = InventoryReservation(
        order_id=order.id,
        group_campaign_id=campaign_id,
        quantity=1,
        status=ReservationStatus.ACTIVE,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    session.add(reservation)
    await session.commit()

    blocked = await client.patch(
        f"/v1/group-campaigns/{campaign_id}",
        json={"shipping_temperature": "ambient"},
        headers=headers,
    )
    assert blocked.status_code == 409
    assert "有效付款保留" in blocked.json()["detail"]

    reservation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    after_expiry = await client.patch(
        f"/v1/group-campaigns/{campaign_id}",
        json={
            "shipping_temperature": "ambient",
            "allowed_shipping_channels": ["seven_eleven"],
        },
        headers=headers,
    )
    assert after_expiry.status_code == 200
    assert after_expiry.json()["shipping_temperature"] == "ambient"
