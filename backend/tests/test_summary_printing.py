import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.feie import FeieError
from app.models import AdminAudit, FulfillmentState, OrderItemOption, PaymentStatus, PrintJob
from app.order_summary import OrderSummaryFilters, build_print_summary
from app.printer_worker import process_print_job
from app.printing import open_receipt_content, receipt_preview, seal_receipt_content
from app.routers.order_summary import order_summary_router
from app.routers.printers import printers_router
from app.summary_printing import format_summary_pages, summary_fingerprint
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_feie_printing import FakeFeie, live_settings
from tests.test_order_summary import NOW, PARAMS, add_order, line, summary_context


BASE = "/v1/admin/order-summary"


@pytest.fixture
async def summary_print_context(summary_context):
    c = summary_context
    settings = make_test_settings(printer_enabled=True, printer_dry_run=True,
        pii_encryption_keys_json=json.dumps({"test": base64.b64encode(b"s" * 32).decode()}),
        pii_encryption_current_version="test")
    async with api_test_context(c.session, [order_summary_router, printers_router], settings=settings) as client:
        yield SimpleNamespace(**{**vars(c), "client": client, "settings": settings,
                                 "factory": async_sessionmaker(c.session.bind, expire_on_commit=False)})


async def paid_order(c, *, label="SUMMARY-PAID", items=None, note=None):
    order = add_order(c, label, items or [line(4, product=c.products[0])])
    order.paid_at = NOW
    order.kitchen_note = note
    await c.session.commit()
    return order


async def preview(c, **filters):
    response = await c.client.get(f"{BASE}/print-preview", params=PARAMS | filters, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    return response.json()


async def create_batch(c, data=None, *, request_id=None, **filters):
    data = data or await preview(c, **filters)
    response = await c.client.post(f"{BASE}/print-jobs", headers=auth_headers(c.admin), json={
        "request_id": request_id or str(uuid4()), "fingerprint": data["fingerprint"],
        "filters": PARAMS | filters,
    })
    assert response.status_code in {200, 201}, response.text
    return response.json()


async def jobs(c):
    async with c.factory() as session:
        return list(await session.scalars(select(PrintJob).where(PrintJob.kind == "summary").order_by(PrintJob.event_key)))


def report(product_count=1):
    return {
        "filters": OrderSummaryFilters(**PARAMS, status="paid").model_dump(mode="json", exclude={"limit", "offset"}),
        "summary": {"order_count": product_count, "quantity": product_count, "product_count": product_count},
        "products": [{"key": str(index), "name": f"商品{index:04d}永續友善農業安心合作便當", "unit": "份", "quantity": 1,
                      "variants": [{"options": ["主食：紫米飯", "飲料：茂谷汽水"], "quantity": 1}]}
                     for index in range(product_count)],
        "notes": [{"order_number": "SUMMARY-NOTE", "note": "不要辣<CUT>\x1b<BR>少飯",
                   "items": [{"name": "合作便當", "quantity": 1, "unit": "份"}]}],
        "source_versions": [{"id": "synthetic-order", "revision": 1}],
    }


def test_summary_pages_preserve_all_items_and_escape_printer_commands():
    data = report(120)
    request_id = str(uuid4())
    pages = format_summary_pages(data, generated_at="2030-01-02 12:00:00", request_id=request_id)
    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 5000 for page in pages)
    printable = "".join(receipt_preview(page).replace("\n", "") for page in pages)
    for item in data["products"]:
        assert item["name"] in printable
    assert "＜CUT＞" in printable and "＜BR＞" in printable
    assert "<CUT>" not in "".join(pages) and "\x1b" not in "".join(pages)
    assert all("彙總" in receipt_preview(page) for page in pages)
    assert "未取消" not in printable and request_id not in printable
    assert "主食：紫米飯－茂谷汽水" in printable


