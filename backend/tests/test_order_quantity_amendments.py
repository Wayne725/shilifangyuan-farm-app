from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import time_machine
from sqlalchemy import func, select

from app.integrations.payment_service import create_payment_attempt
from app.models import (
    FulfillmentStatus, GroupCampaign, GroupDecisionStatus, GroupIntakeStatus, InventoryReservation,
    Order, OrderAmendment, OrderItem, OrderKind, PaymentAttempt, PaymentStatus, Product,
    Promotion, ReservationStatus, SalesChannel, SystemSetting, TargetType, TaxType, User, UserRole,
)
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_cash_payments import cash_context, create_cash, action
from tests.test_integrations import make_regular_order as make_original_order, payment_settings


async def make_regular_order(session):
    buyer, product, order = await make_original_order(session)
    buyer.email = order.contact_email = "buyer@example.com"
    await session.commit()
    return buyer, product, order


def payload(order, quantity, **extra):
    return {"expected_revision": order.revision,
            "items": [{"order_item_id": item.id, "quantity": quantity} for item in order.items], **extra}


async def submit(client, user, order, quantity, *, total=None, key=None):
    body = payload(order, quantity)
    if total is not None:
        body.update(expected_total=total, client_request_id=key or str(uuid4()))
    path = f"/v1/orders/{order.id}/quantity-amendments" + ("" if total is not None else "/quote")
    return await client.post(path, headers=auth_headers(user), json=body)


async def test_regular_quote_confirm_snapshot_idempotency_and_ownership(database_session):
    session = database_session
    buyer, product, order = await make_regular_order(session)
    product.nonmember_price = product.member_price = 999
    outsider = User(email="amendment-outsider@example.test", display_name="外人", password_hash="test")
    session.add(outsider)
    await session.commit()
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        assert (await submit(client, outsider, order, 3)).status_code == 404
        quote = await submit(client, buyer, order, 3)
        assert quote.status_code == 200, quote.text
        assert quote.json()["amount_total"] == 300
        assert order.items[0].quantity == 2 and product.stock_quantity == 3
        key = str(uuid4())
        original = payload(order, 3, expected_total=300, client_request_id=key)
        confirmed = await client.post(f"/v1/orders/{order.id}/quantity-amendments", headers=auth_headers(buyer), json=original)
        assert confirmed.status_code == 200, confirmed.text
        result = confirmed.json()
        assert result["revision"] == 2 and result["order_number"] == "SLFTEST0001"
        assert result["items"][0]["unit_price"] == 100 and result["amount_total"] == 300
        assert result["amendments"][0]["before_snapshot"]["items"][0]["quantity"] == 2
        duplicate = await client.post(f"/v1/orders/{order.id}/quantity-amendments", headers=auth_headers(buyer), json=original)
        assert duplicate.status_code == 200 and duplicate.json()["revision"] == 2
        original["items"][0]["quantity"] = 1
        assert (await client.post(f"/v1/orders/{order.id}/quantity-amendments", headers=auth_headers(buyer), json=original)).status_code == 409
        assert await session.scalar(select(func.count(OrderAmendment.id))) == 1
        assert await session.scalar(select(func.count(InventoryReservation.id))) == 0
        assert await session.scalar(select(func.count(PaymentAttempt.id))) == 0
        assert product.stock_quantity == 3


async def test_online_existing_attempt_releases_once_and_new_attempt_uses_revision(database_session):
    session = database_session
    buyer, product, order = await make_regular_order(session)
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings())
    old_amount, old_payload = attempt.amount, dict(attempt.checkout_payload)
    await session.commit()
    assert product.stock_quantity == 1
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        changed = await submit(client, buyer, order, 3, total=300)
        assert changed.status_code == 200, changed.text
    assert attempt.superseded_at is not None and attempt.status == PaymentStatus.EXPIRED
    assert attempt.amount == old_amount and attempt.checkout_payload == old_payload
    assert product.stock_quantity == 3
    await session.refresh(attempt, ["reservations"])
    assert all(row.status == ReservationStatus.RELEASED for row in attempt.reservations)
    fresh = await create_payment_attempt(session, order.id, buyer, payment_settings())
    assert fresh.id != attempt.id and fresh.order_revision == 2 and fresh.amount == 300
    assert product.stock_quantity == 0


