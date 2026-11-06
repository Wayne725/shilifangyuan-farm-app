import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.feie import FeieError
from app.manual_order_printing import load_manual_print_order
from app.models import AdminAudit, FulfillmentState, FulfillmentStatus, Order, OrderItemOption, PaymentStatus, PrintJob, SalesChannel
from app.printer_worker import process_print_job
from app.printing import enqueue_cancellation_print, open_receipt_content
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import cash_context, create_cash
from tests.test_feie_printing import FakeFeie, live_settings


@pytest.fixture
async def manual_context(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    c.settings.printer_dry_run = True
    c.settings.pii_encryption_keys_json = json.dumps({"test": base64.b64encode(b"m" * 32).decode()})
    c.settings.pii_encryption_current_version = "test"
    response = await create_cash(c, kitchen_note="飯少")
    assert response.status_code == 201, response.text
    c.order_id = response.json()["id"]
    initial = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == c.order_id))
    initial.status = "failed"
    c.initial_id = initial.id
    order = await c.session.get(Order, c.order_id)
    order.payment_status, order.paid_at = PaymentStatus.PAID, datetime.now(timezone.utc)
    await c.session.commit()
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        yield SimpleNamespace(**{**vars(c), "client": client,
            "factory": async_sessionmaker(c.session.bind, expire_on_commit=False)})


def path(c, suffix):
    return f"/v1/admin/printers/orders/{c.order_id}/{suffix}"


async def state(c, payment, *, cancelled=False, paid_at=True):
    order = await load_manual_print_order(c.session, c.order_id)
    order.payment_status = payment
    order.paid_at = datetime.now(timezone.utc) if paid_at and payment == PaymentStatus.PAID else None
    order.cancelled_at = datetime.now(timezone.utc) if cancelled else None
    order.fulfillment.status = FulfillmentState.CANCELLED if cancelled else FulfillmentState.PREPARING
    await c.session.commit()
    return order


async def preview(c):
    result = await c.client.get(path(c, "preview"), headers=auth_headers(c.admin))
    assert result.status_code == 200, result.text
    return result.json()


async def submit(c, data=None, **kwargs):
    data = data or await preview(c)
    body = {"request_id": str(uuid4()), "reason": "核對目前訂單狀態", "preview_token": data["preview_token"], **kwargs}
    result = await c.client.post(path(c, "print"), headers=auth_headers(c.admin), json=body)
    return result, body


async def stored(c, job_id):
    async with c.factory() as session:
        return await session.get(PrintJob, job_id)


@pytest.mark.parametrize("payment,cancelled,paid_at,kind", [
    *[(value, False, True, "order" if value == PaymentStatus.PAID else "reference") for value in PaymentStatus],
    (PaymentStatus.PAID, True, True, "reference"),
    (PaymentStatus.PAID, False, False, "reference"),
])
async def test_only_confirmed_paid_uncancelled_orders_allow_manual_print(manual_context, payment, cancelled, paid_at, kind):
    c = manual_context
    order = await state(c, payment, cancelled=cancelled, paid_at=paid_at)
    order.fulfillment.pickup_code = "876543"
    await c.session.commit()
    data = await preview(c)
    eligible = payment == PaymentStatus.PAID and paid_at and not cancelled
    assert data["kind"] == kind and data["can_print"] is eligible
    assert data["payment_status"] == payment.value
    historical = await c.client.get(f"/v1/admin/printers/jobs/{c.initial_id}", headers=auth_headers(c.admin))
    assert historical.status_code == 200
    assert historical.json()["reprint_blocked_reason"] == data["blocked_reason"]
    assert "管理員補印" in data["preview"].splitlines()
    if payment == PaymentStatus.LATE_PAID_REFUND_REQUIRED:
        assert "逾期付款待退款" in data["preview"]
    if kind == "reference":
        assert "訂單紀錄單" in data["preview"] and "請勿製作" in data["preview"].splitlines()
        assert "876543" not in data["preview"] and "取餐碼" not in data["preview"]
    result, _ = await submit(c, data)
    if not eligible:
        assert data["blocked_reason"] and result.status_code == 409
        assert result.json()["detail"] == data["blocked_reason"]
        assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 1
        return
    assert result.status_code == 201, result.text
    job = await stored(c, result.json()["id"])
    assert job.kind == kind and c.buyer.email not in json.dumps(job.snapshot)
    assert "customer_email_encrypted" in job.snapshot
    assert await process_print_job(c.settings, factory=c.factory)
    job = await stored(c, job.id)
    assert job.status == "simulated" and job.content.startswith("receipt-content:v1:")
    content = open_receipt_content(job.content, c.order_id, c.settings)
    assert "付款狀態" in content and "履約狀態" in content and c.buyer.email in content
    assert "<CB>管理員補印</CB>" in content
    if kind == "reference":
        assert "<CB>請勿製作</CB>" in content


