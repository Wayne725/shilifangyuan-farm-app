"""Run only against the disposable local PostgreSQL CI database."""

import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import app.jobs as jobs
import app.routers.invoices as invoices
from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_service import InvoiceApplicationError
from app.models import (
    Invoice,
    InvoiceStatus,
    Order,
    OutboxEvent,
    PaymentStatus,
    Refund,
    RefundStatus,
    User,
    UserRole,
)
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, fanyu_test_context, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order


@pytest.mark.parametrize("first_actor", ["worker", "admin"])
@pytest.mark.asyncio
async def test_auto_void_and_admin_share_durable_submission_marker(
    postgres_sessions, monkeypatch, first_actor,
):
    invoice_date = datetime.now(timezone.utc)
    provider_date = invoice_date.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    async with postgres_sessions() as session:
        admin, _product, order = await make_regular_order(
            session, invoice_context=fanyu_test_context(),
        )
        admin.user_role = UserRole.ADMIN
        order.payment_status = PaymentStatus.REFUNDED
        order.invoice_status = InvoiceStatus.VOID_PENDING
        invoice = Invoice(
            order=order,
            relate_number="CONCURRENTAUTOVOID",
            provider="fanyu",
            status=InvoiceStatus.VOID_PENDING,
            invoice_number="AB12345678",
            invoice_date=invoice_date,
            total_amount=200,
            provider_request={"sellerID": "15989995"},
            provider_context=fanyu_test_context(),
            void_source="refund",
        )
        refund = Refund(
            order_id=order.id,
            amount=200,
            status=RefundStatus.COMPLETED,
            reason="隔離併發測試",
            requested_by_id=admin.id,
        )
        session.add_all([invoice, refund])
        await session.flush()
        event = OutboxEvent(
            event_type="invoice.adjustment_required",
            aggregate_type="invoice",
            aggregate_id=invoice.id,
            payload={
                "auto_void": True,
                "adjustment": "void",
                "invoice_id": invoice.id,
                "order_id": order.id,
                "refund_id": refund.id,
            },
        )
        session.add(event)
        await session.commit()
        admin_id, order_id, event_id = admin.id, order.id, event.id

    submission_started = asyncio.Event()
    finish_submission = asyncio.Event()
    submitted = []
    queried_by = []
    cancelled = False

    def adapter_for(actor):
        async def transport(url, body, _headers, _timeout):
            nonlocal cancelled
            if url.endswith("/cancelInvoice"):
                submitted.append(actor)
                assert len(submitted) == 1, "作廢 API 不得因管理員與 worker 併發而重送"
                submission_started.set()
                await finish_submission.wait()
                cancelled = True
                data = {
                    "invNo": "AB12345678",
                    "cancelDate": provider_date,
                    "cancelTime": "11:00:00",
                }
            else:
                assert url.endswith("/queryInvoice")
                queried_by.append(actor)
                data = {
                    "invNo": "AB12345678",
                    "invDate": provider_date,
                    "status": "1" if cancelled else "0",
                }
            return HTTPResponse(
                200, json.dumps({"statusCode": "0", "respData": data}), {},
            )

        return FanyuInvoiceAdapter(fanyu_settings(), transport=transport)

    monkeypatch.setattr(jobs, "invoice_adapter_from_settings", lambda _: adapter_for("worker"))
    monkeypatch.setattr(invoices, "invoice_adapter_from_settings", lambda _: adapter_for("admin"))
    settings = make_test_settings(invoice_provider="fanyu")

    async def perform(actor, session, *, expect_pending=False):
        if actor == "worker":
            if expect_pending:
                with pytest.raises(InvoiceApplicationError):
                    await jobs._dispatch_outbox_event(session, settings, event_id)
                await session.rollback()
            else:
                await jobs._dispatch_outbox_event(session, settings, event_id)
                await session.commit()
            return
        admin = await session.get(User, admin_id)
        async with api_test_context(
            session, [invoices.invoices_router], settings=settings,
        ) as client:
            response = await client.post(
                f"/v1/admin/orders/{order_id}/invoice/void",
                headers=auth_headers(admin),
                json={"reason": "隔離併發測試"},
            )
            assert response.status_code in {200, 202}, response.text

    second_actor = "admin" if first_actor == "worker" else "worker"
    async with postgres_sessions() as first_session, postgres_sessions() as second_session:
        # Keep an older ORM snapshot so the locked reload must refresh the marker.
        stale_order = await second_session.scalar(
            select(Order).where(Order.id == order_id).options(selectinload(Order.invoice))
        )
        assert stale_order.invoice.void_source == "refund"
        await second_session.commit()
        first_task = asyncio.create_task(perform(first_actor, first_session))
        try:
            await asyncio.wait_for(submission_started.wait(), timeout=10)
            await asyncio.wait_for(
                perform(second_actor, second_session, expect_pending=second_actor == "worker"),
                timeout=10,
            )
            assert submitted == [first_actor]
            assert set(queried_by) == {"worker", "admin"}
        finally:
            finish_submission.set()
            await asyncio.wait_for(first_task, timeout=10)

    async with postgres_sessions() as session:
        await perform("worker", session)
        final_order = await session.scalar(
            select(Order).where(Order.id == order_id).options(selectinload(Order.invoice))
        )
        assert final_order.invoice_status == InvoiceStatus.VOIDED
        assert final_order.invoice.status == InvoiceStatus.VOIDED
        assert final_order.invoice.provider_status == "1"
    assert submitted == [first_actor]