@pytest.mark.parametrize("change", ["paid", "preparing", "cancelled", "invoice", "cash_correction"])
async def test_recorded_payment_or_fulfillment_blocks_quantity_changes(database_session, change):
    session = database_session
    buyer, product, order = await make_regular_order(session)
    if change == "paid":
        order.payment_status = PaymentStatus.PAID
    elif change == "preparing":
        order.fulfillment_status = FulfillmentStatus.PREPARING
    elif change == "cancelled":
        order.cancelled_at = datetime.now(timezone.utc)
    elif change == "invoice":
        from app.models import InvoiceStatus
        order.invoice_status = InvoiceStatus.PENDING
    else:
        session.add(PaymentAttempt(order=order, provider="onsite", merchant_trade_no="CORRECTED",
            amount=200, status=PaymentStatus.FAILED, expires_at=datetime.now(timezone.utc),
            provider_response={"payment_correction": {"reason": "誤記"}}))
    await session.commit()
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        result = await submit(client, buyer, order, 1, total=100)
        assert result.status_code == 409, result.text
        read = await client.get(f"/v1/orders/{order.id}", headers=auth_headers(buyer))
        assert "amend_quantities" not in read.json()["available_actions"]
    assert order.revision == 1 and order.amount_total == 200 and product.stock_quantity == 3


async def test_remove_single_item_and_reject_all_zero_stale_total_and_limit(database_session):
    session = database_session
    buyer, product, order = await make_regular_order(session)
    second = Product(slug="amendment-other", name="另一品項", category="米食", unit="包", member_price=50, nonmember_price=50, stock_quantity=4)
    session.add(second)
    await session.flush()
    order.items.append(OrderItem(source_product_id=second.id, product_name=second.name, unit_label="包",
        quantity=1, unit_price=50, subtotal=50, tax_type=TaxType.TAXABLE))
    order.amount_total = 250
    await session.commit()
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        assert (await submit(client, buyer, order, 0)).status_code == 422
        assert (await submit(client, buyer, order, 1000)).status_code == 422
        body = payload(order, 1, expected_total=50, client_request_id=str(uuid4()))
        body["items"][0]["quantity"] = 0
        url = f"/v1/orders/{order.id}/quantity-amendments"
        wrong = {**body, "expected_total": 49}
        assert (await client.post(url, headers=auth_headers(buyer), json=wrong)).status_code == 409
        assert order.amount_total == 250
        response = await client.post(url, headers=auth_headers(buyer), json=body)
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 1 and response.json()["amount_total"] == 50
        body["client_request_id"] = str(uuid4())
        assert (await client.post(url, headers=auth_headers(buyer), json=body)).status_code == 409


async def test_closed_platform_and_disabled_product_allow_only_reduction(database_session):
    session = database_session
    buyer, product, order = await make_regular_order(session)
    product.is_active = False
    session.add(SystemSetting(key="storefront_operations", value={"orders_open": False, "closed_message": "暫停接單"}))
    await session.commit()
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        assert (await submit(client, buyer, order, 3)).status_code == 409
        reduction = await submit(client, buyer, order, 1, total=100)
        assert reduction.status_code == 200, reduction.text


async def test_cash_amendment_adjusts_reservation_without_extending_expiry(cash_context):
    c = cash_context
    created = await create_cash(c, needs_utensils=True)
    order = await c.session.get(Order, created.json()["id"])
    reservation = await c.session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == order.id))
    expires_at = reservation.expires_at
    code, pickup_at = order.fulfillment.pickup_code, order.fulfillment.pickup_at
    result = await submit(c.client, c.buyer, order, 2, total=240)
    assert result.status_code == 200, result.text
    await c.session.refresh(reservation)
    await c.session.refresh(c.event, ["offerings"])
    assert reservation.quantity == 2 and reservation.expires_at == expires_at
    assert c.event.offerings[0].reserved_quantity == 2 and c.event.offerings[0].paid_quantity == 0
    assert order.fulfillment.pickup_code == code and order.fulfillment.pickup_at.replace(tzinfo=timezone.utc) == pickup_at.replace(tzinfo=timezone.utc)
    assert order.fulfillment.needs_utensils
    c.session.expire(order, ["amendments", "payment_attempts"])
    search = await c.client.get("/v1/admin/meal-orders", headers=auth_headers(c.admin))
    assert search.status_code == 200, search.text
    assert search.json()["items"][0]["order"]["amendments"][0]["revision"] == 2
    assert await c.session.scalar(select(func.count(PaymentAttempt.id))) == 0
    paid = await action(c, order.id, amount=240)
    assert paid.status_code == 200, paid.text
    assert c.event.offerings[0].reserved_quantity == 0 and c.event.offerings[0].paid_quantity == 2


