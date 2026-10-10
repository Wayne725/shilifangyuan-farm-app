from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.routers.logistics as logistics_module
from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter, FanyuInvoiceSettings
from app.integrations.invoice_service import (
    invoice_request_from_order,
    issue_paid_order_invoice,
)
from app.models import (
    FulfillmentMethod,
    FulfillmentState,
    GroupCampaign,
    Invoice,
    InvoiceStatus,
    Meal,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    Membership,
    MembershipStatus,
    MembershipType,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    PaymentStatus,
    Product,
    ProductCategory,
    Shipment,
    ShipmentStatus,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
    TargetType,
    TaxType,
    User,
)
from app.routers.groups import groups_router
from app.routers.logistics import logistics_router
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, fanyu_test_context, make_test_settings, tomorrow_meal_timeline
from tests.test_logistics_router import FakeLogisticsAdapter


CHANNELS = ["regular", "meal", "group"]
ROUTERS = [orders_router, meals_router, groups_router, logistics_router]


def tax_settings():
    context = fanyu_test_context()
    return make_test_settings(
        invoice_provider="fanyu",
        fanyu_invoice_base_url=context["base_url"],
        fanyu_invoice_company_id=context["company_id"],
        fanyu_invoice_seller_id=context["seller_id"],
        fanyu_invoice_user_id=context["user_id"],
        pii_encryption_keys_json=json.dumps({"v1": base64.b64encode(b"p" * 32).decode("ascii")}),
    )


def invoice_adapter(transport=None):
    return FanyuInvoiceAdapter(
        FanyuInvoiceSettings(
            company_id="15989995",
            seller_id="15989995",
            user_id="15989995ADMIN",
            auth_password="isolated-test-password",
            api_key="isolated-test-key",
            signature_verified=True,
        ),
        transport=transport,
    )


async def checkout_context(
    session,
    channel,
    *,
    membership_state="active",
    buyer_type="personal",
    base_tax_type=TaxType.TAXABLE,
    mixed=False,
):
    now = datetime.now(timezone.utc)
    user = User(
        email="buyer@example.com",
        display_name="稅別測試買家",
        password_hash="test",
        membership_type=(
            MembershipType.NONMEMBER
            if membership_state == "nonmember"
            else MembershipType.MEMBER
        ),
    )
    if membership_state not in {"nonmember", "legacy"}:
        user.membership = Membership(status=MembershipStatus(membership_state))
    tax_types = [base_tax_type, TaxType.TAX_EXEMPT] if mixed else [base_tax_type]
    products = [
        Product(
            slug=f"invoice-tax-product-{index}",
            name=f"稅別測試商品 {index}",
            category=ProductCategory.PROCESSED,
            unit="包",
            member_price=105 if index == 0 else 80,
            nonmember_price=105 if index == 0 else 80,
            stock_quantity=20,
            tax_type=tax_type,
            can_ship=True,
            shipping_temperature=ShippingTemperature.AMBIENT,
            allowed_shipping_channels=["home_delivery", "seven_eleven"],
        )
        for index, tax_type in enumerate(tax_types)
    ]
    session.add_all([user, *products])
    await session.flush()
    payload = {"contact_email": user.email, "invoice_buyer_type": buyer_type}
    if buyer_type == "company":
        payload.update(
            invoice_buyer_tax_id="12345675",
            invoice_buyer_name="稅別測試公司",
            invoice_buyer_email="accounting@example.com",
        )
    sources = products
    if channel == "regular":
        quote_path, create_path = "/v1/orders/quote", "/v1/orders"
        payload["items"] = [{"product_id": product.id, "quantity": 1} for product in products]
    elif channel == "group":
        campaign = GroupCampaign(
            target_type=TargetType.PRODUCT,
            target_id=products[0].id,
            title="稅別測試團購",
            member_price=105,
            nonmember_price=105,
            min_paid_quantity=1,
            supply_cap=20,
            deadline=now + timedelta(days=1),
            estimated_pickup_start=now + timedelta(days=2),
            estimated_pickup_end=now + timedelta(days=3),
            created_by_id=user.id,
            can_ship=True,
            shipping_temperature=ShippingTemperature.AMBIENT,
            allowed_shipping_channels=["home_delivery", "seven_eleven"],
        )
        session.add(campaign)
        await session.flush()
        quote_path = f"/v1/group-campaigns/{campaign.id}/quote"
        create_path = f"/v1/group-campaigns/{campaign.id}/join"
        payload["quantity"] = 1
    else:
        event = MealEvent(
            title="稅別測試便當",
            location="測試取餐點",
            status=MealEventStatus.PUBLISHED,
            **tomorrow_meal_timeline(now),
            created_by_id=user.id,
        )
        offerings = [
            MealEventOffering(
                event=event,
                meal=Meal(
                    slug=f"invoice-tax-meal-{index}",
                    name=f"稅別測試便當 {index}",
                    price=product.member_price,
                    tax_type=product.tax_type,
                ),
                price=product.member_price,
                capacity=20,
            )
            for index, product in enumerate(products)
        ]
        session.add_all(offerings)
        await session.flush()
        quote_path = f"/v1/meal-events/{event.id}/quote"
        create_path = f"/v1/meal-events/{event.id}/orders"
        payload["items"] = [{"offering_id": offering.id, "quantity": 1} for offering in offerings]
        sources = [offering.meal for offering in offerings]
    await session.commit()
    return SimpleNamespace(
        user=user,
        sources=sources,
        quote_path=quote_path,
        create_path=create_path,
        payload=payload,
    )


