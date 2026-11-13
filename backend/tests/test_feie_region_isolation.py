from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app import printing
from app.models import Order, PrintJob
from app.printer_worker import process_print_job
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import cash_context, create_cash
from tests.test_feie_printing import FakeFeie, live_settings, print_context, read_job, set_job
from tests.test_quantity_amendment_printing import amend


BASE_URLS = {
    "jp": "https://api.jp.feieyun.com/Api/Open/",
    "cn": "https://api.feieyun.cn/Api/Open/",
}


def region_settings(region, **overrides):
    return live_settings().model_copy(update={
        "feie_api_base_url": BASE_URLS[region],
        "feie_cn_endpoint_verified": True,
        **overrides,
    })


def test_existing_jp_fingerprint_stays_compatible():
    expected = sha256(b"https://api.jp.feieyun.com/Api/Open/|test-user|test-device").hexdigest()
    assert printing.printer_fingerprint(region_settings("jp")) == expected


def test_same_account_and_device_in_distinct_regions_have_distinct_fingerprints():
    assert printing.printer_fingerprint(region_settings("jp")) != printing.printer_fingerprint(region_settings("cn"))


@pytest.mark.parametrize("snapshot,expected", [
    ({}, "jp"), ({"printer_region": "jp"}, "jp"),
    ({"printer_region": "cn"}, "cn"), ({"printer_region": None}, None),
    ({"printer_region": "unknown"}, None),
])
def test_job_region_distinguishes_legacy_jp_from_unknown_configuration(snapshot, expected):
    assert printing.job_region(SimpleNamespace(snapshot=snapshot)) == expected


@pytest.mark.parametrize("source_region,current_region", [("jp", "cn"), ("cn", "jp")])
@pytest.mark.parametrize("status", ["pending", "submitted", "unknown"])
async def test_worker_never_submits_or_queries_another_region(print_context, source_region, current_region, status):
    c = print_context
    source = region_settings(source_region)
    current = region_settings(current_region)
    original = await read_job(c)
    vendor_id = None if status == "pending" else "original-cloud-job"
    await set_job(c, mode="live", status=status, vendor_order_id=vendor_id,
        printer_fingerprint=printing.printer_fingerprint(source),
        snapshot={**original.snapshot, "printer_region": source_region})
    fake = FakeFeie()
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    await process_print_job(current, factory=c.factory, client=fake, now=now)

    result = await read_job(c)
    assert result.status == status and result.vendor_order_id == vendor_id
    assert result.last_error == "printer_configuration_changed"
    assert result.attempts == 0
    assert fake.submissions == fake.queries == 0


@pytest.mark.parametrize("source_region", ["cn", None])
async def test_worker_rejects_region_metadata_even_when_fingerprint_matches(print_context, source_region):
    c = print_context
    settings = region_settings("jp")
    original = await read_job(c)
    await set_job(c, mode="live", status="submitted", vendor_order_id="original-cloud-job",
        printer_fingerprint=printing.printer_fingerprint(settings),
        snapshot={**original.snapshot, "printer_region": source_region})
    fake = FakeFeie()
    await process_print_job(settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(seconds=1))
    result = await read_job(c)
    assert result.status == "submitted" and result.vendor_order_id == "original-cloud-job"
    assert result.last_error == "printer_configuration_changed"
    assert fake.submissions == fake.queries == 0


async def test_legacy_jp_submitted_job_is_queried_without_resubmission(print_context):
    c = print_context
    settings = region_settings("jp")
    snapshot = {key: value for key, value in (await read_job(c)).snapshot.items() if key != "printer_region"}
    await set_job(c, mode="live", status="submitted", vendor_order_id="legacy-jp-job", snapshot=snapshot,
        printer_fingerprint=printing.printer_fingerprint(settings))
    fake = FakeFeie()
    fake.printed = True
    await process_print_job(settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(seconds=1))
    result = await read_job(c)
    assert result.status == "printed" and result.vendor_order_id == "legacy-jp-job"
    assert fake.submissions == 0 and fake.queries == 1


@pytest.mark.parametrize("mismatch", ["mode", "fingerprint", "region", "unknown_region"])
async def test_manual_query_rejects_incompatible_target_before_rescheduling(print_context, mismatch):
    c = print_context
    settings = region_settings("jp")
    original = await read_job(c)
    old_due = datetime.now(timezone.utc) - timedelta(minutes=10)
    snapshot = {**original.snapshot, "printer_region": "jp"}
    if mismatch == "region":
        snapshot["printer_region"] = "cn"
    elif mismatch == "unknown_region":
        snapshot["printer_region"] = None
    await set_job(c, mode="dry_run" if mismatch == "mode" else "live", status="submitted",
        vendor_order_id="original-cloud-job", next_attempt_at=old_due, snapshot=snapshot,
        printer_fingerprint="different-device" if mismatch == "fingerprint" else printing.printer_fingerprint(settings))
    async with api_test_context(c.session, [printers_router], settings=settings) as client:
        response = await client.post(f"/v1/admin/printers/jobs/{c.job_id}/query", headers=auth_headers(c.admin))
    assert response.status_code == 409, response.text
    result = await read_job(c)
    assert result.status == "submitted" and result.vendor_order_id == "original-cloud-job"
    assert result.next_attempt_at.replace(tzinfo=timezone.utc) == old_due


