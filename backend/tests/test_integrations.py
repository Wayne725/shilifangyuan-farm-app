import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.integrations.ecpay import build_check_mac_value
from app.integrations.common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
)
from app.integrations.email_sender import (
    EmailSendResult,
    FailoverEmailSender,
    email_sender_from_settings,
)
from app.integrations.invoice import (
    ECPayInvoiceAdapter,
    ECPayInvoiceSettings,
    InvoiceIssueRequest,
    InvoiceIssueResult,
    InvoiceLine,
    decrypt_ecpay_invoice_data,
    encrypt_ecpay_invoice_data,
    is_mobile_barcode_format,
    normalize_mobile_barcode,
)
from app.integrations.fanyu_invoice import (
    FanyuInvoiceAdapter,
    FanyuInvoiceSettings,
)
from app.integrations.invoice_service import (
    InvoiceApplicationError,
    enqueue_invoice_adjustment_after_refund,
    invoice_request_from_order,
    issue_paid_order_invoice,
    reconcile_order_invoice,
)
from app.integrations.notifications import (
    NotificationCommand,
    NotificationService,
)
from app.integrations.payment_service import (
    PaymentApplicationError,
    SQLAlchemyPaymentCallbackRepository,
    create_payment_attempt,
    payment_adapter_from_settings,
    release_attempt_reservations,
)
from app.integrations.mailersend import (
    EmailMessage,
    MailerSendAdapter,
    MailerSendSettings,
)
from app.integrations.resend import ResendAdapter, ResendSettings
from app.models import (
    InventoryReservation,
    ExternalEvent,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    Invoice,
    InvoiceBuyerType,
    InvoiceCarrierType,
    InvoiceStatus,
    MembershipType,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    PaymentAttempt,
    PaymentStatus,
    Product,
    Refund,
    RefundStatus,
    ReservationStatus,
    Shipment,
    ShipmentStatus,
    ShippingChannel,
    ShippingTemperature,
    TaxType,
    TargetType,
    User,
)
from tests.support import make_test_settings, prepare_test_invoice


INVOICE_MERCHANT_ID = "2000132"
INVOICE_HASH_KEY = "ejCk326UnaZWKisg"
INVOICE_HASH_IV = "q9jcZX8Ib9LM8wYk"


def payment_settings() -> Settings:
    return make_test_settings(
        ecpay_payment_merchant_id=TEST_PAYMENT_MERCHANT_ID,
        ecpay_payment_hash_key=TEST_PAYMENT_HASH_KEY,
        ecpay_payment_hash_iv=TEST_PAYMENT_HASH_IV,
    )


TEST_PAYMENT_MERCHANT_ID = "3002607"
TEST_PAYMENT_HASH_KEY = "pwFHCqoQZGmho4w6"
TEST_PAYMENT_HASH_IV = "EkRm7iFT261dpevs"


def preview_raygate_acceptance_settings(**overrides) -> Settings:
    values = {
        "environment": "preview",
        "payment_provider": "raygate",
        "raygate_payment_store_identifier": "acceptance-store",
        "raygate_payment_key_hex": "11" * 32,
        "raygate_payment_iv_hex": "22" * 16,
        "raygate_payment_merchant_id": "merchant",
        "raygate_payment_terminal_id": "terminal",
        "raygate_payment_base_url": "https://pay.example.test",
        "raygate_payment_allowed_hostname": "pay.example.test",
        "raygate_payment_stage": False,
        **overrides,
    }
    return make_test_settings(**values)


async def make_regular_order(database_session):
    user = User(
        email="buyer@example.test",
        display_name="測試買家",
        password_hash="test",
        membership_type=MembershipType.MEMBER,
    )
    product = Product(
        slug="test-rice",
        name="測試白米",
        category="米食",
        unit="包",
        member_price=100,
        nonmember_price=120,
        stock_quantity=3,
        tax_type=TaxType.TAXABLE,
    )
    database_session.add_all([user, product])
    await database_session.flush()
    order = Order(
        order_number="SLFTEST0001",
        order_kind=OrderKind.REGULAR,
        user_id=user.id,
        membership_type_snapshot=user.membership_type,
        amount_total=200,
        contact_email=user.email,
        items=[
            OrderItem(
                source_product_id=product.id,
                product_name=product.name,
                unit_label=product.unit,
                quantity=2,
                unit_price=100,
                subtotal=200,
                tax_type=product.tax_type,
            )
        ],
    )
    database_session.add(order)
    await database_session.commit()
    return user, product, order


def signed_payment_callback(attempt, payment_date, simulate_paid="0"):
    payload = {
        "MerchantID": TEST_PAYMENT_MERCHANT_ID,
        "MerchantTradeNo": attempt.merchant_trade_no,
        "TradeAmt": str(attempt.amount),
        "RtnCode": "1",
        "RtnMsg": "交易成功",
        "SimulatePaid": simulate_paid,
        "PaymentDate": payment_date.astimezone(
            ZoneInfo("Asia/Taipei")
        ).strftime("%Y/%m/%d %H:%M:%S"),
        "TradeDate": payment_date.astimezone(
            ZoneInfo("Asia/Taipei")
        ).strftime("%Y/%m/%d %H:%M:%S"),
        "TradeNo": "2607291200000001",
    }
    payload["CheckMacValue"] = build_check_mac_value(
        payload, TEST_PAYMENT_HASH_KEY, TEST_PAYMENT_HASH_IV
    )
    return payload


