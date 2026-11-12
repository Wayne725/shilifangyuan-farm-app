import json
from datetime import timedelta

import pytest
import time_machine
from sqlalchemy import select

from app.business_meals import business_payment_fields
from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_service import InvoiceApplicationError, issue_paid_order_invoice
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.models import AdminAudit, Invoice, InvoiceStatus, Order, OutboxEvent, OutboxStatus, User
from app.routers.invoices import invoices_router
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_business_meals import OPEN, CLOSE, business_body
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_meal_booking_policy import booking_context
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback


@pytest.mark.parametrize('existing_date', [None, '20300102', '20300103'])
async def test_recovery_after_pickup_day_only_queries_existing_invoice(booking_context, fake_payment, existing_date):
    client, session, buyer, _admin, event, body = booking_context
    calls = []
    async def transport(url, envelope, *_args):
        calls.append(url.rsplit('/', 1)[-1])
        assert url.endswith('/queryInvoice'), '會餐過日不得新開票'
        return HTTPResponse(200, json.dumps({'statusCode': '0' if existing_date else '3',
            'respData': {'invNo': 'AB12345678', 'invDate': existing_date, 'status': '0'} if existing_date else {}}), {})
    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport)
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    order = await session.get(Order, created.json()['id'])
    order.invoice_provider_context = adapter.binding_context
    await session.commit()
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=OPEN)
    await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback('business-invoice', successful_callback(attempt, OPEN, 'BIZINV'))
    with time_machine.travel(CLOSE + timedelta(hours=1), tick=False):
        if existing_date:
            result = await issue_paid_order_invoice(session, order.id, adapter)
            assert result.invoice_number == 'AB12345678'
        else:
            with pytest.raises(InvoiceApplicationError, match='超過取餐開票日'):
                await issue_paid_order_invoice(session, order.id, adapter)
        assert calls == ['queryInvoice']
        invoice = await session.scalar(select(Invoice).where(Invoice.order_id == order.id))
        assert invoice.status == (InvoiceStatus.ISSUED if existing_date else InvoiceStatus.FAILED)
        assert business_payment_fields(order)['invoice_followup_required'] is (existing_date != '20300102')


async def test_provider_wrong_day_is_preserved_and_marked_for_followup(booking_context, fake_payment):
    client, session, buyer, _admin, event, body = booking_context
    calls = []
    async def transport(url, envelope, *_args):
        calls.append(url.rsplit('/', 1)[-1])
        if url.endswith('/queryInvoice'):
            return HTTPResponse(200, '{"statusCode":"3","respData":{}}', {})
        return HTTPResponse(200, '{"statusCode":"0","respData":{"invNo":"AB12345678","invDate":"20300103","status":"0"}}', {})
    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport)
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
    order = await session.get(Order, created.json()['id'])
    order.invoice_provider_context = adapter.binding_context
    await session.commit()
    attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=OPEN)
    await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback('business-invoice', successful_callback(attempt, OPEN, 'BIZINV'))
    with time_machine.travel(OPEN, tick=False):
        await issue_paid_order_invoice(session, order.id, adapter)
        assert calls == ['queryInvoice', 'openInvoice']
        assert order.invoice.invoice_number == 'AB12345678'
        assert order.invoice.invoice_date.day == 2  # 01/03 00:00 Taipei is 01/02 16:00 UTC.
        assert '取餐日不符' in order.invoice.error_message
        assert business_payment_fields(order)['invoice_followup_required'] is True


async def test_invoice_query_crossing_midnight_cannot_start_new_invoice(booking_context, fake_payment):
    client, session, buyer, _admin, event, body = booking_context
    calls = []
    with time_machine.travel(CLOSE - timedelta(seconds=1), tick=False) as clock:
        async def transport(url, envelope, *_args):
            calls.append(url.rsplit('/', 1)[-1])
            assert url.endswith('/queryInvoice')
            clock.move_to(CLOSE)
            return HTTPResponse(200, '{"statusCode":"3","respData":{}}', {})
        adapter = FanyuInvoiceAdapter(fanyu_settings(), transport)
        # The booking fixture owns the preorder clock independently of this worker clock.
        created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
        order = await session.get(Order, created.json()['id'])
        order.invoice_provider_context = adapter.binding_context
        await session.commit()
        attempt = await create_payment_attempt(session, order.id, buyer, payment_settings(), now=CLOSE - timedelta(seconds=1))
        await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback('business-query-midnight', successful_callback(attempt, CLOSE - timedelta(seconds=1), 'BIZ-QUERY-MIDNIGHT'))
        with pytest.raises(InvoiceApplicationError, match='超過取餐開票日'):
            await issue_paid_order_invoice(session, order.id, adapter)
        assert calls == ['queryInvoice']


@pytest.mark.parametrize("action", ["arrange", "retry"])
@pytest.mark.parametrize("expired", [False, True])
async def test_admin_business_invoice_mutations_require_pickup_day(booking_context, fake_payment, action, expired):
    client, session, buyer, admin, event, body = booking_context
    created = await client.post(f"/v1/meal-events/{event.id}/orders", json=business_body(body), headers=auth_headers(buyer))
    assert created.status_code == 201
    order_id, admin_id = created.json()["id"], admin.id
    order = await session.get(Order, order_id)
    attempt = await create_payment_attempt(session, order_id, buyer, payment_settings(), now=OPEN)
    await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        "admin-business-issue", successful_callback(attempt, OPEN, "BIZ-ADMIN-ISSUE"),
    )
    job = await session.scalar(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == order_id, OutboxEvent.event_type == "invoice.issue_requested",
    ))
    job_id = job.id
    if action == "arrange":
        order.invoice_status = InvoiceStatus.NOT_ELIGIBLE
        job.status = OutboxStatus.COMPLETED
    else:
        job.status = OutboxStatus.FAILED
        job.attempts = 8
    await session.commit()
    session.expunge_all()

    current = CLOSE if expired else CLOSE - timedelta(seconds=1)
    with time_machine.travel(current, tick=False):
        headers = auth_headers(await session.get(User, admin_id))
        async with api_test_context(session, [invoices_router, operations_router], settings=make_test_settings()) as admin_client:
            if action == "arrange":
                response = await admin_client.post(
                    f"/v1/admin/orders/{order_id}/invoice/issue", headers=headers,
                    json={"handling": "pickup_day", "confirm_not_invoiced_elsewhere": True},
                )
                audit_action = "invoice.arranged"
            else:
                listing = await admin_client.get("/v1/admin/failed-jobs", headers=headers)
                assert listing.status_code == 200
                response = await admin_client.post(f"/v1/admin/failed-jobs/{job_id}/retry", headers=headers, json={})
                audit_action = "outbox.retry_requested"

    assert response.status_code == (409 if expired else 200), response.text
    if action == "retry":
        assert listing.json()["items"][0]["retryable"] is not expired
    jobs = list(await session.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == order_id, OutboxEvent.event_type == "invoice.issue_requested",
    )))
    audit = await session.scalar(select(AdminAudit).where(AdminAudit.action == audit_action))
    if expired:
        assert "超過取餐開票日" in response.json()["detail"]
        assert audit is None
        assert len(jobs) == 1 and jobs[0].id == job_id
        assert jobs[0].status == (OutboxStatus.COMPLETED if action == "arrange" else OutboxStatus.FAILED)
    else:
        assert audit is not None
        assert len([row for row in jobs if row.status == OutboxStatus.PENDING]) == 1
