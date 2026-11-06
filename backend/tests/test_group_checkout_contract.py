from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (GroupCampaign, Order, PaymentAttempt, Product, ShippingChannel,
    ShippingRate, ShippingTemperature, SystemSetting, TaxType, User, UserRole)
from app.routers.groups import groups_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers
from tests.test_integrations import payment_settings


async def prepare_group(session):
    admin = User(email="group-contract-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyers = [User(email=f"group-contract-{index}@example.com", display_name=f"買家{index}", password_hash="test") for index in range(2)]
    products = [Product(slug=f"group-contract-{index}", name=f"商品{index}", category="測試", unit="份",
                        member_price=100, nonmember_price=100, stock_quantity=20) for index in range(2)]
    session.add_all([admin, *buyers, *products])
    await session.commit()
    now = datetime.now(timezone.utc)
    async with api_test_context(session, [groups_router], settings=payment_settings()) as client:
        response = await client.post("/v1/group-campaigns", headers=auth_headers(admin), json={
            "target_type": "product", "target_id": products[0].id, "title": "合約確認共同購買",
            "member_price": 100, "nonmember_price": 100, "min_paid_quantity": 4,
            "supply_cap": 20, "per_user_cap": 1,
            "deadline": (now + timedelta(days=2)).isoformat(),
            "confirmation_deadline": (now + timedelta(days=3)).isoformat(),
            "estimated_pickup_start": (now + timedelta(days=4)).isoformat(),
            "estimated_pickup_end": (now + timedelta(days=5)).isoformat(),
        })
    assert response.status_code == 201, response.text
    return SimpleNamespace(session=session, campaign_id=response.json()["id"],
        admin_headers=auth_headers(admin), buyer_headers=[auth_headers(buyer) for buyer in buyers],
        buyer_ids=[buyer.id for buyer in buyers], product_ids=[product.id for product in products])


@pytest.fixture
async def group_checkout(database_session):
    context = await prepare_group(database_session)
    async with api_test_context(database_session, [groups_router, orders_router, payments_router], settings=payment_settings()) as client:
        context.client = client
        yield context


def join_body(**changes):
    return {"quantity": 1, "contact_email": "group-buyer@example.com", **changes}


async def join(context, body=None, *, buyer=0, campaign_id=None):
    return await context.client.post(f"/v1/group-campaigns/{campaign_id or context.campaign_id}/join",
        headers=context.buyer_headers[buyer], json=body or join_body())


async def quote(context, **changes):
    response = await context.client.post(f"/v1/group-campaigns/{context.campaign_id}/quote",
        headers=context.buyer_headers[0], json={"quantity": 1, **changes})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_target_edit_preserves_admin_flow_but_stops_old_order_and_allows_reconfirmation(group_checkout, legacy):
    c = group_checkout
    first = await join(c)
    assert first.status_code == 201, first.text
    if legacy:
        old_order = await c.session.get(Order, first.json()["id"])
        old_order.group_contract_fingerprint = None
        await c.session.commit()
    edited = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"target_id": c.product_ids[1]})
    assert edited.status_code == 200, edited.text
    old_payment = await c.client.post(f"/v1/orders/{first.json()['id']}/payment-attempts", headers=c.buyer_headers[0])
    assert old_payment.status_code == 409, old_payment.text
    assert "共同購買" in old_payment.json()["detail"]
    confirmed = await quote(c)
    new_order = await join(c, join_body(client_request_id=str(uuid4()), expected_total=confirmed["amount_total"],
        expected_quote_fingerprint=confirmed["confirmation_fingerprint"]))
    assert new_order.status_code == 201, new_order.text
    assert new_order.json()["items"][0]["product_id"] == c.product_ids[1]
    new_payment = await c.client.post(f"/v1/orders/{new_order.json()['id']}/payment-attempts", headers=c.buyer_headers[0])
    assert new_payment.status_code == 201, new_payment.text
    assert await c.session.scalar(select(func.count(PaymentAttempt.id)).where(PaymentAttempt.order_id == first.json()["id"])) == 0
    campaign = await c.session.get(GroupCampaign, c.campaign_id)
    assert (campaign.paid_quantity, campaign.reserved_quantity) == (0, 1)


@pytest.mark.asyncio
async def test_group_submission_replays_before_mutable_campaign_gate_and_never_replaces_order(group_checkout):
    c = group_checkout
    body = join_body(client_request_id=str(uuid4()), expected_total=100)
    first = await join(c, body)
    assert first.status_code == 201, first.text
    # The retry is the same submission, even after an administrator changes the target.
    edited = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"target_id": c.product_ids[1]})
    assert edited.status_code == 200, edited.text
    replay = await join(c, body)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["items"] == first.json()["items"]
    assert await c.session.scalar(select(func.count(Order.id))) == 1


@pytest.mark.asyncio
async def test_group_submission_changed_content_is_conflict_and_other_user_has_independent_scope(group_checkout):
    c = group_checkout
    request_id = str(uuid4())
    body = join_body(client_request_id=request_id, expected_total=100)
    first = await join(c, body)
    assert first.status_code == 201, first.text
    changed = await join(c, {**body, "contact_email": "different@example.com"})
    assert changed.status_code == 409, changed.text
    assert changed.headers["X-Error-Code"] == "idempotency_conflict"
    other = await join(c, body, buyer=1)
    assert other.status_code == 201, other.text
    assert other.json()["id"] != first.json()["id"]
    assert await c.session.scalar(select(func.count(Order.id))) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["price", "target", "tax"])