def test_invoice_aes_matches_official_vector_and_round_trips() -> None:
    encrypted = encrypt_ecpay_invoice_data(
        {"Name": "Test", "ID": "A123456789"},
        INVOICE_HASH_KEY,
        INVOICE_HASH_IV,
    )

    assert encrypted == (
        "uvI4yrErM37XNQkXGAgRgJAgHn2t72jahaMZzYhWL1H"
        "mvH4WV18VJDP2i9pTbC+tby5nxVExLLFyAkbjbS2Dvg=="
    )
    assert decrypt_ecpay_invoice_data(
        encrypted, INVOICE_HASH_KEY, INVOICE_HASH_IV
    ) == {"Name": "Test", "ID": "A123456789"}


def test_mobile_barcode_format_normalizes_ascii_only() -> None:
    assert normalize_mobile_barcode(" /ab12+-. ") == "/AB12+-."
    assert is_mobile_barcode_format("/AB12+-.")
    assert not is_mobile_barcode_format("AB12+-.")
    assert not is_mobile_barcode_format("/ＡＢ１２３４５")


@pytest.mark.asyncio
async def test_mobile_barcode_validation_uses_encrypted_mock_response() -> None:
    async def transport(url, payload, headers, timeout):
        request_data = decrypt_ecpay_invoice_data(
            payload["Data"], INVOICE_HASH_KEY, INVOICE_HASH_IV
        )
        assert request_data == {
            "MerchantID": INVOICE_MERCHANT_ID,
            "BarCode": "/AB12+-.",
        }
        response_data = encrypt_ecpay_invoice_data(
            {"RtnCode": 1, "RtnMsg": "", "IsExist": "Y"},
            INVOICE_HASH_KEY,
            INVOICE_HASH_IV,
        )
        return HTTPResponse(
            status_code=200,
            body=(
                '{"MerchantID":"2000132","RpHeader":{"Timestamp":1},'
                '"TransCode":1,"TransMsg":"","Data":"%s"}' % response_data
            ),
            headers={},
        )

    adapter = ECPayInvoiceAdapter(
        ECPayInvoiceSettings(
            merchant_id=INVOICE_MERCHANT_ID,
            hash_key=INVOICE_HASH_KEY,
            hash_iv=INVOICE_HASH_IV,
        ),
        transport=transport,
    )
    result = await adapter.validate_mobile_barcode("/ab12+-.")

    assert result.valid is True
    assert result.provider_checked is True
    assert result.barcode == "/AB12+-."


def test_invoice_payload_supports_taxable_and_exempt_snapshots() -> None:
    adapter = ECPayInvoiceAdapter(
        ECPayInvoiceSettings(
            merchant_id=INVOICE_MERCHANT_ID,
            hash_key=INVOICE_HASH_KEY,
            hash_iv=INVOICE_HASH_IV,
        )
    )
    payload = adapter.build_issue_data(
        InvoiceIssueRequest(
            relate_number="INV20260729001",
            customer_email="sandbox@example.test",
            carrier_type="mobile_barcode",
            carrier_number="/AB12+-.",
            items=[
                InvoiceLine("白米", 2, 100, tax_type="1"),
                InvoiceLine("蔬菜", 1, 50, tax_type="3"),
            ],
        )
    )

    assert payload["TaxType"] == "9"
    assert payload["SalesAmount"] == 250
    assert payload["CarrierType"] == "3"
    assert [item["ItemTaxType"] for item in payload["Items"]] == ["1", "3"]


def test_resend_rejects_url_shaped_sender_email() -> None:
    with pytest.raises(IntegrationConfigurationError):
        ResendSettings(
            api_key="re_test-secret",
            sender_email="no-reply@mail.https://example.com.com",
        ).validate()


@pytest.mark.asyncio
async def test_mailersend_adapter_uses_mocked_network() -> None:
    captured = {}

    async def transport(url, payload, headers, timeout):
        captured.update(
            {"url": url, "payload": payload, "headers": headers}
        )
        return HTTPResponse(
            status_code=202,
            body="",
            headers={"X-Message-Id": "message-123"},
        )

    adapter = MailerSendAdapter(
        MailerSendSettings(
            api_token="mlsn.test-secret",
            sender_email="sender@example.test",
        ),
        transport=transport,
    )
    result = await adapter.send(
        EmailMessage(
            to_email="buyer@example.test",
            subject="付款成功",
            text_content="訂單已付款。",
        )
    )

    assert result.accepted is True
    assert result.provider_message_id == "message-123"
    assert captured["headers"]["Authorization"] == "Bearer mlsn.test-secret"
    assert captured["payload"]["from"]["name"] == "十里方圓"


