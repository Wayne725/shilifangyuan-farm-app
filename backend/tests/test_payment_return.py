from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.database import Base, get_session
from app.integrations.ecpay import build_check_mac_value
from app.models import (
    FulfillmentMethod,
    MembershipType,
    Order,
    OrderItem,
    OrderKind,
    PaymentAttempt,
    PaymentStatus,
    SalesChannel,
    TaxType,
    User,
)
from app.routers.payments import payments_router


MERCHANT_ID = "3002607"
HASH_KEY = "pwFHCqoQZGmho4w6"
HASH_IV = "EkRm7iFT261dpevs"


def payment_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        app_base_url="https://api.example.test",
        web_base_url="https://app.example.test",
        ecpay_payment_merchant_id=MERCHANT_ID,
        ecpay_payment_hash_key=HASH_KEY,
        ecpay_payment_hash_iv=HASH_IV,
    )


def successful_result(attempt: PaymentAttempt) -> dict[str, str]:
    now = datetime.now(timezone(timedelta(hours=8)))
    payload = {
        "MerchantID": MERCHANT_ID,
        "MerchantTradeNo": attempt.merchant_trade_no,
        "TradeAmt": str(attempt.amount),
        "RtnCode": "1",
        "RtnMsg": "交易成功",
        "SimulatePaid": "0",
        "PaymentDate": now.strftime("%Y/%m/%d %H:%M:%S"),
        "TradeDate": now.strftime("%Y/%m/%d %H:%M:%S"),
        "TradeNo": "2608101200000001",
    }
    payload["CheckMacValue"] = build_check_mac_value(
        payload,
        HASH_KEY,
        HASH_IV,
    )
    return payload


@pytest.fixture
async def payment_return_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        app = FastAPI()
        app.include_router(payments_router)

        async def override_get_session():
            yield session

        app.dependency_overrides[get_session] = override_get_session
        app.dependency_overrides[get_settings] = payment_settings

        user = User(
            email="buyer@example.test",
            display_name="付款買家",
            password_hash="test",
        )
        order = Order(
            order_number="ORD-RETURN-0001",
            order_kind=OrderKind.REGULAR,
            sales_channel=SalesChannel.REGULAR,
            fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
            user=user,
            membership_type_snapshot=MembershipType.NONMEMBER,
            amount_total=200,
            contact_email=user.email,
            payment_status=PaymentStatus.PENDING,
            items=[
                OrderItem(
                    product_name="測試商品",
                    unit_label="份",
                    quantity=1,
                    unit_price=200,
                    subtotal=200,
                    tax_type=TaxType.TAX_EXEMPT,
                )
            ],
        )
        attempt = PaymentAttempt(
            order=order,
            merchant_trade_no="260810120000000001AB",
            amount=200,
            status=PaymentStatus.PENDING,
            checkout_payload={
                "MerchantID": MERCHANT_ID,
                "MerchantTradeNo": "260810120000000001AB",
                "TradeAmt": "200",
                "ReturnURL": "https://api.example.test/webhooks/ecpay/payment",
                "OrderResultURL": "https://api.example.test/payments/result",
                "CheckMacValue": "ORIGINAL",
            },
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        session.add_all([user, order, attempt])
        await session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            yield client, session, order, attempt
    await engine.dispose()


@pytest.mark.asyncio
async def test_order_result_applies_payment_before_redirect(
    payment_return_context,
) -> None:
    client, session, order, attempt = payment_return_context

    response = await client.post(
        "/payments/result",
        data=successful_result(attempt),
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"https://app.example.test/orders?order_id={order.id}"
        "&payment=paid"
    )
    await session.refresh(attempt)
    await session.refresh(order)
    assert attempt.status == PaymentStatus.PAID
    assert order.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_order_result_repository_error_still_redirects_to_confirming(
    payment_return_context,
) -> None:
    client, _session, order, attempt = payment_return_context
    order_id = order.id
    payload = successful_result(attempt)
    payload["TradeAmt"] = "201"
    payload["CheckMacValue"] = build_check_mac_value(
        {key: value for key, value in payload.items() if key != "CheckMacValue"},
        HASH_KEY,
        HASH_IV,
    )

    response = await client.post("/payments/result", data=payload)

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"https://app.example.test/orders?order_id={order_id}"
        "&payment=confirming"
    )


@pytest.mark.asyncio
async def test_native_checkout_returns_to_app_scheme(
    payment_return_context,
) -> None:
    client, _session, order, attempt = payment_return_context

    checkout = await client.get(
        f"/payments/{attempt.id}/checkout?client=native",
    )
    assert checkout.status_code == 200
    assert (
        "https://api.example.test/payments/result?client=native"
        in checkout.text
    )

    result = await client.post(
        "/payments/result?client=native",
        data=successful_result(attempt),
    )
    assert result.status_code == 303
    assert result.headers["location"] == (
        f"shilifangyuan://payment-return?order_id={order.id}"
        "&payment=paid"
    )


@pytest.mark.asyncio
async def test_order_result_is_idempotent_with_server_callback(
    payment_return_context,
) -> None:
    client, session, order, attempt = payment_return_context
    payload = successful_result(attempt)

    result = await client.post("/payments/result", data=payload)
    callback = await client.post("/webhooks/ecpay/payment", data=payload)

    assert result.status_code == 303
    assert callback.status_code == 200
    assert callback.text == "1|OK"
    await session.refresh(order)
    assert order.payment_status == PaymentStatus.PAID
