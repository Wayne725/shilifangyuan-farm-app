from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

import app.jobs as jobs_module
import app.routers.payments as payments_module
from app.integrations.common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
)
from app.integrations.payment_service import (
    PaymentApplicationError,
    create_provider_aware_refund,
)
from app.integrations.raygate import (
    RAYGATE_IDENTIFIER,
    RayGateAdapter,
    RayGatePaymentResult,
    RayGateRefundResult,
    RayGateSettings,
    RayGateTransactionNotFoundError,
    hash_digest,
)
from app.models import (
    ExternalEvent,
    FulfillmentMethod,
    FulfillmentStatus,
    InventoryReservation,
    MembershipType,
    Order,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentAttempt,
    PaymentStatus,
    PointAccount,
    PointSourceType,
    PointTransaction,
    Product,
    Refund,
    RefundStatus,
    ReservationStatus,
    SalesChannel,
    TaxType,
    User,
)
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings


STORE_IDENTIFIER = "test-store-raygate"
KEY_HEX = "11" * 32
IV_HEX = "22" * 16
MERCHANT_ID = "test-merchant"
TERMINAL_ID = "testterm"


def raygate_settings(**overrides) -> RayGateSettings:
    values = {
        "store_identifier": STORE_IDENTIFIER,
        "key_hex": KEY_HEX,
        "iv_hex": IV_HEX,
        "base_url": "https://pay.example.test",
        "allowed_hostname": "pay.example.test",
        "merchant_id": MERCHANT_ID,
        "terminal_id": TERMINAL_ID,
        "device_type": "WEB",
    }
    values.update(overrides)
    return RayGateSettings(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": "https://127.0.0.1", "allowed_hostname": "127.0.0.1"},
        {"base_url": "https://2130706433", "allowed_hostname": "2130706433"},
        {"base_url": "https://0177.0.0.1", "allowed_hostname": "0177.0.0.1"},
        {"base_url": "https://pay.example.test/api"},
        {"base_url": "https://pay.example.test", "allowed_hostname": "other.example.test"},
        {"base_url": "https://user:secret@pay.example.test"},
    ],
)
def test_settings_reject_untrusted_payment_origins(overrides) -> None:
    with pytest.raises(IntegrationConfigurationError):
        RayGateAdapter(raygate_settings(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"key_hex": f"{KEY_HEX[:2]} {KEY_HEX[2:]}"},
        {"key_hex": f" {KEY_HEX}"},
        {"iv_hex": f"{IV_HEX[:2]}\t{IV_HEX[2:]}"},
        {"iv_hex": f"{IV_HEX}\n"},
    ],
)
def test_settings_reject_whitespace_in_hex_credentials(overrides) -> None:
    with pytest.raises(IntegrationConfigurationError):
        RayGateAdapter(raygate_settings(**overrides))


def payment_result(**overrides):
    values = {
        "order_id": "RG2026090200000001",
        "amount": 450,
        "pay_type": "linepay",
        "return_code": "0000",
        "message": "交易成功",
        "transaction_time": "2026-09-02 12:30:00",
        "store_name": "十里方圓",
        "store_code": STORE_IDENTIFIER,
        "status": 2,
        "associated_order_id": "",
        "pos_id": "WEB00001",
        "pos_order_number": "260902123000ABCDEF12",
    }
    values.update(overrides)
    return values


def test_aes_256_cbc_has_stable_ciphertext_and_digest() -> None:
    adapter = RayGateAdapter(raygate_settings())
    payload = {
        "set_price": "100",
        "pos_id": "123",
        "pos_order_number": "num777",
    }

    transaction_data = adapter.encrypt_transaction(payload)

    assert transaction_data == (
        "k8GRq8LKbrieuMMIxI6UJras/Zl76uY1hFefHPYP+aOaP0VdemLy96E5"
        "DMI7Uis92FxBnmvSVyViuKN8H0jBGA=="
    )
    assert hash_digest(transaction_data) == (
        "84dc1ff7a1ffcfb28bd17f6923adaa3b01f07dc4f89f0b50c048246d88a52dd7"
    )
    assert adapter.decrypt_transaction(transaction_data) == payload


def test_checkout_url_encrypts_server_owned_amount_and_urls() -> None:
    adapter = RayGateAdapter(raygate_settings())

    url = adapter.create_checkout_url(
        pos_order_number="260902123000ABCDEF12",
        amount=450,
        callback_url="https://api.example.test/webhooks/raygate/payment",
        return_url=(
            "https://api.example.test/payments/raygate/result?attempt_id=attempt-1"
        ),
        pos_id="WEB00001",
    )

    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    transaction_data = query["TransactionData"][0]
    assert parsed.path == f"/calc/pay_encrypt/{STORE_IDENTIFIER}"
    assert query["HashDigest"] == [hash_digest(transaction_data)]
    assert adapter.decrypt_transaction(transaction_data) == {
        "set_price": "450",
        "pos_order_number": "260902123000ABCDEF12",
        "callback_url": "https://api.example.test/webhooks/raygate/payment",
        "return_url": (
            "https://api.example.test/payments/raygate/result?attempt_id=attempt-1"
        ),
        "pos_id": "WEB00001",
    }