@pytest.mark.parametrize("fulfillment", [FulfillmentState.PICKED_UP, FulfillmentState.DELIVERED, FulfillmentState.NO_SHOW])
async def test_finished_paid_orders_are_reference_only(manual_context, fulfillment):
    c = manual_context
    order = await state(c, PaymentStatus.PAID)
    order.fulfillment.status = fulfillment
    await c.session.commit()
    data = await preview(c)
    assert data["kind"] == "reference" and "請勿製作" in data["preview"]
    assert "取餐碼" not in data["preview"]
    assert data["can_print"] is True
    response, _ = await submit(c, data)
    assert response.status_code == 201
    assert await process_print_job(c.settings, factory=c.factory)
    job = await stored(c, response.json()["id"])
    assert job.status == "simulated" and job.kind == "reference"
    assert "請勿製作" in open_receipt_content(job.content, c.order_id, c.settings)


async def test_manual_order_print_requires_admin_and_meal_order(manual_context):
    c = manual_context
    body = {"request_id": str(uuid4()), "reason": "權限測試", "preview_token": "a" * 64}
    for method, target, payload in [("GET", "preview", None), ("POST", "print", body),
                                    ("GET", f"print-requests/{uuid4()}", None)]:
        assert (await c.client.request(method, path(c, target), json=payload)).status_code == 401
        assert (await c.client.request(method, path(c, target), json=payload, headers=auth_headers(c.buyer))).status_code == 403
    order = await c.session.get(Order, c.order_id)
    order.sales_channel = SalesChannel.REGULAR
    await c.session.commit()
    assert (await c.client.get(path(c, "preview"), headers=auth_headers(c.admin))).status_code == 409


async def test_manual_preview_rebuilds_current_items_options_note_and_unknown_pickup(manual_context):
    c = manual_context
    order = await state(c, PaymentStatus.PENDING)
    order.items[0].product_name = "最新餐點"
    order.items[0].selected_options = [OrderItemOption(group_name="主食", option_name="豆皮壽司", price_delta=0)]
    order.kitchen_note = "最新備註"
    order.fulfillment.pickup_at = order.fulfillment.pickup_starts_at = None
    await c.session.commit()
    data = await preview(c)
    assert "最新餐點" in data["preview"] and "豆皮壽司" in data["preview"] and "最新備註" in data["preview"]
    assert "取餐：未指定" in data["preview"]
    assert (await preview(c))["preview_token"] == data["preview_token"]


async def test_manual_request_replay_precedes_current_state_token_and_enabled_guards(manual_context):
    c = manual_context
    result, body = await submit(c)
    assert result.status_code == 201, result.text
    await state(c, PaymentStatus.REFUNDED, cancelled=True)
    c.settings.printer_enabled = False
    again = await c.client.post(path(c, "print"), headers=auth_headers(c.admin), json=body)
    assert again.status_code == 201 and again.json()["id"] == result.json()["id"]
    lookup = await c.client.get(path(c, f"print-requests/{body['request_id']}"), headers=auth_headers(c.admin))
    assert lookup.status_code == 200 and lookup.json()["id"] == result.json()["id"]
    assert (await c.client.get(path(c, f"print-requests/{uuid4()}"), headers=auth_headers(c.admin))).status_code == 404
    assert await c.session.scalar(select(func.count()).select_from(AdminAudit).where(AdminAudit.action == "printer.order.print")) == 1


async def test_state_change_invalidates_preview_before_creation(manual_context):
    c = manual_context
    await state(c, PaymentStatus.PAID)
    data = await preview(c)
    await state(c, PaymentStatus.REFUNDED)
    response, _ = await submit(c, data)
    assert response.status_code == 409 and "退款" in response.text
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 1


async def test_waiting_payment_auto_job_cannot_be_bypassed_by_manual_print(manual_context):
    c = manual_context
    await state(c, PaymentStatus.PENDING)
    initial = await c.session.get(PrintJob, c.initial_id)
    initial.status = "pending"
    await c.session.commit()
    data = await preview(c)
    assert not data["can_print"] and "付款" in data["blocked_reason"]
    response, _ = await submit(c, data)
    assert response.status_code == 409
    await state(c, PaymentStatus.PAID)
    data = await preview(c)
    assert not data["can_print"] and "等待發送" in data["blocked_reason"]
    second, _ = await submit(c, data)
    assert second.status_code == 409


