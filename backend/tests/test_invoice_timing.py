import json
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import time_machine
from sqlalchemy import func, select

from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_service import InvoiceApplicationError, issue_paid_order_invoice, reconcile_order_invoice
from app.invoice_timing import pickup_invoice_due
from app.jobs import _process_invoice_event, _process_outbox
from app.models import AdminAudit, Invoice, InvoiceStatus, MembershipType, Order, OutboxEvent, OutboxStatus
from app.routers.invoices import invoices_router
from app.routers.operations import operations_router
from app.schemas import MealOrderCreate
from tests.support import api_test_context, auth_headers, tomorrow_meal_timeline
from tests.test_cash_payments import cash_context, create_cash, action, approve_cancellation
from tests.test_fanyu_invoice import fanyu_settings


async def arrange(c, order_id, *, actor=None, **payload):
    async with api_test_context(c.session, [invoices_router], settings=c.settings) as client:
        return await client.post(f"/v1/admin/orders/{order_id}/invoice/issue", headers=auth_headers(actor or c.admin), json={
            "handling": "pickup_day", "reason": "活動當日報帳", "confirm_not_invoiced_elsewhere": True, **payload,
        })


async def issue_jobs(c, order_id):
    return list(await c.session.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == order_id, OutboxEvent.event_type == "invoice.issue_requested",
    ).order_by(OutboxEvent.created_at, OutboxEvent.id)))


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def seed_scheduled_cash_order(c, *, request_id=None):
    created = await create_cash(c, client_request_id=request_id)
    assert created.status_code == 201, created.text
    order = await c.session.get(Order, created.json()["id"])
    await c.session.refresh(order, ["fulfillment"])
    order.invoice_handling = "pickup_day"
    order.invoice_issue_after = pickup_invoice_due(order.fulfillment.pickup_at)
    order.invoice_status = InvoiceStatus.NOT_ELIGIBLE
    if request_id:
        order.request_fingerprint = previous_meal_fingerprint(c, "cash_on_pickup", pickup_day=True)
    await c.session.commit()
    return order.id