@pytest.mark.parametrize(
    "callback_url",
    [
        "http://api.example.test/webhooks/raygate/payment",
        "https://127.0.0.1/webhooks/raygate/payment",
        "https://2130706433/webhooks/raygate/payment",
        "https://user:password@api.example.test/webhooks/raygate/payment",
        "https://api.example.test/webhooks/raygate/payment#fragment",
    ],
)
def test_checkout_url_rejects_nonpublic_callback_urls(
    callback_url: str,
) -> None:
    adapter = RayGateAdapter(raygate_settings())

    with pytest.raises(ValueError, match="public HTTPS URL"):
        adapter.create_checkout_url(
            pos_order_number="260902123000ABCDEF12",
            amount=450,
            callback_url=callback_url,
            return_url=(
                "https://api.example.test/payments/raygate/result"
                "?attempt_id=attempt-1"
            ),
        )


def test_callback_verifies_envelope_store_and_normalizes_result() -> None:
    adapter = RayGateAdapter(raygate_settings())
    transaction_data = adapter.encrypt_transaction(payment_result())

    result = adapter.verify_callback(
        {
            "TransactionData": transaction_data,
            "HashDigest": hash_digest(transaction_data),
        },
        RAYGATE_IDENTIFIER,
    )

    canonical = result.to_canonical_payload()
    assert result.disposition == "paid"
    assert canonical["MerchantTradeNo"] == "260902123000ABCDEF12"
    assert canonical["TradeAmt"] == "450"
    assert canonical["TradeNo"] == "RG2026090200000001"
    assert canonical["RtnCode"] == "1"
    assert canonical["PaymentDisposition"] == "paid"


def test_callback_does_not_treat_created_order_as_paid() -> None:
    adapter = RayGateAdapter(raygate_settings())
    transaction_data = adapter.encrypt_transaction(payment_result(status=1))

    result = adapter.verify_callback(
        {
            "TransactionData": transaction_data,
            "HashDigest": hash_digest(transaction_data),
        },
        RAYGATE_IDENTIFIER,
    )

    assert result.disposition == "pending"
    assert result.to_canonical_payload()["TradeStatus"] == "0"


def test_callback_fails_closed_for_incoherent_pending_status() -> None:
    adapter = RayGateAdapter(raygate_settings())
    transaction_data = adapter.encrypt_transaction(
        payment_result(status=1, return_code="RG999999")
    )

    result = adapter.verify_callback(
        {
            "TransactionData": transaction_data,
            "HashDigest": hash_digest(transaction_data),
        },
        RAYGATE_IDENTIFIER,
    )

    assert result.disposition == "failed"


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": 450.9},
        {"status": 2.9},
        {"order_id": None},
    ],
)
def test_callback_rejects_non_contract_field_types(overrides) -> None:
    adapter = RayGateAdapter(raygate_settings())
    transaction_data = adapter.encrypt_transaction(payment_result(**overrides))

    with pytest.raises(IntegrationResponseError):
        adapter.verify_callback(
            {
                "TransactionData": transaction_data,
                "HashDigest": hash_digest(transaction_data),
            },
            RAYGATE_IDENTIFIER,
        )


def test_callback_rejects_forged_header_digest_and_store() -> None:
    adapter = RayGateAdapter(raygate_settings())
    transaction_data = adapter.encrypt_transaction(payment_result())
    envelope = {
        "TransactionData": transaction_data,
        "HashDigest": hash_digest(transaction_data),
    }

    with pytest.raises(IntegrationResponseError, match="identifier"):
        adapter.verify_callback(envelope, "Not-RayGate")
    with pytest.raises(IntegrationResponseError, match="HashDigest"):
        adapter.verify_callback({**envelope, "HashDigest": "0" * 64}, RAYGATE_IDENTIFIER)

    wrong_store = adapter.encrypt_transaction(payment_result(store_code="other"))
    with pytest.raises(IntegrationResponseError, match="store_code"):
        adapter.verify_callback(
            {
                "TransactionData": wrong_store,
                "HashDigest": hash_digest(wrong_store),
            },
            RAYGATE_IDENTIFIER,
        )


@pytest.mark.asyncio
async def test_query_uses_encrypted_json_and_required_headers() -> None:
    requests = []
    result = payment_result()

    async def transport(url, payload, headers, timeout):
        requests.append((url, payload, headers, timeout))
        return HTTPResponse(
            status_code=200,
            body=json.dumps(
                {"ErrorCode": "0000", "Message": "成功", "Data": [result]},
                ensure_ascii=False,
            ),
            headers={},
        )

    adapter = RayGateAdapter(raygate_settings(), transport=transport)
    queried = await adapter.query_order(result["pos_order_number"])

    assert queried["TradeStatus"] == "1"
    assert queried["PaymentDisposition"] == "paid"
    assert len(requests) == 1
    url, envelope, headers, timeout = requests[0]
    assert url == f"https://pay.example.test/api/query/{STORE_IDENTIFIER}"
    assert adapter.decrypt_transaction(envelope["TransactionData"]) == {
        "pos_order_number": result["pos_order_number"]
    }
    assert envelope["HashDigest"] == hash_digest(envelope["TransactionData"])
    assert headers == {
        "X-ePay-MerchantID": MERCHANT_ID,
        "X-ePay-TerminalID": TERMINAL_ID,
        "X-Merchant-DeviceType": "WEB",
    }
    assert timeout == 15.0