def test_summary_compact_options_preserve_each_combination_and_option_name():
    data = report()
    data["products"][0].update(name="嫩煮雞胸", quantity=10, variants=[
        {"quantity": 5, "options": ["飲品加購：茂谷汽水", "主食選擇：紫米飯"]},
        {"quantity": 2, "options": ["主食：紫米飯"]},
        {"quantity": 1, "options": ["飲品：果汁：無糖", "加購：溏心蛋", "主食：麵包"]},
        {"quantity": 1, "options": ["配菜：玉米", "加購：玉米"]},
        {"quantity": 1, "options": []},
    ])
    data["summary"]["quantity"] = 10
    data["notes"] = []
    printable = "".join(receipt_preview(page).replace("\n", "") for page in format_summary_pages(
        data, generated_at="2030-01-02 12:00:00", request_id=str(uuid4()),
    ))
    assert "10 份 × 嫩煮雞胸" in printable
    assert "5 份 × 主食：紫米飯－茂谷汽水" in printable
    assert "2 份 × 主食：紫米飯" in printable
    assert "1 份 × 主食：麵包－果汁：無糖－溏心蛋" in printable
    assert "1 份 × 玉米－玉米" in printable
    assert "1 份 × 無選項" in printable
    assert all(label not in printable for label in ["主食選擇", "飲品", "加購", "配菜"])


def test_summary_too_many_pages_is_explicit_and_never_truncated():
    with pytest.raises(FeieError, match="summary_too_many_pages"):
        format_summary_pages(report(4000), generated_at="2030-01-02 12:00:00", request_id=str(uuid4()))


def test_summary_preview_fingerprint_covers_device_mode_and_layout():
    data = report()
    settings = live_settings()
    original = summary_fingerprint(data, settings)
    assert original == summary_fingerprint(data, settings)
    for change in [{"feie_printer_sn": "another-test-device"}, {"printer_dry_run": True}, {"printer_line_width": 24}]:
        assert summary_fingerprint(data, live_settings(**change)) != original
    changed = {**data, "summary": {**data["summary"], "quantity": 2}}
    assert summary_fingerprint(changed, settings) != original


async def test_summary_print_routes_require_admin(summary_print_context):
    c = summary_print_context
    request_id = str(uuid4())
    paths = [f"{BASE}/print-preview", f"{BASE}/print-jobs", f"{BASE}/print-jobs/{request_id}"]
    for path in paths:
        params = PARAMS if path.endswith("preview") else {}
        assert (await c.client.get(path, params=params)).status_code == 401
        assert (await c.client.get(path, params=params, headers=auth_headers(c.buyer))).status_code == 403
    body = {"filters": PARAMS, "request_id": request_id, "fingerprint": "a" * 64}
    assert (await c.client.post(f"{BASE}/print-jobs", json=body)).status_code == 401
    assert (await c.client.post(f"{BASE}/print-jobs", json=body, headers=auth_headers(c.buyer))).status_code == 403


async def test_summary_preview_only_confirmed_paid_uncancelled_and_no_customer_fields(summary_print_context):
    c = summary_print_context
    item = line(4, product=c.products[0])
    item.selected_options = [OrderItemOption(group_name="主食", option_name="紫米飯", price_delta=0)]
    await paid_order(c, items=[item], note="少飯")
    add_order(c, "SUMMARY-UNCONFIRMED", [line(20)])
    add_order(c, "SUMMARY-PENDING", [line(30)], payment=PaymentStatus.PENDING)
    cancelled = add_order(c, "SUMMARY-CANCELLED", [line(40)], cancelled_at=NOW)
    cancelled.paid_at = NOW
    cancelled_fulfillment = add_order(c, "SUMMARY-FULFILLMENT-CANCELLED", [line(50)], fulfillment=FulfillmentState.CANCELLED)
    cancelled_fulfillment.paid_at = NOW
    await c.session.commit()
    data = await preview(c, status="active")
    assert data["summary"] == {"order_count": 1, "quantity": 4, "product_count": 1}
    assert data["filters"]["status"] == "paid"
    assert data["mode"] == "dry_run" and data["page_count"] == len(data["pages"])
    rendered = "".join(data["pages"])
    assert "紫米飯" in rendered and "少飯" in rendered
    assert c.buyer.display_name not in rendered and c.buyer.email not in rendered
    assert "顧客：" not in rendered and "電話：" not in rendered and "Email：" not in rendered
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 0