async def stored_order(session, order_id):
    return await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.fulfillment).selectinload(OrderFulfillment.shipment),
        )
        .execution_options(populate_existing=True)
    )


async def quote_and_create(client, context):
    headers = auth_headers(context.user)
    quote = await client.post(context.quote_path, json=context.payload, headers=headers)
    assert quote.status_code == 200, quote.text
    created = await client.post(context.create_path, json=context.payload, headers=headers)
    assert created.status_code == 201, created.text
    return quote.json(), created.json()


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize(
    ("membership_state", "buyer_type", "base_tax_type", "is_member_exempt", "expected_tax_type"),
    [
        ("active", "personal", TaxType.TAXABLE, True, TaxType.TAX_EXEMPT),
        ("trainee", "personal", TaxType.TAXABLE, False, TaxType.TAXABLE),
        ("suspended", "personal", TaxType.TAXABLE, False, TaxType.TAXABLE),
        ("pending_payment", "personal", TaxType.TAXABLE, False, TaxType.TAXABLE),
        ("legacy", "personal", TaxType.TAXABLE, False, TaxType.TAXABLE),
        ("nonmember", "personal", TaxType.TAXABLE, False, TaxType.TAXABLE),
        ("nonmember", "personal", TaxType.TAX_EXEMPT, False, TaxType.TAX_EXEMPT),
        ("active", "company", TaxType.TAXABLE, False, TaxType.TAXABLE),
        ("active", "company", TaxType.TAX_EXEMPT, False, TaxType.TAX_EXEMPT),
    ],
)
async def test_checkout_quote_order_snapshot_and_fanyu_share_tax_policy(
    database_session,
    channel,
    membership_state,
    buyer_type,
    base_tax_type,
    is_member_exempt,
    expected_tax_type,
):
    context = await checkout_context(
        database_session,
        channel,
        membership_state=membership_state,
        buyer_type=buyer_type,
        base_tax_type=base_tax_type,
    )
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        quote, created = await quote_and_create(client, context)
    order = await stored_order(database_session, created["id"])
    expected_tax_amount = 5 if expected_tax_type == TaxType.TAXABLE else 0
    assert quote["member_tax_exempt"] is is_member_exempt
    assert created["member_tax_exempt_snapshot"] is is_member_exempt
    assert quote["tax_amount"] == created["tax_amount"] == order.tax_amount == expected_tax_amount
    assert quote["amount_total"] == created["amount_total"] == order.amount_total == 105
    quoted_tax_type = quote["tax_type"] if channel == "group" else quote["items"][0]["tax_type"]
    assert quoted_tax_type == expected_tax_type.value
    assert order.member_tax_exempt_snapshot is is_member_exempt
    assert order.items[0].tax_type == expected_tax_type
    assert context.sources[0].tax_type == base_tax_type
    expected_membership = {
        "active": MembershipType.MEMBER,
        "trainee": MembershipType.TRAINEE,
    }.get(membership_state, MembershipType.NONMEMBER)
    assert order.membership_type_snapshot == expected_membership

    request = invoice_request_from_order(order, "INVTAXPOLICY01")
    data = invoice_adapter().build_issue_data(request)
    invoice_tax_type = "1" if expected_tax_type == TaxType.TAXABLE else "3"
    assert [line.tax_type for line in request.items] == [invoice_tax_type]
    assert data["taxType"] == data["Details"][0]["taxType"] == invoice_tax_type
    assert data["totalAmount"] == "105"
    assert data["freetaxSalesamount"] == ("105" if invoice_tax_type == "3" else "0")
    assert data["process_type"] == ("B" if buyer_type == "company" else "C")
    if buyer_type == "company" and invoice_tax_type == "1":
        assert data["salesAmount"] == "100"
        assert data["taxAmount"] == "5"
    else:
        assert data["salesAmount"] == ("105" if invoice_tax_type == "1" else "0")
        assert data["taxAmount"] == "0"