@pytest.mark.asyncio
async def test_resend_adapter_accepts_email_through_public_api() -> None:
    captured = {}

    async def transport(url, payload, headers, timeout):
        captured.update(
            {"url": url, "payload": payload, "headers": headers}
        )
        return HTTPResponse(
            status_code=200,
            body='{"id":"resend-message-123"}',
            headers={"Content-Type": "application/json"},
        )

    adapter = ResendAdapter(
        ResendSettings(
            api_key="re_test-secret",
            sender_email="sender@example.test",
        ),
        transport=transport,
    )
    result = await adapter.send(
        EmailMessage(
            to_email="buyer@example.test",
            subject="付款成功",
            text_content="訂單已付款。",
            idempotency_key="outbox-event-123",
        )
    )

    assert result.provider_message_id == "resend-message-123"
    assert captured == {
        "url": "https://api.resend.com/emails",
        "payload": {
            "from": "十里方圓 <sender@example.test>",
            "to": ["buyer@example.test"],
            "subject": "付款成功",
            "text": "訂單已付款。",
        },
        "headers": {
            "Authorization": "Bearer re_test-secret",
            "Idempotency-Key": "outbox-event-123",
        },
    }


@pytest.mark.asyncio
async def test_email_sender_falls_back_when_primary_provider_rejects() -> None:
    class RejectingSender:
        async def send(self, message):
            raise IntegrationResponseError("primary unavailable")

    class AcceptingSender:
        async def send(self, message):
            return EmailSendResult(
                accepted=True,
                provider_message_id="fallback-message-123",
                status_code=202,
                provider="mailersend",
            )

    sender = FailoverEmailSender([RejectingSender(), AcceptingSender()])

    result = await sender.send(
        EmailMessage(
            to_email="buyer@example.test",
            subject="付款成功",
            text_content="訂單已付款。",
        )
    )

    assert result.provider == "mailersend"
    assert result.provider_message_id == "fallback-message-123"


@pytest.mark.asyncio
async def test_configured_sender_prefers_resend_then_mailersend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def reject_resend(self, message):
        calls.append("resend")
        raise IntegrationResponseError("resend unavailable")

    async def accept_mailersend(self, message):
        calls.append("mailersend")
        return EmailSendResult(
            accepted=True,
            provider_message_id="mailersend-message-123",
            status_code=202,
            provider="mailersend",
        )

    monkeypatch.setattr(ResendAdapter, "send", reject_resend)
    monkeypatch.setattr(MailerSendAdapter, "send", accept_mailersend)
    sender = email_sender_from_settings(
        Settings(
            _env_file=None,
            resend_api_key="re_test-secret",
            email_from_email="sender@example.test",
            mailersend_api_token="mlsn.test-secret",
            mailersend_from_email="sender@example.test",
        )
    )

    result = await sender.send(
        EmailMessage(
            to_email="buyer@example.test",
            subject="付款成功",
            text_content="訂單已付款。",
        )
    )

    assert calls == ["resend", "mailersend"]
    assert result.provider == "mailersend"


@pytest.mark.asyncio
async def test_email_sender_skips_invalid_resend_when_mailersend_is_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def accept_mailersend(self, message):
        return EmailSendResult(
            accepted=True,
            provider_message_id="mailersend-valid-backup",
            status_code=202,
            provider="mailersend",
        )

    monkeypatch.setattr(MailerSendAdapter, "send", accept_mailersend)
    sender = email_sender_from_settings(
        Settings(
            _env_file=None,
            resend_api_key="re_test-secret",
            email_from_email="broken@sender",
            mailersend_api_token="mlsn.test-secret",
            mailersend_from_email="sender@example.test",
        )
    )

    result = await sender.send(
        EmailMessage(
            to_email="buyer@example.test",
            subject="驗證信",
            text_content="驗證碼 123456",
        )
    )

    assert result.provider == "mailersend"
    assert result.provider_message_id == "mailersend-valid-backup"


@pytest.mark.asyncio
async def test_notification_service_only_queues_selected_email_events() -> None:
    class Repository:
        def __init__(self):
            self.notifications = []
            self.emails = []

        async def create_notification(self, command):
            self.notifications.append(command)
            return command

        async def enqueue_email_notification(self, command):
            self.emails.append(command)

    repository = Repository()
    service = NotificationService(repository)
    base = NotificationCommand(
        user_id="user-1",
        event_type="payment_succeeded",
        title="付款成功",
        body="已收到付款",
        email="buyer@example.test",
    )
    await service.publish(base)
    await service.publish(
        replace(base, event_type="group_threshold_reached")
    )

    assert len(repository.notifications) == 2
    assert len(repository.emails) == 1
    assert repository.emails[0].event_type == "payment_succeeded"


@pytest.mark.asyncio
async def test_regular_payment_reserves_and_consumes_product_stock(
    database_session,
) -> None:
    user, product, order = await make_regular_order(database_session)
    now = datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc)
    settings = payment_settings()

    attempt = await create_payment_attempt(
        database_session, order.id, user, settings, now=now
    )
    await database_session.refresh(product)
    reservation = await database_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.payment_attempt_id == attempt.id
        )
    )

    assert product.stock_quantity == 1
    assert reservation.status == ReservationStatus.ACTIVE

    payload = signed_payment_callback(attempt, now + timedelta(minutes=2))
    await payment_adapter_from_settings(settings).process_callback(
        payload,
        SQLAlchemyPaymentCallbackRepository(database_session),
    )
    attempt_status = await database_session.scalar(
        select(PaymentAttempt.status).where(PaymentAttempt.id == attempt.id)
    )
    order_status = await database_session.scalar(
        select(Order.payment_status).where(Order.id == order.id)
    )
    reservation_status = await database_session.scalar(
        select(InventoryReservation.status).where(
            InventoryReservation.id == reservation.id
        )
    )
    stock_quantity = await database_session.scalar(
        select(Product.stock_quantity).where(Product.id == product.id)
    )

    assert attempt_status == PaymentStatus.PAID
    assert order_status == PaymentStatus.PAID
    assert reservation_status == ReservationStatus.CONSUMED
    assert stock_quantity == 1


