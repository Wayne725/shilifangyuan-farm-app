import base64
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import printing
from app.integrations.feie import FeieError
from app.integrations.pii_crypto import pii_cipher_from_settings
from app.models import MemberProfile, Order, OrderItem, PaymentAttempt, PaymentMethod, PaymentStatus, PrintJob
from app.printer_worker import process_print_job
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import cash_context, create_cash
from tests.test_feie_printing import live_settings, print_context, read_job, set_job
from tests.test_quantity_amendment_printing import amend


def taipei_time(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")


def configure_test_cipher(settings):
    settings.pii_encryption_keys_json = json.dumps({"v1": base64.b64encode(b"r" * 32).decode()})
    return pii_cipher_from_settings(settings)


async def add_linepay_attempt(session, order, **overrides):
    now = datetime.now(timezone.utc)
    attempt = PaymentAttempt(order_id=order.id, order_revision=order.revision,
        provider="raygate", merchant_trade_no=f"R{uuid4().hex}", amount=order.amount_total,
        status=PaymentStatus.PAID, paid_at=now, expires_at=now + timedelta(hours=1),
        provider_response={"PaymentType": "linepay"})
    for name, value in overrides.items():
        setattr(attempt, name, value)
    session.add(attempt)
    return attempt


class RecordingFeie:
    def __init__(self, *, fail_first=False):
        self.contents = []
        self.fail_first = fail_first

    async def submit(self, content):
        self.contents.append(content)
        if self.fail_first and len(self.contents) == 1:
            raise FeieError("connection_not_established", safe_to_retry=True)
        return "isolated-vendor-job"


@pytest.mark.parametrize("has_phone", [True, False])
async def test_order_snapshot_records_phone_or_order_email_and_taipei_order_time(cash_context, has_phone):
    c = cash_context
    c.settings.printer_enabled = True
    if has_phone:
        cipher = configure_test_cipher(c.settings)
        c.session.add(MemberProfile(user_id=c.buyer.id,
            phone_encrypted=cipher.encrypt_text("0900123456", associated_data=f"member-profile:{c.buyer.id}"),
            legal_name_encrypted="unused", birth_date_encrypted="unused", address_encrypted="unused",
            emergency_contact_encrypted="unused", consent_version="test", consented_at=datetime.now(timezone.utc)))
        await c.session.commit()
    response = await create_cash(c, client_request_id=str(uuid4()), contact_email="receipt-contact@example.com")
    assert response.status_code == 201, response.text
    order = await c.session.get(Order, response.json()["id"])
    job = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == order.id))
    if has_phone:
        assert job.snapshot.get("customer_phone_encrypted")
        assert "customer_phone" not in job.snapshot
        assert printing.render_receipt_snapshot(job.snapshot, order.id, c.settings)["customer_phone"] == "0900123456"
        assert "customer_email" not in job.snapshot
    else:
        assert "customer_phone" not in job.snapshot
        assert job.snapshot.get("customer_email") == "receipt-contact@example.com"
    assert job.snapshot.get("ordered_at") == taipei_time(order.created_at)


async def test_first_submission_refreshes_confirmed_linepay_and_persists_taipei_print_time(print_context):
    c = print_context
    settings = live_settings()
    await set_job(c, mode="live", printer_fingerprint=printing.printer_fingerprint(settings))
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_method = PaymentMethod.ONLINE
        order.payment_status = PaymentStatus.PAID
        await add_linepay_attempt(session, order)
        await session.commit()
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    class ObserveDurableContext(RecordingFeie):
        async def submit(self, content):
            claimed = await read_job(c)
            assert claimed.status == "sending" and claimed.content == content
            assert claimed.snapshot["payment_method"] == "LINE Pay"
            assert claimed.snapshot["print_requested_at"] == taipei_time(now)
            return await super().submit(content)

    fake = ObserveDurableContext()
    await process_print_job(settings, factory=c.factory, client=fake, now=now)
    job = await read_job(c)
    assert job.status == "submitted"
    assert job.snapshot["payment_method"] == "LINE Pay" and job.snapshot["payment_status"] == "已付款"
    assert job.snapshot.get("print_requested_at") == taipei_time(now)
    assert job.content == fake.contents[0] and "LINE Pay" in job.content