async def test_unknown_other_job_requires_acknowledgement_and_same_request_never_resends(manual_context):
    c = manual_context
    other = await c.session.get(PrintJob, c.initial_id)
    other.status = "unknown"
    await c.session.commit()
    data = await preview(c)
    assert data["duplicate_risk"] and data["can_print"]
    response, _ = await submit(c, data)
    assert response.status_code == 409
    response, body = await submit(c, data, acknowledge_duplicate=True)
    assert response.status_code == 201
    response2 = await c.client.post(path(c, "print"), headers=auth_headers(c.admin), json=body)
    assert response2.json()["id"] == response.json()["id"]


async def test_reference_refreshes_status_before_sending_but_never_becomes_production(manual_context):
    c = manual_context
    order = await state(c, PaymentStatus.PAID)
    order.fulfillment.status = FulfillmentState.PICKED_UP
    await c.session.commit()
    response, _ = await submit(c)
    await state(c, PaymentStatus.PAID)
    assert await process_print_job(c.settings, factory=c.factory)
    job = await stored(c, response.json()["id"])
    text = open_receipt_content(job.content, c.order_id, c.settings)
    assert job.kind == "reference" and "請勿製作" in text and "已付款" in text
    assert "取餐碼" not in text


@pytest.mark.parametrize("payment,cancelled", [(PaymentStatus.PENDING, False), (PaymentStatus.REFUNDED, False), (PaymentStatus.PAID, True)])
async def test_manual_production_does_not_send_after_payment_or_cancellation_change(manual_context, payment, cancelled):
    c = manual_context
    await state(c, PaymentStatus.PAID)
    response, _ = await submit(c)
    assert response.status_code == 201
    await state(c, payment, cancelled=cancelled)
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = await stored(c, response.json()["id"])
    assert job.status == "failed" and job.attempts == 0 and fake.submissions == 0


@pytest.mark.parametrize("payment,cancelled,paid_at", [
    *[(payment, False, True) for payment in PaymentStatus if payment != PaymentStatus.PAID],
    (PaymentStatus.PAID, True, True), (PaymentStatus.PAID, False, False),
])
async def test_legacy_queued_reference_never_sends_ineligible_order(manual_context, payment, cancelled, paid_at):
    c = manual_context
    await state(c, payment, cancelled=cancelled, paid_at=paid_at)
    job = await c.session.get(PrintJob, c.initial_id)
    job.kind, job.status = "reference", "pending"
    job.snapshot = {**job.snapshot, "kind": "reference", "manual": True}
    await c.session.commit()
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = await stored(c, c.initial_id)
    assert job.status == "failed" and job.attempts == 0 and fake.submissions == 0


@pytest.mark.parametrize("cancel_field", ["order", "fulfillment"])
async def test_cancellation_flags_also_block_preview_post_and_queued_reference(manual_context, cancel_field):
    c = manual_context
    order = await state(c, PaymentStatus.PAID)
    order.fulfillment.status = FulfillmentState.PICKED_UP
    await c.session.commit()
    data = await preview(c)
    response, _ = await submit(c, data)
    assert response.status_code == 201
    if cancel_field == "order":
        order.fulfillment_status = FulfillmentStatus.CANCELLED
    else:
        order.fulfillment.status = FulfillmentState.CANCELLED
    await c.session.commit()
    data = await preview(c)
    assert not data["can_print"] and "取消" in data["blocked_reason"]
    rejected, _ = await submit(c, data)
    assert rejected.status_code == 409
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    assert (await stored(c, response.json()["id"])).status == "failed" and fake.submissions == 0


@pytest.mark.parametrize("kind", ["order", "amendment", "cancellation"])
async def test_legacy_reprint_rejects_refunded_order_for_every_document_kind(manual_context, kind):
    c = manual_context
    await state(c, PaymentStatus.REFUNDED, cancelled=True)
    source = await c.session.get(PrintJob, c.initial_id)
    source.kind, source.status = kind, "printed"
    await c.session.commit()
    response = await c.client.post(f"/v1/admin/printers/jobs/{source.id}/reprint", headers=auth_headers(c.admin),
        json={"request_id": str(uuid4()), "reason": "已退款不可再次出單"})
    assert response.status_code == 409
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 1


async def test_queued_cancellation_reprint_is_blocked_but_original_notice_is_preserved(manual_context):
    c = manual_context
    await state(c, PaymentStatus.REFUNDED, cancelled=True)
    source = await c.session.get(PrintJob, c.initial_id)
    source.kind, source.status = "cancellation", "printed"
    retry = PrintJob(order_id=c.order_id, event_key=f"reprint:{uuid4()}", kind="cancellation",
        mode=source.mode, status="pending", reprint_of_id=source.id,
        printer_fingerprint=source.printer_fingerprint, snapshot=source.snapshot)
    c.session.add(retry)
    await c.session.commit()
    assert await process_print_job(c.settings, factory=c.factory)
    assert (await stored(c, retry.id)).status == "failed"


