from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.models import InventoryReservation, Order, OrderFulfillment, OrderItem, OutboxEvent, PaymentAttempt, PickupLocation, Product, SystemSetting, User
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers
from tests.test_integrations import payment_settings, signed_payment_callback


async def seed_regular_checkout(session):
    session.autoflush = False
    buyer = User(email="checkout@example.com", notification_email="checkout@example.com", display_name="買家", password_hash="test")
    other = User(email="other-checkout@example.com", display_name="另一位買家", password_hash="test")
    products = [Product(slug=f"checkout-{index}", name=f"結帳商品{index}", category="測試", unit="份",
                        member_price=price, nonmember_price=price, stock_quantity=100)
                for index, price in enumerate((100, 200))]
    pickups = [PickupLocation(code=f"checkout-{index}", name=f"取貨點{index}") for index in range(2)]
    session.add_all([buyer, other, *products, *pickups])
    await session.commit()
    payload = {"client_request_id": str(uuid4()), "expected_total": 400, "contact_email": buyer.email,
               "pickup_location_id": pickups[0].id,
               "items": [{"product_id": products[0].id, "quantity": 2}, {"product_id": products[1].id, "quantity": 1}]}
    return SimpleNamespace(buyer=buyer, other=other, products=products, pickups=pickups, payload=payload)


@pytest.fixture
async def checkout(database_session):
    context = await seed_regular_checkout(database_session)
    async with api_test_context(database_session, [orders_router, payments_router], settings=payment_settings()) as client:
        context.client, context.session = client, database_session
        yield context


async def submit(context, payload=None, user=None):
    return await context.client.post("/v1/orders", headers=auth_headers(user or context.buyer),
                                     json=context.payload if payload is None else payload)


async def assert_no_order_effects(session):
    for model in (Order, OrderItem, OrderFulfillment, InventoryReservation, PaymentAttempt, OutboxEvent):
        assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_lost_response_replay_returns_same_order_and_full_children(checkout):
    c = checkout
    first = await submit(c)
    assert first.status_code == 201
    replay = await submit(c)
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["items"] == first.json()["items"]
    stored = await c.session.get(Order, first.json()["id"])
    assert stored.client_request_id == c.payload["client_request_id"]
    assert len(stored.request_fingerprint) == 64
    assert await c.session.scalar(select(func.count(Order.id))) == 1
    assert await c.session.scalar(select(func.count(OrderItem.id))) == 2
    assert await c.session.scalar(select(func.count(OrderFulfillment.id))) == 1
    assert [product.stock_quantity for product in c.products] == [100, 100]


async def test_semantically_same_cart_and_normalized_email_reuse_request(checkout):
    c = checkout
    first = await submit(c)
    replay = await submit(c, {**c.payload, "contact_email": c.payload["contact_email"].upper(),
                              "items": [c.payload["items"][1], {"product_id": c.products[0].id, "quantity": 1},
                                        {"product_id": c.products[0].id, "quantity": 1}]})
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]


@pytest.mark.parametrize("change", ["quantity", "email", "invoice_email", "invoice_carrier", "pickup", "fulfillment", "payment", "total"])
async def test_same_request_id_with_different_content_is_conflict(checkout, change):
    c = checkout
    first = await submit(c)
    changed = {**c.payload}
    updates = {
        "quantity": {"items": [{"product_id": c.products[0].id, "quantity": 3}, c.payload["items"][1]]},
        "email": {"contact_email": "changed@example.com"},
        "invoice_email": {"invoice_buyer_email": "invoice@example.com"},
        "invoice_carrier": {"invoice_carrier_type": "mobile_barcode", "invoice_carrier_value": "/ABC1234"},
        "pickup": {"pickup_location_id": c.pickups[1].id},
        "fulfillment": {"fulfillment_method": "ecpay_logistics"},
        "payment": {"payment_method": "cash_on_pickup"},
        "total": {"expected_total": 401},
    }
    changed.update(updates[change])
    rejected = await submit(c, changed)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == "此送單識別碼已使用，請重新確認訂單"
    assert await c.session.scalar(select(func.count(Order.id))) == 1
    assert (await c.session.get(Order, first.json()["id"])).amount_total == 400