@pytest.mark.asyncio
async def test_preview_cannot_create_a_payment_attempt(
    database_session,
) -> None:
    user, product, order = await make_regular_order(database_session)
    settings = payment_settings().model_copy(update={"environment": "preview"})

    with pytest.raises(PaymentApplicationError, match="Preview"):
        await create_payment_attempt(
            database_session,
            order.id,
            user,
            settings,
        )

    attempts = list(await database_session.scalars(select(PaymentAttempt)))
    await database_session.refresh(product)
    assert attempts == []
    assert product.stock_quantity == 3


@pytest.mark.asyncio
async def test_preview_allows_only_the_exact_ten_dollar_raygate_order(
    database_session,
) -> None:
    user, product, order = await make_regular_order(database_session)
    order.amount_total = 10
    order.items[0].unit_price = 5
    order.items[0].subtotal = 10
    await database_session.commit()
    settings = preview_raygate_acceptance_settings(
        raygate_payment_acceptance_order_id=order.id,
    )

    attempt = await create_payment_attempt(
        database_session,
        order.id,
        user,
        settings,
    )

    assert attempt.provider == "raygate"
    assert attempt.amount == 10
    assert attempt.checkout_payload["redirect_url"].startswith(
        "https://pay.example.test/calc/pay_encrypt/acceptance-store"
    )


@pytest.mark.asyncio
async def test_preview_allows_a_dedicated_ten_dollar_product_sku(
    database_session,
) -> None:
    user, product, order = await make_regular_order(database_session)
    product.sku = "REMOTE-PAYMENT-10"
    order.amount_total = 10
    order.items[0].quantity = 1
    order.items[0].unit_price = 10
    order.items[0].subtotal = 10
    await database_session.commit()
    settings = preview_raygate_acceptance_settings(
        raygate_payment_acceptance_sku=product.sku,
    )

    attempt = await create_payment_attempt(
        database_session,
        order.id,
        user,
        settings,
    )

    assert attempt.provider == "raygate"
    assert attempt.amount == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product_sku", "quantity", "unit_price"),
    [
        ("ANOTHER-PRODUCT", 1, 10),
        ("REMOTE-PAYMENT-10", 2, 5),
    ],
)
async def test_preview_product_allowlist_rejects_other_products_and_quantities(
    database_session,
    product_sku: str,
    quantity: int,
    unit_price: int,
) -> None:
    user, product, order = await make_regular_order(database_session)
    product.sku = product_sku
    order.amount_total = 10
    order.items[0].quantity = quantity
    order.items[0].unit_price = unit_price
    order.items[0].subtotal = 10
    await database_session.commit()
    settings = preview_raygate_acceptance_settings(
        raygate_payment_acceptance_sku="REMOTE-PAYMENT-10",
    )

    with pytest.raises(PaymentApplicationError, match="Preview"):
        await create_payment_attempt(
            database_session,
            order.id,
            user,
            settings,
        )

    attempts = list(await database_session.scalars(select(PaymentAttempt)))
    assert attempts == []


@pytest.mark.asyncio
async def test_preview_acceptance_rejects_a_different_order(
    database_session,
) -> None:
    user, product, order = await make_regular_order(database_session)
    order.amount_total = 10
    await database_session.commit()
    settings = make_test_settings(
        environment="preview",
        payment_provider="raygate",
        raygate_payment_stage=False,
        raygate_payment_acceptance_order_id=(
            "00000000-0000-4000-8000-000000000001"
        ),
    )

    with pytest.raises(PaymentApplicationError, match="Preview"):
        await create_payment_attempt(
            database_session,
            order.id,
            user,
            settings,
        )

    attempts = list(await database_session.scalars(select(PaymentAttempt)))
    await database_session.refresh(product)
    assert attempts == []
    assert product.stock_quantity == 3


@pytest.mark.asyncio
async def test_simulated_payment_is_rejected_and_releases_stock(
    database_session,
) -> None:
    user, product, order = await make_regular_order(database_session)
    now = datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc)
    settings = payment_settings()
    attempt = await create_payment_attempt(
        database_session, order.id, user, settings, now=now
    )
    payload = signed_payment_callback(
        attempt,
        now + timedelta(minutes=2),
        simulate_paid="1",
    )

    result = await payment_adapter_from_settings(settings).process_callback(
        payload,
        SQLAlchemyPaymentCallbackRepository(database_session),
    )
    reservation = await database_session.scalar(
        select(InventoryReservation).where(
            InventoryReservation.payment_attempt_id == attempt.id
        )
    )
    await database_session.refresh(product)

    assert result == "1|OK"
    assert attempt.status == PaymentStatus.FAILED
    assert reservation.status == ReservationStatus.RELEASED
    assert product.stock_quantity == 3