@pytest.mark.asyncio
async def test_query_accepts_single_object_returned_by_production() -> None:
    result = payment_result(amount=10, pay_type="newebpay", pos_id="001")

    async def transport(_url, _payload, _headers, _timeout):
        return HTTPResponse(
            status_code=200,
            body=json.dumps(
                {"ErrorCode": "0000", "Message": "查詢成功", "Data": result},
                ensure_ascii=False,
            ),
            headers={},
        )

    adapter = RayGateAdapter(raygate_settings(), transport=transport)
    queried = await adapter.query_order(result["pos_order_number"])

    assert queried["TradeAmt"] == "10"
    assert queried["PaymentType"] == "newebpay"
    assert queried["PaymentDisposition"] == "paid"


@pytest.mark.asyncio
async def test_query_identifies_missing_transaction() -> None:
    async def transport(_url, _payload, _headers, _timeout):
        return HTTPResponse(
            status_code=200,
            body=json.dumps(
                {
                    "ErrorCode": "RG999922",
                    "Message": "查無此交易單號",
                    "Data": [],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    adapter = RayGateAdapter(raygate_settings(), transport=transport)

    with pytest.raises(RayGateTransactionNotFoundError):
        await adapter.query_order("260902123000ABCDEF12")


@pytest.mark.asyncio
async def test_refund_uses_original_provider_order_and_payment_type() -> None:
    requests = []

    async def transport(url, payload, headers, timeout):
        requests.append((url, payload, headers, timeout))
        return HTTPResponse(
            status_code=200,
            body=json.dumps(
                {
                    "ErrorCode": "0000",
                    "Message": "退款成功",
                    "status": 3,
                    "refund_order_id": "RF202609020000001",
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    adapter = RayGateAdapter(raygate_settings(), transport=transport)
    refunded = await adapter.refund(
        provider_order_id="RG2026090200000001",
        payment_type="linepay",
        amount=450,
        reason="訂單取消",
        idempotency_key="refund-1",
    )

    assert refunded.refund_id == "RF202609020000001"
    assert refunded.provider_refund_performed is True
    assert adapter.decrypt_transaction(requests[0][1]["TransactionData"]) == {
        "order_id": "RG2026090200000001",
        "refund_type": "linepay",
    }


@pytest.mark.asyncio
async def test_refund_rejects_fractional_status_and_null_refund_id() -> None:
    responses = [
        {
            "ErrorCode": "0000",
            "Message": "退款成功",
            "status": 3.9,
            "refund_order_id": "RF202609020000001",
        },
        {
            "ErrorCode": "0000",
            "Message": "退款成功",
            "status": 3,
            "refund_order_id": None,
        },
    ]

    async def transport(_url, _payload, _headers, _timeout):
        return HTTPResponse(
            status_code=200,
            body=json.dumps(responses.pop(0), ensure_ascii=False),
            headers={},
        )

    adapter = RayGateAdapter(raygate_settings(), transport=transport)
    for _ in range(2):
        with pytest.raises(IntegrationResponseError):
            await adapter.refund(
                provider_order_id="RG2026090200000001",
                payment_type="linepay",
                amount=450,
                reason="訂單取消",
                idempotency_key="refund-1",
            )


@pytest.fixture
async def raygate_callback_context(database_session, monkeypatch):
    query_result = payment_result()

    async def transport(_url, _payload, _headers, _timeout):
        return HTTPResponse(
            status_code=200,
            body=json.dumps(
                {
                    "ErrorCode": "0000",
                    "Message": "成功",
                    "Data": [query_result],
                },
                ensure_ascii=False,
            ),
            headers={},
        )

    adapter = RayGateAdapter(raygate_settings(), transport=transport)
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
    monkeypatch.setattr(
        payments_module,
        "raygate_payment_adapter_from_settings",
        lambda _settings: adapter,
    )
    user = User(
        email="raygate-buyer@example.test",
        display_name="雷門付款買家",
        password_hash="test",
    )
    order = Order(
        order_number="ORD-RAYGATE-0001",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user=user,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=450,
        contact_email=user.email,
        payment_status=PaymentStatus.PENDING,
        items=[
            OrderItem(
                product_name="測試商品",
                unit_label="份",
                quantity=1,
                unit_price=450,
                subtotal=450,
                tax_type=TaxType.TAX_EXEMPT,
            )
        ],
    )
    attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no=query_result["pos_order_number"],
        amount=450,
        status=PaymentStatus.PENDING,
        checkout_payload={
            "redirect_url": (
                f"https://pay.example.test/calc/pay_encrypt/{STORE_IDENTIFIER}"
                "?TransactionData=test&HashDigest=test"
            )
        },
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    database_session.add_all([user, order, attempt])
    await database_session.commit()

    async with api_test_context(
        database_session,
        [payments_router],
        settings=settings,
    ) as client:
        yield client, database_session, order, attempt, adapter, query_result


@pytest.mark.asyncio
async def test_callback_is_confirmed_by_query_before_marking_paid(
    raygate_callback_context,
) -> None:
    client, session, order, attempt, adapter, result = raygate_callback_context
    transaction_data = adapter.encrypt_transaction(result)

    response = await client.post(
        "/webhooks/raygate/payment",
        json={
            "TransactionData": transaction_data,
            "HashDigest": hash_digest(transaction_data),
        },
        headers={"X-ePay-Identifier": RAYGATE_IDENTIFIER},
    )

    assert response.status_code == 200
    assert response.text == "OK"
    await session.refresh(attempt)
    await session.refresh(order)
    assert attempt.status == PaymentStatus.PAID
    assert attempt.provider_trade_no == result["order_id"]
    assert attempt.paid_at is not None
    paid_at = (
        attempt.paid_at
        if attempt.paid_at.tzinfo is not None
        else attempt.paid_at.replace(tzinfo=timezone.utc)
    )
    assert paid_at == datetime(
        2026,
        9,
        2,
        4,
        30,
        tzinfo=timezone.utc,
    )
    assert order.payment_status == PaymentStatus.PAID
    event = await session.scalar(
        select(ExternalEvent).where(
            ExternalEvent.provider == "raygate_payment"
        )
    )
    assert event is not None


@pytest.mark.asyncio
async def test_preview_acceptance_checkout_redirects_only_allowlisted_order(
    database_session,
) -> None:
    user = User(
        email="acceptance@example.test",
        display_name="驗收買家",
        password_hash="test",
    )
    order = Order(
        order_number="ORD-RAYGATE-ACCEPTANCE",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user=user,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=10,
        contact_email=user.email,
        payment_status=PaymentStatus.PENDING,
        items=[
            OrderItem(
                product_name="付款驗收品",
                unit_label="份",
                quantity=1,
                unit_price=10,
                subtotal=10,
                tax_type=TaxType.TAX_EXEMPT,
            )
        ],
    )
    attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="ACCEPTANCE10",
        amount=10,
        status=PaymentStatus.PENDING,
        checkout_payload={
            "redirect_url": (
                f"https://pay.example.test/calc/pay_encrypt/{STORE_IDENTIFIER}"
                "?TransactionData=test&HashDigest=test"
            )
        },
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    database_session.add_all([user, order, attempt])
    await database_session.commit()
    settings = make_test_settings(
        environment="preview",
        payment_provider="raygate",
        raygate_payment_store_identifier=STORE_IDENTIFIER,
        raygate_payment_key_hex=KEY_HEX,
        raygate_payment_iv_hex=IV_HEX,
        raygate_payment_merchant_id=MERCHANT_ID,
        raygate_payment_terminal_id=TERMINAL_ID,
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
        raygate_payment_stage=False,
        raygate_payment_acceptance_order_id=order.id,
    )

    async with api_test_context(
        database_session,
        [payments_router],
        settings=settings,
    ) as client:
        response = await client.get(f"/payments/{attempt.id}/checkout")

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"https://pay.example.test/calc/pay_encrypt/{STORE_IDENTIFIER}"
    )


@pytest.mark.asyncio
async def test_preview_acceptance_checkout_redirects_for_allowlisted_product_sku(
    database_session,
) -> None:
    user = User(
        email="sku-acceptance@example.test",
        display_name="商品白名單買家",
        password_hash="test",
    )
    product = Product(
        slug="remote-payment-10",
        sku="REMOTE-PAYMENT-10",
        name="遠端付款驗收品（測試）",
        category="生活用品",
        unit="份",
        member_price=10,
        nonmember_price=10,
        stock_quantity=20,
        tax_type=TaxType.TAXABLE,
    )
    database_session.add_all([user, product])
    await database_session.flush()
    order = Order(
        order_number="ORD-RAYGATE-SKU-ACCEPTANCE",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user=user,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=10,
        contact_email=user.email,
        payment_status=PaymentStatus.PENDING,
        items=[
            OrderItem(
                source_product_id=product.id,
                product_name=product.name,
                unit_label=product.unit,
                quantity=1,
                unit_price=10,
                subtotal=10,
                tax_type=product.tax_type,
            )
        ],
    )
    attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="SKUACCEPTANCE10",
        amount=10,
        status=PaymentStatus.PENDING,
        checkout_payload={
            "redirect_url": (
                f"https://pay.example.test/calc/pay_encrypt/{STORE_IDENTIFIER}"
                "?TransactionData=test&HashDigest=test"
            )
        },
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    database_session.add_all([order, attempt])
    await database_session.commit()
    settings = make_test_settings(
        environment="preview",
        payment_provider="raygate",
        raygate_payment_store_identifier=STORE_IDENTIFIER,
        raygate_payment_key_hex=KEY_HEX,
        raygate_payment_iv_hex=IV_HEX,
        raygate_payment_merchant_id=MERCHANT_ID,
        raygate_payment_terminal_id=TERMINAL_ID,
        raygate_payment_base_url="https://pay.example.test",
        raygate_payment_allowed_hostname="pay.example.test",
        raygate_payment_stage=False,
        raygate_payment_acceptance_sku=product.sku,
    )

    async with api_test_context(
        database_session,
        [payments_router],
        settings=settings,
    ) as client:
        response = await client.get(f"/payments/{attempt.id}/checkout")

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"https://pay.example.test/calc/pay_encrypt/{STORE_IDENTIFIER}"
    )


@pytest.mark.asyncio
async def test_replayed_callback_can_advance_when_query_changes_to_paid(
    raygate_callback_context,
) -> None:
    client, session, order, attempt, adapter, query_result = (
        raygate_callback_context
    )
    query_result.update(
        status=1,
        return_code="RG000001",
        message="交易尚未完成",
    )
    callback_result = dict(query_result)
    transaction_data = adapter.encrypt_transaction(callback_result)
    envelope = {
        "TransactionData": transaction_data,
        "HashDigest": hash_digest(transaction_data),
    }

    first = await client.post(
        "/webhooks/raygate/payment",
        json=envelope,
        headers={"X-ePay-Identifier": RAYGATE_IDENTIFIER},
    )
    assert first.status_code == 200
    await session.refresh(attempt)
    assert attempt.status == PaymentStatus.PENDING

    query_result.update(
        status=2,
        return_code="0000",
        message="交易成功",
    )
    second = await client.post(
        "/webhooks/raygate/payment",
        json=envelope,
        headers={"X-ePay-Identifier": RAYGATE_IDENTIFIER},
    )

    assert second.status_code == 200
    await session.refresh(attempt)
    await session.refresh(order)
    assert attempt.status == PaymentStatus.PAID
    assert order.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_browser_return_queries_provider_before_marking_paid(
    raygate_callback_context,
) -> None:
    client, session, order, attempt, _adapter, _result = raygate_callback_context

    response = await client.get(
        "/payments/raygate/result",
        params={"attempt_id": attempt.id},
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"https://app.example.test/orders?order_id={order.id}"
        f"&payment=paid&attempt_id={attempt.id}"
    )
    await session.refresh(attempt)
    await session.refresh(order)
    assert attempt.status == PaymentStatus.PAID
    assert order.payment_status == PaymentStatus.PAID
    assert await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "invoice.issue_requested",
            OutboxEvent.aggregate_id == order.id,
        )
    ) is not None


@pytest.mark.asyncio
async def test_browser_return_keeps_unsettled_query_confirming(
    raygate_callback_context,
) -> None:
    client, session, order, attempt, _adapter, query_result = (
        raygate_callback_context
    )
    query_result.update(
        status=1,
        return_code="RG000001",
        message="交易尚未完成",
    )

    response = await client.get(
        "/payments/raygate/result",
        params={"attempt_id": attempt.id},
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"https://app.example.test/orders?order_id={order.id}"
        f"&payment=confirming&attempt_id={attempt.id}"
    )
    await session.refresh(attempt)
    assert attempt.status == PaymentStatus.PENDING


@pytest.mark.parametrize(
    ("status_value", "return_code"),
    [(0, "RG000000"), (4, "RG000004"), (5, "RG000099")],
)
@pytest.mark.asyncio
async def test_browser_return_keeps_unconfirmed_states_retryable(
    raygate_callback_context,
    status_value: int,
    return_code: str,
) -> None:
    client, session, order, attempt, _adapter, query_result = (
        raygate_callback_context
    )
    query_result.update(
        status=status_value,
        return_code=return_code,
        message="尚未確認最終結果",
    )

    response = await client.get(
        "/payments/raygate/result",
        params={"attempt_id": attempt.id},
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"https://app.example.test/orders?order_id={order.id}"
        f"&payment=confirming&attempt_id={attempt.id}"
    )
    await session.refresh(attempt)
    assert attempt.status == PaymentStatus.PENDING
    assert await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "invoice.issue_requested",
            OutboxEvent.aggregate_id == order.id,
        )
    ) is None