async def test_group_quote_confirmation_rejects_changed_total_or_same_total_terms_without_order(group_checkout, change):
    c = group_checkout
    confirmed = await quote(c)
    if change == "tax":
        product = await c.session.get(Product, c.product_ids[0])
        product.tax_type = TaxType.TAX_EXEMPT
        await c.session.commit()
    else:
        response = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
            json={"nonmember_price": 120} if change == "price" else {"target_id": c.product_ids[1]})
        assert response.status_code == 200, response.text
    response = await join(c, join_body(client_request_id=str(uuid4()), expected_total=confirmed["amount_total"],
        expected_quote_fingerprint=confirmed.get("confirmation_fingerprint", "0" * 64)))
    assert response.status_code == 409, response.text
    assert response.headers["X-Error-Code"] == "quote_changed"
    assert await c.session.scalar(select(func.count(Order.id))) == 0


@pytest.mark.asyncio
async def test_group_quote_has_deterministic_confirmation_and_rejects_bad_digest(group_checkout):
    c = group_checkout
    first, second = await quote(c), await quote(c)
    assert len(first["confirmation_fingerprint"]) == 64
    assert first["confirmation_fingerprint"] == second["confirmation_fingerprint"]
    bad = await join(c, join_body(client_request_id=str(uuid4()), expected_total=100, expected_quote_fingerprint="bad"))
    assert bad.status_code == 422
    assert await c.session.scalar(select(func.count(Order.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_metadata_edit_does_not_block_existing_order_payment(group_checkout, legacy):
    c = group_checkout
    created = await join(c)
    assert created.status_code == 201, created.text
    order = await c.session.get(Order, created.json()["id"])
    if legacy and hasattr(order, "group_contract_fingerprint"):
        order.group_contract_fingerprint = None
        await c.session.commit()
    edited = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"title": "更新介紹標題", "description": "更新說明"})
    assert edited.status_code == 200, edited.text
    payment = await c.client.post(f"/v1/orders/{order.id}/payment-attempts", headers=c.buyer_headers[0])
    assert payment.status_code == 201, payment.text


@pytest.mark.asyncio
async def test_group_legacy_body_stays_valid_and_active_attempt_still_blocks_core_change(group_checkout):
    c = group_checkout
    created = await join(c)
    assert created.status_code == 201, created.text
    payment = await c.client.post(f"/v1/orders/{created.json()['id']}/payment-attempts", headers=c.buyer_headers[0])
    assert payment.status_code == 201, payment.text
    edited = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"target_id": c.product_ids[1]})
    assert edited.status_code == 409
    campaign = await c.session.get(GroupCampaign, c.campaign_id)
    assert (campaign.target_id, campaign.paid_quantity, campaign.reserved_quantity) == (c.product_ids[0], 0, 1)


@pytest.mark.asyncio
async def test_group_replay_survives_closed_store_and_does_not_change_saved_preference(group_checkout):
    c = group_checkout
    body = join_body(client_request_id=str(uuid4()), expected_total=100)
    first = await join(c, body)
    assert first.status_code == 201, first.text
    user = await c.session.get(User, c.buyer_ids[0])
    user.notification_email = "newer-preference@example.com"
    c.session.add(SystemSetting(key="storefront_operations", value={"orders_open": False}))
    await c.session.commit()
    replay = await join(c, body)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    await c.session.refresh(user)
    assert user.notification_email == "newer-preference@example.com"
    assert await c.session.scalar(select(func.count(Order.id))) == 1


@pytest.mark.asyncio
async def test_new_group_keys_are_independent_legitimate_orders_and_equivalent_body_replays(group_checkout):
    c = group_checkout
    campaign = await c.session.get(GroupCampaign, c.campaign_id)
    campaign.per_user_cap = 2
    await c.session.commit()
    body = join_body(client_request_id=str(uuid4()), contact_email="GROUP-BUYER@EXAMPLE.COM")
    first = await join(c, body)
    second = await join(c, join_body(client_request_id=str(uuid4())))
    assert [first.status_code, second.status_code] == [201, 201]
    assert first.json()["id"] != second.json()["id"]
    replay = await join(c, {**body, "contact_email": "group-buyer@example.com"})
    assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]
    assert await c.session.scalar(select(func.count(Order.id))) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change_rate", [False, True])
async def test_group_shipping_confirmation_includes_fee_but_initial_order_keeps_subtotal(group_checkout, change_rate):
    c = group_checkout
    changed = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"can_ship": True, "shipping_temperature": "ambient", "allowed_shipping_channels": ["home_delivery"]})
    assert changed.status_code == 200, changed.text
    rate = ShippingRate(channel=ShippingChannel.HOME_DELIVERY, temperature=ShippingTemperature.AMBIENT,
        fee=60, free_shipping_threshold=1000, effective_from=date(2020, 1, 1), is_active=True)
    c.session.add(rate)
    await c.session.commit()
    confirmed = await quote(c, fulfillment_method="ecpay_logistics", shipping_channel="home_delivery")
    assert (confirmed["product_subtotal"], confirmed["shipping_fee"], confirmed["amount_total"]) == (100, 60, 160)
    if change_rate:
        rate.fee = 70
        await c.session.commit()
    response = await join(c, join_body(client_request_id=str(uuid4()), expected_total=160,
        expected_quote_fingerprint=confirmed["confirmation_fingerprint"], fulfillment_method="ecpay_logistics", shipping_channel="home_delivery"))
    assert response.status_code == (409 if change_rate else 201), response.text
    if change_rate:
        assert response.headers["X-Error-Code"] == "quote_changed"
        assert await c.session.scalar(select(func.count(Order.id))) == 0
    else:
        assert response.json()["amount_total"] == 100
