from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.integrations import payment_service
from app.integrations.common import ProviderMutationBlocked
from app.integrations.ecpay_logistics import (
    LogisticsSelectionRequest,
    UpdateTempLogisticsRequest,
    ecpay_logistics_adapter_from_settings,
)
from app.integrations.email_sender import email_sender_from_settings
from app.integrations.feie import FeieClient, FeieError
from app.integrations.invoice import InvoiceIssueRequest, InvoiceLine
from app.integrations.invoice_service import invoice_adapter_from_settings
from app.integrations.mailersend import mailersend_adapter_from_settings
from app.models import InventoryReservation, PaymentAttempt
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_feie_printing import live_settings
from tests.test_integrations import make_regular_order, payment_settings, preview_raygate_acceptance_settings
from tests.test_raygate import raygate_settings


class TransportReached(Exception):
    pass


class TransportProbe:
    def __init__(self):
        self.paths = []

    async def __call__(self, url, _data, _headers, _timeout):
        self.paths.append(url)
        raise TransportReached


def test_recovery_mode_is_explicit_and_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RECOVERY_MODE", raising=False)
    assert Settings(_env_file=None).recovery_mode is False
    monkeypatch.setenv("RECOVERY_MODE", "true")
    assert Settings(_env_file=None).recovery_mode is True
    monkeypatch.setenv("RECOVERY_MODE", "misspelled")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def payment_config(recovery_mode: bool):
    config = raygate_settings()
    return preview_raygate_acceptance_settings(
        environment="test", recovery_mode=recovery_mode,
        raygate_payment_store_identifier=config.store_identifier,
        raygate_payment_key_hex=config.key_hex,
        raygate_payment_iv_hex=config.iv_hex,
        raygate_payment_merchant_id=config.merchant_id,
        raygate_payment_terminal_id=config.terminal_id,
    )


async def test_raygate_refund_blocked_before_transport_and_query_reaches_mock():
    args = dict(provider_order_id="synthetic", payment_type="linepay", amount=200,
                reason="synthetic", idempotency_key="synthetic")
    for recovery_mode in (True, False):
        adapter = payment_service.refund_adapter_from_settings(payment_config(recovery_mode), "raygate")
        probe = TransportProbe()
        adapter.transport = probe
        with pytest.raises(ProviderMutationBlocked if recovery_mode else TransportReached):
            await adapter.refund(**args)
        assert len(probe.paths) == (0 if recovery_mode else 1)
        with pytest.raises(TransportReached):
            await adapter.query_order("SYNTHETIC")
        assert "/api/query/" in probe.paths[-1]


def invoice_settings(provider: str, recovery_mode: bool):
    config = fanyu_settings()
    return make_test_settings(
        recovery_mode=recovery_mode, invoice_provider=provider,
        fanyu_invoice_company_id=config.company_id, fanyu_invoice_user_id=config.user_id,
        fanyu_invoice_auth_password=config.auth_password, fanyu_invoice_api_key=config.api_key,
        fanyu_invoice_seller_id=config.seller_id, fanyu_invoice_signature_verified=True,
        ecpay_invoice_merchant_id="synthetic", ecpay_invoice_hash_key="k" * 16, ecpay_invoice_hash_iv="i" * 16,
    )


@pytest.mark.parametrize("provider", ["fanyu", "ecpay"])
async def test_invoice_issue_guard_does_not_block_query(provider):
    request = InvoiceIssueRequest("SYNTHETIC", "buyer@example.test", [InvoiceLine(name="合成", quantity=1, unit_price=100)])
    for recovery_mode in (True, False):
        adapter = invoice_adapter_from_settings(invoice_settings(provider, recovery_mode))
        probe = TransportProbe()
        adapter.transport = probe
        with pytest.raises(ProviderMutationBlocked if recovery_mode else TransportReached):
            await adapter.issue_invoice(request)
        assert len(probe.paths) == (0 if recovery_mode else 1)
        with pytest.raises(TransportReached):
            await adapter.query_invoice("SYNTHETIC")
        assert "queryinvoice" in probe.paths[-1].lower() or "getissue" in probe.paths[-1].lower()


async def test_fanyu_void_and_allowance_block_before_transport():
    adapter = invoice_adapter_from_settings(invoice_settings("fanyu", True))
    probe = TransportProbe()
    adapter.transport = probe
    with pytest.raises(ProviderMutationBlocked):
        await adapter.cancel_invoice(
            order_id="SYNTHETIC", buyer_type="personal", buyer_tax_id="",
            invoice_number="AB12345678", invoice_date="20261111", reason="合成",
        )
    with pytest.raises(ProviderMutationBlocked):
        await adapter.open_allowance({"orderID": "SYNTHETIC"})
    assert probe.paths == []


