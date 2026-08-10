from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

import app.routers.logistics as logistics_module
from app.auth import make_token_pair
from app.config import Settings, get_settings
from app.database import Base, get_session
from app.integrations.common import IntegrationResponseError
from app.integrations.ecpay_logistics import (
    decrypt_ecpay_logistics_data,
    encrypt_ecpay_logistics_data,
)
from app.models import (
    AdminAudit,
    ExternalEvent,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    InvoiceStatus,
    MembershipType,
    Order,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentAttempt,
    PaymentStatus,
    Product,
    SalesChannel,
    Shipment,
    ShipmentStatus,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
    TaxType,
    User,
    UserRole,
)
from app.routers.logistics import logistics_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router


class FakeLogisticsAdapter:
    def __init__(self) -> None:
        self.selection_request = None
        self.updated_temp_request = None
        self.created_order = None
        self.query_result = {
            "RtnCode": 1,
            "LogisticsID": "90001",
            "MerchantTradeNo": "SLF260731000001",
            "LogisticsStatus": "2030",
            "LogisticsStatusName": "商品已送至物流中心",
            "ShipmentNo": "TRACK-001",
        }
        self.selection_result = {
            "RtnCode": 1,
            "TempLogisticsID": "80001",
            "LogisticsType": "HOME",
            "LogisticsSubType": "TCAT",
            "ReceiverName": "測試社員",
            "ReceiverCellPhone": "0912345678",
            "ReceiverAddress": "臺北市測試路一號",
        }
        self.callback_result = {
            "RtnCode": 1,
            "MerchantID": "2000132",
            "MerchantTradeNo": "SLF260731000001",
            "LogisticsID": "90001",
            "LogisticsStatus": "2063",
            "LogisticsStatusName": "商品已送達門市",
            "UpdateStatusDate": "2026/07/31 12:00:00",
        }
        self.query_calls = 0
        self.create_order_error = None

    async def create_selection_page(self, request):
        self.selection_request = request
        return "<html><body><form>物流選擇</form></body></html>"

    def decode_selection_result(self, envelope):
        return dict(self.selection_result)

    async def update_temp_order(self, request):
        self.updated_temp_request = request
        return {"RtnCode": 1, "RtnMsg": "成功"}

    async def create_order(self, *, temp_logistics_id, merchant_trade_no):
        self.created_order = (temp_logistics_id, merchant_trade_no)
        if self.create_order_error is not None:
            raise self.create_order_error
        return {
            "RtnCode": 1,
            "RtnMsg": "成功",
            "LogisticsID": "90001",
        }

    async def query_order(
        self,
        *,
        logistics_id=None,
        merchant_trade_no=None,
    ):
        self.query_calls += 1
        if logistics_id is not None:
            assert logistics_id == "90001"
        else:
            assert merchant_trade_no == "SLF260731000001"
        return dict(self.query_result)

    async def create_print_document_page(
        self,
        *,
        logistics_ids,
        logistics_sub_type,
    ):
        assert logistics_ids == ["90001"]
        assert logistics_sub_type == "TCAT"
        return "<html><body>託運單</body></html>"

    def verify_callback(self, envelope):
        return dict(self.callback_result)

    def callback_acknowledgement(self, *, success=True):
        return {
            "MerchantID": "2000132",
            "RpHeader": {
                "Timestamp": str(int(datetime.now(timezone.utc).timestamp())),
            },
            "TransCode": 1,
            "TransMsg": "",
            "Data": encrypt_ecpay_logistics_data(
                {
                    "RtnCode": 1 if success else 0,
                    "RtnMsg": "成功" if success else "失敗",
                },
                "5294y06JbISpM5x9",
                "v77hoKGq4kWxNNIS",
            ),
        }


def make_test_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        ecpay_payment_merchant_id="3002607",
        ecpay_payment_hash_key="pwFHCqoQZGmho4w6",
        ecpay_payment_hash_iv="EkRm7iFT261dpevs",
        ecpay_logistics_merchant_id="2000132",
        ecpay_logistics_hash_key="5294y06JbISpM5x9",
        ecpay_logistics_hash_iv="v77hoKGq4kWxNNIS",
        pii_encryption_keys_json=json.dumps(
            {
                "v1": base64.b64encode(b"p" * 32).decode("ascii"),
            }
        ),
    )


