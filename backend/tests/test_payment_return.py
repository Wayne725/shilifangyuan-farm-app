from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
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
from tests.support import api_test_context, make_test_settings


MERCHANT_ID = "3002607"
HASH_KEY = "pwFHCqoQZGmho4w6"
HASH_IV = "EkRm7iFT261dpevs"


def payment_settings() -> Settings:
    return make_test_settings(
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
async def payment_return_context(database_session):
    async with api_test_context(
        database_session,
        [payments_router],
        settings=payment_settings(),
    ) as client:
        session = database_session
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

        yield client, session, order, attempt


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