async def test_feie_submit_stops_before_transport_without_disabling_read_queries():
    paths = []

    def mock(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"ret": 0, "data": True})

    client = FeieClient(live_settings(recovery_mode=True), transport=httpx.MockTransport(mock))
    with pytest.raises(FeieError, match="recovery_mode") as blocked:
        await client.submit("合成測試")
    assert not blocked.value.uncertain and not blocked.value.safe_to_retry
    assert paths == []
    assert await client.query("SYNTHETIC")
    assert paths == ["/Api/Open/queryOrderState"]


async def test_logistics_write_and_print_paths_block_before_transport():
    config = make_test_settings(
        recovery_mode=True, ecpay_logistics_merchant_id="synthetic",
        ecpay_logistics_hash_key="k" * 16, ecpay_logistics_hash_iv="i" * 16,
    )
    probe = TransportProbe()
    adapter = ecpay_logistics_adapter_from_settings(config, transport=probe)
    request = LogisticsSelectionRequest(
        goods_amount=100, goods_name="合成", sender_name="合成",
        sender_zip_code="100", sender_address="合成地址",
        server_reply_url="https://app.example.test/callback",
        client_reply_url="https://app.example.test/result",
    )
    with pytest.raises(ProviderMutationBlocked):
        await adapter.create_selection_page(request)
    with pytest.raises(ProviderMutationBlocked):
        await adapter.update_temp_order(UpdateTempLogisticsRequest(temp_logistics_id="SYNTHETIC"))
    with pytest.raises(ProviderMutationBlocked):
        await adapter.create_order(temp_logistics_id="SYNTHETIC", merchant_trade_no="SYNTHETIC")
    with pytest.raises(ProviderMutationBlocked):
        await adapter.create_print_document_page(logistics_ids=["SYNTHETIC"], logistics_sub_type="UNIMART")
    assert probe.paths == []
    with pytest.raises(TransportReached):
        await adapter.query_order(merchant_trade_no="SYNTHETIC")
    assert probe.paths == [config.ecpay_logistics_query_url]


def test_both_email_factories_reject_recovery_before_an_outbound_sender_is_created():
    config = make_test_settings(
        recovery_mode=True, resend_api_key="synthetic",
        mailersend_api_token="synthetic", mailersend_from_email="synthetic@example.test",
    )
    for factory in (email_sender_from_settings, mailersend_adapter_from_settings):
        with pytest.raises(ProviderMutationBlocked):
            factory(config)


@pytest.mark.parametrize("provider", ["ecpay", "raygate"])
async def test_hosted_payment_start_and_restored_redirect_are_blocked(provider, database_session):
    buyer, product, order = await make_regular_order(database_session)
    config = payment_settings() if provider == "ecpay" else payment_config(False)
    config.recovery_mode = True
    path = f"/v1/orders/{order.id}/payment-attempts"
    order_id, product_id = order.id, product.id
    headers = auth_headers(buyer)
    async with api_test_context(database_session, [payments_router], settings=config) as client:
        with patch.object(payment_service, "is_meals_test_product_order", AsyncMock(return_value=True)) as bypass:
            denied = await client.post(path, headers=headers)
            assert denied.status_code == 409 and "暫停線上付款" in denied.json()["detail"]
            bypass.assert_not_awaited()
        assert await database_session.scalar(select(func.count()).select_from(PaymentAttempt)) == 0
        assert await database_session.scalar(select(func.count()).select_from(InventoryReservation)) == 0

        config.recovery_mode = False
        started = await client.post(path, headers=headers)
        assert started.status_code == 201, started.text
        attempt_id = started.json()["attempt_id"]
        local_checkout = f"/payments/{attempt_id}/checkout"
        allowed = await client.get(local_checkout)
        assert allowed.status_code == (200 if provider == "ecpay" else 303)

        config.recovery_mode = True
        restored = await client.get(local_checkout)
        assert restored.status_code == 409 and "Location" not in restored.headers
        status_read = await client.get(f"/v1/payment-attempts/{attempt_id}", headers=headers)
        assert status_read.status_code == 200
        assert status_read.json()["order_id"] == order_id
        assert await database_session.scalar(select(func.count()).select_from(PaymentAttempt)) == 1
        assert await database_session.scalar(select(func.count()).select_from(InventoryReservation)) == 1
        await database_session.refresh(product)
        assert product.id == product_id and product.stock_quantity == 1
