from datetime import datetime, timezone

import pytest

from app.integrations.payment_service import PaymentApplicationError, create_payment_attempt
from app.models import (
    FulfillmentStatus, Invoice, InvoiceBuyerType, InvoiceStatus, PaymentStatus,
    SalesChannel, User, UserRole,
)
from app.routers.invoices import invoices_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_integrations import make_regular_order, payment_settings

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("channel", list(SalesChannel))
@pytest.mark.parametrize("marker", ["timestamp", "fulfillment"])
async def test_cancelled_order_cannot_restart_payment(database_session, channel, marker):
    user, _product, order = await make_regular_order(database_session)
    order.sales_channel = channel
    order.payment_status = PaymentStatus.EXPIRED
    if marker == "timestamp":
        order.cancelled_at = datetime.now(timezone.utc)
    else:
        order.fulfillment_status = FulfillmentStatus.CANCELLED
    await database_session.commit()
    with pytest.raises(PaymentApplicationError, match="已取消"):
        await create_payment_attempt(database_session, order.id, user, payment_settings())


async def test_preexisting_checkout_link_rejects_cancelled_order(database_session):
    user, _product, order = await make_regular_order(database_session)
    settings = payment_settings()
    attempt = await create_payment_attempt(database_session, order.id, user, settings)
    order.cancelled_at = datetime.now(timezone.utc)
    await database_session.commit()
    async with api_test_context(database_session, [payments_router], settings=settings) as client:
        response = await client.get(f"/payments/{attempt.id}/checkout")
    assert response.status_code == 410


@pytest.mark.parametrize("provider_status,previous,expected", [
    ("1", InvoiceStatus.VOID_PENDING, "voided"),
    ("0", InvoiceStatus.VOID_PENDING, "void_pending"),
    ("0", InvoiceStatus.VOIDED, "voided"),
    ("3", InvoiceStatus.VOID_PENDING, "failed"),
])
async def test_refunded_invoice_query_preserves_adjustment_until_confirmed(
    database_session, monkeypatch, provider_status, previous, expected,
):
    import app.routers.invoices as invoice_routes

    _buyer, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.REFUNDED
    order.invoice_status = previous
    # Historical buyer snapshot must remain authoritative even if the order differs.
    invoice = Invoice(
        order=order, relate_number="AUDIT-INVOICE-QUERY", provider="fanyu",
        invoice_number="AB12345671", buyer_type=InvoiceBuyerType.COMPANY, status=previous,
    )
    original_voided_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    if previous == InvoiceStatus.VOIDED:
        invoice.voided_at = original_voided_at
    admin = User(email="audit-admin@example.com", display_name="Admin", password_hash="unused", user_role=UserRole.ADMIN)
    database_session.add_all([invoice, admin])
    await database_session.commit()

    class Provider:
        provider_name = "fanyu"

        async def query_invoice(self, relate_number, *, buyer_type):
            assert relate_number == "AUDIT-INVOICE-QUERY"
            assert buyer_type == "company"
            return {"RtnCode": 1, "InvoiceNo": "AB12345671", "ProviderStatus": provider_status}

        def prepare_invoice(self, request):
            raise AssertionError("Read-only query must not rebuild historical issue data")

        async def issue_prepared_invoice(self, request):
            raise AssertionError("Read-only query must not issue")

    monkeypatch.setattr(invoice_routes, "invoice_adapter_from_settings", lambda _settings: Provider())
    async with api_test_context(database_session, [invoices_router], settings=make_test_settings()) as client:
        response = await client.post(
            f"/v1/admin/orders/{order.id}/invoice/query",
            headers=auth_headers(admin), json={"reason": "核對供應商作廢狀態"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == expected
    assert response.json()["invoice_number"] == "AB12345671"
    if previous == InvoiceStatus.VOIDED:
        await database_session.refresh(invoice)
        assert invoice.voided_at.replace(tzinfo=timezone.utc) == original_voided_at


@pytest.mark.parametrize("status", [PaymentStatus.PENDING, PaymentStatus.REFUNDED])
async def test_invoice_query_cannot_create_invoice_for_unpaid_subject(database_session, monkeypatch, status):
    import app.routers.invoices as invoice_routes

    buyer, _product, order = await make_regular_order(database_session)
    buyer.user_role = UserRole.ADMIN
    order.payment_status = status
    await database_session.commit()
    class Provider:
        provider_name = "fanyu"

        async def query_invoice(self, *args, **kwargs):
            raise AssertionError("No provider query without existing invoice or successful payment")

        def prepare_invoice(self, request):
            raise AssertionError("No issue data may be created")

    monkeypatch.setattr(invoice_routes, "invoice_adapter_from_settings", lambda _: Provider())
    async with api_test_context(database_session, [invoices_router], settings=make_test_settings()) as client:
        response = await client.post(
            f"/v1/admin/orders/{order.id}/invoice/query", headers=auth_headers(buyer), json={"reason": "確認沒有未付款開票"},
        )
    assert response.status_code == 409


async def test_supplier_write_still_rejects_reserved_email():
    from pydantic import ValidationError
    from app.schemas import SupplierUpdate

    with pytest.raises(ValidationError):
        SupplierUpdate(email="eggs@example.test")