@pytest.mark.parametrize("channel", ["regular", "meal"])
@pytest.mark.parametrize(
    ("membership_state", "buyer_type", "expected_tax_types", "expected_invoice_tax_type"),
    [
        ("active", "personal", ["3", "3"], "3"),
        ("nonmember", "personal", ["1", "3"], "9"),
        ("active", "company", ["1", "3"], "9"),
    ],
)
async def test_mixed_cart_tax_follows_buyer_and_preserves_amounts(
    database_session, channel, membership_state, buyer_type, expected_tax_types, expected_invoice_tax_type
):
    context = await checkout_context(
        database_session, channel, membership_state=membership_state, buyer_type=buyer_type, mixed=True
    )
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        quote, created = await quote_and_create(client, context)
    order = await stored_order(database_session, created["id"])
    request = invoice_request_from_order(order, "INVMIXEDTAX01")
    data = invoice_adapter().build_issue_data(request)
    assert [line.tax_type for line in request.items] == expected_tax_types
    assert data["taxType"] == expected_invoice_tax_type
    assert data["freetaxSalesamount"] == ("185" if expected_invoice_tax_type == "3" else "80")
    assert data["totalAmount"] == "185"
    assert quote["amount_total"] == created["amount_total"] == order.amount_total == 185
    assert quote["tax_amount"] == order.tax_amount == (0 if expected_invoice_tax_type == "3" else 5)
    assert data["taxAmount"] == ("5" if buyer_type == "company" else "0")
    assert data["salesAmount"] == (
        "0" if expected_invoice_tax_type == "3" else "100" if buyer_type == "company" else "105"
    )


async def test_public_meal_quote_preserves_base_tax_without_login(database_session):
    context = await checkout_context(database_session, "meal")
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        response = await client.post(context.quote_path, json=context.payload)
    assert response.status_code == 200, response.text
    assert response.json()["member_tax_exempt"] is False
    assert response.json()["tax_amount"] == 5
    assert response.json()["items"][0]["tax_type"] == "taxable"


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("initial_state", ["active", "nonmember"])
async def test_paid_order_tax_snapshot_survives_membership_and_catalog_changes(
    database_session, channel, initial_state
):
    context = await checkout_context(database_session, channel, membership_state=initial_state)
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        _quote, created = await quote_and_create(client, context)
    order = await stored_order(database_session, created["id"])
    order.payment_status = PaymentStatus.PAID
    order.invoice_status = InvoiceStatus.PENDING
    order.paid_at = datetime.now(timezone.utc)
    if initial_state == "active":
        context.user.membership.status = MembershipStatus.RESIGNED
    else:
        context.user.membership = Membership(status=MembershipStatus.ACTIVE)
    context.sources[0].tax_type = TaxType.TAX_EXEMPT
    await database_session.commit()
    order = await stored_order(database_session, order.id)
    request = invoice_request_from_order(order, "INVSNAPSHOTTAX01")
    data = invoice_adapter().build_issue_data(request)
    assert order.member_tax_exempt_snapshot is (initial_state == "active")
    assert order.tax_amount == (0 if initial_state == "active" else 5)
    assert data["taxType"] == ("3" if initial_state == "active" else "1")
    assert data["totalAmount"] == "105"