async def test_summary_empty_disabled_and_changed_preview_are_rejected(summary_print_context):
    c = summary_print_context
    empty = await c.client.get(f"{BASE}/print-preview", params=PARAMS, headers=auth_headers(c.admin))
    assert empty.status_code == 422
    order = await paid_order(c)
    data = await preview(c)
    body = {"filters": PARAMS, "request_id": str(uuid4()), "fingerprint": data["fingerprint"]}
    c.settings.printer_enabled = False
    disabled = await c.client.post(f"{BASE}/print-jobs", headers=auth_headers(c.admin), json=body)
    assert disabled.status_code == 409
    c.settings.printer_enabled = True
    order.payment_status = PaymentStatus.PENDING
    order.paid_at = None
    await c.session.commit()
    stale = await c.client.post(f"{BASE}/print-jobs", headers=auth_headers(c.admin), json=body)
    assert stale.status_code == 409
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 0


async def test_summary_old_layout_preview_requires_refresh(summary_print_context):
    c = summary_print_context
    await paid_order(c)
    old_fingerprint = summary_fingerprint(
        await build_print_summary(c.session, OrderSummaryFilters(**PARAMS)), c.settings, format_version=1,
    )
    response = await c.client.post(f"{BASE}/print-jobs", headers=auth_headers(c.admin), json={
        "filters": PARAMS, "request_id": str(uuid4()), "fingerprint": old_fingerprint,
    })
    assert response.status_code == 409
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 0
    await create_batch(c)
    assert all(job.snapshot["format_version"] == 2 for job in await jobs(c))


@pytest.mark.parametrize("cancelled", [False, True])
async def test_summary_existing_pending_batch_preserves_frozen_layout(summary_print_context, cancelled):
    c = summary_print_context
    order = await paid_order(c)
    await create_batch(c)
    original_report = await build_print_summary(c.session, OrderSummaryFilters(**PARAMS))
    original_receipts = []
    for job in await c.session.scalars(select(PrintJob).where(PrintJob.kind == "summary")):
        job.content = seal_receipt_content(
            "訂單彙總<BR>已付款・未取消<BR>彙總：舊批次<BR>主食：紫米飯／飲品：茂谷汽水",
            {}, f"summary:{job.id}", c.settings, force=True,
        )
        original_receipts.append(job.content)
        snapshot = {**job.snapshot, "fingerprint": summary_fingerprint(original_report, c.settings, format_version=1)}
        snapshot.pop("format_version")
        job.snapshot = snapshot
    if cancelled:
        order.cancelled_at = NOW
    await c.session.commit()
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    printed = await jobs(c)
    assert all(job.status == ("failed" if cancelled else "simulated") for job in printed)
    if cancelled:
        assert all(job.last_error == "summary_changed_before_send" for job in printed)
    assert [job.content for job in printed] == original_receipts
    assert fake.submissions == 0


async def test_summary_request_replay_is_frozen_and_searchable_after_reopening(summary_print_context):
    c = summary_print_context
    order = await paid_order(c, note="原本備註")
    data = await preview(c)
    request_id = str(uuid4())
    first = await create_batch(c, data, request_id=request_id)
    order.kitchen_note = "後來更改備註"
    await c.session.commit()
    replay = await create_batch(c, data, request_id=request_id)
    assert [page["id"] for page in first["pages"]] == [page["id"] for page in replay["pages"]]
    assert len(await jobs(c)) == len(first["pages"])
    mismatch = await c.client.post(f"{BASE}/print-jobs", headers=auth_headers(c.admin), json={
        "filters": PARAMS | {"search": "不同搜尋"}, "request_id": request_id, "fingerprint": data["fingerprint"],
    })
    assert mismatch.status_code == 409
    fetched = await c.client.get(f"{BASE}/print-jobs/{request_id}", headers=auth_headers(c.admin))
    assert fetched.status_code == 200 and fetched.json()["request_id"] == request_id
    assert fetched.json()["fingerprint"] == data["fingerprint"]
    recent = await c.client.get(f"{BASE}/print-jobs", params={"limit": 10}, headers=auth_headers(c.admin))
    assert recent.status_code == 200
    assert request_id in [batch["request_id"] for batch in recent.json()["items"]]
    assert "原本備註" not in recent.text and "後來更改備註" not in recent.text
    assert c.buyer.email not in recent.text and c.buyer.display_name not in recent.text
    assert await c.session.scalar(select(func.count()).select_from(AdminAudit)) == 1