@pytest.mark.asyncio
async def test_browser_return_keeps_query_failure_confirming(
    raygate_callback_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session, order, attempt, adapter, _query_result = (
        raygate_callback_context
    )

    async def unavailable(_merchant_trade_no: str) -> dict[str, str]:
        raise IntegrationResponseError("query unavailable")

    monkeypatch.setattr(adapter, "query_order", unavailable)

    response = await client.get(
        "/payments/raygate/result",
        params={"attempt_id": attempt.id},
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"https://app.example.test/orders?order_id={order.id}"
        f"&payment=confirming&attempt_id={attempt.id}"
    )
    await session.refresh(attempt)
    assert attempt.status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_owner_refresh_queries_pending_raygate_attempt(
    raygate_callback_context,
) -> None:
    client, session, order, attempt, _adapter, _query_result = (
        raygate_callback_context
    )

    response = await client.post(
        f"/v1/payment-attempts/{attempt.id}/refresh",
        headers=auth_headers(order.user),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    await session.refresh(order)
    assert order.payment_status == PaymentStatus.PAID

    repeated = await client.post(
        f"/v1/payment-attempts/{attempt.id}/refresh",
        headers=auth_headers(order.user),
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "paid"
    invoice_events = list(
        await session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "invoice.issue_requested",
                OutboxEvent.aggregate_id == order.id,
            )
        )
    )
    assert len(invoice_events) == 1


@pytest.mark.asyncio
async def test_reconcile_queries_active_raygate_without_callback(
    raygate_callback_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, session, order, attempt, adapter, _query_result = (
        raygate_callback_context
    )
    settings = make_test_settings(payment_provider="raygate")
    monkeypatch.setattr(
        jobs_module,
        "payment_adapter_from_settings",
        lambda _settings, _provider=None: adapter,
    )

    processed = await jobs_module._reconcile_expired_payments(
        session,
        settings,
        datetime.now(timezone.utc),
        100,
    )

    assert processed == 1
    await session.refresh(attempt)
    await session.refresh(order)
    assert attempt.status == PaymentStatus.PAID
    assert order.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_reconcile_catches_recent_late_raygate_payment(
    raygate_callback_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, session, order, attempt, adapter, _query_result = (
        raygate_callback_context
    )
    now = datetime.now(timezone.utc)
    attempt.status = PaymentStatus.EXPIRED
    attempt.expires_at = now - timedelta(minutes=5)
    order.payment_status = PaymentStatus.EXPIRED
    await session.commit()
    settings = make_test_settings(payment_provider="raygate")
    monkeypatch.setattr(
        jobs_module,
        "payment_adapter_from_settings",
        lambda _settings, _provider=None: adapter,
    )

    processed = await jobs_module._reconcile_expired_payments(
        session,
        settings,
        now,
        100,
    )

    assert processed == 1
    await session.refresh(attempt)
    await session.refresh(order)
    assert attempt.status == PaymentStatus.PAID
    assert order.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_reconcile_missing_raygate_expires_only_after_deadline(
    raygate_callback_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, session, _order, attempt, adapter, _query_result = (
        raygate_callback_context
    )
    now = datetime.now(timezone.utc)

    async def missing(_merchant_trade_no: str) -> dict[str, str]:
        raise RayGateTransactionNotFoundError("missing")

    monkeypatch.setattr(adapter, "query_order", missing)
    monkeypatch.setattr(
        jobs_module,
        "payment_adapter_from_settings",
        lambda _settings, _provider=None: adapter,
    )
    settings = make_test_settings(payment_provider="raygate")

    before_deadline = await jobs_module._reconcile_expired_payments(
        session,
        settings,
        now,
        100,
    )
    await session.refresh(attempt)
    assert before_deadline == 0
    assert attempt.status == PaymentStatus.PENDING

    after_deadline = await jobs_module._reconcile_expired_payments(
        session,
        settings,
        now + timedelta(minutes=20),
        100,
    )
    await session.refresh(attempt)
    assert after_deadline == 1
    assert attempt.status == PaymentStatus.EXPIRED


@pytest.mark.asyncio
async def test_reconcile_stops_querying_after_configured_grace_window(
    raygate_callback_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, session, _order, attempt, adapter, _query_result = (
        raygate_callback_context
    )
    now = datetime.now(timezone.utc)
    attempt.status = PaymentStatus.EXPIRED
    attempt.expires_at = now - timedelta(hours=25)
    await session.commit()
    calls = 0

    async def counted_query(_merchant_trade_no: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return payment_result()

    monkeypatch.setattr(adapter, "query_order", counted_query)
    monkeypatch.setattr(
        jobs_module,
        "payment_adapter_from_settings",
        lambda _settings, _provider=None: adapter,
    )

    processed = await jobs_module._reconcile_expired_payments(
        session,
        make_test_settings(
            payment_provider="raygate",
            raygate_payment_reconcile_hours=24,
        ),
        now,
        100,
    )

    assert processed == 0
    assert calls == 0


@pytest.mark.asyncio
async def test_reconcile_isolates_payment_adapter_configuration_failure(
    raygate_callback_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, session, _order, _attempt, _adapter, _query_result = (
        raygate_callback_context
    )

    def unavailable_adapter(_settings, _provider=None):
        raise IntegrationConfigurationError("credentials unavailable")

    monkeypatch.setattr(
        jobs_module,
        "payment_adapter_from_settings",
        unavailable_adapter,
    )

    processed = await jobs_module._reconcile_expired_payments(
        session,
        make_test_settings(payment_provider="raygate"),
        datetime.now(timezone.utc),
        100,
    )

    assert processed == 0


@pytest.mark.asyncio
async def test_reconcile_isolates_invalid_provider_result(
    raygate_callback_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client, session, _order, _attempt, adapter, _query_result = (
        raygate_callback_context
    )

    class RejectingRepository:
        def __init__(self, _session) -> None:
            pass

        async def apply_query_result(self, _attempt, _payload) -> str:
            raise PaymentApplicationError("provider amount mismatch")

    monkeypatch.setattr(
        jobs_module,
        "payment_adapter_from_settings",
        lambda _settings, _provider=None: adapter,
    )
    monkeypatch.setattr(
        jobs_module,
        "SQLAlchemyPaymentCallbackRepository",
        RejectingRepository,
    )

    processed = await jobs_module._reconcile_expired_payments(
        session,
        make_test_settings(payment_provider="raygate"),
        datetime.now(timezone.utc),
        100,
    )

    assert processed == 0


@pytest.mark.asyncio
async def test_raygate_refund_request_is_queued_instead_of_marked_complete(
    raygate_callback_context,
) -> None:
    _client, session, order, attempt, _adapter, result = (
        raygate_callback_context
    )
    attempt.status = PaymentStatus.PAID
    attempt.provider_trade_no = result["order_id"]
    attempt.provider_response = {"PaymentType": result["pay_type"]}
    order.payment_status = PaymentStatus.PAID
    await session.commit()

    refund, queued = await create_provider_aware_refund(
        session,
        order=order,
        payment_attempt=attempt,
        amount=order.amount_total,
        reason="買家取消",
        requested_by_id=order.user_id,
    )
    await session.commit()

    outbox = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "refund.requested",
            OutboxEvent.aggregate_id == refund.id,
        )
    )
    assert queued is True
    assert refund.status == RefundStatus.PENDING
    assert refund.provider == "raygate"
    assert refund.payment_attempt_id == attempt.id
    assert attempt.status == PaymentStatus.REFUND_PENDING
    assert outbox is not None


@pytest.mark.asyncio
async def test_confirmed_provider_refund_stops_fulfillment_and_releases_stock(
    database_session,
) -> None:
    user = User(
        email="raygate-reversed@example.test",
        display_name="雷門退款買家",
        password_hash="test",
    )
    product = Product(
        slug="raygate-refunded-stock",
        name="退款庫存商品",
        description="測試",
        category="測試",
        unit="份",
        member_price=450,
        nonmember_price=450,
        stock_quantity=4,
        tax_type=TaxType.TAX_EXEMPT,
    )
    database_session.add_all([user, product])
    await database_session.flush()
    order = Order(
        order_number="ORD-RG-REVERSED-01",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user=user,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=450,
        contact_email=user.email,
        payment_status=PaymentStatus.PENDING,
        fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
        items=[
            OrderItem(
                product_name=product.name,
                unit_label="份",
                quantity=1,
                unit_price=450,
                subtotal=450,
                tax_type=TaxType.TAX_EXEMPT,
                source_product_id=product.id,
            )
        ],
    )
    attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no="260902REVERSED0001",
        amount=450,
        status=PaymentStatus.PENDING,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    reservation = InventoryReservation(
        order=order,
        payment_attempt=attempt,
        source_product_id=product.id,
        quantity=1,
        status=ReservationStatus.ACTIVE,
        expires_at=attempt.expires_at,
    )
    database_session.add_all([order, attempt, reservation])
    await database_session.flush()
    point_account = PointAccount(user_id=user.id)
    database_session.add(point_account)
    await database_session.flush()
    database_session.add(
        PointTransaction(
            account_id=point_account.id,
            amount=4,
            source_type=PointSourceType.PURCHASE,
            reference_id=order.id,
            note="消費累積",
        )
    )
    await database_session.commit()

    result = RayGatePaymentResult(
        order_id="RG2026090200000099",
        amount=450,
        pay_type="linepay",
        return_code="0000",
        message="已退款",
        transaction_time="2026-09-02 12:30:00",
        store_name="十里方圓",
        store_code=STORE_IDENTIFIER,
        status=3,
        associated_order_id="RF2026090200000099",
        pos_order_number=attempt.merchant_trade_no,
    )
    repository = payments_module.SQLAlchemyPaymentCallbackRepository(
        database_session
    )
    outcome = await repository.apply_raygate_payment_callback(
        "confirmed-provider-refund",
        result.to_canonical_payload(),
    )

    assert outcome == "refunded"
    await database_session.refresh(order)
    await database_session.refresh(attempt)
    await database_session.refresh(reservation)
    await database_session.refresh(product)
    saved_refund = await database_session.scalar(
        select(Refund).where(Refund.payment_attempt_id == attempt.id)
    )
    assert attempt.status == PaymentStatus.REFUNDED
    assert order.payment_status == PaymentStatus.REFUNDED
    assert order.fulfillment_status == FulfillmentStatus.CANCELLED
    assert reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == 5
    assert saved_refund is not None
    assert saved_refund.provider_refund_id == "RF2026090200000099"
    reversed_points = await database_session.scalar(
        select(PointTransaction).where(
            PointTransaction.account_id == point_account.id,
            PointTransaction.source_type == PointSourceType.REFUND,
            PointTransaction.reference_id == saved_refund.id,
        )
    )
    assert reversed_points is not None
    assert reversed_points.amount == -4


async def _make_pending_raygate_refund(database_session, *, suffix: str):
    user = User(
        email=f"raygate-refund-{suffix}@example.test",
        display_name="雷門退款買家",
        password_hash="test",
    )
    order = Order(
        order_number=f"ORD-RG-REFUND-{suffix}",
        order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        user=user,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=450,
        contact_email=user.email,
        payment_status=PaymentStatus.REFUND_PENDING,
        fulfillment_status=FulfillmentStatus.PREPARING,
        items=[
            OrderItem(
                product_name="退款測試商品",
                unit_label="份",
                quantity=1,
                unit_price=450,
                subtotal=450,
                tax_type=TaxType.TAX_EXEMPT,
            )
        ],
    )
    attempt = PaymentAttempt(
        order=order,
        provider="raygate",
        merchant_trade_no=f"260902REFUND{suffix}",
        provider_trade_no=f"RG20260902{suffix}",
        amount=450,
        status=PaymentStatus.REFUND_PENDING,
        provider_response={"PaymentType": "linepay"},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        paid_at=datetime.now(timezone.utc),
    )
    refund = Refund(
        order=order,
        payment_attempt=attempt,
        provider="raygate",
        amount=450,
        status=RefundStatus.PENDING,
        reason="買家取消",
        requested_by=user,
    )
    database_session.add_all([user, order, attempt, refund])
    await database_session.flush()
    event = OutboxEvent(
        event_type="refund.requested",
        aggregate_type="refund",
        aggregate_id=refund.id,
        payload={"refund_id": refund.id, "order_id": order.id},
    )
    database_session.add(event)
    await database_session.commit()
    return order, attempt, refund, event


class FakeRayGateRefundAdapter:
    def __init__(self, query_result: dict, *, fail_if_refunded: bool = False):
        self.query_result = query_result
        self.fail_if_refunded = fail_if_refunded
        self.calls = []

    async def query_order(self, merchant_trade_no: str) -> dict:
        self.calls.append(("query", merchant_trade_no))
        return dict(self.query_result)

    async def refund(
        self,
        *,
        provider_order_id: str,
        payment_type: str,
        amount: int,
        reason: str,
        idempotency_key: str,
    ) -> RayGateRefundResult:
        if self.fail_if_refunded:
            raise AssertionError("已退款交易不應再次呼叫 refund")
        self.calls.append(
            (
                "refund",
                {
                    "provider_order_id": provider_order_id,
                    "payment_type": payment_type,
                    "amount": amount,
                    "reason": reason,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        response = {
            "ErrorCode": "0000",
            "Message": "退款成功",
            "status": 3,
            "refund_order_id": "RF202609020000001",
        }
        return RayGateRefundResult(
            refund_id="RF202609020000001",
            status="refunded",
            provider_refund_performed=True,
            amount=amount,
            reason=reason,
            created_at=datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc),
            provider_response=response,
        )


@pytest.mark.asyncio
async def test_refund_job_queries_before_refunding_and_persists_provider_audit(
    database_session,
    monkeypatch,
) -> None:
    order, attempt, refund, event = await _make_pending_raygate_refund(
        database_session,
        suffix="01",
    )
    adapter = FakeRayGateRefundAdapter(
        {
            "MerchantTradeNo": attempt.merchant_trade_no,
            "TradeNo": attempt.provider_trade_no,
            "TradeAmt": "450",
            "PaymentType": "linepay",
            "PaymentDisposition": "paid",
            "RayGateStatus": "2",
            "RayGateAssociatedOrderID": "",
        }
    )
    monkeypatch.setattr(
        jobs_module,
        "refund_adapter_from_settings",
        lambda _settings, provider: adapter if provider == "raygate" else None,
    )

    await jobs_module._process_refund_event(
        session=database_session,
        settings=make_test_settings(payment_provider="raygate"),
        event=event,
    )

    assert adapter.calls[0] == ("query", attempt.merchant_trade_no)
    assert adapter.calls[1][0] == "refund"
    assert adapter.calls[1][1] == {
        "provider_order_id": attempt.provider_trade_no,
        "payment_type": "linepay",
        "amount": 450,
        "reason": "買家取消",
        "idempotency_key": refund.id,
    }
    await database_session.refresh(order)
    await database_session.refresh(attempt)
    await database_session.refresh(refund)
    assert refund.status == RefundStatus.COMPLETED
    assert refund.payment_attempt_id == attempt.id
    assert refund.provider == "raygate"
    assert refund.provider_refund_id == "RF202609020000001"
    assert refund.provider_response == {
        "ErrorCode": "0000",
        "Message": "退款成功",
        "status": 3,
        "refund_order_id": "RF202609020000001",
    }
    assert refund.completed_at is not None
    assert attempt.status == PaymentStatus.REFUNDED
    assert order.payment_status == PaymentStatus.REFUNDED
    assert order.fulfillment_status == FulfillmentStatus.CANCELLED


@pytest.mark.asyncio
async def test_refund_job_does_not_repeat_refund_when_query_is_already_refunded(
    database_session,
    monkeypatch,
) -> None:
    order, attempt, refund, event = await _make_pending_raygate_refund(
        database_session,
        suffix="02",
    )
    query_result = {
        "MerchantTradeNo": attempt.merchant_trade_no,
        "TradeNo": attempt.provider_trade_no,
        "TradeAmt": "450",
        "PaymentType": "linepay",
        "PaymentDisposition": "refunded",
        "RayGateStatus": "3",
        "RayGateAssociatedOrderID": "RF202609020000002",
    }
    adapter = FakeRayGateRefundAdapter(query_result, fail_if_refunded=True)
    monkeypatch.setattr(
        jobs_module,
        "refund_adapter_from_settings",
        lambda _settings, provider: adapter if provider == "raygate" else None,
    )

    await jobs_module._process_refund_event(
        session=database_session,
        settings=make_test_settings(payment_provider="raygate"),
        event=event,
    )

    assert adapter.calls == [("query", attempt.merchant_trade_no)]
    await database_session.refresh(order)
    await database_session.refresh(attempt)
    await database_session.refresh(refund)
    assert refund.status == RefundStatus.COMPLETED
    assert refund.provider_refund_id == "RF202609020000002"
    assert refund.provider_response == query_result
    assert attempt.status == PaymentStatus.REFUNDED
    assert order.payment_status == PaymentStatus.REFUNDED