async def test_committed_replay_precedes_closed_store_stock_price_and_active_gates(checkout):
    c = checkout
    first = await submit(c)
    for product in c.products:
        product.is_active = False
        product.stock_quantity = 0
        product.nonmember_price += 100
    c.session.add(SystemSetting(key="storefront_operations", value={"orders_open": False}))
    await c.session.commit()
    replay = await submit(c)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["amount_total"] == 400


async def test_quote_change_conflicts_without_order_and_new_confirmation_succeeds(checkout):
    c = checkout
    quote = await c.client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": c.payload["items"]})
    assert quote.json()["amount_total"] == 400
    c.products[0].nonmember_price = 120
    await c.session.commit()
    rejected = await submit(c)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == "價格已變動，請重新確認訂單明細"
    await assert_no_order_effects(c.session)
    assert c.buyer.notification_email == "checkout@example.com"
    confirmed = await submit(c, {**c.payload, "client_request_id": str(uuid4()), "expected_total": 440})
    assert confirmed.status_code == 201
    assert confirmed.json()["amount_total"] == 440


async def test_request_ids_are_scoped_to_user_and_new_ids_allow_new_purchases(checkout):
    c = checkout
    first = await submit(c)
    other = await submit(c, user=c.other)
    next_purchase = await submit(c, {**c.payload, "client_request_id": str(uuid4())})
    assert {first.status_code, other.status_code, next_purchase.status_code} == {201}
    assert len({first.json()["id"], other.json()["id"], next_purchase.json()["id"]}) == 3
    assert (await c.session.get(Order, other.json()["id"])).user_id == c.other.id


async def test_replay_of_paid_order_does_not_repeat_payment_or_order_effects(checkout):
    c = checkout
    first = await submit(c)
    order_id = first.json()["id"]
    started = await c.client.post(f"/v1/orders/{order_id}/payment-attempts", headers=auth_headers(c.buyer))
    assert started.status_code == 201
    attempt = await c.session.get(PaymentAttempt, started.json()["id"])
    await SQLAlchemyPaymentCallbackRepository(c.session).apply_ecpay_payment_callback(
        "regular-checkout-paid", signed_payment_callback(attempt, datetime.now(timezone.utc)))
    replay = await submit(c)
    assert replay.status_code == 201
    assert replay.json()["id"] == order_id and replay.json()["payment_status"] == "paid"
    assert await c.session.scalar(select(func.count(PaymentAttempt.id))) == 1
    assert await c.session.scalar(select(func.count(Order.id))) == 1
    assert [product.stock_quantity for product in c.products] == [98, 99]


async def test_original_submission_replay_preserves_later_amendment_and_payment_revision(checkout):
    c = checkout
    first = await submit(c)
    order_id = first.json()["id"]
    original_fingerprint = (await c.session.get(Order, order_id)).request_fingerprint
    amended = await c.client.post(f"/v1/orders/{order_id}/quantity-amendments", headers=auth_headers(c.buyer),
                                   json={"expected_revision": 1, "expected_total": 900, "client_request_id": str(uuid4()),
                                         "items": [{"order_item_id": item["id"], "quantity": 3} for item in first.json()["items"]]})
    assert amended.status_code == 200, amended.text
    replay = await submit(c)
    assert replay.status_code == 201 and replay.json()["id"] == order_id
    assert replay.json()["revision"] == 2 and replay.json()["amount_total"] == 900
    assert (await c.session.get(Order, order_id)).request_fingerprint == original_fingerprint
    started = await c.client.post(f"/v1/orders/{order_id}/payment-attempts", headers=auth_headers(c.buyer))
    assert started.status_code == 201
    attempt = await c.session.get(PaymentAttempt, started.json()["id"])
    assert attempt.order_revision == 2 and attempt.amount == 900
    assert await c.session.scalar(select(func.count(Order.id))) == 1