@pytest.mark.parametrize("has_phone", [False, True])
async def test_safe_retry_keeps_first_content_payment_and_print_time_frozen(print_context, has_phone):
    c = print_context
    settings = live_settings()
    original = await read_job(c)
    snapshot = dict(original.snapshot)
    if has_phone:
        cipher = configure_test_cipher(settings)
        snapshot.pop("customer_email", None)
        snapshot["customer_phone_encrypted"] = cipher.encrypt_text("0900123456",
            associated_data=f"receipt-contact:{c.order_id}:phone")
    await set_job(c, mode="live", printer_fingerprint=printing.printer_fingerprint(settings), snapshot=snapshot)
    fake = RecordingFeie(fail_first=True)
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    await process_print_job(settings, factory=c.factory, client=fake, now=now)
    first = await read_job(c)
    assert first.status == "pending" and first.snapshot.get("print_requested_at") == taipei_time(now)
    assert printing.open_receipt_content(first.content, c.order_id, settings) == fake.contents[0]
    if has_phone:
        assert "0900123456" in fake.contents[0]
        assert "0900123456" not in first.content and "0900123456" not in json.dumps(first.snapshot)
    frozen_snapshot = dict(first.snapshot)
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_method = PaymentMethod.ONLINE
        order.payment_status = PaymentStatus.PAID
        await add_linepay_attempt(session, order)
        await session.commit()
    await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(minutes=1))
    retried = await read_job(c)
    assert retried.status == "submitted"
    assert retried.snapshot == frozen_snapshot
    assert retried.content == first.content and fake.contents == [fake.contents[0], fake.contents[0]]


async def test_recipient_phone_takes_priority_over_profile_and_time_crosses_midnight(cash_context):
    c = cash_context
    response = await create_cash(c, client_request_id=str(uuid4()))
    assert response.status_code == 201, response.text
    order = await c.session.scalar(select(Order).where(Order.id == response.json()["id"]).options(
        selectinload(Order.items).selectinload(OrderItem.selected_options), selectinload(Order.fulfillment)))
    cipher = configure_test_cipher(c.settings)
    c.session.add(MemberProfile(user_id=c.buyer.id,
        phone_encrypted=cipher.encrypt_text("0900111111", associated_data=f"member-profile:{c.buyer.id}"),
        legal_name_encrypted="unused", birth_date_encrypted="unused", address_encrypted="unused",
        emergency_contact_encrypted="unused", consent_version="test", consented_at=datetime.now(timezone.utc)))
    order.fulfillment.recipient_phone_encrypted = cipher.encrypt_text("0900222222",
        associated_data=f"order-fulfillment:{order.fulfillment.id}:recipient_phone")
    order.created_at = datetime(2026, 10, 31, 16, 0, 5, tzinfo=timezone.utc)
    c.settings.printer_enabled = True
    job = await printing.enqueue_order_print(c.session, order, c.buyer, c.settings)
    await c.session.commit()
    assert "customer_phone" not in job.snapshot
    assert printing.render_receipt_snapshot(job.snapshot, order.id, c.settings)["customer_phone"] == "0900222222"
    assert "customer_email" not in job.snapshot
    assert job.snapshot["ordered_at"] == "2026-11-01 00:00:05"


@pytest.mark.parametrize("configure_key", [True, False])
async def test_unreadable_phone_falls_back_to_order_email_without_losing_order(cash_context, configure_key):
    c = cash_context
    c.settings.printer_enabled = True
    if configure_key:
        configure_test_cipher(c.settings)
    c.session.add(MemberProfile(user_id=c.buyer.id, phone_encrypted="invalid-phone-envelope",
        legal_name_encrypted="unused", birth_date_encrypted="unused", address_encrypted="unused",
        emergency_contact_encrypted="unused", consent_version="test", consented_at=datetime.now(timezone.utc)))
    await c.session.commit()
    response = await create_cash(c, client_request_id=str(uuid4()), contact_email="safe-fallback@example.com")
    assert response.status_code == 201, response.text
    job = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == response.json()["id"]))
    assert job.snapshot["customer_email"] == "safe-fallback@example.com"
    assert "customer_phone" not in job.snapshot


@pytest.mark.parametrize("excluded", [
    "old_revision", "superseded", "failed", "pending", "missing_paid_at", "wrong_amount", "wrong_provider", "other_channel",
])
async def test_linepay_label_requires_current_confirmed_successful_attempt(print_context, excluded):
    c = print_context
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_method = PaymentMethod.ONLINE
        order.payment_status = PaymentStatus.PAID
        overrides = {
            "old_revision": {"order_revision": order.revision - 1},
            "superseded": {"superseded_at": datetime.now(timezone.utc)},
            "failed": {"status": PaymentStatus.FAILED},
            "pending": {"status": PaymentStatus.PENDING},
            "missing_paid_at": {"paid_at": None},
            "wrong_amount": {"amount": order.amount_total - 1},
            "wrong_provider": {"provider": "ecpay"},
            "other_channel": {"provider_response": {"PaymentType": "jkopay"}},
        }
        await add_linepay_attempt(session, order, **overrides[excluded])
        await session.commit()
        snapshot = await printing.payment_snapshot_from_session(session, order)
        assert snapshot["payment_method"] == "線上付款"
        assert snapshot["payment_status"] == "已付款"