async def test_legacy_member_order_default_keeps_product_and_shipping_taxable(database_session):
    context = await checkout_context(database_session, "regular")
    order = Order(
        order_number="LEGACYMEMBERTAX01",
        order_kind=OrderKind.REGULAR,
        user_id=context.user.id,
        membership_type_snapshot=MembershipType.MEMBER,
        amount_total=210,
        tax_amount=10,
        contact_email=context.user.email,
        fulfillment_method=FulfillmentMethod.ECPAY_LOGISTICS,
        items=[OrderItem(product_name="歷史應稅商品", unit_label="包", quantity=1, unit_price=105, subtotal=105, tax_type=TaxType.TAXABLE)],
        fulfillment=OrderFulfillment(
            method=FulfillmentMethod.ECPAY_LOGISTICS,
            status=FulfillmentState.PENDING_CONFIRMATION,
            shipment=Shipment(
                channel=ShippingChannel.HOME_DELIVERY,
                temperature=ShippingTemperature.AMBIENT,
                status=ShipmentStatus.DRAFT,
                shipping_fee=105,
            ),
        ),
    )
    database_session.add(order)
    await database_session.commit()
    order = await stored_order(database_session, order.id)
    request = invoice_request_from_order(order, "INVLEGACYMEMBERTAX01")
    data = invoice_adapter().build_issue_data(request)
    assert order.member_tax_exempt_snapshot is False
    assert [line.tax_type for line in request.items] == ["1", "1"]
    assert order.tax_amount == 10
    assert data["taxType"] == "1"
    assert data["freetaxSalesamount"] == "0"
    assert data["totalAmount"] == "210"


@pytest.mark.parametrize("channel", ["regular", "group"])
@pytest.mark.parametrize(
    ("membership_state", "buyer_type", "base_tax_type", "expected_member_exempt"),
    [
        ("active", "personal", TaxType.TAXABLE, True),
        ("nonmember", "personal", TaxType.TAXABLE, False),
        ("active", "company", TaxType.TAXABLE, False),
        ("nonmember", "personal", TaxType.TAX_EXEMPT, False),
        ("active", "company", TaxType.TAX_EXEMPT, False),
    ],
)
async def test_shipping_selection_and_changed_channel_recompute_order_tax_from_snapshot(
    database_session, monkeypatch, channel, membership_state, buyer_type, base_tax_type, expected_member_exempt
):
    context = await checkout_context(database_session, channel, membership_state=membership_state, buyer_type=buyer_type, base_tax_type=base_tax_type)
    product_tax = 0 if expected_member_exempt or base_tax_type == TaxType.TAX_EXEMPT else 5
    database_session.add_all([
        ShippingRate(channel=ShippingChannel.HOME_DELIVERY, temperature=ShippingTemperature.AMBIENT, fee=105, free_shipping_threshold=1000, effective_from=date.today() - timedelta(days=1), is_active=True),
        ShippingRate(channel=ShippingChannel.SEVEN_ELEVEN, temperature=ShippingTemperature.AMBIENT, fee=210, free_shipping_threshold=1000, effective_from=date.today() - timedelta(days=1), is_active=True),
    ])
    await database_session.commit()
    adapter = FakeLogisticsAdapter()
    adapter.selection_result.update(LogisticsType="CVS", LogisticsSubType="UNIMART")
    monkeypatch.setattr(logistics_module, "ecpay_logistics_adapter_from_settings", lambda _settings: adapter)
    context.payload["fulfillment_method"] = "ecpay_logistics"
    if channel == "group":
        context.payload["shipping_channel"] = "home_delivery"
    headers = auth_headers(context.user)
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        quote, created = await quote_and_create(client, context)
        if membership_state == "active":
            context.user.membership.status = MembershipStatus.RESIGNED
            await database_session.commit()
        selection = await client.post(
            f"/v1/orders/{created['id']}/logistics/selection",
            json={"channel": "home_delivery", "temperature": "ambient", "recipient_name": "測試社員", "recipient_phone": "0912345678", "shipping_address": "臺北市測試路一號"},
            headers=headers,
        )
        assert selection.status_code == 201, selection.text
        order = await stored_order(database_session, created["id"])
        assert order.member_tax_exempt_snapshot is expected_member_exempt
        assert order.amount_total == 210
        assert order.tax_amount == product_tax + (0 if expected_member_exempt else 5)
        if channel == "group":
            assert quote["amount_total"] == order.amount_total
            assert quote["tax_amount"] == order.tax_amount
        page = await client.get(urlparse(selection.json()["selection_url"]).path)
        assert page.status_code == 200, page.text
        callback_url = urlparse(adapter.selection_request.client_reply_url)
        callback_query = parse_qs(callback_url.query)
        callback = await client.post(
            callback_url.path,
            params={"order_id": callback_query["order_id"][0], "token": callback_query["token"][0]},
            json={"provider": "isolated-test"},
        )
        assert callback.status_code == 303, callback.text
    order = await stored_order(database_session, created["id"])
    request = invoice_request_from_order(order, "INVSHIPPINGTAX01")
    data = invoice_adapter().build_issue_data(request)
    assert order.fulfillment.shipment.channel == ShippingChannel.SEVEN_ELEVEN
    assert order.fulfillment.shipment.shipping_fee == 210
    assert order.amount_total == 315
    assert order.tax_amount == product_tax + (0 if expected_member_exempt else 10)
    if expected_member_exempt:
        assert [line.tax_type for line in request.items] == ["3", "3"]
        assert data["taxType"] == "3"
        assert data["freetaxSalesamount"] == "315"
    elif base_tax_type == TaxType.TAX_EXEMPT:
        assert [line.tax_type for line in request.items] == ["3", "1"]
        assert data["taxType"] == "9"
        assert data["freetaxSalesamount"] == "105"
    else:
        assert [line.tax_type for line in request.items] == ["1", "1"]
        assert data["taxType"] == "1"
        assert data["freetaxSalesamount"] == "0"
    assert data["totalAmount"] == "315"
    assert data["taxAmount"] == (str(product_tax + 10) if buyer_type == "company" else "0")


