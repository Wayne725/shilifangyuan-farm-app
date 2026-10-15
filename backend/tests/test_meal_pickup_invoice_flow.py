from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import app.routers.payments as payments_module
from app.integrations.common import HTTPResponse
from app.integrations.raygate import RAYGATE_IDENTIFIER, RayGateAdapter, hash_digest
from app.models import (
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    Invoice,
    InvoiceStatus,
    MealEvent,
    MealEventStatus,
    MembershipType,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    PaymentAttempt,
    PaymentStatus,
    SalesChannel,
    TaxType,
    User,
    UserRole,
)
from app.routers.meals import meals_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_raygate import IV_HEX, KEY_HEX, MERCHANT_ID, STORE_IDENTIFIER, TERMINAL_ID, payment_result, raygate_settings


@pytest.fixture
async def pickup_context(database_session):
    now = datetime.now(timezone.utc)
    buyer = User(email="meal-buyer@example.test", display_name="取餐買家", password_hash="test")
    other = User(email="meal-other@example.test", display_name="另一買家", password_hash="test")
    admin = User(email="meal-admin@example.test", display_name="取餐管理員", password_hash="test", user_role=UserRole.ADMIN)
    database_session.add_all([buyer, other, admin])
    await database_session.flush()
    event = MealEvent(
        title="離線取餐驗收",
        location="測試取餐點",
        ordering_starts_at=now - timedelta(hours=3),
        ordering_ends_at=now - timedelta(hours=1),
        pickup_starts_at=now - timedelta(minutes=10),
        pickup_ends_at=now + timedelta(hours=1),
        status=MealEventStatus.PICKUP_OPEN,
        created_by_id=admin.id,
    )
    order = Order(
        id="meal-pickup-flow-order",
        order_number="M260912OFFLINE01",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        user=buyer,
        meal_event=event,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=120,
        contact_email=buyer.email,
        payment_status=PaymentStatus.PAID,
        invoice_status=InvoiceStatus.PENDING,
        paid_at=now,
        fulfillment_status=FulfillmentStatus.PREPARING,
        items=[OrderItem(product_name="離線測試便當", unit_label="份", quantity=1, unit_price=120, subtotal=120, tax_type=TaxType.TAXABLE)],
        fulfillment=OrderFulfillment(
            method=FulfillmentMethod.EVENT_PICKUP,
            status=FulfillmentState.READY_FOR_PICKUP,
            pickup_code="723456",
            pickup_qr_token_hash=hashlib.sha256(b"slf-meal:meal-pickup-flow-order:723456").hexdigest(),
        ),
    )
    database_session.add_all([buyer, other, admin, event, order])
    await database_session.commit()
    async with api_test_context(database_session, [meals_router], settings=make_test_settings()) as client:
        yield client, database_session, buyer, other, admin, event, order


@pytest.mark.asyncio
@pytest.mark.parametrize("fulfillment_status", [FulfillmentState.CANCELLED, FulfillmentState.NO_SHOW, FulfillmentState.AWAITING_SHIPMENT, FulfillmentState.SHIPPED, FulfillmentState.DELIVERED])
async def test_paid_but_closed_fulfillment_cannot_redeem(pickup_context, fulfillment_status):
    client, session, buyer, _other, admin, event, order = pickup_context
    order.fulfillment.status = fulfillment_status
    await session.commit()

    credential = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(buyer))
    redeem = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": "723456"}, headers=auth_headers(admin))

    assert credential.status_code == 409
    assert redeem.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("event_id", ["meal-event-preorder-demo", "meal-event-dinner-demo", "meal-event-pickup-demo"])
@pytest.mark.parametrize("action", ["quote", "orders"])
async def test_production_rejects_new_demo_meal_transactions(pickup_context, event_id, action):
    _client, session, buyer, _other, _admin, _event, _order = pickup_context
    settings = make_test_settings().model_copy(update={"environment": "production", "sales_scope": "meals_only"})
    async with api_test_context(session, [meals_router], settings=settings) as client:
        response = await client.post(
            f"/v1/meal-events/{event_id}/{action}",
            json={"items": [{"offering_id": "demo-offering", "quantity": 1}], "contact_email": "buyer@example.com", "invoice_carrier_type": "cloud"},
            headers=auth_headers(buyer),
        )
    assert response.status_code == 409
    assert "展示" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invoice_status", [InvoiceStatus.PENDING, InvoiceStatus.ISSUED, InvoiceStatus.FAILED])