async def test_newer_failed_retry_does_not_hide_confirmed_linepay(print_context):
    c = print_context
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_method = PaymentMethod.ONLINE
        order.payment_status = PaymentStatus.PAID
        await add_linepay_attempt(session, order, created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        await add_linepay_attempt(session, order, status=PaymentStatus.FAILED)
        await session.commit()
        snapshot = await printing.payment_snapshot_from_session(session, order)
        assert snapshot["payment_method"] == "LINE Pay" and snapshot["payment_status"] == "已付款"


@pytest.mark.parametrize("operation", ["reprint", "amendment", "cancellation"])
async def test_new_print_work_drops_old_print_time_but_keeps_contact(print_context, operation):
    c = print_context
    cipher = configure_test_cipher(c.settings)
    original = await read_job(c)
    phone_envelope = cipher.encrypt_text("0900123456", associated_data=f"receipt-contact:{c.order_id}:phone")
    source_snapshot = {**original.snapshot, "customer_phone_encrypted": phone_envelope, "print_requested_at": "2026-10-31 12:34:56"}
    source_snapshot.pop("customer_email", None)
    await set_job(c, status="printed", snapshot=source_snapshot,
        content=printing.seal_receipt_content("previous printed content", source_snapshot, c.order_id, c.settings))
    if operation == "reprint":
        async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
            response = await client.post(f"/v1/admin/printers/jobs/{c.job_id}/reprint", headers=auth_headers(c.admin),
                json={"request_id": str(uuid4()), "reason": "明確建立另一張補印單"})
        assert response.status_code == 201, response.text
    elif operation == "amendment":
        await amend(c, revision=2, quantity=2)
    else:
        async with c.factory() as session:
            order = await session.get(Order, c.order_id)
            order.cancelled_at = datetime.now(timezone.utc)
            order.cancellation_reason = "取消測試"
            await printing.enqueue_cancellation_print(session, order, c.settings)
            await session.commit()
    async with c.factory() as session:
        replacement = await session.scalar(select(PrintJob).where(PrintJob.order_id == c.order_id, PrintJob.id != c.job_id))
        assert replacement.snapshot["customer_phone_encrypted"] == phone_envelope
        assert "customer_phone" not in replacement.snapshot
        assert printing.render_receipt_snapshot(replacement.snapshot, c.order_id, c.settings)["customer_phone"] == "0900123456"
        assert "customer_email" not in replacement.snapshot
        assert replacement.snapshot.get("print_requested_at") is None
        assert replacement.content is None


async def test_pending_preview_does_not_freeze_or_persist_receipt_context(print_context):
    c = print_context
    original = await read_job(c)
    snapshot = deepcopy(original.snapshot)
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        response = await client.get(f"/v1/admin/printers/jobs/{c.job_id}", headers=auth_headers(c.admin))
    assert response.status_code == 200 and response.json()["preview"]
    persisted = await read_job(c)
    assert persisted.snapshot == snapshot
    assert persisted.content is None and persisted.status == "pending"
    assert persisted.updated_at == original.updated_at


@pytest.mark.parametrize("null_print_time", [False, True])
async def test_legacy_snapshot_without_contact_or_timestamps_still_prints(print_context, null_print_time):
    c = print_context
    original = await read_job(c)
    legacy = {key: value for key, value in original.snapshot.items()
        if key not in {"customer_phone", "customer_phone_encrypted", "customer_email", "ordered_at", "print_requested_at", "pickup_code"}}
    if null_print_time:
        legacy["print_requested_at"] = None
    settings = live_settings()
    await set_job(c, mode="live", printer_fingerprint=printing.printer_fingerprint(settings), snapshot=legacy)
    fake = RecordingFeie()
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    await process_print_job(settings, factory=c.factory, client=fake, now=now)
    result = await read_job(c)
    assert result.status == "submitted" and result.content == fake.contents[0]
    assert result.snapshot["print_requested_at"] == taipei_time(now)