def previous_meal_fingerprint(c, payment_method, *, pickup_day=False):
    body = MealOrderCreate(
        items=[{"offering_id": c.event.offerings[0].id, "quantity": 1}],
        contact_email=c.buyer.email, payment_method=payment_method,
        invoice_handling="pickup_day" if pickup_day else None,
    ).model_dump(
        mode="json",
        exclude={"client_request_id", "pickup_location_id", "needs_utensils", "contact_phone", "meal_booking_type"},
    )
    if not pickup_day:
        body.pop("invoice_handling")
    return hashlib.sha256(json.dumps({"event_id": c.event.id, "body": body}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@pytest.mark.asyncio
async def test_17th_payment_only_issues_on_18th_taipei_and_repeated_call_reuses_invoice(cash_context, monkeypatch):
    c = cash_context
    preorder_day = datetime(2026, 11, 17, 4, tzinfo=timezone.utc)
    calls = []

    async def transport(url, envelope, *_args):
        calls.append((url.rsplit("/", 1)[-1], envelope))
        if url.endswith("/queryInvoice"):
            return HTTPResponse(200, '{"statusCode":"3","statusDesc":"查無資料","respData":{}}', {})
        assert envelope["createDateTime"].startswith("20261118")
        assert "invoiceDate" not in envelope["reqData"] and "invDate" not in envelope["reqData"]
        assert envelope["reqData"]["printMark"] == "N"
        return HTTPResponse(200, json.dumps({"statusCode": "0", "respData": {
            "invNo": "AB12345678", "invDate": "20261118", "randomNumber": "2468",
        }}), {})

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport)
    monkeypatch.setattr("app.jobs.invoice_adapter_from_settings", lambda _: adapter)
    with time_machine.travel(preorder_day, tick=False):
        for field, value in tomorrow_meal_timeline(preorder_day).items():
            setattr(c.event, field, value)
        await c.session.commit()
        order_id = await seed_scheduled_cash_order(c)
        order = await c.session.get(Order, order_id)
        order.invoice_provider_context = adapter.binding_context
        await c.session.commit()
        collected = await action(c, order_id)
        assert collected.status_code == 200, collected.text
        assert collected.json()["invoice_status"] == "pending"
        jobs = await issue_jobs(c, order_id)
        assert len(jobs) == 1
        due = datetime(2026, 11, 17, 16, tzinfo=timezone.utc)
        assert aware(jobs[0].available_at) == due
        assert pickup_invoice_due(datetime(2026, 11, 18, 3)) == due
        assert await _process_outbox(c.session, c.settings, preorder_day, 100) == (0, 0)
        with pytest.raises(InvoiceApplicationError, match="尚未到"):
            await _process_invoice_event(c.session, c.settings, jobs[0])
        for operation in (issue_paid_order_invoice, reconcile_order_invoice):
            with pytest.raises(InvoiceApplicationError):
                await operation(c.session, order_id, adapter)
        assert calls == []
        assert await c.session.scalar(select(func.count(Invoice.id))) == 0
    with time_machine.travel(due, tick=False):
        assert await _process_outbox(c.session, c.settings, due, 100) == (1, 0)
        assert (await issue_jobs(c, order_id))[0].status == OutboxStatus.COMPLETED
        result = await issue_paid_order_invoice(c.session, order_id, adapter)
        again = await issue_paid_order_invoice(c.session, order_id, adapter)
        assert result.invoice_number == again.invoice_number == "AB12345678"
        assert [name for name, _ in calls] == ["queryInvoice", "openInvoice"]
        invoice = await c.session.scalar(select(Invoice).where(Invoice.order_id == order_id))
        assert aware(invoice.invoice_date).astimezone(ZoneInfo("Asia/Taipei")).date().isoformat() == "2026-11-18"
        assert invoice.payment_attempt_id is not None


@pytest.mark.asyncio
async def test_worker_recovers_after_pickup_day_with_real_current_date_without_backdating(cash_context, monkeypatch):
    c = cash_context
    order_id = await seed_scheduled_cash_order(c)
    assert (await action(c, order_id)).status_code == 200
    job = (await issue_jobs(c, order_id))[0]
    due = aware(job.available_at)
    recovery = due + timedelta(days=1, hours=1)
    expected_date = recovery.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    calls = []

    async def transport(url, envelope, *_args):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/queryInvoice"):
            return HTTPResponse(200, '{"statusCode":"3","statusDesc":"查無資料","respData":{}}', {})
        assert envelope["createDateTime"].startswith(expected_date)
        assert "invoiceDate" not in envelope["reqData"] and "invDate" not in envelope["reqData"]
        return HTTPResponse(200, json.dumps({"statusCode": "0", "respData": {
            "invNo": "AB12345678", "invDate": expected_date, "randomNumber": "2468",
        }}), {})

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport)
    order = await c.session.get(Order, order_id)
    order.invoice_provider_context = adapter.binding_context
    await c.session.commit()
    monkeypatch.setattr("app.jobs.invoice_adapter_from_settings", lambda _: adapter)
    with time_machine.travel(recovery, tick=False):
        assert await _process_outbox(c.session, c.settings, recovery, 100) == (1, 0)
    invoice = await c.session.scalar(select(Invoice).where(Invoice.order_id == order_id))
    assert aware(order.invoice_issue_after) == due
    assert aware(invoice.invoice_date).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d") == expected_date
    assert calls == ["queryInvoice", "openInvoice"]


@pytest.mark.asyncio
async def test_admin_arrangement_is_durable_idempotent_and_preserves_existing_carrier(cash_context):
    c = cash_context
    created = await create_cash(c, invoice_carrier_type="mobile_barcode", invoice_carrier_value="/AB12+-." )
    order_id = created.json()["id"]
    assert (await action(c, order_id)).json()["invoice_status"] == "external"
    assert await issue_jobs(c, order_id) == []
    for _ in range(2):
        response = await arrange(c, order_id)
        assert response.status_code == 200, response.text
        assert response.json()["invoice_handling"] == "pickup_day"
    jobs = await issue_jobs(c, order_id)
    assert len(jobs) == 1 and jobs[0].status == OutboxStatus.PENDING
    assert await c.session.scalar(select(func.count(Invoice.id))) == 0
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "invoice.arranged")) == 1
    order = await c.session.get(Order, order_id)
    assert order.invoice_carrier_value == "/AB12+-."
    changed = await arrange(c, order_id, handling="on_payment")
    assert changed.status_code == 200
    jobs = await issue_jobs(c, order_id)
    assert sum(job.status == OutboxStatus.PENDING for job in jobs) == 1
    assert sum(job.status == OutboxStatus.COMPLETED for job in jobs) == 1