@pytest.fixture
async def database_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def logistics_context(database_session, monkeypatch):
    settings = make_test_settings()
    adapter = FakeLogisticsAdapter()
    monkeypatch.setattr(
        logistics_module,
        "ecpay_logistics_adapter_from_settings",
        lambda _settings: adapter,
    )
    application = FastAPI()
    application.include_router(logistics_router)
    application.include_router(orders_router)
    application.include_router(payments_router)

    async def override_get_session():
        yield database_session

    application.dependency_overrides[get_session] = override_get_session
    application.dependency_overrides[get_settings] = lambda: settings

    admin = User(
        email="admin@example.com",
        display_name="管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    customer = User(
        email="customer@example.com",
        display_name="測試社員",
        password_hash="test",
        user_role=UserRole.CUSTOMER,
    )
    product = Product(
        slug="shipping-rice",
        name="配送白米",
        category="米・雜糧",
        unit="包",
        member_price=1300,
        nonmember_price=1400,
        stock_quantity=10,
        tax_type=TaxType.TAX_EXEMPT,
        can_ship=True,
        shipping_temperature=ShippingTemperature.AMBIENT,
        allowed_shipping_channels=[
            ShippingChannel.HOME_DELIVERY.value,
            ShippingChannel.SEVEN_ELEVEN.value,
        ],
    )
    database_session.add_all([admin, customer, product])
    await database_session.flush()
    order = Order(
        order_number="SLF260731000001",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user_id=customer.id,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=1400,
        contact_email=customer.email,
        payment_status=PaymentStatus.PENDING,
        items=[
            OrderItem(
                source_product_id=product.id,
                product_name=product.name,
                unit_label=product.unit,
                quantity=1,
                unit_price=1400,
                subtotal=1400,
                tax_type=product.tax_type,
            )
        ],
    )
    rate = ShippingRate(
        channel=ShippingChannel.HOME_DELIVERY,
        temperature=ShippingTemperature.AMBIENT,
        fee=160,
        free_shipping_threshold=1500,
        effective_from=date(2020, 1, 1),
    )
    database_session.add_all([order, rate])
    await database_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        yield {
            "client": client,
            "adapter": adapter,
            "admin": admin,
            "customer": customer,
            "order": order,
            "rate": rate,
            "session": database_session,
            "settings": settings,
        }


def auth_headers(user: User) -> dict[str, str]:
    token = make_token_pair(user)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def selection_payload(**overrides) -> dict:
    payload = {
        "channel": "home_delivery",
        "temperature": "ambient",
        "recipient_name": "測試社員",
        "recipient_phone": "0912345678",
        "shipping_address": "臺北市測試路一號",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_shipping_intent_is_saved_before_logistics_selection(
    logistics_context,
) -> None:
    context = logistics_context
    response = await context["client"].post(
        "/v1/orders",
        json={
            "items": [
                {
                    "product_id": context["order"].items[0].source_product_id,
                    "quantity": 1,
                }
            ],
            "contact_email": context["customer"].email,
            "invoice_carrier_type": "ecpay",
            "fulfillment_method": "ecpay_logistics",
        },
        headers=auth_headers(context["customer"]),
    )

    assert response.status_code == 201
    assert response.json()["fulfillment_method"] == "ecpay_logistics"
    assert response.json()["fulfillment"]["method"] == "ecpay_logistics"
    assert "pay" not in response.json()["available_actions"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipient_name", "A王"),
        ("recipient_name", "王😀明"),
        ("recipient_phone", "09123456"),
    ],
)
async def test_logistics_recipient_is_validated_before_ecpay(
    logistics_context,
    field: str,
    value: str,
) -> None:
    context = logistics_context
    response = await context["client"].post(
        f"/v1/orders/{context['order'].id}/logistics/selection",
        json=selection_payload(**{field: value}),
        headers=auth_headers(context["customer"]),
    )

    assert response.status_code == 422
    assert context["adapter"].selection_request is None


