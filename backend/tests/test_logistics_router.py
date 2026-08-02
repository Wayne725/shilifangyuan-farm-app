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
        return {
            "RtnCode": 1,
            "RtnMsg": "成功",
            "LogisticsID": "90001",
        }

    async def query_order(self, *, logistics_id):
        self.query_calls += 1
        assert logistics_id == "90001"
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


def make_test_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
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

    assert callback_first.json() == {"RtnCode": 1}
    assert callback_duplicate.json() == {"RtnCode": 1}
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
