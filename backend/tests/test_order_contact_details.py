import base64
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.integrations.common import IntegrationError
from app.integrations.pii_crypto import pii_cipher_from_settings
from app.models import Order
from app.routers.operations import operations_router
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import cash_context, create_cash


pytestmark = pytest.mark.asyncio


def contact_settings(c):
    c.settings.pii_encryption_keys_json = json.dumps({"v1": base64.b64encode(b"C" * 32).decode()})
    return c.settings


@pytest.mark.parametrize("payment_method", ["online", "cash_on_pickup"])
async def test_checkout_phone_is_encrypted_and_only_admin_search_exposes_it(cash_context, payment_method):
    c = cash_context
    settings = contact_settings(c)
    request_id = str(uuid4())
    payload = {"contact_phone": "0912 345 678", "payment_method": payment_method, "client_request_id": request_id}
    response = await create_cash(c, **payload)
    assert response.status_code == 201, response.text
    order_id = response.json()["id"]
    order = await c.session.get(Order, order_id)
    stored = order.fulfillment.recipient_phone_encrypted
    assert stored.startswith("pii:1:v1:") and "0912345678" not in stored
    cipher = pii_cipher_from_settings(settings)
    assert cipher.decrypt_text(stored, associated_data=f"order-fulfillment:{order.fulfillment.id}:recipient_phone") == "0912345678"
    with pytest.raises(IntegrationError):
        cipher.decrypt_text(stored, associated_data="order-fulfillment:different:recipient_phone")
    assert "0912345678" not in response.text and stored not in response.text

    repeated = await create_cash(c, **payload)
    changed = await create_cash(c, **(payload | {"contact_phone": "0987654321"}))
    assert repeated.status_code == 201 and repeated.json()["id"] == order_id
    assert changed.status_code == 409
    assert await c.session.scalar(select(func.count(Order.id))) == 1

    async with api_test_context(c.session, [operations_router, orders_router], settings=settings) as client:
        listing = await client.get("/v1/admin/order-search", headers=auth_headers(c.admin))
        assert listing.status_code == 200, listing.text
        assert listing.headers["cache-control"] == "no-store"
        row = listing.json()["items"][0]
        assert (row["customer_name"], row["contact_phone"], row["contact_phone_status"]) == (c.buyer.display_name, "0912345678", "available")
        assert stored not in listing.text
        denied = await client.get("/v1/admin/order-search", headers=auth_headers(c.buyer))
        assert denied.status_code == 403 and "0912345678" not in denied.text
        own = await client.get(f"/v1/orders/{order_id}", headers=auth_headers(c.buyer))
        assert own.status_code == 200 and "contact_phone" not in own.json()
        assert "0912345678" not in own.text and stored not in own.text


@pytest.mark.parametrize("phone", [None, "", "   ", "02-23456789"])
async def test_checkout_keeps_legacy_no_phone_and_landline_support(cash_context, phone):
    c = cash_context
    contact_settings(c)
    response = await create_cash(c, contact_phone=phone)
    assert response.status_code == 201, response.text
    order = await c.session.get(Order, response.json()["id"])
    assert bool(order.fulfillment.recipient_phone_encrypted) == bool(phone and phone.strip())


@pytest.mark.parametrize("phone", ["123", "091234567", "09123456789", "abc", "tel:0912345678", "09１２３４５６７８"])
async def test_bad_contact_phone_does_not_create_order(cash_context, phone):
    response = await create_cash(cash_context, contact_phone=phone)
    assert response.status_code == 422
    assert await cash_context.session.scalar(select(func.count(Order.id))) == 0


async def test_bad_phone_cipher_does_not_hide_order_or_expose_cipher(cash_context):
    c = cash_context
    settings = contact_settings(c)
    created = await create_cash(c)
    assert created.status_code == 201, created.text
    order = await c.session.get(Order, created.json()["id"])
    async with api_test_context(c.session, [operations_router], settings=settings) as client:
        legacy = await client.get("/v1/admin/order-search", headers=auth_headers(c.admin))
        assert legacy.json()["items"][0]["contact_phone_status"] == "missing"
        order.fulfillment.recipient_phone_encrypted = "invalid-private-cipher"
        await c.session.commit()
        result = await client.get("/v1/admin/order-search", headers=auth_headers(c.admin))
        assert result.status_code == 200, result.text
        row = result.json()["items"][0]
        assert row["id"] == order.id and row["contact_phone"] is None
        assert row["contact_phone_status"] == "unavailable"
        assert "invalid-private-cipher" not in result.text