@pytest.mark.asyncio
async def test_selection_page_and_temp_result_encrypt_recipient(
    logistics_context,
) -> None:
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    adapter = context["adapter"]
    session = context["session"]

    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )

    assert selection.status_code == 201
    assert selection.json()["shipping_fee"] == 160
    assert selection.json()["amount_total"] == 1560
    selection_path = urlparse(selection.json()["selection_url"]).path
    # Deliberately unauthenticated: a browser navigation carries no token.
    page = await client.get(selection_path)
    assert page.status_code == 200
    assert "物流選擇" in page.text
    assert adapter.selection_request.goods_amount == 1400
    assert adapter.selection_request.temperature == "0001"
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    callback_query = parse_qs(callback_url.query)
    callback = await client.post(
        callback_url.path,
        params={
            "order_id": callback_query["order_id"][0],
            "token": callback_query["token"][0],
        },
        json={"provider": "mocked"},
    )

    assert callback.status_code == 303
    await session.refresh(order)
    shipment = await session.scalar(
        select(Shipment).options(selectinload(Shipment.fulfillment))
    )
    assert order.amount_total == 1560
    assert shipment.status == ShipmentStatus.READY_TO_CREATE
    assert shipment.shipping_fee == 160
    assert shipment.provider_payload["TempLogisticsID"] == "80001"
    fulfillment = shipment.fulfillment
    assert "測試社員" not in fulfillment.recipient_name_encrypted
    assert "0912345678" not in fulfillment.recipient_phone_encrypted
    assert fulfillment.encryption_key_version == "v1"


@pytest.mark.asyncio
async def test_selection_result_uses_actual_ecpay_channel_and_reprices_order(
    logistics_context,
) -> None:
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    adapter = context["adapter"]
    session = context["session"]
    session.add(
        ShippingRate(
            channel=ShippingChannel.SEVEN_ELEVEN,
            temperature=ShippingTemperature.AMBIENT,
            fee=60,
            free_shipping_threshold=2000,
            effective_from=date(2020, 1, 1),
        )
    )
    await session.commit()
    adapter.selection_result.update(
        {
            "LogisticsType": "CVS",
            "LogisticsSubType": "UNIMART",
        }
    )

    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(channel="home_delivery"),
        headers=auth_headers(customer),
    )
    page = await client.get(urlparse(selection.json()["selection_url"]).path)
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    callback = await client.post(
        callback_url.path,
        params={
            key: values[0]
            for key, values in parse_qs(callback_url.query).items()
        },
        json={"provider": "mocked"},
    )
    current = await client.get(
        f"/v1/orders/{order.id}",
        headers=auth_headers(customer),
    )

    assert page.status_code == 200
    assert callback.status_code == 303
    assert current.status_code == 200
    assert current.json()["shipment"]["channel"] == "seven_eleven"
    assert current.json()["shipment"]["shipping_fee"] == 60
    assert current.json()["amount_total"] == 1460


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("logistics_type", "logistics_sub_type", "expected_status"),
    [
        ("CVS", "FAMI", 409),
        ("CVS", "UNKNOWN", 422),
    ],
)
async def test_selection_result_rejects_invalid_channel_and_can_be_retried(
    logistics_context,
    logistics_type: str,
    logistics_sub_type: str,
    expected_status: int,
) -> None:
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    adapter = context["adapter"]
    adapter.selection_result.update(
        {
            "LogisticsType": logistics_type,
            "LogisticsSubType": logistics_sub_type,
        }
    )

    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(channel="home_delivery"),
        headers=auth_headers(customer),
    )
    await client.get(urlparse(selection.json()["selection_url"]).path)
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    rejected = await client.post(
        callback_url.path,
        params={
            key: values[0]
            for key, values in parse_qs(callback_url.query).items()
        },
        json={"provider": "mocked"},
    )
    retried = await client.post(
        f"/v1/orders/{order.id}/logistics/selection-link",
        headers=auth_headers(customer),
    )

    assert rejected.status_code == expected_status
    assert retried.status_code == 200
    assert retried.json()["shipment"]["status"] == "selection_pending"


