from datetime import datetime, timezone

import pytest

from app.integrations.common import HTTPResponse
from app.integrations.ecpay import (
    ECPayAIOAdapter,
    ECPayAIOSettings,
    LocalSandboxRefundAdapter,
    build_check_mac_value,
    verify_check_mac_value,
)


TEST_MERCHANT_ID = "3002607"
TEST_HASH_KEY = "pwFHCqoQZGmho4w6"
TEST_HASH_IV = "EkRm7iFT261dpevs"


def test_check_mac_value_matches_official_aio_example() -> None:
    parameters = {
        "ChoosePayment": "ALL",
        "EncryptType": "1",
        "ItemName": "Apple iphone 15",
        "MerchantID": TEST_MERCHANT_ID,
        "MerchantTradeDate": "2023/03/12 15:30:23",
        "MerchantTradeNo": "ecpay20230312153023",
        "PaymentType": "aio",
        "ReturnURL": "https://www.ecpay.com.tw/receive.php",
        "TotalAmount": "30000",
        "TradeDesc": "促銷方案",
    }

    result = build_check_mac_value(
        parameters,
        TEST_HASH_KEY,
        TEST_HASH_IV,
    )

    assert result == (
        "6C51C9E6888DE861FD62FB1DD17029FC"
        "742634498FD813DC43D4243B5685B840"
    )
    signed = {**parameters, "CheckMacValue": result}
    assert verify_check_mac_value(signed, TEST_HASH_KEY, TEST_HASH_IV)
    assert not verify_check_mac_value(
        {**signed, "TotalAmount": "30001"},
        TEST_HASH_KEY,
        TEST_HASH_IV,
    )


def test_checkout_is_credit_only_signed_auto_post_html() -> None:
    adapter = ECPayAIOAdapter(
        ECPayAIOSettings(
            merchant_id=TEST_MERCHANT_ID,
            hash_key=TEST_HASH_KEY,
            hash_iv=TEST_HASH_IV,
        )
    )
    checkout = adapter.create_checkout_form(
        merchant_trade_no="TEST202607290001",
        amount=450,
        item_name='好米 <script>"',
        return_url="https://api.example.test/webhooks/ecpay/payment",
        order_result_url="https://api.example.test/payments/result",
        now=datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc),
    )
    page = checkout.to_html()

    assert checkout.fields["ChoosePayment"] == "Credit"
    assert checkout.fields["TotalAmount"] == "450"
    assert verify_check_mac_value(
        checkout.fields, TEST_HASH_KEY, TEST_HASH_IV
    )
    assert "onload=" in page
    assert "好米 &lt;script&gt;&quot;" in page
    assert TEST_HASH_KEY not in page
    assert TEST_HASH_IV not in page


@pytest.mark.asyncio
async def test_query_order_uses_mocked_transport() -> None:
    requests = []

    async def transport(url, values, headers, timeout):
        requests.append((url, values, headers, timeout))
        return HTTPResponse(
            status_code=200,
            body=(
                "MerchantID=3002607&MerchantTradeNo=TEST202607290001"
                "&TradeStatus=1&TradeAmt=450"
            ),
            headers={},
        )

    adapter = ECPayAIOAdapter(
        ECPayAIOSettings(
            merchant_id=TEST_MERCHANT_ID,
            hash_key=TEST_HASH_KEY,
            hash_iv=TEST_HASH_IV,
        ),
        transport=transport,
    )
    result = await adapter.query_order("TEST202607290001")

    assert result["TradeStatus"] == "1"
    assert len(requests) == 1
    assert verify_check_mac_value(
        requests[0][1], TEST_HASH_KEY, TEST_HASH_IV
    )


@pytest.mark.asyncio
async def test_local_refund_never_claims_provider_reversal() -> None:
    result = await LocalSandboxRefundAdapter().refund(
        order_id="order-1",
        amount=450,
        reason="團購未成立",
        idempotency_key="refund-1",
    )

    assert result.status == "refunded"
    assert result.provider_refund_performed is False
    assert result.refund_id.startswith("sandbox_")