@pytest.mark.asyncio
async def test_late_regular_callback_reacquires_stock_before_accepting(
    database_session,
) -> None:
    user, product, order = await make_regular_order(database_session)
    now = datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc)
    settings = payment_settings()
    attempt = await create_payment_attempt(
        database_session, order.id, user, settings, now=now
    )
    attempt = await database_session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == attempt.id)
        .options(selectinload(PaymentAttempt.reservations))
    )
    await release_attempt_reservations(database_session, attempt)
    attempt.status = PaymentStatus.EXPIRED
    await database_session.commit()
    await database_session.refresh(product)
    assert product.stock_quantity == 3

    attempt.status = PaymentStatus.PENDING
    await database_session.commit()
    payload = signed_payment_callback(
        attempt, now + timedelta(minutes=20)
    )
    await payment_adapter_from_settings(settings).process_callback(
        payload,
        SQLAlchemyPaymentCallbackRepository(database_session),
    )
    await database_session.refresh(product)
    reservation = attempt.reservations[0]

    assert attempt.status == PaymentStatus.PAID
    assert reservation.status == ReservationStatus.CONSUMED
    assert product.stock_quantity == 1


@pytest.mark.asyncio
async def test_reset_tombstone_acknowledges_late_callback_without_order(
    database_session,
) -> None:
    merchant_trade_no = "RESET20260729000001"
    database_session.add(
        ExternalEvent(
            provider="ecpay_reset",
            external_event_key=merchant_trade_no,
            event_type="payment_tombstone",
            payload={},
            processed=True,
        )
    )
    await database_session.commit()
    payload = {
        "MerchantID": TEST_PAYMENT_MERCHANT_ID,
        "MerchantTradeNo": merchant_trade_no,
        "TradeAmt": "200",
        "RtnCode": "1",
        "RtnMsg": "交易成功",
        "SimulatePaid": "0",
        "PaymentDate": "2026/07/29 12:02:00",
        "TradeNo": "2607291200000002",
    }
    payload["CheckMacValue"] = build_check_mac_value(
        payload, TEST_PAYMENT_HASH_KEY, TEST_PAYMENT_HASH_IV
    )

    acknowledgement = await payment_adapter_from_settings(
        payment_settings()
    ).process_callback(
        payload,
        SQLAlchemyPaymentCallbackRepository(database_session),
    )

    assert acknowledgement == "1|OK"
    tombstoned = await database_session.scalar(
        select(ExternalEvent).where(
            ExternalEvent.provider == "ecpay_aio",
            ExternalEvent.event_type == "payment_callback_tombstoned",
        )
    )
    assert tombstoned is not None


@pytest.mark.asyncio
async def test_paid_order_invoice_is_queried_then_issued_once(
    database_session,
) -> None:
    _user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.fulfillment_status = FulfillmentStatus.PENDING_CONFIRMATION
    order.invoice_status = InvoiceStatus.PENDING
    await database_session.commit()

    class InvoiceAdapter:
        provider_name = "ecpay"

        def __init__(self):
            self.query_count = 0
            self.issue_count = 0

        def prepare_invoice(self, request):
            return prepare_test_invoice(request)

        async def query_invoice(self, relate_number, *, buyer_type="personal"):
            self.query_count += 1
            return {"RtnCode": 0, "RtnMsg": "not found"}

        async def issue_prepared_invoice(self, request):
            self.issue_count += 1
            assert request.items[0].tax_type == "1"
            return InvoiceIssueResult(
                relate_number=request.relate_number,
                invoice_number="AB12345678",
                invoice_date="2026-07-29 12:30:00",
                random_number="1234",
                raw={"RtnCode": 1, "InvoiceNo": "AB12345678"},
            )

    adapter = InvoiceAdapter()
    result = await issue_paid_order_invoice(
        database_session, order.id, adapter
    )
    invoice = await database_session.scalar(
        select(Invoice).where(Invoice.order_id == order.id)
    )

    assert result.invoice_number == "AB12345678"
    assert adapter.query_count == 1
    assert adapter.issue_count == 1
    assert invoice.status == InvoiceStatus.ISSUED
    assert invoice.random_number == "1234"


@pytest.mark.asyncio
async def test_fanyu_no_data_query_is_followed_by_one_issue(
    database_session,
) -> None:
    _user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.fulfillment_status = FulfillmentStatus.PICKED_UP
    order.invoice_status = InvoiceStatus.PENDING
    await database_session.commit()

    class InvoiceAdapter:
        provider_name = "fanyu"

        def __init__(self):
            self.issue_count = 0

        def prepare_invoice(self, request):
            return prepare_test_invoice(request, provider="fanyu")

        async def query_invoice(self, relate_number, *, buyer_type):
            assert buyer_type == "personal"
            return {
                "RtnCode": 0,
                "RtnMsg": "查無電子發票",
                "InvoiceNo": "",
            }

        async def issue_prepared_invoice(self, request):
            self.issue_count += 1
            return InvoiceIssueResult(
                relate_number=request.relate_number,
                invoice_number="AB12345670",
                invoice_date="2026-07-29 123000",
                random_number="9012",
                raw={"invNo": "AB12345670"},
            )

    adapter = InvoiceAdapter()
    await issue_paid_order_invoice(database_session, order.id, adapter)
    invoice = await database_session.scalar(
        select(Invoice).where(Invoice.order_id == order.id)
    )

    assert adapter.issue_count == 1
    assert invoice.provider == "fanyu"
    assert invoice.invoice_date is not None
    assert invoice.status == InvoiceStatus.ISSUED