@pytest.mark.asyncio
async def test_active_payment_attempt_blocks_logistics_repricing(
    logistics_context,
) -> None:
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    adapter = context["adapter"]
    session = context["session"]
    customer_headers = auth_headers(customer)
    session.add(
        ShippingRate(
            channel=ShippingChannel.SEVEN_ELEVEN,
            temperature=ShippingTemperature.AMBIENT,
            fee=60,
            free_shipping_threshold=2000,
            effective_from=date(2020, 1, 1),
        )
    )
    await session.commit()

    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(channel="home_delivery"),
        headers=customer_headers,
    )
    await client.get(urlparse(selection.json()["selection_url"]).path)
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    selected = await client.post(
        callback_url.path,
        params={
            key: values[0]
            for key, values in parse_qs(callback_url.query).items()
        },
        json={"provider": "mocked"},
    )
    payment = await client.post(
        f"/v1/orders/{order.id}/payment-attempts",
        headers=customer_headers,
    )
    payment_path = urlparse(payment.json()["payment_url"]).path
    original_checkout = await client.get(payment_path)

    reissued = await client.post(
        f"/v1/orders/{order.id}/logistics/selection-link",
        headers=customer_headers,
    )
    repriced = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(channel="seven_eleven"),
        headers=customer_headers,
    )
    current = await client.get(
        f"/v1/orders/{order.id}",
        headers=customer_headers,
    )
    unchanged_checkout = await client.get(payment_path)

    assert selected.status_code == 303
    assert payment.status_code == 201
    assert original_checkout.status_code == 200
    assert 'name="TotalAmount" value="1560"' in original_checkout.text
    assert reissued.status_code == 409
    assert reissued.json()["detail"] == "已有待確認付款，無法重新選擇物流"
    assert repriced.status_code == 409
    assert repriced.json()["detail"] == "已有待確認付款，無法重新選擇物流"
    assert current.json()["amount_total"] == 1560
    assert 'name="TotalAmount" value="1560"' in unchanged_checkout.text


@pytest.mark.asyncio
async def test_selection_result_rejects_payment_attempt_created_during_picker(
    logistics_context,
) -> None:
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    adapter = context["adapter"]
    session = context["session"]
    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )
    await client.get(urlparse(selection.json()["selection_url"]).path)
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    session.add(
        PaymentAttempt(
            order_id=order.id,
            merchant_trade_no="260810120000RACE0001",
            amount=1560,
            status=PaymentStatus.PENDING,
            checkout_payload={"TotalAmount": "1560"},
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
    )
    await session.commit()

    callback = await client.post(
        callback_url.path,
        params={
            key: values[0]
            for key, values in parse_qs(callback_url.query).items()
        },
        json={"provider": "mocked"},
    )

    assert callback.status_code == 409
    assert callback.json()["detail"] == "已有待確認付款，無法重新選擇物流"


@pytest.mark.asyncio
async def test_native_logistics_selection_returns_to_app_scheme(
    logistics_context,
) -> None:
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    adapter = context["adapter"]

    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )
    selection_path = urlparse(selection.json()["selection_url"]).path

    page = await client.get(selection_path, params={"client": "native"})

    assert page.status_code == 200
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    callback_query = parse_qs(callback_url.query)
    assert callback_query["client"] == ["native"]

    callback = await client.post(
        callback_url.path,
        params={key: values[0] for key, values in callback_query.items()},
        json={"provider": "mocked"},
    )

    assert callback.status_code == 303
    assert callback.headers["location"] == (
        f"shilifangyuan://logistics-return?order_id={order.id}"
        "&logistics=selected"
    )