@pytest.mark.parametrize("omitted", ["both", "client_request_id", "expected_total"])
async def test_legacy_optional_field_compatibility_is_explicit(checkout, omitted):
    c = checkout
    payload = dict(c.payload)
    for key in (("client_request_id", "expected_total") if omitted == "both" else (omitted,)):
        payload.pop(key)
    first, second = await submit(c, payload), await submit(c, payload)
    assert first.status_code == second.status_code == 201
    same_order = first.json()["id"] == second.json()["id"]
    assert same_order is (omitted == "expected_total")


@pytest.mark.parametrize("change", ["offset_prices", "tax", "membership"])
async def test_quote_confirmation_guards_changes_even_when_total_stays_equal(checkout, change):
    from app.models import Membership, MembershipStatus, TaxType
    c = checkout
    body = {**c.payload, "expected_total": 300,
            "items": [{"product_id": product.id, "quantity": 1} for product in c.products]}
    before = (await c.client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": body["items"]})).json()
    # A valid-shaped confirmation was ignored before the new contract existed.
    body["expected_quote_fingerprint"] = before.get("confirmation_fingerprint", "0" * 64)
    control = await submit(c, body)
    assert control.status_code == 201, control.text
    if change == "offset_prices":
        c.products[0].nonmember_price = 120
        c.products[1].member_price = c.products[1].nonmember_price = 180
    elif change == "tax":
        c.products[0].tax_type = TaxType.TAX_EXEMPT
    else:
        c.buyer.membership = Membership(status=MembershipStatus.TRAINEE)
    await c.session.commit()
    current = (await c.client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": body["items"]})).json()
    assert current["amount_total"] == before["amount_total"] == 300
    if change != "tax":
        assert current["tax_amount"] == before["tax_amount"]
    rejected = await submit(c, {**body, "client_request_id": str(uuid4())})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"] == "價格已變動，請重新確認訂單明細"
    assert await c.session.scalar(select(func.count(Order.id))) == 1
    replay = await submit(c, body)
    assert replay.status_code == 201 and replay.json()["id"] == control.json()["id"]
    confirmed = await submit(c, {**body, "client_request_id": str(uuid4()), "expected_quote_fingerprint": current["confirmation_fingerprint"]})
    assert confirmed.status_code == 201, confirmed.text
    assert await c.session.scalar(select(func.count(Order.id))) == 2


async def test_quote_confirmation_is_cart_order_independent(checkout):
    c = checkout
    first = (await c.client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": c.payload["items"]})).json()
    reordered = [c.payload["items"][1], {"product_id": c.products[0].id, "quantity": 1}, {"product_id": c.products[0].id, "quantity": 1}]
    second = (await c.client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": reordered})).json()
    assert len(first["confirmation_fingerprint"]) == 64
    assert second["confirmation_fingerprint"] == first["confirmation_fingerprint"]


async def test_quote_confirmation_is_part_of_submission_identity(checkout):
    c = checkout
    quote = (await c.client.post("/v1/orders/quote", headers=auth_headers(c.buyer), json={"items": c.payload["items"]})).json()
    body = {**c.payload, "expected_quote_fingerprint": quote.get("confirmation_fingerprint", "f" * 64)}
    original = await submit(c, body)
    assert original.status_code == 201
    changed = await submit(c, {**body, "expected_quote_fingerprint": "0" * 64})
    assert changed.status_code == 409
    assert changed.json()["detail"] == "此送單識別碼已使用，請重新確認訂單"


async def test_tagless_submission_fingerprint_remains_compatible_with_initial_contract(checkout):
    import hashlib
    import json
    from app.schemas import OrderCreate
    c = checkout
    legacy = OrderCreate.model_validate(c.payload).model_dump(mode="json", exclude={"client_request_id", "expected_quote_fingerprint"})
    legacy["items"] = sorted(legacy["items"], key=lambda item: item["product_id"])
    legacy_hash = hashlib.sha256(json.dumps({"channel": "regular", "body": legacy}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    first = await submit(c)
    stored = await c.session.get(Order, first.json()["id"])
    stored.request_fingerprint = legacy_hash
    await c.session.commit()
    replay = await submit(c)
    assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]