@pytest.mark.asyncio
async def test_fanyu_invoice_persists_exact_provider_request_snapshot(
    database_session,
) -> None:
    _user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.invoice_status = InvoiceStatus.PENDING
    order.invoice_buyer_type = InvoiceBuyerType.COMPANY
    order.invoice_buyer_tax_id = "12345675"
    order.invoice_buyer_name = "測試股份有限公司"
    order.invoice_buyer_email = "accounting@example.test"
    order.invoice_carrier_type = InvoiceCarrierType.MOBILE_BARCODE
    order.invoice_carrier_value = "/AB12+-."
    await database_session.commit()
    provider_requests = []

    async def transport(url, payload, headers, timeout):
        provider_requests.append(payload["reqData"])
        if url.endswith("/queryInvoice"):
            body = '{"statusCode":"3","statusDesc":"查無資料","respData":{}}'
        else:
            body = (
                '{"statusCode":"0","statusDesc":"","respData":'
                '{"invNo":"AB12345674","invDate":"20260902",'
                '"invTime":"10:00:00","randomNumber":"2468"}}'
            )
        return HTTPResponse(status_code=200, body=body, headers={})

    adapter = FanyuInvoiceAdapter(
        FanyuInvoiceSettings(
            company_id="15989995",
            user_id="15989995ADMIN",
            auth_password="A15989995",
            api_key="test-api-key",
            seller_id="15989995",
            signature_verified=True,
        ),
        transport=transport,
    )

    await issue_paid_order_invoice(database_session, order.id, adapter)
    invoice = await database_session.scalar(
        select(Invoice)
        .where(Invoice.order_id == order.id)
        .options(selectinload(Invoice.items))
    )

    assert invoice is not None
    assert invoice.provider_request == provider_requests[1]
    assert invoice.provider_request["carrierType"] == "3J0002"
    assert invoice.provider_request["notifyEmail"] == "accounting@example.test"
    assert invoice.provider_request["Details"][0]["amount"] == "190.47619"
    assert invoice.sales_amount == 190
    assert invoice.tax_amount == 10
    assert invoice.total_amount == 200
    assert invoice.items[0].unit_price == Decimal("95.238095")
    assert invoice.items[0].amount == Decimal("190.476190")


@pytest.mark.asyncio
async def test_fanyu_personal_cloud_order_uses_contact_email_as_member_carrier(
    database_session,
) -> None:
    _user, _product, order = await make_regular_order(database_session)
    order = await database_session.scalar(
        select(Order)
        .where(Order.id == order.id)
        .options(
            selectinload(Order.items),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
    )
    assert order is not None

    request = invoice_request_from_order(order, "INVSLFTEST0001")
    data = FanyuInvoiceAdapter(
        FanyuInvoiceSettings(
            company_id="15989995",
            user_id="15989995ADMIN",
            auth_password="A15989995",
            api_key="test-api-key",
            seller_id="15989995",
            signature_verified=True,
        )
    ).build_issue_data(request)

    assert data["carrierType"] == "EG0478"
    assert data["carrierID1"] == order.contact_email
    assert data["carrierID2"] == order.contact_email
    assert data["notifyEmail"] == order.contact_email


@pytest.mark.asyncio
async def test_invoice_query_recovers_provider_result_without_reissuing(
    database_session,
) -> None:
    _user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.fulfillment_status = FulfillmentStatus.PICKED_UP
    order.invoice_status = InvoiceStatus.FAILED
    await database_session.commit()

    class InvoiceAdapter:
        provider_name = "fanyu"

        def prepare_invoice(self, request):
            return prepare_test_invoice(request, provider="fanyu")

        async def query_invoice(self, relate_number, *, buyer_type):
            return {
                "RtnCode": 1,
                "InvoiceNo": "AB12345671",
                "InvoiceDate": "2026-07-29 124500",
                "RandomNumber": "3456",
                "ProviderStatus": "0",
            }

        async def issue_prepared_invoice(self, request):
            raise AssertionError("查到既有發票後不可重複開立")

    result = await reconcile_order_invoice(
        database_session,
        order.id,
        InvoiceAdapter(),
    )
    invoice = await database_session.scalar(
        select(Invoice).where(Invoice.order_id == order.id)
    )

    assert result is not None
    assert result.invoice_number == "AB12345671"
    assert invoice.status == InvoiceStatus.ISSUED
    assert order.invoice_status == InvoiceStatus.ISSUED


@pytest.mark.asyncio
async def test_invoice_provider_cannot_change_after_snapshot(
    database_session,
) -> None:
    _user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.fulfillment_status = FulfillmentStatus.PICKED_UP
    database_session.add(
        Invoice(
            order=order,
            relate_number="INVSLFTEST0001",
            provider="ecpay",
            status=InvoiceStatus.FAILED,
        )
    )
    await database_session.commit()

    class InvoiceAdapter:
        provider_name = "fanyu"

    with pytest.raises(InvoiceApplicationError, match="不能改由 fanyu"):
        await reconcile_order_invoice(
            database_session,
            order.id,
            InvoiceAdapter(),
        )


@pytest.mark.asyncio
async def test_invoice_intent_survives_process_interruption_before_writeback(
    database_session,
) -> None:
    user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.PAID
    order.invoice_status = InvoiceStatus.PENDING
    await database_session.commit()
    order_id = order.id
    user_id = user.id
    order_amount = order.amount_total

    class InterruptedInvoiceAdapter:
        provider_name = "ecpay"
        external_issue_started = False

        def prepare_invoice(self, request):
            return prepare_test_invoice(request)

        async def query_invoice(self, relate_number, *, buyer_type):
            return {"RtnCode": 0, "RtnMsg": "查無資料"}

        async def issue_prepared_invoice(self, request):
            self.external_issue_started = True
            raise asyncio.CancelledError

    adapter = InterruptedInvoiceAdapter()
    with pytest.raises(asyncio.CancelledError):
        await issue_paid_order_invoice(database_session, order_id, adapter)
    assert adapter.external_issue_started is True
    await database_session.rollback()
    database_session.expunge_all()

    saved_order = await database_session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.invoice))
    )
    invoice = saved_order.invoice
    assert invoice is not None
    assert invoice.status == InvoiceStatus.PENDING
    assert invoice.provider_request

    saved_order.payment_status = PaymentStatus.REFUNDED
    refund = Refund(
        order=saved_order,
        amount=order_amount,
        status=RefundStatus.COMPLETED,
        reason="付款已退款",
        requested_by_id=user_id,
    )
    database_session.add(refund)
    await database_session.flush()
    assert enqueue_invoice_adjustment_after_refund(
        database_session,
        saved_order,
        refund,
    ) is True
    assert invoice.status == InvoiceStatus.VOID_PENDING


