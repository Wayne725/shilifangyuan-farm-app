import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import printing
from app.integrations.feie import FeieError
from app.models import MemberProfile, PrintJob
from app.printer_worker import process_print_job
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import cash_context, create_cash, action
from tests.test_feie_printing import live_settings, print_context, read_job, set_job
from tests.test_receipt_context import RecordingFeie, configure_test_cipher


PHONE = "0900123456"


async def private_job(c):
    cipher = configure_test_cipher(c.settings)
    original = await read_job(c)
    snapshot = {key: value for key, value in original.snapshot.items()
        if key not in {"customer_phone", "customer_email"}}
    snapshot["customer_phone_encrypted"] = cipher.encrypt_text(PHONE,
        associated_data=f"receipt-contact:{c.order_id}:phone")
    settings = live_settings(pii_encryption_keys_json=c.settings.pii_encryption_keys_json)
    await set_job(c, mode="live", printer_fingerprint=printing.printer_fingerprint(settings), snapshot=snapshot)
    return settings, snapshot


async def test_phone_order_never_persists_plaintext_in_snapshot_or_receipt(cash_context):
    c = cash_context
    cipher = configure_test_cipher(c.settings)
    c.settings.printer_enabled = True
    c.session.add(MemberProfile(user_id=c.buyer.id,
        phone_encrypted=cipher.encrypt_text(PHONE, associated_data=f"member-profile:{c.buyer.id}"),
        legal_name_encrypted="unused", birth_date_encrypted="unused", address_encrypted="unused",
        emergency_contact_encrypted="unused", consent_version="test", consented_at=datetime.now(timezone.utc)))
    await c.session.commit()
    response = await create_cash(c, client_request_id=str(uuid4()), contact_phone=None)
    assert response.status_code == 201, response.text
    c.order_id = response.json()["id"]
    c.job_id = await c.session.scalar(select(PrintJob.id).where(PrintJob.order_id == c.order_id))
    c.factory = async_sessionmaker(c.session.bind, expire_on_commit=False)
    confirmed = await action(c, c.order_id)
    assert confirmed.status_code == 200, confirmed.text
    await process_print_job(c.settings, factory=c.factory, now=datetime.now(timezone.utc) + timedelta(seconds=1))
    job = await read_job(c)
    assert PHONE not in json.dumps(job.snapshot, ensure_ascii=False)
    assert PHONE not in job.content
    assert "customer_phone" not in job.snapshot and job.snapshot["customer_phone_encrypted"]
    assert cipher.decrypt_text(job.snapshot["customer_phone_encrypted"],
        associated_data=f"receipt-contact:{c.order_id}:phone") == PHONE
    assert PHONE in printing.open_receipt_content(job.content, c.order_id, c.settings)


async def test_private_snapshot_renders_for_preview_and_submit_without_plaintext_storage(print_context):
    c = print_context
    settings, snapshot = await private_job(c)
    async with api_test_context(c.session, [printers_router], settings=settings) as client:
        response = await client.get(f"/v1/admin/printers/jobs/{c.job_id}", headers=auth_headers(c.admin))
    assert response.status_code == 200 and PHONE in response.json()["preview"]
    assert (await read_job(c)).snapshot == snapshot
    fake = RecordingFeie()
    await process_print_job(settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(seconds=1))
    job = await read_job(c)
    assert job.status == "submitted" and PHONE in fake.contents[0]
    assert PHONE not in job.content and PHONE not in json.dumps(job.snapshot)
    assert printing.open_receipt_content(job.content, c.order_id, settings) == fake.contents[0]


async def test_receipt_phone_and_body_cannot_be_opened_for_another_order(print_context):
    c = print_context
    settings, snapshot = await private_job(c)
    another_order_id = str(uuid4())
    with pytest.raises(FeieError, match="receipt_contact_decryption_failed"):
        printing.render_receipt_snapshot(snapshot, another_order_id, settings)
    body = printing.seal_receipt_content(f"電話：{PHONE}<BR>", snapshot, c.order_id, settings)
    assert PHONE not in body
    with pytest.raises(FeieError, match="receipt_content_decryption_failed"):
        printing.open_receipt_content(body, another_order_id, settings)


@pytest.mark.parametrize("fault", ["contact", "content"])
async def test_unreadable_archived_phone_blocks_preview_and_fails_job_without_submission(print_context, fault):
    c = print_context
    settings, snapshot = await private_job(c)
    wrong_order_id = str(uuid4())
    if fault == "contact":
        cipher = configure_test_cipher(settings)
        snapshot["customer_phone_encrypted"] = cipher.encrypt_text(PHONE,
            associated_data=f"receipt-contact:{wrong_order_id}:phone")
        await set_job(c, snapshot=snapshot)
    else:
        snapshot["payment_status"] = "已付款"
        snapshot["print_requested_at"] = "2026-11-01 12:00:00"
        body = printing.seal_receipt_content(f"電話：{PHONE}<BR>", snapshot, wrong_order_id, settings)
        await set_job(c, snapshot=snapshot, content=body)
    async with api_test_context(c.session, [printers_router], settings=settings) as client:
        response = await client.get(f"/v1/admin/printers/jobs/{c.job_id}", headers=auth_headers(c.admin))
    assert response.status_code == 200
    preview = response.json()["preview"]
    assert "解密" in preview or f"receipt_{fault}_decryption_failed" in preview
    assert PHONE not in preview and "Email：" not in preview
    fake = RecordingFeie()
    await process_print_job(settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(seconds=1))
    job = await read_job(c)
    assert job.status == "failed" and job.last_error == f"receipt_{fault}_decryption_failed"
    assert job.lease_token is None and job.lease_until is None
    assert fake.contents == []
    assert job.snapshot["customer_phone_encrypted"] == snapshot["customer_phone_encrypted"]
    assert PHONE not in json.dumps(job.snapshot)
    if fault == "content":
        assert job.content == body


@pytest.mark.parametrize("field", ["phone", "content"])
async def test_unavailable_receipt_key_raises_specific_failure(print_context, field):
    c = print_context
    settings, snapshot = await private_job(c)
    body = printing.seal_receipt_content(f"電話：{PHONE}<BR>", snapshot, c.order_id, settings)
    settings.pii_encryption_keys_json = "{}"
    if field == "phone":
        with pytest.raises(FeieError, match="receipt_contact_decryption_failed"):
            printing.render_receipt_snapshot(snapshot, c.order_id, settings)
    else:
        with pytest.raises(FeieError, match="receipt_content_decryption_failed"):
            printing.open_receipt_content(body, c.order_id, settings)


def test_legacy_receipt_content_without_phone_still_opens_as_plain_text():
    body = "十里方圓<BR>舊訂單<BR>"
    assert printing.open_receipt_content(body, str(uuid4()), live_settings()) == body