async def test_summary_dry_run_uses_outbox_without_real_network(summary_print_context, monkeypatch):
    c = summary_print_context
    await paid_order(c)
    batch = await create_batch(c)

    def forbidden(*args, **kwargs):
        raise AssertionError("test must not send a real Feie request")

    monkeypatch.setattr("app.printer_worker.FeieClient", forbidden)
    for _ in batch["pages"]:
        assert await process_print_job(c.settings, factory=c.factory)
    rows = await jobs(c)
    assert all(job.order_id is None and job.status == "simulated" for job in rows)
    assert all(job.vendor_order_id is None and job.printed_at is None for job in rows)
    assert not await process_print_job(c.settings, factory=c.factory)


async def test_summary_stale_pending_page_is_not_sent(summary_print_context):
    c = summary_print_context
    order = await paid_order(c)
    await create_batch(c)
    order.cancelled_at = NOW
    await c.session.commit()
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = (await jobs(c))[0]
    assert job.status == "failed" and job.last_error == "summary_changed_before_send"
    assert fake.submissions == 0


async def test_summary_changed_printer_fails_unsent_page_without_blocking_new_preview(summary_print_context):
    c = summary_print_context
    await paid_order(c)
    await create_batch(c)
    c.settings.printer_dry_run = False
    fake = FakeFeie()
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = (await jobs(c))[0]
    assert job.status == "failed" and job.last_error == "printer_configuration_changed"
    assert job.attempts == 0 and not fake.submissions
    assert not await process_print_job(c.settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(hours=1))


async def test_summary_that_grows_beyond_source_limit_fails_instead_of_blocking_queue(summary_print_context, monkeypatch):
    c = summary_print_context
    await paid_order(c)
    await create_batch(c)

    async def over_limit(*args):
        raise HTTPException(422, "訂單明細超過上限")

    monkeypatch.setattr("app.summary_printing.build_print_summary", over_limit)
    assert await process_print_job(c.settings, factory=c.factory)
    job = (await jobs(c))[0]
    assert job.status == "failed" and job.last_error == "summary_changed_before_send"
    assert not await process_print_job(c.settings, factory=c.factory)


def configure_live(c):
    settings = live_settings()
    for key in ["printer_dry_run", "feie_user", "feie_ukey", "feie_printer_sn", "feie_ukey_rotated", "feie_ap_endpoint_verified"]:
        setattr(c.settings, key, getattr(settings, key))


async def test_summary_unknown_send_is_not_submitted_again(summary_print_context):
    c = summary_print_context
    configure_live(c)
    await paid_order(c)
    await create_batch(c)
    fake = FakeFeie(error=FeieError("transport_result_unknown", uncertain=True))
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert await process_print_job(c.settings, factory=c.factory, client=fake, now=now)
    assert (await jobs(c))[0].status == "unknown"
    assert not await process_print_job(c.settings, factory=c.factory, client=fake, now=now + timedelta(hours=1))
    assert fake.submissions == 1


async def test_summary_payment_correction_after_first_page_stops_remaining_pages(summary_print_context):
    c = summary_print_context
    configure_live(c)
    order = await paid_order(c, items=[line(1, name=f"品項{index:04d}特別訂製安心便當") for index in range(120)])
    batch = await create_batch(c)
    assert len(batch["pages"]) > 1
    fake = FakeFeie()
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert await process_print_job(c.settings, factory=c.factory, client=fake, now=now)
    order.payment_status = PaymentStatus.PENDING
    order.paid_at = None
    await c.session.commit()
    for _ in batch["pages"][1:]:
        assert await process_print_job(c.settings, factory=c.factory, client=fake, now=now)
    rows = await jobs(c)
    assert sum(job.status == "submitted" for job in rows) == 1
    assert sum(job.status == "failed" and job.last_error == "summary_changed_before_send" for job in rows) == len(rows) - 1
    assert fake.submissions == 1