@pytest.mark.asyncio
async def test_delivered_logistics_invoice_includes_shipping_fee(
    database_session,
) -> None:
    _user, _product, order = await make_regular_order(database_session)
    order.amount_total = 360
    order.payment_status = PaymentStatus.PAID
    order.fulfillment_status = FulfillmentStatus.PICKED_UP
    order.invoice_status = InvoiceStatus.PENDING
    order.fulfillment_method = FulfillmentMethod.ECPAY_LOGISTICS
    order.fulfillment = OrderFulfillment(
        method=FulfillmentMethod.ECPAY_LOGISTICS,
        status=FulfillmentState.DELIVERED,
        shipment=Shipment(
            channel=ShippingChannel.HOME_DELIVERY,
            temperature=ShippingTemperature.AMBIENT,
            status=ShipmentStatus.DELIVERED,
            shipping_fee=160,
        ),
    )
    await database_session.commit()

    class InvoiceAdapter:
        provider_name = "ecpay"
        issued_request = None

        def prepare_invoice(self, request):
            return prepare_test_invoice(request)

        async def query_invoice(self, relate_number, *, buyer_type="personal"):
            return {"RtnCode": 0, "RtnMsg": "not found"}

        async def issue_prepared_invoice(self, request):
            self.issued_request = request
            return InvoiceIssueResult(
                relate_number=request.relate_number,
                invoice_number="AB12345679",
                invoice_date="2026-07-29 12:30:00",
                random_number="5678",
                raw={"RtnCode": 1, "InvoiceNo": "AB12345679"},
            )

    adapter = InvoiceAdapter()
    await issue_paid_order_invoice(database_session, order.id, adapter)

    assert adapter.issued_request is not None
    actual_lines = [
        (line.name, line.amount, line.tax_type)
        for line in adapter.issued_request.items
    ]
    assert actual_lines == [
        ("測試白米", 200, "1"),
        ("運費", 160, "1"),
    ]
    assert (
        sum(line.amount for line in adapter.issued_request.items)
        == order.amount_total
    )