async def test_submitted_reference_after_refund_only_queries_without_resending(manual_context):
    c = manual_context
    settings = live_settings(pii_encryption_keys_json=c.settings.pii_encryption_keys_json,
        pii_encryption_current_version=c.settings.pii_encryption_current_version)
    for key in ("printer_dry_run", "feie_user", "feie_ukey", "feie_printer_sn", "feie_ukey_rotated", "feie_ap_endpoint_verified"):
        setattr(c.settings, key, getattr(settings, key))
    order = await state(c, PaymentStatus.PAID)
    order.fulfillment.status = FulfillmentState.PICKED_UP
    await c.session.commit()
    response, _ = await submit(c)
    fake = FakeFeie(printed=True)
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    await state(c, PaymentStatus.REFUNDED, cancelled=True)
    assert await process_print_job(c.settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert fake.submissions == 1 and fake.queries == 1
    assert (await stored(c, response.json()["id"])).status == "printed"


async def test_reference_legacy_reprint_is_rejected_and_reason_cannot_be_blank(manual_context):
    c = manual_context
    response, _ = await submit(c, reason="   ")
    assert response.status_code == 422
    response, _ = await submit(c)
    await process_print_job(c.settings, factory=c.factory)
    result = await c.client.post(f"/v1/admin/printers/jobs/{response.json()['id']}/reprint", headers=auth_headers(c.admin),
        json={"request_id": str(uuid4()), "reason": "測試舊補印入口"})
    assert result.status_code == 409 and "訂單頁" in result.text


async def test_cancellation_after_manual_production_keeps_stop_notice_and_reason(manual_context):
    c = manual_context
    await state(c, PaymentStatus.PAID)
    response, _ = await submit(c)
    assert response.status_code == 201
    await process_print_job(c.settings, factory=c.factory)
    order = await state(c, PaymentStatus.REFUND_PENDING, cancelled=True)
    order.cancellation_reason = "顧客臨時取消"
    await enqueue_cancellation_print(c.session, order, c.settings)
    await c.session.commit()
    cancellation = await c.session.scalar(select(PrintJob).where(PrintJob.kind == "cancellation"))
    assert not cancellation.snapshot.get("manual")
    assert await process_print_job(c.settings, factory=c.factory)
    cancellation = await stored(c, cancellation.id)
    content = open_receipt_content(cancellation.content, c.order_id, c.settings)
    assert cancellation.status == "simulated"
    assert "取消通知" in content and "請停止製作" in content and "顧客臨時取消" in content
    assert "訂單紀錄單" not in content and "管理員補印" not in content
    assert "履約狀態：製作中" not in content


@pytest.mark.parametrize("fulfillment", [FulfillmentState.PREPARING, FulfillmentState.PICKED_UP])
async def test_unsent_manual_job_fails_when_device_changes(manual_context, fulfillment):
    c = manual_context
    order = await state(c, PaymentStatus.PAID)
    order.fulfillment.status = fulfillment
    await c.session.commit()
    response, _ = await submit(c)
    c.settings.feie_printer_sn = SecretStr("different-test-device")
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = await stored(c, response.json()["id"])
    assert job.status == "failed" and job.last_error == "printer_configuration_changed" and fake.submissions == 0
    assert (await preview(c))["can_print"]


async def test_manual_submit_timeout_stays_unknown_and_request_retry_does_not_duplicate(manual_context):
    c = manual_context
    settings = live_settings(pii_encryption_keys_json=c.settings.pii_encryption_keys_json,
        pii_encryption_current_version=c.settings.pii_encryption_current_version)
    for key in ("printer_dry_run", "feie_user", "feie_ukey", "feie_printer_sn", "feie_ukey_rotated", "feie_ap_endpoint_verified"):
        setattr(c.settings, key, getattr(settings, key))
    response, body = await submit(c)
    fake = FakeFeie(error=FeieError("printer_timeout", uncertain=True))
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = await stored(c, response.json()["id"])
    assert job.status == "unknown" and job.attempts == 1 and fake.submissions == 1
    retry = await c.client.post(path(c, "print"), headers=auth_headers(c.admin), json=body)
    assert retry.status_code == 201 and retry.json()["id"] == job.id
    assert not await process_print_job(c.settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(hours=1))
    assert fake.submissions == 1 and (await preview(c))["duplicate_risk"]