@pytest.mark.asyncio
async def test_cancel_before_due_cancels_queue_and_stale_dispatch_cannot_issue(cash_context, monkeypatch):
    c = cash_context
    order_id = await seed_scheduled_cash_order(c)
    assert (await action(c, order_id)).status_code == 200
    job = (await issue_jobs(c, order_id))[0]
    await approve_cancellation(c, order_id)
    assert (await issue_jobs(c, order_id))[0].status == OutboxStatus.COMPLETED
    assert (await action(c, order_id, "cash-refund")).status_code == 200
    monkeypatch.setattr("app.jobs.invoice_adapter_from_settings", lambda *_: pytest.fail("取消後不得連線發票服務"))
    with time_machine.travel(aware(job.available_at) + timedelta(hours=1), tick=False):
        await _process_invoice_event(c.session, c.settings, job)
    assert await c.session.scalar(select(func.count(Invoice.id))) == 0
    assert (await arrange(c, order_id)).status_code == 409


@pytest.mark.asyncio
async def test_cash_collection_correction_cancels_unissued_job_and_recollection_queues_once(cash_context):
    c = cash_context
    order_id = await seed_scheduled_cash_order(c)
    assert (await action(c, order_id)).status_code == 200
    assert (await action(c, order_id, "cash-unpaid")).status_code == 200
    assert (await issue_jobs(c, order_id))[0].status == OutboxStatus.COMPLETED
    for _ in range(2):
        assert (await action(c, order_id)).status_code == 200
    jobs = await issue_jobs(c, order_id)
    assert len(jobs) == 2 and sum(job.status == OutboxStatus.PENDING for job in jobs) == 1


@pytest.mark.asyncio
async def test_permissions_unpaid_missing_confirmation_and_uncertain_issue_are_blocked(cash_context):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    assert (await arrange(c, order_id, actor=c.buyer)).status_code == 403
    assert (await arrange(c, order_id)).status_code == 409
    assert (await action(c, order_id)).status_code == 200
    assert (await arrange(c, order_id, confirm_not_invoiced_elsewhere=False)).status_code == 422
    order = await c.session.get(Order, order_id)
    c.session.add(Invoice(order=order, relate_number="UNKNOWNISSUE", status=InvoiceStatus.FAILED))
    await c.session.commit()
    assert (await arrange(c, order_id)).status_code == 409
    assert (await action(c, order_id, "cash-unpaid")).status_code == 409
    assert await issue_jobs(c, order_id) == []


@pytest.mark.asyncio
async def test_system_cash_invoice_refund_enqueues_adjustment(cash_context):
    c = cash_context
    order_id = (await create_cash(c, invoice_handling="on_payment")).json()["id"]
    assert (await action(c, order_id)).status_code == 200
    order = await c.session.get(Order, order_id)
    c.session.add(Invoice(order=order, relate_number="CASHISSUED", invoice_number="AB87654321", status=InvoiceStatus.ISSUED))
    order.invoice_status = InvoiceStatus.ISSUED
    await c.session.commit()
    await approve_cancellation(c, order_id)
    assert (await action(c, order_id, "cash-refund")).status_code == 200
    assert order.invoice_status == InvoiceStatus.VOID_PENDING
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.adjustment_required")) == 1


@pytest.mark.asyncio
async def test_invoice_preference_change_cannot_change_paid_order_tax_snapshot(cash_context):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    assert (await action(c, order_id)).status_code == 200
    order = await c.session.get(Order, order_id)
    order.membership_type_snapshot = MembershipType.MEMBER
    order.member_tax_exempt_snapshot = True
    await c.session.commit()
    response = await arrange(c, order_id, preferences={
        "invoice_buyer_type": "company", "invoice_buyer_tax_id": "24536806",
        "invoice_buyer_name": "合成學校", "invoice_buyer_email": "school@example.com",
    })
    assert response.status_code == 409
    assert await issue_jobs(c, order_id) == []


@pytest.mark.asyncio
async def test_online_order_rejects_external_invoice_ownership(cash_context):
    c = cash_context
    response = await create_cash(c, payment_method="online", invoice_handling="external")
    assert response.status_code == 422