@pytest.mark.asyncio
async def test_group_payment_after_hold_and_deadline_is_refunded_even_if_stale_active(
    database_session,
) -> None:
    user = User(
        email="group-buyer@example.test",
        display_name="團購買家",
        password_hash="test",
        membership_type=MembershipType.MEMBER,
    )
    product = Product(
        slug="group-rice",
        name="團購白米",
        category="米食",
        unit="包",
        member_price=100,
        nonmember_price=120,
        stock_quantity=20,
        tax_type=TaxType.TAXABLE,
    )
    database_session.add_all([user, product])
    await database_session.flush()
    now = datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc)
    campaign = GroupCampaign(
        target_type=TargetType.PRODUCT,
        target_id=product.id,
        title="白米共同購買",
        member_price=90,
        nonmember_price=110,
        min_paid_quantity=10,
        supply_cap=20,
        per_user_cap=5,
        deadline=now + timedelta(minutes=10),
        estimated_pickup_start=now + timedelta(days=2),
        estimated_pickup_end=now + timedelta(days=3),
        decision_status=GroupDecisionStatus.RECRUITING,
        intake_status=GroupIntakeStatus.OPEN,
        created_by_id=user.id,
    )
    database_session.add(campaign)
    await database_session.flush()
    order = Order(
        order_number="SLFGROUP0001",
        order_kind=OrderKind.GROUP,
        user_id=user.id,
        group_campaign_id=campaign.id,
        membership_type_snapshot=user.membership_type,
        amount_total=180,
        contact_email=user.email,
        items=[
            OrderItem(
                source_product_id=product.id,
                product_name=product.name,
                unit_label=product.unit,
                quantity=2,
                unit_price=90,
                subtotal=180,
                tax_type=product.tax_type,
            )
        ],
    )
    database_session.add(order)
    await database_session.commit()
    settings = payment_settings()
    attempt = await create_payment_attempt(
        database_session, order.id, user, settings, now=now
    )
    payload = signed_payment_callback(
        attempt, now + timedelta(minutes=20)
    )

    await payment_adapter_from_settings(settings).process_callback(
        payload,
        SQLAlchemyPaymentCallbackRepository(database_session),
    )
    reservation_status = await database_session.scalar(
        select(InventoryReservation.status).where(
            InventoryReservation.payment_attempt_id == attempt.id
        )
    )
    refund = await database_session.scalar(
        select(Refund).where(Refund.order_id == order.id)
    )
    attempt_status = await database_session.scalar(
        select(PaymentAttempt.status).where(PaymentAttempt.id == attempt.id)
    )
    order_status = await database_session.scalar(
        select(Order.payment_status).where(Order.id == order.id)
    )
    paid_quantity, reserved_quantity = (
        await database_session.execute(
            select(
                GroupCampaign.paid_quantity,
                GroupCampaign.reserved_quantity,
            ).where(GroupCampaign.id == campaign.id)
        )
    ).one()

    assert attempt_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert order_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert reservation_status == ReservationStatus.RELEASED
    assert paid_quantity == 0
    assert reserved_quantity == 0
    assert refund is not None


@pytest.mark.asyncio
async def test_cancelled_group_order_late_callback_is_refunded(
    database_session,
) -> None:
    user = User(
        email="cancelled-group@example.test",
        display_name="取消團購買家",
        password_hash="test",
        membership_type=MembershipType.MEMBER,
    )
    product = Product(
        slug="cancelled-group-rice",
        name="取消測試白米",
        category="米食",
        unit="包",
        member_price=100,
        nonmember_price=120,
        stock_quantity=20,
        tax_type=TaxType.TAXABLE,
    )
    database_session.add_all([user, product])
    await database_session.flush()
    now = datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc)
    campaign = GroupCampaign(
        target_type=TargetType.PRODUCT,
        target_id=product.id,
        title="取消測試共同購買",
        member_price=90,
        nonmember_price=110,
        min_paid_quantity=10,
        supply_cap=20,
        per_user_cap=5,
        deadline=now + timedelta(days=1),
        estimated_pickup_start=now + timedelta(days=2),
        estimated_pickup_end=now + timedelta(days=3),
        decision_status=GroupDecisionStatus.RECRUITING,
        intake_status=GroupIntakeStatus.OPEN,
        created_by_id=user.id,
    )
    database_session.add(campaign)
    await database_session.flush()
    order = Order(
        order_number="SLFGROUPCANCEL001",
        order_kind=OrderKind.GROUP,
        user_id=user.id,
        group_campaign_id=campaign.id,
        membership_type_snapshot=user.membership_type,
        amount_total=180,
        contact_email=user.email,
        items=[
            OrderItem(
                source_product_id=product.id,
                product_name=product.name,
                unit_label=product.unit,
                quantity=2,
                unit_price=90,
                subtotal=180,
                tax_type=product.tax_type,
            )
        ],
    )
    database_session.add(order)
    await database_session.commit()
    settings = payment_settings()
    attempt = await create_payment_attempt(
        database_session, order.id, user, settings, now=now
    )
    loaded_attempt = await database_session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == attempt.id)
        .options(selectinload(PaymentAttempt.reservations))
    )
    await release_attempt_reservations(database_session, loaded_attempt)
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    order.payment_status = PaymentStatus.EXPIRED
    await database_session.commit()

    payload = signed_payment_callback(
        attempt, now + timedelta(minutes=5)
    )
    await payment_adapter_from_settings(settings).process_callback(
        payload,
        SQLAlchemyPaymentCallbackRepository(database_session),
    )

    reservation_status = await database_session.scalar(
        select(InventoryReservation.status).where(
            InventoryReservation.payment_attempt_id == attempt.id
        )
    )
    refund = await database_session.scalar(
        select(Refund).where(Refund.order_id == order.id)
    )
    paid_quantity, reserved_quantity = (
        await database_session.execute(
            select(
                GroupCampaign.paid_quantity,
                GroupCampaign.reserved_quantity,
            ).where(GroupCampaign.id == campaign.id)
        )
    ).one()

    assert order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert reservation_status == ReservationStatus.RELEASED
    assert paid_quantity == 0
    assert reserved_quantity == 0
    assert refund is not None