async def prepare_formal_shipment(context) -> None:
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    adapter = context["adapter"]
    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )
    assert selection.status_code == 201
    page = await client.get(urlparse(selection.json()["selection_url"]).path)
    assert page.status_code == 200
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    callback_query = parse_qs(callback_url.query)
    callback = await client.post(
        callback_url.path,
        params={
            "order_id": callback_query["order_id"][0],
            "token": callback_query["token"][0],
        },
        json={"provider": "mocked"},
    )
    assert callback.status_code == 303
    order.payment_status = PaymentStatus.PAID
    await context["session"].commit()
    preparing = await client.post(
        f"/v1/orders/{order.id}/fulfillment",
        json={"status": "preparing"},
        headers=auth_headers(context["admin"]),
    )
    assert preparing.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_state", ["cancelled", "pickup_method"])
async def test_formal_logistics_creation_rejects_non_shippable_order(
    logistics_context,
    invalid_state: str,
) -> None:
    context = logistics_context
    await prepare_formal_shipment(context)
    order = context["order"]
    if invalid_state == "cancelled":
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        order.fulfillment.status = FulfillmentState.CANCELLED
    else:
        order.fulfillment_method = FulfillmentMethod.COOPERATIVE_PICKUP
        order.fulfillment.method = FulfillmentMethod.COOPERATIVE_PICKUP
    await context["session"].commit()

    response = await context["client"].post(
        f"/v1/admin/orders/{order.id}/logistics/create",
        headers=auth_headers(context["admin"]),
    )

    assert response.status_code == 409
    assert context["adapter"].created_order is None


@pytest.mark.asyncio
async def test_formal_query_print_callback_and_sandbox_audit(
    logistics_context,
) -> None:
    context = logistics_context
    await prepare_formal_shipment(context)
    client = context["client"]
    admin = context["admin"]
    customer = context["customer"]
    order = context["order"]
    adapter = context["adapter"]
    session = context["session"]

    forbidden = await client.post(
        f"/v1/admin/orders/{order.id}/logistics/create",
        headers=auth_headers(customer),
    )
    created = await client.post(
        f"/v1/admin/orders/{order.id}/logistics/create",
        headers=auth_headers(admin),
    )

    assert forbidden.status_code == 403
    assert created.status_code == 200
    assert created.json()["shipment"]["status"] == "created"
    assert adapter.updated_temp_request.temp_logistics_id == "80001"
    assert adapter.created_order == ("80001", order.order_number)

    queried = await client.get(
        f"/v1/orders/{order.id}/logistics",
        headers=auth_headers(customer),
    )
    printed = await client.get(
        f"/v1/admin/orders/{order.id}/logistics/print",
        headers=auth_headers(admin),
    )

    assert queried.status_code == 200
    assert queried.json()["shipment"]["status"] == "in_transit"
    assert queried.json()["shipment"]["tracking_number"] == "TRACK-001"
    assert printed.status_code == 200
    assert "託運單" in printed.text

    callback_first = await client.post(
        "/webhooks/ecpay/logistics",
        json={"provider": "mocked"},
    )
    callback_duplicate = await client.post(
        "/webhooks/ecpay/logistics",
        json={"provider": "mocked"},
    )
    event_count = await session.scalar(
        select(func.count(ExternalEvent.id)).where(
            ExternalEvent.provider == "ecpay_logistics"
        )
    )

    first_ack = callback_first.json()
    duplicate_ack = callback_duplicate.json()
    assert first_ack["MerchantID"] == "2000132"
    assert first_ack["TransCode"] == 1
    assert decrypt_ecpay_logistics_data(
        first_ack["Data"],
        context["settings"].ecpay_logistics_hash_key,
        context["settings"].ecpay_logistics_hash_iv,
    ) == {"RtnCode": 1, "RtnMsg": "成功"}
    assert decrypt_ecpay_logistics_data(
        duplicate_ack["Data"],
        context["settings"].ecpay_logistics_hash_key,
        context["settings"].ecpay_logistics_hash_iv,
    ) == {"RtnCode": 1, "RtnMsg": "成功"}
    assert event_count == 1

    delivered = await client.post(
        f"/v1/admin/orders/{order.id}/logistics/sandbox-status",
        json={"status": "delivered", "reason": "Sandbox 展示送達"},
        headers=auth_headers(admin),
    )
    audit = await session.scalar(
        select(AdminAudit).where(
            AdminAudit.action == "shipment.sandbox_status.delivered"
        )
    )
    invoice_jobs = await session.scalar(
        select(func.count(OutboxEvent.id)).where(
            OutboxEvent.event_type == "invoice.issue",
            OutboxEvent.aggregate_id == order.id,
        )
    )

    assert delivered.status_code == 200
    assert delivered.json()["status"] == "delivered"
    assert audit.reason == "Sandbox 展示送達"
    assert audit.data["sandbox"] is True
    assert invoice_jobs == 1
    assert order.invoice_status == InvoiceStatus.PENDING