async def test_summary_job_cannot_enter_legacy_order_receipt_actions(summary_print_context):
    c = summary_print_context
    await paid_order(c)
    batch = await create_batch(c)
    job_id = batch["pages"][0]["id"]
    path = f"/v1/admin/printers/jobs/{job_id}"
    assert (await c.client.get(path, headers=auth_headers(c.admin))).status_code == 409
    for suffix, body in [
        ("query", {}),
        ("resolve", {"printed": True, "reason": "驗收紙本"}),
        ("reprint", {"request_id": str(uuid4()), "reason": "驗收補印"}),
    ]:
        assert (await c.client.post(f"{path}/{suffix}", headers=auth_headers(c.admin), json=body)).status_code == 409
    listing = await c.client.get("/v1/admin/printers/jobs", headers=auth_headers(c.admin))
    assert listing.status_code == 200 and listing.json()["items"] == []


async def test_summary_receipt_notes_are_encrypted_and_bound_to_the_page(summary_print_context):
    c = summary_print_context
    await paid_order(c, note="客人備註隱私文字")
    await create_batch(c)
    job = (await jobs(c))[0]
    assert job.content.startswith("receipt-content:v1:")
    assert "客人備註隱私文字" not in job.content
    assert "客人備註隱私文字" not in json.dumps(job.snapshot, ensure_ascii=False)
    content = open_receipt_content(job.content, f"summary:{job.id}", c.settings)
    assert "客人備註隱私文字" in content
    with pytest.raises(FeieError, match="receipt_content_decryption_failed"):
        open_receipt_content(job.content, "summary:another-page", c.settings)


async def test_summary_manual_resolution_keeps_unseen_unknown_without_resubmission(summary_print_context):
    c = summary_print_context
    configure_live(c)
    await paid_order(c)
    batch = await create_batch(c)
    fake = FakeFeie(error=FeieError("transport_result_unknown", uncertain=True))
    assert await process_print_job(c.settings, factory=c.factory, client=fake)
    job = (await jobs(c))[0]
    path = f"{BASE}/print-jobs/{batch['request_id']}/pages/{job.id}/resolve"
    body = {"printed": False, "reason": "已核對目前沒有紙本"}
    assert (await c.client.post(path, json=body)).status_code == 401
    assert (await c.client.post(path, json=body, headers=auth_headers(c.buyer))).status_code == 403
    unseen = await c.client.post(path, json={**body, "reason": "   "}, headers=auth_headers(c.admin))
    assert unseen.status_code == 200
    assert unseen.json()["pages"][0]["status"] == "unknown"
    assert not await process_print_job(c.settings, factory=c.factory, client=fake,
        now=datetime.now(timezone.utc) + timedelta(hours=1))
    seen = await c.client.post(path, json={"printed": True, "reason": "已收到這張彙總紙本"}, headers=auth_headers(c.admin))
    assert seen.status_code == 200 and seen.json()["pages"][0]["status"] == "printed"
    assert fake.submissions == 1
    assert (await c.client.post(path, json=body, headers=auth_headers(c.admin))).status_code == 409
    assert await c.session.scalar(select(func.count()).select_from(AdminAudit).where(
        AdminAudit.action == "printer.summary.resolve")) == 2


async def test_summary_manual_resolution_invalidates_inflight_query_cas(summary_print_context):
    c = summary_print_context
    configure_live(c)
    await paid_order(c)
    batch = await create_batch(c)
    fake = FakeFeie()
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert await process_print_job(c.settings, factory=c.factory, client=fake, now=now)
    started, release = asyncio.Event(), asyncio.Event()

    class HeldQuery:
        async def query(self, vendor_id):
            started.set()
            await release.wait()
            return False

    worker = asyncio.create_task(process_print_job(c.settings, factory=c.factory, client=HeldQuery(),
        now=now + timedelta(hours=1)))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        page = batch["pages"][0]
        path = f"{BASE}/print-jobs/{batch['request_id']}/pages/{page['id']}/resolve"
        result = await c.client.post(path, json={"printed": True, "reason": "現場已取得紙本"}, headers=auth_headers(c.admin))
        assert result.status_code == 200
    finally:
        release.set()
        await asyncio.wait_for(worker, timeout=5)
    job = (await jobs(c))[0]
    assert job.status == "printed" and job.lease_token is None
    assert job.last_result == {"source": "admin_paper_check", "printed": True}
    assert fake.submissions == 1