async def test_meal_deadline_and_99_limit(cash_context):
    c = cash_context
    created = await create_cash(c)
    order = await c.session.get(Order, created.json()["id"])
    assert (await submit(c.client, c.buyer, order, 100)).status_code == 422
    cutoff = order.fulfillment.meal_ordering_ends_at
    with time_machine.travel(cutoff, tick=False):
        response = await submit(c.client, c.buyer, order, 2)
        assert response.status_code == 409


async def test_existing_promotion_keeps_original_rules_without_extra_usage(cash_context):
    c = cash_context
    c.event.offerings[0].capacity = 10
    now = datetime.now(timezone.utc)
    promotion = Promotion(name="原優惠", code="ONCE", enabled=True, minimum_amount=200,
        discount_kind="fixed", discount_value=20, starts_at=now-timedelta(days=1), ends_at=now+timedelta(days=1),
        max_uses=1, per_user_limit=1, created_by_id=c.admin.id)
    c.session.add(promotion)
    await c.session.commit()
    created = await create_cash(c, items=[{"offering_id": c.event.offerings[0].id, "quantity": 2}], coupon_code="ONCE")
    assert created.status_code == 201, created.text
    order = await c.session.get(Order, created.json()["id"])
    promotion.discount_value = 90
    promotion.enabled = False
    await c.session.commit()
    changed = await submit(c.client, c.buyer, order, 3, total=340)
    assert changed.status_code == 200, changed.text
    assert order.promotion_snapshot["discount"] == 20
    decreased = await submit(c.client, c.buyer, order, 1, total=120)
    assert decreased.status_code == 200, decreased.text
    assert order.promotion_id == promotion.id and order.promotion_snapshot["discount"] == 0
    assert await c.session.scalar(select(func.count(Order.id)).where(Order.promotion_id == promotion.id)) == 1


async def test_group_cap_counts_other_pending_orders_and_does_not_reserve(database_session):
    session = database_session
    buyer, product, order = await make_regular_order(session)
    now = datetime.now(timezone.utc)
    campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=product.id, title="共同購買",
        member_price=100, nonmember_price=120, min_paid_quantity=3, supply_cap=20, per_user_cap=5,
        deadline=now+timedelta(days=1), estimated_pickup_start=now+timedelta(days=2), estimated_pickup_end=now+timedelta(days=3),
        created_by_id=buyer.id)
    session.add(campaign)
    await session.flush()
    order.order_kind = OrderKind.GROUP
    order.sales_channel = SalesChannel.GROUP
    order.group_campaign = campaign
    other = Order(order_number="OTHER-GROUP", order_kind=OrderKind.GROUP, sales_channel=SalesChannel.GROUP,
        user_id=buyer.id, group_campaign=campaign, membership_type_snapshot=buyer.membership_type,
        amount_total=200, contact_email=buyer.email, items=[OrderItem(source_product_id=product.id,
        product_name=product.name, unit_label="包", quantity=2, unit_price=100, subtotal=200, tax_type=TaxType.TAXABLE)])
    session.add(other)
    await session.commit()
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        assert (await submit(client, buyer, order, 4)).status_code == 409
        allowed = await submit(client, buyer, order, 3, total=300)
        assert allowed.status_code == 200, allowed.text
        assert campaign.reserved_quantity == 0 and campaign.paid_quantity == 0
        assert await session.scalar(select(func.count(InventoryReservation.id))) == 0
        attempt = await create_payment_attempt(session, order.id, buyer, payment_settings())
        await session.commit()
        assert campaign.reserved_quantity == 3
        reduced = await submit(client, buyer, order, 2, total=200)
        assert reduced.status_code == 200, reduced.text
        assert campaign.reserved_quantity == 0 and campaign.paid_quantity == 0
        assert attempt.superseded_at is not None