@pytest.mark.asyncio
async def test_formal_logistics_creation_recovers_ambiguous_provider_success(
    logistics_context,
) -> None:
    context = logistics_context
    await prepare_formal_shipment(context)
    client = context["client"]
    admin = context["admin"]
    order = context["order"]
    order_id = order.id
    admin_headers = auth_headers(admin)
    adapter = context["adapter"]
    adapter.create_order_error = IntegrationResponseError(
        "connection lost after provider created the order"
    )

    first = await client.post(
        f"/v1/admin/orders/{order_id}/logistics/create",
        headers=admin_headers,
    )
    second = await client.post(
        f"/v1/admin/orders/{order_id}/logistics/create",
        headers=admin_headers,
    )

    assert first.status_code == 200
    assert first.json()["shipment"]["ecpay_logistics_id"] == "90001"
    assert second.status_code == 200
    assert second.json()["shipment"]["ecpay_logistics_id"] == "90001"


@pytest.mark.asyncio
async def test_logistics_callbacks_do_not_regress_newer_or_delivered_status(
    logistics_context,
) -> None:
    context = logistics_context
    await prepare_formal_shipment(context)
    client = context["client"]
    admin = context["admin"]
    customer = context["customer"]
    order = context["order"]
    adapter = context["adapter"]
    session = context["session"]
    created = await client.post(
        f"/v1/admin/orders/{order.id}/logistics/create",
        headers=auth_headers(admin),
    )
    assert created.status_code == 200

    async def send_status(code: str, updated_at: str) -> None:
        adapter.callback_result.update(
            {
                "LogisticsStatus": code,
                "UpdateStatusDate": updated_at,
            }
        )
        response = await client.post(
            "/webhooks/ecpay/logistics",
            json={"provider": "mocked"},
        )
        assert response.status_code == 200

    async def current_shipment_status() -> str:
        response = await client.get(
            f"/v1/orders/{order.id}",
            headers=auth_headers(customer),
        )
        assert response.status_code == 200
        return response.json()["shipment"]["status"]

    await send_status("2030", "2026/07/31 13:00:00")
    assert await current_shipment_status() == "in_transit"

    await send_status("2074", "2026/07/31 12:00:00")
    assert await current_shipment_status() == "in_transit"

    await send_status("2074", "not-a-provider-time")
    assert await current_shipment_status() == "in_transit"

    await send_status("2067", "2026/07/31 14:00:00")
    assert await current_shipment_status() == "delivered"

    await send_status("2030", "2026/07/31 15:00:00")
    assert await current_shipment_status() == "delivered"

    event_count = await session.scalar(
        select(func.count(ExternalEvent.id)).where(
            ExternalEvent.provider == "ecpay_logistics"
        )
    )
    assert event_count == 5