@pytest.mark.parametrize("payment_method", ["online", "cash_on_pickup"])
@pytest.mark.asyncio
async def test_new_standard_meal_rejects_pickup_day_invoice_without_creating_order(cash_context, payment_method):
    c = cash_context
    response = await create_cash(c, payment_method=payment_method, invoice_handling="pickup_day", client_request_id=str(uuid4()))
    assert response.status_code == 422, response.text
    assert "取餐日開票適用會餐" in response.json()["detail"]
    assert await c.session.scalar(select(func.count(Order.id))) == 0
    assert c.event.offerings[0].reserved_quantity == 0


@pytest.mark.asyncio
async def test_existing_standard_pickup_day_order_replays_without_changing_schedule(cash_context):
    c = cash_context
    request_id = str(uuid4())
    order_id = await seed_scheduled_cash_order(c, request_id=request_id)
    scheduled = (await c.session.get(Order, order_id)).invoice_issue_after
    response = await create_cash(c, invoice_handling="pickup_day", client_request_id=request_id)
    assert response.status_code == 201, response.text
    assert response.json()["id"] == order_id
    assert response.json()["invoice_handling"] == "pickup_day"
    assert (await c.session.get(Order, order_id)).invoice_issue_after == scheduled
    assert await c.session.scalar(select(func.count(Order.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payment_method,legacy_policy", [("online", "on_payment"), ("cash_on_pickup", "external")])
async def test_predeployment_lost_response_replays_explicit_legacy_policy_without_second_order(cash_context, payment_method, legacy_policy):
    c = cash_context
    request_id = str(uuid4())
    response = await create_cash(c, payment_method=payment_method, client_request_id=request_id)
    assert response.status_code == 201, response.text
    order = await c.session.get(Order, response.json()["id"])
    assert response.json()["invoice_handling"] == legacy_policy
    assert response.json()["invoice_issue_after"] is None
    order.request_fingerprint = previous_meal_fingerprint(c, payment_method)
    await c.session.commit()
    for policy in (legacy_policy, None):
        repeat = await create_cash(c, payment_method=payment_method, client_request_id=request_id, invoice_handling=policy)
        assert repeat.status_code == 201, repeat.text
        assert repeat.json()["id"] == response.json()["id"]
    changed = await create_cash(c, payment_method=payment_method, client_request_id=request_id, invoice_handling="pickup_day")
    assert changed.status_code == 409
    assert await c.session.scalar(select(func.count(Order.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["", "invalid-email"])
async def test_admin_arrangement_requires_invoice_email_before_queuing(cash_context, email):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    assert (await action(c, order_id)).status_code == 200
    order = await c.session.get(Order, order_id)
    order.contact_email = email
    order.invoice_buyer_email = None
    await c.session.commit()
    response = await arrange(c, order_id)
    assert response.status_code == 422, response.text
    assert await issue_jobs(c, order_id) == []
    assert order.invoice_status == InvoiceStatus.EXTERNAL
    fixed = await arrange(c, order_id, preferences={"invoice_buyer_email": "corrected@example.com"})
    assert fixed.status_code == 200, fixed.text
    assert len(await issue_jobs(c, order_id)) == 1


@pytest.mark.asyncio
async def test_failed_job_retry_api_waits_for_invoice_day_without_consuming_early_retry(cash_context):
    c = cash_context
    order_id = await seed_scheduled_cash_order(c)
    assert (await action(c, order_id)).status_code == 200
    job = (await issue_jobs(c, order_id))[0]
    due = aware(job.available_at)
    job.status = OutboxStatus.FAILED
    job.attempts = 8
    await c.session.commit()
    async with api_test_context(c.session, [operations_router], settings=c.settings) as client:
        path = f"/v1/admin/failed-jobs/{job.id}/retry"
        blocked = await client.post(path, headers=auth_headers(c.admin), json={"reason": "核對排程"})
        assert blocked.status_code == 409 and "尚未到" in blocked.text
        await c.session.refresh(job)
        assert job.status == OutboxStatus.FAILED and job.attempts == 8
        assert aware(job.available_at) == due
        with time_machine.travel(due, tick=False):
            retry = await client.post(path, headers=auth_headers(c.admin), json={"reason": "已到活動日，恢復開票"})
            assert retry.status_code == 200, retry.text
            await c.session.refresh(job)
            assert job.status == OutboxStatus.PENDING and job.attempts == 8
            assert aware(job.available_at) == due