@pytest.mark.parametrize("source_region,current_region", [("jp", "cn"), ("cn", "jp")])
async def test_reprint_cannot_cross_regions_even_with_duplicate_acknowledgement(print_context, source_region, current_region):
    c = print_context
    original = await read_job(c)
    await set_job(c, mode="live", status="unknown", vendor_order_id="original-cloud-job",
        printer_fingerprint=printing.printer_fingerprint(region_settings(source_region)),
        snapshot={**original.snapshot, "printer_region": source_region})
    async with api_test_context(c.session, [printers_router], settings=region_settings(current_region)) as client:
        response = await client.post(f"/v1/admin/printers/jobs/{c.job_id}/reprint", headers=auth_headers(c.admin),
            json={"request_id": str(uuid4()), "reason": "核對後明確補印", "acknowledge_duplicate": True})
    assert response.status_code == 409, response.text
    async with c.factory() as session:
        assert await session.scalar(select(func.count()).select_from(PrintJob)) == 1


@pytest.mark.parametrize("region", ["jp", "cn"])
async def test_same_region_can_explicitly_reprint_to_replacement_device(print_context, region):
    c = print_context
    source_settings = region_settings(region)
    replacement = region_settings(region, feie_printer_sn=SecretStr("replacement-device"))
    original = await read_job(c)
    await set_job(c, mode="live", status="printed", snapshot={**original.snapshot, "printer_region": region},
        printer_fingerprint=printing.printer_fingerprint(source_settings))
    request = {"request_id": str(uuid4()), "reason": "原設備故障改由同區設備補印"}
    async with api_test_context(c.session, [printers_router], settings=replacement) as client:
        url = f"/v1/admin/printers/jobs/{c.job_id}/reprint"
        response = await client.post(url, headers=auth_headers(c.admin), json=request)
        replay = await client.post(url, headers=auth_headers(c.admin), json=request)
    assert response.status_code == replay.status_code == 201, response.text
    assert response.json()["id"] == replay.json()["id"]
    async with c.factory() as session:
        job = await session.get(PrintJob, response.json()["id"])
        assert job.printer_fingerprint == printing.printer_fingerprint(replacement)
        assert job.reprint_of_id == c.job_id and job.snapshot["printer_region"] == region


@pytest.mark.parametrize("region", ["jp", "cn"])
async def test_same_region_dry_run_can_be_explicitly_reprinted_live(print_context, region):
    c = print_context
    original = await read_job(c)
    settings = region_settings(region)
    await set_job(c, mode="dry_run", status="simulated",
        snapshot={**original.snapshot, "printer_region": region}, printer_fingerprint="dry-run-without-credentials")
    async with api_test_context(c.session, [printers_router], settings=settings) as client:
        response = await client.post(f"/v1/admin/printers/jobs/{c.job_id}/reprint", headers=auth_headers(c.admin),
            json={"request_id": str(uuid4()), "reason": "同區正式驗收補印"})
    assert response.status_code == 201, response.text
    async with c.factory() as session:
        job = await session.get(PrintJob, response.json()["id"])
        assert job.mode == "live" and job.snapshot["printer_region"] == region
        assert job.printer_fingerprint == printing.printer_fingerprint(settings)


@pytest.mark.parametrize("base_url,expected_region", [
    (BASE_URLS["jp"], "jp"), (BASE_URLS["cn"], "cn"), ("https://invalid.test/Api/Open/", None),
])
async def test_new_order_records_region_without_blocking_order_on_bad_endpoint(cash_context, base_url, expected_region):
    c = cash_context
    c.settings.printer_enabled = True
    c.settings.feie_api_base_url = base_url
    response = await create_cash(c, client_request_id=str(uuid4()))
    assert response.status_code == 201, response.text
    job = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == response.json()["id"]))
    assert "printer_region" in job.snapshot
    assert job.snapshot["printer_region"] == expected_region


@pytest.mark.parametrize("source_region,current_region,source_has_region", [
    ("jp", "cn", False), ("jp", "cn", True), ("cn", "jp", True),
])
async def test_amendment_keeps_source_region_after_configuration_switch(
    print_context, source_region, current_region, source_has_region,
):
    c = print_context
    original = await read_job(c)
    snapshot = {key: value for key, value in original.snapshot.items() if key != "printer_region"}
    if source_has_region:
        snapshot["printer_region"] = source_region
    original_fingerprint = printing.printer_fingerprint(region_settings(source_region))
    await set_job(c, mode="live", status="printed", snapshot=snapshot, printer_fingerprint=original_fingerprint)
    c.settings.feie_api_base_url = BASE_URLS[current_region]

    jobs = await amend(c, revision=2, quantity=2)
    revised = next(job for job in jobs if job.id != c.job_id)
    assert revised.printer_fingerprint == original_fingerprint
    assert printing.job_region(revised) == source_region
    assert revised.snapshot["printer_region"] == source_region
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = "管理員核准取消"
        await printing.stop_cancelled_order_prints(session, order)
        await session.commit()
        stopped = await session.get(PrintJob, revised.id)
        assert stopped.status == "failed" and stopped.last_error == "order_cancelled_before_send"