@pytest.mark.asyncio
async def test_shipping_rate_crud_and_public_query(
    logistics_context,
) -> None:
    context = logistics_context
    client = context["client"]
    admin = context["admin"]
    customer = context["customer"]
    session = context["session"]
    payload = {
        "channel": "family_mart",
        "temperature": "ambient",
        "fee": 70,
        "free_shipping_threshold": 1500,
        "effective_from": "2020-01-01",
    }

    public_rates = await client.get(
        "/v1/shipping-rates",
        params={"channel": "home_delivery", "temperature": "ambient"},
    )
    forbidden = await client.post(
        "/v1/admin/shipping-rates",
        json=payload,
        headers=auth_headers(customer),
    )
    created = await client.post(
        "/v1/admin/shipping-rates",
        json=payload,
        headers=auth_headers(admin),
    )
    rate_id = created.json()["id"]
    updated = await client.patch(
        f"/v1/admin/shipping-rates/{rate_id}",
        json={"fee": 80},
        headers=auth_headers(admin),
    )
    deleted = await client.delete(
        f"/v1/admin/shipping-rates/{rate_id}",
        headers=auth_headers(admin),
    )
    filtered = await client.get(
        "/v1/shipping-rates",
        params={"channel": "family_mart", "temperature": "ambient"},
    )
    admin_rates = await client.get(
        "/v1/admin/shipping-rates",
        headers=auth_headers(admin),
    )
    audits = list(
        await session.scalars(
            select(AdminAudit).where(
                AdminAudit.aggregate_type == "shipping_rate"
            )
        )
    )

    assert public_rates.status_code == 200
    assert public_rates.json()[0]["fee"] == 160
    assert forbidden.status_code == 403
    assert created.status_code == 201
    assert updated.json()["fee"] == 80
    assert deleted.status_code == 204
    assert filtered.json() == []
    assert any(item["id"] == rate_id for item in admin_rates.json())
    assert {audit.action for audit in audits} == {
        "shipping_rate.create",
        "shipping_rate.update",
        "shipping_rate.deactivate",
    }


@pytest.mark.asyncio
async def test_selection_rejects_wrong_temperature_and_paid_order(
    logistics_context,
) -> None:
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]

    wrong_temperature = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(temperature="chilled"),
        headers=auth_headers(customer),
    )
    order.payment_status = PaymentStatus.PAID
    await context["session"].commit()
    already_paid = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )

    assert wrong_temperature.status_code == 422
    assert already_paid.status_code == 409


@pytest.mark.asyncio
async def test_selection_token_is_single_use_and_expires(
    logistics_context,
) -> None:
    """The token is the only credential guarding the ECPay picker page."""
    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    session = context["session"]

    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )
    assert selection.status_code == 201
    selection_path = urlparse(selection.json()["selection_url"]).path

    unknown_token = await client.get("/logistics/not-a-real-token/select")
    assert unknown_token.status_code == 404

    session.expire(order, ["fulfillment"])
    first_open = await client.get(selection_path)
    assert first_open.status_code == 200

    # Completing the picker consumes the token.
    adapter = context["adapter"]
    callback_url = urlparse(adapter.selection_request.client_reply_url)
    callback_query = parse_qs(callback_url.query)
    callback = await client.post(
        callback_url.path,
        params={
            "order_id": callback_query["order_id"][0],
            "token": callback_query["token"][0],
        },
        json={"provider": "mocked"},
    )
    assert callback.status_code == 303

    reused = await client.get(selection_path)
    assert reused.status_code == 404

    # A token past its TTL is refused even before it is used.
    fresh = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )
    assert fresh.status_code == 201
    shipment = await session.scalar(select(Shipment))
    shipment.selection_token_expires_at = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )
    await session.commit()
    expired = await client.get(
        urlparse(fresh.json()["selection_url"]).path
    )
    assert expired.status_code == 410


@pytest.mark.asyncio
async def test_logistics_order_cannot_be_paid_before_store_selection(
    logistics_context,
) -> None:
    """Paying first would strand an order no admin could ever ship."""
    from app.integrations.payment_service import (
        PaymentApplicationError,
        create_payment_attempt,
    )

    context = logistics_context
    client = context["client"]
    order = context["order"]
    customer = context["customer"]
    session = context["session"]
    settings = context["settings"]

    selection = await client.post(
        f"/v1/orders/{order.id}/logistics/selection",
        json=selection_payload(),
        headers=auth_headers(customer),
    )
    assert selection.status_code == 201

    with pytest.raises(PaymentApplicationError, match="物流門市或地址"):
        await create_payment_attempt(
            session=session,
            order_id=order.id,
            user=customer,
            settings=settings,
        )