async def test_legacy_expired_promotion_never_revives_on_second_change(cash_context):
    c = cash_context
    c.event.offerings[0].capacity = 10
    now = datetime.now(timezone.utc)
    promotion = Promotion(name="舊優惠", code="OLD", enabled=True, minimum_amount=100,
        discount_kind="percent", discount_value=10, starts_at=now-timedelta(days=1), ends_at=now+timedelta(days=1),
        max_uses=1, per_user_limit=1, created_by_id=c.admin.id)
    c.session.add(promotion)
    await c.session.commit()
    created = await create_cash(c, coupon_code="OLD")
    order = await c.session.get(Order, created.json()["id"])
    order.promotion_snapshot = {key: value for key, value in order.promotion_snapshot.items() if key != "pricing"}
    promotion.ends_at = now-timedelta(seconds=1)
    await c.session.commit()
    first = await submit(c.client, c.buyer, order, 2, total=240)
    assert first.status_code == 200, first.text
    second = await submit(c.client, c.buyer, order, 3, total=360)
    assert second.status_code == 200, second.text
    assert order.promotion_snapshot["discount"] == 0 and order.promotion_snapshot["eligible"] is False


async def test_cash_same_offering_lines_keep_aggregate_capacity_and_option_snapshots(cash_context):
    from app.models import OrderItemOption
    c = cash_context
    c.event.offerings[0].capacity = 10
    await c.session.commit()
    created = await create_cash(c)
    order = await c.session.get(Order, created.json()["id"])
    existing = order.items[0]
    existing.selected_options = [OrderItemOption(group_name="主食", option_name="飯", price_delta=0)]
    extra = OrderItem(source_meal_offering_id=existing.source_meal_offering_id, product_name=existing.product_name,
        unit_label="份", quantity=1, unit_price=130, subtotal=130, tax_type=existing.tax_type,
        selected_options=[OrderItemOption(group_name="主食", option_name="換菜", price_delta=10)])
    order.items.append(extra)
    order.amount_total += 130
    old_reservation = await c.session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == order.id))
    c.session.add(InventoryReservation(order=order, source_meal_offering_id=existing.source_meal_offering_id,
        quantity=1, status=ReservationStatus.ACTIVE, expires_at=old_reservation.expires_at))
    c.event.offerings[0].reserved_quantity += 1
    await c.session.commit()
    result = await submit(c.client, c.buyer, order, 2, total=500)
    assert result.status_code == 200, result.text
    await c.session.refresh(c.event, ["offerings"])
    assert c.event.offerings[0].reserved_quantity == 4
    active = list(await c.session.scalars(select(InventoryReservation).where(InventoryReservation.order_id == order.id,
        InventoryReservation.status == ReservationStatus.ACTIVE)))
    assert sum(row.quantity for row in active) == 4
    await c.session.refresh(order.items[1], ["selected_options"])
    assert order.items[1].selected_options[0].option_name == "換菜"
    history = result.json()["amendments"][0]["before_snapshot"]
    assert history["items"][1]["selected_options"][0]["option_name"] == "換菜"