async def test_invoice_worker_persists_exempt_provider_request_and_items(database_session):
    context = await checkout_context(database_session, "meal")
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        _quote, created = await quote_and_create(client, context)
    order = await stored_order(database_session, created["id"])
    order.payment_status = PaymentStatus.PAID
    order.invoice_status = InvoiceStatus.PENDING
    order.paid_at = datetime.now(timezone.utc)
    await database_session.commit()
    requests = []

    async def transport(url, payload, *_args):
        requests.append((url, payload["reqData"]))
        if url.endswith("/queryInvoice"):
            return HTTPResponse(200, '{"statusCode":"3","statusDesc":"查無資料","respData":{}}', {})
        return HTTPResponse(200, '{"statusCode":"0","respData":{"invNo":"AB12345678","invDate":"20261007","randomNumber":"2468"}}', {})

    await issue_paid_order_invoice(database_session, order.id, invoice_adapter(transport))
    invoice = await database_session.scalar(
        select(Invoice).where(Invoice.order_id == order.id).options(selectinload(Invoice.items))
    )
    sent_payload = next(payload for url, payload in requests if url.endswith("/openInvoice"))
    assert invoice.status == InvoiceStatus.ISSUED
    assert invoice.provider_request == sent_payload
    assert invoice.provider_request["taxType"] == "3"
    assert invoice.provider_request["freetaxSalesamount"] == "105"
    assert invoice.provider_request["taxAmount"] == "0"
    assert invoice.tax_amount == 0
    assert invoice.total_amount == 105
    assert [item.tax_type for item in invoice.items] == ["3"]


async def test_invoice_retry_replaces_stale_exempt_tax_rate_after_provider_query(
    database_session,
):
    context = await checkout_context(database_session, "meal")
    async with api_test_context(database_session, ROUTERS, settings=tax_settings()) as client:
        _quote, created = await quote_and_create(client, context)
    order = await stored_order(database_session, created["id"])
    order.payment_status = PaymentStatus.PAID
    order.invoice_status = InvoiceStatus.FAILED
    order.paid_at = datetime.now(timezone.utc)
    request = invoice_request_from_order(order, "INVTAXRETRY01")
    stale_provider_request = invoice_adapter().build_issue_data(request)
    stale_provider_request["taxrate"] = "0.05"
    database_session.add(
        Invoice(
            order=order,
            relate_number=request.relate_number,
            provider="fanyu",
            provider_context=order.invoice_provider_context,
            status=InvoiceStatus.FAILED,
            provider_request=stale_provider_request,
        )
    )
    await database_session.commit()
    sent_payloads = []

    async def transport(url, payload, *_args):
        if url.endswith("/queryInvoice"):
            return HTTPResponse(200, '{"statusCode":"3","statusDesc":"查無資料","respData":{}}', {})
        sent_payloads.append(payload["reqData"])
        return HTTPResponse(
            200,
            '{"statusCode":"0","respData":{"invNo":"AB12345679",'
            '"invDate":"20261007","randomNumber":"2469"}}',
            {},
        )

    await issue_paid_order_invoice(database_session, order.id, invoice_adapter(transport))
    invoice = await database_session.scalar(
        select(Invoice).where(Invoice.order_id == order.id)
    )

    assert sent_payloads[0]["taxrate"] == "0"
    assert invoice.provider_request["taxrate"] == "0"
    assert invoice.status == InvoiceStatus.ISSUED
