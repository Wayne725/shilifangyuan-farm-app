"""Acceptance contracts. Failures are recorded in the dated audit report.

These isolated reproductions do not call payment, invoice, or email providers.
Run explicitly: PYTHONPATH=backend pytest backend/audits
"""

from datetime import datetime, timedelta, timezone

import pytest

import app.jobs as jobs
from app.integrations.invoice_service import reconcile_order_invoice
from app.integrations.common import IntegrationError
from app.integrations.payment_service import (
    PaymentApplicationError,
    create_payment_attempt,
)
from app.integrations.pii_crypto import pii_cipher_from_settings
from app.models import (
    FulfillmentStatus,
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentStatus,
    Supplier,
    User,
    UserRole,
)
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers, prepare_test_invoice
from tests.test_integrations import make_regular_order, payment_settings
from tests.test_real_cooperative_records import real_data_settings

pytestmark = pytest.mark.asyncio


async def test_a02_supplier_seed_email_does_not_break_admin_list(database_session):
    settings = real_data_settings()
    cipher = pii_cipher_from_settings(settings)
    supplier_id = "00000000-0000-4000-8000-000000000001"
    aad = f"supplier:{supplier_id}"
    fields = {
        "responsible_person": "Audit supplier",
        "contact_person": "Audit contact",
        "phone": "0900000000",
        "email": "eggs@example.test",
        "bank_account": "audit-not-a-bank-account",
    }
    supplier = Supplier(
        id=supplier_id,
        business_name="Isolated seeded supplier",
        **{
            f"{key}_encrypted": cipher.encrypt_text(value, associated_data=aad)
            for key, value in fields.items()
        },
    )
    admin = User(
        email="audit-admin@example.test",
        display_name="Audit admin",
        password_hash="unused",
        user_role=UserRole.ADMIN,
    )
    database_session.add_all([supplier, admin])
    await database_session.commit()
    async with api_test_context(
        database_session, [operations_router], settings=settings
    ) as client:
        response = await client.get(
            "/v1/admin/suppliers", headers=auth_headers(admin)
        )
    assert response.status_code == 200
    assert response.json()[0]["id"] == supplier_id


async def test_a03_refunded_invoice_can_query_provider_void_status(database_session):
    _user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.REFUNDED
    order.invoice_status = InvoiceStatus.VOID_PENDING
    database_session.add(
        Invoice(
            order=order,
            relate_number="INVSLFTEST0001",
            provider="fanyu",
            invoice_number="AB12345671",
            status=InvoiceStatus.VOID_PENDING,
        )
    )
    await database_session.commit()

    class Adapter:
        provider_name = "fanyu"

        def prepare_invoice(self, request):
            return prepare_test_invoice(request, provider="fanyu")

        async def query_invoice(self, relate_number, *, buyer_type):
            return {
                "RtnCode": 1,
                "InvoiceNo": "AB12345671",
                "InvoiceDate": "2026-09-01 12:00:00",
                "ProviderStatus": "1",
            }

        async def issue_prepared_invoice(self, request):
            raise AssertionError("Query must never issue a new invoice")

    await reconcile_order_invoice(database_session, order.id, Adapter())
    assert order.invoice_status == InvoiceStatus.VOIDED


async def test_a04_cancelled_order_cannot_create_new_payment(database_session):
    user, _product, order = await make_regular_order(database_session)
    order.payment_status = PaymentStatus.EXPIRED
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    order.cancelled_at = datetime.now(timezone.utc)
    await database_session.commit()
    with pytest.raises(PaymentApplicationError):
        await create_payment_attempt(
            database_session, order.id, user, payment_settings()
        )


@pytest.mark.parametrize("query_fails", [False, True])
async def test_a05_repeated_reconciliation_eventually_checks_later_attempts(
    database_session, monkeypatch, query_fails
):
    _user, _product, order = await make_regular_order(database_session)
    now = datetime.now(timezone.utc)
    for index in range(2):
        database_session.add(
            PaymentAttempt(
                order_id=order.id,
                provider="raygate",
                merchant_trade_no=f"AUDIT-EXPIRED-{index}",
                amount=order.amount_total,
                status=PaymentStatus.EXPIRED,
                expires_at=now - timedelta(minutes=10 - index),
            )
        )
    await database_session.commit()
    queried = []

    class Adapter:
        async def query_order(self, trade_no):
            queried.append(trade_no)
            if query_fails:
                raise IntegrationError("isolated provider timeout")
            return {"TradeStatus": "0", "PaymentDisposition": "pending"}

    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *args: Adapter())
    for offset in range(3):
        await jobs.reconcile_once(
            database_session, payment_settings(), limit=1, now=now + timedelta(minutes=offset)
        )
    assert set(queried) == {"AUDIT-EXPIRED-0", "AUDIT-EXPIRED-1"}


async def test_slow_reconciliation_keeps_poll_delay_after_actual_query(database_session, monkeypatch):
    _user, _product, order = await make_regular_order(database_session)
    current = datetime.now(timezone.utc)
    attempt = PaymentAttempt(
        order_id=order.id, provider="raygate", merchant_trade_no="AUDIT-SLOW-POLL",
        amount=order.amount_total, status=PaymentStatus.EXPIRED,
        expires_at=current - timedelta(minutes=10),
    )
    database_session.add(attempt)
    await database_session.commit()

    class Adapter:
        async def query_order(self, trade_no):
            raise IntegrationError("isolated provider timeout")

    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *args: Adapter())
    await jobs.reconcile_once(
        database_session, payment_settings(), now=current - timedelta(minutes=5)
    )
    await database_session.refresh(attempt)
    assert attempt.next_reconcile_at.replace(tzinfo=timezone.utc) >= current + timedelta(seconds=60)