async def test_retry_after_platform_closure_and_admin_actor_are_preserved(database_session):
    session = database_session
    buyer, _, order = await make_regular_order(session)
    admin = User(email="amendment-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    session.add(admin)
    await session.commit()
    body = payload(order, 3, expected_total=300, client_request_id=str(uuid4()))
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        url = f"/v1/orders/{order.id}/quantity-amendments"
        response = await client.post(url, json=body, headers=auth_headers(admin))
        assert response.status_code == 200, response.text
        session.add(SystemSetting(key="storefront_operations", value={"orders_open": False}))
        await session.commit()
        assert (await client.post(url, json=body, headers=auth_headers(admin))).status_code == 200
    amendment = await session.scalar(select(OrderAmendment))
    assert amendment.actor_id == admin.id
    from app.routers.operations import operations_router
    session.expire(order, ["amendments", "payment_attempts"])
    async with api_test_context(session, [operations_router], settings=make_test_settings()) as client:
        search = await client.get("/v1/admin/order-search", headers=auth_headers(admin))
        assert search.status_code == 200, search.text
        assert search.json()["items"][0]["amendments"][0]["revision"] == 2


async def test_online_meal_revises_attempt_and_original_submission_remains_idempotent(cash_context):
    c = cash_context
    c.event.offerings[0].capacity = 10
    await c.session.commit()
    request_key = str(uuid4())
    created = await create_cash(c, payment_method="online", client_request_id=request_key)
    assert created.status_code == 201, created.text
    order = await c.session.get(Order, created.json()["id"])
    original_number, original_fingerprint = order.order_number, order.request_fingerprint
    attempt = await create_payment_attempt(c.session, order.id, c.buyer, payment_settings())
    await c.session.commit()
    result = await submit(c.client, c.buyer, order, 2, total=240)
    assert result.status_code == 200, result.text
    assert attempt.amount == 120 and attempt.superseded_at is not None
    await c.session.refresh(c.event, ["offerings"])
    assert c.event.offerings[0].reserved_quantity == 0
    duplicate_create = await create_cash(c, payment_method="online", client_request_id=request_key)
    assert duplicate_create.status_code == 201, duplicate_create.text
    assert duplicate_create.json()["order_number"] == original_number
    assert duplicate_create.json()["revision"] == 2 and duplicate_create.json()["items"][0]["quantity"] == 2
    assert order.request_fingerprint == original_fingerprint
    fresh = await create_payment_attempt(c.session, order.id, c.buyer, payment_settings())
    assert fresh.amount == 240 and fresh.order_revision == 2


async def test_cash_expired_reservation_cannot_be_extended_by_amendment(cash_context):
    c = cash_context
    created = await create_cash(c)
    order = await c.session.get(Order, created.json()["id"])
    reservation = await c.session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == order.id))
    reservation.expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
    await c.session.commit()
    rejected = await submit(c.client, c.buyer, order, 2, total=240)
    assert rejected.status_code == 409
    assert order.revision == 1 and reservation.quantity == 1


@pytest.mark.parametrize("target_type", [TargetType.PRODUCT, TargetType.BUNDLE])
async def test_disabled_group_target_allows_reduction_but_not_increase(database_session, target_type):
    from app.models import GroupBundle
    session = database_session
    buyer, product, order = await make_regular_order(session)
    target = product
    if target_type == TargetType.BUNDLE:
        target = GroupBundle(name="停用套組", description="測試", member_price=100, nonmember_price=120, is_active=False)
        session.add(target)
        await session.flush()
    target.is_active = False
    now = datetime.now(timezone.utc)
    campaign = GroupCampaign(target_type=target_type, target_id=target.id, title="仍開放的共同購買",
        member_price=100, nonmember_price=120, min_paid_quantity=3, supply_cap=20, per_user_cap=5,
        deadline=now+timedelta(days=1), estimated_pickup_start=now+timedelta(days=2), estimated_pickup_end=now+timedelta(days=3),
        created_by_id=buyer.id, decision_status=GroupDecisionStatus.CONFIRMED, intake_status=GroupIntakeStatus.OPEN)
    session.add(campaign)
    await session.flush()
    order.group_campaign = campaign
    order.order_kind = OrderKind.GROUP
    order.sales_channel = SalesChannel.GROUP
    for item in order.items:
        item.group_campaign_id = campaign.id
        item.source_product_id = target.id if target_type == TargetType.PRODUCT else None
        item.source_bundle_id = target.id if target_type == TargetType.BUNDLE else None
    await session.commit()
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        assert (await submit(client, buyer, order, 3)).status_code == 409
        result = await submit(client, buyer, order, 1, total=100)
        assert result.status_code == 200, result.text


async def test_removing_the_only_priced_item_does_not_create_unpayable_zero_order(database_session):
    session = database_session
    buyer, product, order = await make_regular_order(session)
    free = Product(slug="amendment-free", name="贈品", category="米食", unit="包", member_price=0, nonmember_price=0, stock_quantity=4)
    session.add(free)
    await session.flush()
    order.items.append(OrderItem(source_product_id=free.id, product_name=free.name, unit_label="包", quantity=1,
        unit_price=0, subtotal=0, tax_type=TaxType.TAXABLE))
    await session.commit()
    body = payload(order, 1)
    body["items"][0]["quantity"] = 0
    async with api_test_context(session, [orders_router], settings=make_test_settings()) as client:
        result = await client.post(f"/v1/orders/{order.id}/quantity-amendments/quote", headers=auth_headers(buyer), json=body)
        assert result.status_code == 422
        assert order.revision == 1 and order.amount_total == 200