async def test_invoice_details_are_independent_of_paid_pickup(pickup_context, invoice_status):
    client, session, buyer, other, admin, event, order = pickup_context
    order.invoice_status = invoice_status
    if invoice_status == InvoiceStatus.ISSUED:
        order.invoice = Invoice(
            relate_number="INVMEALOFFLINE01",
            provider="fanyu",
            status=invoice_status,
            invoice_number="AB12345678",
            invoice_date=datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc),
            random_number="9384",
            buyer_email="private-invoice@example.test",
            provider_response={"private_secret": "not-for-customer"},
        )
    await session.commit()

    detail = await client.get(f"/v1/meal-orders/{order.id}", headers=auth_headers(buyer))
    assert detail.status_code == 200
    assert detail.json()["invoice_status"] == invoice_status.value
    assert detail.json()["invoice_number"] == ("AB12345678" if invoice_status == InvoiceStatus.ISSUED else None)
    assert (detail.json()["invoice_date"] is not None) == (invoice_status == InvoiceStatus.ISSUED)
    assert "private-invoice" not in detail.text
    assert "not-for-customer" not in detail.text
    assert "9384" not in detail.text
    credential = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(buyer))
    assert credential.status_code == 200
    other_credential = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(other))
    assert other_credential.status_code == 404
    customer_redeem = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": "723456"}, headers=auth_headers(buyer))
    assert customer_redeem.status_code == 403
    redeemed = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"qr_token": credential.json()["qr_token"]}, headers=auth_headers(admin))
    assert redeemed.status_code == 200
    duplicate = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": "723456"}, headers=auth_headers(admin))
    assert duplicate.status_code == 409
    after = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(buyer))
    assert after.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("closed_marker", ["order_cancelled", "event_cancelled", "event_completed"])
async def test_closed_orders_do_not_expose_pickup_credentials(pickup_context, closed_marker):
    client, session, buyer, _other, _admin, event, order = pickup_context
    if closed_marker == "order_cancelled":
        order.cancelled_at = datetime.now(timezone.utc)
    else:
        event.status = MealEventStatus.CANCELLED if closed_marker == "event_cancelled" else MealEventStatus.COMPLETED
    await session.commit()

    detail = await client.get(f"/v1/meal-orders/{order.id}", headers=auth_headers(buyer))
    credential = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(buyer))

    assert detail.status_code == 200
    assert detail.json()["pickup_code"] is None
    assert detail.json()["pickup_qr_payload"] is None
    assert credential.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_status", [PaymentStatus.PENDING, PaymentStatus.FAILED, PaymentStatus.EXPIRED, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED])
async def test_unpaid_or_refunding_orders_cannot_get_or_redeem_credentials(pickup_context, payment_status):
    client, session, buyer, _other, admin, event, order = pickup_context
    order.payment_status = payment_status
    await session.commit()

    detail = await client.get(f"/v1/meal-orders/{order.id}", headers=auth_headers(buyer))
    assert detail.json()["pickup_code"] is None
    assert detail.json()["pickup_qr_payload"] is None
    credential = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(buyer))
    assert credential.status_code == 409
    for body in [{"pickup_code": "723456"}, {"qr_token": "slf-meal:meal-pickup-flow-order:723456"}]:
        redeem = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json=body, headers=auth_headers(admin))
        assert redeem.status_code == 404


@pytest.mark.asyncio
async def test_verified_payment_callback_activates_pickup_before_invoice_issues(pickup_context, monkeypatch):
    _client, session, buyer, _other, admin, event, order = pickup_context
    order.payment_status = PaymentStatus.PENDING
    order.paid_at = None
    order.invoice_status = InvoiceStatus.NOT_ELIGIBLE
    order.fulfillment.status = FulfillmentState.PENDING_CONFIRMATION
    now = datetime.now(timezone.utc)
    result = payment_result(
        amount=120,
        pos_order_number="260912MEALFLOW01",
        transaction_time=now.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S"),
    )
    session.add(PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260912MEALFLOW01",
        amount=120,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=15),
    ))
    await session.commit()

    async def provider_transport(_url, _payload, _headers, _timeout):
        return HTTPResponse(status_code=200, body=json.dumps({"ErrorCode": "0000", "Message": "成功", "Data": [result]}), headers={})

    adapter = RayGateAdapter(raygate_settings(), transport=provider_transport)
    monkeypatch.setattr(payments_module, "raygate_payment_adapter_from_settings", lambda _settings: adapter)
    settings = make_test_settings(
        payment_provider="raygate",
        raygate_payment_store_identifier=STORE_IDENTIFIER,
        raygate_payment_key_hex=KEY_HEX,
        raygate_payment_iv_hex=IV_HEX,
        raygate_payment_merchant_id=MERCHANT_ID,
        raygate_payment_terminal_id=TERMINAL_ID,
        raygate_payment_device_type="WEB",
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
    )
    async with api_test_context(session, [meals_router, payments_router], settings=settings) as client:
        before = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(buyer))
        assert before.status_code == 409
        encrypted = adapter.encrypt_transaction(result)
        callback = await client.post("/webhooks/raygate/payment", json={"TransactionData": encrypted, "HashDigest": hash_digest(encrypted)}, headers={"X-ePay-Identifier": RAYGATE_IDENTIFIER})
        assert callback.status_code == 200
        assert callback.text == "OK"
        paid = await client.get(f"/v1/meal-orders/{order.id}", headers=auth_headers(buyer))
        assert paid.json()["payment_status"] == "paid"
        assert paid.json()["paid_at"] is not None
        assert paid.json()["invoice_status"] == "pending"
        assert paid.json()["invoice_number"] is None
        credential = await client.get(f"/v1/meal-orders/{order.id}/pickup-credential", headers=auth_headers(buyer))
        assert credential.status_code == 200
        assert credential.json()["pickup_code"] == "723456"
        redeemed = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"qr_token": credential.json()["qr_token"]}, headers=auth_headers(admin))
        assert redeemed.status_code == 200
        duplicate = await client.post(f"/v1/admin/meal-events/{event.id}/redeem", json={"pickup_code": "723456"}, headers=auth_headers(admin))
        assert duplicate.status_code == 409
