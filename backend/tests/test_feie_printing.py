from app.printing import open_receipt_content
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.feie import FeieClient, FeieError, configuration_read
from app.models import AdminAudit, Order, PaymentStatus, PrintJob
from app.printer_worker import process_print_job
from app.printing import stop_cancelled_order_prints, format_receipt, printer_fingerprint, wrap_text
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_cash_payments import cash_context, create_cash, approve_cancellation, action


def live_settings(**overrides):
    return make_test_settings(**{ "printer_enabled": True, "printer_dry_run": False, "feie_user": "test-user",
        "feie_ukey": "test-secret", "feie_printer_sn": "test-device", "feie_ukey_rotated": True,
        "feie_ap_endpoint_verified": True, **overrides})


@pytest.mark.parametrize("method,args,api,path,data,expected", [
    ("submit", ["繁體中文測試<BR>"], "Open_printMsg", "printMsg", "vendor-job", "vendor-job"),
    ("query", ["vendor-job"], "Open_queryOrderState", "queryOrderState", True, True),
    ("printer_status", [], "Open_queryPrinterStatus", "queryPrinterStatus", "在线，工作状态正常", "online"),
])
@pytest.mark.parametrize("host,region_options", [
    ("api.jp.feieyun.com", {}),
    ("api.feieyun.cn", {"feie_ap_endpoint_verified": False, "feie_cn_endpoint_verified": True}),
])
async def test_feie_uses_dedicated_regional_routes(method, args, api, path, data, expected, host, region_options):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == f"https://{host}/Api/Open/{path}"
        form = parse_qs(request.content.decode())
        assert form["apiname"] == [api]
        assert form["sig"] == [sha1(f"test-usertest-secret{form['stime'][0]}".encode()).hexdigest()]
        return httpx.Response(200, json={"ret": 0, "data": data})

    client = FeieClient(live_settings(feie_api_base_url=f"https://{host}/Api/Open/", **region_options), transport=httpx.MockTransport(handler))
    assert await getattr(client, method)(*args) == expected
    assert len(calls) == 1


def test_china_confirmation_defaults_to_disabled():
    assert make_test_settings().feie_cn_endpoint_verified is False


@pytest.mark.parametrize("host,ap_verified,cn_verified,region,check,ready", [
    ("api.jp.feieyun.com", True, False, "jp", "asia_pacific_endpoint_confirmed", True),
    ("api.jp.feieyun.com", False, True, "jp", "asia_pacific_endpoint_confirmed", False),
    ("api.feieyun.cn", False, True, "cn", "china_endpoint_confirmed", True),
    ("api.feieyun.cn", True, False, "cn", "china_endpoint_confirmed", False),
])
def test_configuration_requires_confirmation_of_the_selected_region(host, ap_verified, cn_verified, region, check, ready):
    settings = live_settings(feie_api_base_url=f"https://{host}/Api/Open/", feie_ap_endpoint_verified=ap_verified,
                             feie_cn_endpoint_verified=cn_verified)
    result = configuration_read(settings)
    assert result["configured_region"] == region
    assert result["checks"][check] is ready
    assert ("china_endpoint_confirmed" if region == "jp" else "asia_pacific_endpoint_confirmed") not in result["checks"]
    assert result["live_config_ready"] is ready
    if ready:
        FeieClient(settings)
    else:
        with pytest.raises(FeieError, match="live_activation_not_confirmed"):
            FeieClient(settings)


async def test_changing_from_confirmed_jp_to_unconfirmed_cn_cannot_send():
    def forbidden(request):
        raise AssertionError("CN needs its own confirmation before any request")

    settings = live_settings()
    client = FeieClient(settings, transport=httpx.MockTransport(forbidden))
    settings.feie_api_base_url = "https://api.feieyun.cn/Api/Open/"
    with pytest.raises(FeieError, match="live_activation_not_confirmed"):
        await client.submit("不能跨區沿用同意")


@pytest.mark.parametrize("api", ["Open_printerDelList", "", "../printMsg", "Open_printMsg/../queryOrderState", "https://evil.test/printMsg"])
async def test_feie_rejects_unsupported_method_before_network(api):
    def forbidden(request):
        raise AssertionError("unsupported method must not send a request")

    client = FeieClient(live_settings(), transport=httpx.MockTransport(forbidden))
    with pytest.raises(FeieError, match="unsupported_printer_api"):
        await client._call(api)


async def test_feie_form_signature_region_and_utf8(monkeypatch):
    monkeypatch.setattr("app.integrations.feie.time.time", lambda: 1234567890)
    requests = []

    async def handler(request):
        requests.append(request)
        assert str(request.url) == "https://api.jp.feieyun.com/Api/Open/printMsg"
        form = parse_qs(request.content.decode())
        assert form["sig"] == [sha1(b"test-usertest-secret1234567890").hexdigest()]
        assert form["stime"] == ["1234567890"]
        assert form["apiname"] == ["Open_printMsg"]
        assert form["content"] == ["繁體中文便當<BR>"]
        assert form["times"] == ["1"]
        assert "application/x-www-form-urlencoded" in request.headers["content-type"]
        return httpx.Response(200, json={"ret": 0, "data": "vendor-123"})

    client = FeieClient(live_settings(), transport=httpx.MockTransport(handler))
    assert await client.submit("繁體中文便當<BR>") == "vendor-123"
    assert len(requests) == 1


@pytest.mark.parametrize("url", [
    "http://api.jp.feieyun.com/Api/Open/", "https://api.de.feieyun.com/Api/Open/", "https://api.jp.feieyun.com/Api/Open/printMsg",
    "https://evil.test/Api/Open/", "https://api.jp.feieyun.com/Api/Open/?debug=1", "http://api.feieyun.cn/Api/Open/",
    "https://api.feieyun.cn:444/Api/Open/", "https://api.feieyun.cn/Api/Open/printMsg", "https://admin.feieyun.com/index.php",
    "https://user:secret@api.feieyun.cn/Api/Open/", "https://api.feieyun.cn/Api/Open/#fragment",
])
def test_only_allowed_regional_https_endpoint(url):
    with pytest.raises(FeieError, match="invalid_printer_endpoint"):
        FeieClient(live_settings(feie_api_base_url=url))


async def test_feie_revalidates_endpoint_before_sending():
    def forbidden(request):
        raise AssertionError("invalid endpoint must not send a request")

    settings = live_settings()
    client = FeieClient(settings, transport=httpx.MockTransport(forbidden))
    settings.feie_api_base_url = "https://evil.test/Api/Open/"
    with pytest.raises(FeieError, match="invalid_printer_endpoint"):
        await client.submit("單")


async def test_disabling_live_mode_after_client_creation_stops_http():
    def forbidden(request):
        raise AssertionError("dry run must never send HTTP")

    settings = live_settings()
    client = FeieClient(settings, transport=httpx.MockTransport(forbidden))
    settings.printer_dry_run = True
    with pytest.raises(FeieError, match="live_printing_disabled"):
        await client.submit("單")


@pytest.mark.parametrize("field,value", [("feie_ukey_rotated", False), ("feie_ap_endpoint_verified", False), ("feie_ukey", "")])
def test_live_activation_requires_rotated_key_and_verified_region(field, value):
    settings = live_settings().model_copy(update={field: value}) if isinstance(value, bool) else live_settings(feie_ukey=value)
    with pytest.raises(FeieError):
        FeieClient(settings)
    assert "test-secret" not in repr(settings)


@pytest.mark.parametrize("failure,safe,uncertain", [(httpx.ConnectTimeout, True, False), (httpx.ConnectError, True, False), (httpx.ReadTimeout, False, True), (httpx.WriteTimeout, False, True), (httpx.RemoteProtocolError, False, True)])
async def test_transport_failure_classification(failure, safe, uncertain):
    def handler(request):
        raise failure("secret-vendor-body", request=request)
    with pytest.raises(FeieError) as error:
        await FeieClient(live_settings(), transport=httpx.MockTransport(handler)).submit("單")
    assert error.value.safe_to_retry is safe
    assert error.value.uncertain is uncertain
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("response,uncertain", [
    (httpx.Response(500, text="sensitive"), True), (httpx.Response(302, headers={"location": "https://api.de.feieyun.com"}), True),
    (httpx.Response(200, text="bad-json"), True), (httpx.Response(200, json={"ret": 0, "data": ""}), True),
    (httpx.Response(200, json={"ret": False, "data": "id"}), True),
    (httpx.Response(200, json={"ret": -3, "msg": "sensitive"}), False),
    (httpx.Response(200, json={"ret": -999, "msg": "sensitive"}), True),
])
@pytest.mark.parametrize("host,region_options", [
    ("api.jp.feieyun.com", {}),
    ("api.feieyun.cn", {"feie_ap_endpoint_verified": False, "feie_cn_endpoint_verified": True}),
])
async def test_ambiguous_responses_never_resubmitted(response, uncertain, host, region_options):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == f"https://{host}/Api/Open/printMsg"
        return response
    with pytest.raises(FeieError) as error:
        settings = live_settings(feie_api_base_url=f"https://{host}/Api/Open/", **region_options)
        await FeieClient(settings, transport=httpx.MockTransport(handler)).submit("單")
    assert error.value.uncertain is uncertain
    assert not error.value.safe_to_retry
    assert len(calls) == 1
    assert "sensitive" not in str(error.value)


async def test_query_false_is_unprinted_not_failed():
    def handler(request):
        assert str(request.url) == "https://api.jp.feieyun.com/Api/Open/queryOrderState"
        form = parse_qs(request.content.decode())
        assert form["apiname"] == ["Open_queryOrderState"]
        assert form["orderid"] == ["vendor-job"]
        return httpx.Response(200, json={"ret": 0, "data": False})
    assert await FeieClient(live_settings(), transport=httpx.MockTransport(handler)).query("vendor-job") is False


@pytest.mark.parametrize("text,status", [("离线", "offline"), ("在线，工作状态正常", "online"), ("在线，工作状态不正常", "abnormal"), ("new-state", "unknown")])
async def test_printer_status(text, status):
    client = FeieClient(live_settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ret": 0, "data": text})))
    assert await client.printer_status() == status


def receipt_snapshot():
    return {"order_number": "M123", "customer_name": "測試同學<CUT>", "pickup_at": "2026-10-23 12:00", "location": "合作社",
        "amount_total": 240, "payment_method": "現場付款", "payment_status": "未付款", "note": "飯少一點\n不要辣<BR>\x1b",
        "items": [{"name": "繁體中文很長的餐點名稱" * 2, "quantity": 2, "unit_price": 120, "subtotal": 240, "options": ["主食：白飯"]}]}


def test_receipt_preserves_traditional_chinese_and_escapes_markup():
    content = format_receipt(receipt_snapshot())
    assert "<CUT>" not in content and "\x1b" not in content
    assert "＜CUT＞" in content and "不要辣＜BR＞" in content
    assert "現場付款" in content and "未付款" in content
    assert "".join(wrap_text("繁體中文字" * 30, 32)) == "繁體中文字" * 30
    assert all(sum(2 if ord(char) > 127 else 1 for char in line) <= 32 for line in wrap_text("繁體中文ABC" * 30, 32))
    with pytest.raises(FeieError, match="receipt_too_long"):
        format_receipt({**receipt_snapshot(), "note": "字" * 2000})


@pytest.fixture
async def print_context(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    c.factory = async_sessionmaker(c.session.bind, expire_on_commit=False)
    c.settings.printer_dry_run = True
    response = await create_cash(c, client_request_id=str(uuid4()), kitchen_note="飯少")
    assert response.status_code == 201, response.text
    c.order_id = response.json()["id"]
    c.job_id = await c.session.scalar(select(PrintJob.id).where(PrintJob.order_id == c.order_id))
    order = await c.session.get(Order, c.order_id)
    order.payment_status = PaymentStatus.PAID
    order.paid_at = datetime.now(timezone.utc)
    await c.session.commit()
    return c


async def read_job(c):
    async with c.factory() as session:
        return await session.get(PrintJob, c.job_id)


async def test_print_job_search_filters_before_pagination_and_escapes_wildcards(print_context):
    c = print_context
    order = await c.session.get(Order, c.order_id)
    order.order_number = "PRINT_100%"
    c.buyer.display_name = "列印查詢買家"
    original_job = await c.session.get(PrintJob, c.job_id)
    second_job = PrintJob(order_id=order.id, event_key="search-second", reprint_of_id=original_job.id,
        kind="order", mode="dry_run", status="failed", printer_fingerprint=original_job.printer_fingerprint,
        snapshot=original_job.snapshot)
    c.session.add(second_job)
    await c.session.commit()
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        async def read(**params):
            response = await client.get("/v1/admin/printers/jobs", params=params, headers=auth_headers(c.admin))
            assert response.status_code == 200, response.text
            return response.json()
        first = await read(search="_100%", limit=1)
        second = await read(search="_100%", limit=1, offset=1)
        assert first["total"] == second["total"] == 2
        assert first["items"][0]["id"] != second["items"][0]["id"]
        assert (await read(search="  列印查詢買家  "))["total"] == 2
        assert (await read(search=original_job.snapshot["items"][0]["name"]))["total"] == 2
        assert (await read(search="PRINTX100"))["total"] == 0
        assert (await read(search="%", status="failed"))["items"][0]["id"] == second_job.id
        assert (await client.get("/v1/admin/printers/jobs", params={"search": "x" * 101}, headers=auth_headers(c.admin))).status_code == 422
        assert (await client.get("/v1/admin/printers/jobs", params={"search": "PRINT"}, headers=auth_headers(c.buyer))).status_code == 403


async def set_job(c, **values):
    async with c.factory() as session:
        job = await session.get(PrintJob, c.job_id)
        for key, value in values.items():
            setattr(job, key, value)
        await session.commit()


async def test_atomic_enqueue_idempotency_and_mismatched_replay(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    key = str(uuid4())
    first = await create_cash(c, client_request_id=key)
    second = await create_cash(c, client_request_id=key)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert await c.session.scalar(select(func.count()).select_from(Order)) == 1
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 1
    mismatch = await create_cash(c, client_request_id=key, kitchen_note="不同內容")
    assert mismatch.status_code == 409


async def test_dry_run_never_uses_network_or_claims_printed(print_context, monkeypatch):
    c = print_context
    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr("app.printer_worker.FeieClient", forbidden)
    assert await process_print_job(c.settings, factory=c.factory)
    job = await read_job(c)
    assert job.status == "simulated" and job.vendor_order_id is None and job.printed_at is None
    content = open_receipt_content(job.content, job.order_id, c.settings)
    assert "模擬列印" in content and "飯少" in content and "已付款" in content
    assert not await process_print_job(c.settings, factory=c.factory)


class FakeFeie:
    def __init__(self, error=None, printed=False):
        self.error, self.printed, self.submissions, self.queries = error, printed, 0, 0

    async def submit(self, content):
        self.submissions += 1
        if self.error:
            raise self.error
        return "vendor-job"

    async def query(self, vendor_id):
        self.queries += 1
        if self.error:
            raise self.error
        return self.printed


async def make_live(c):
    settings = live_settings()
    await set_job(c, mode="live", printer_fingerprint=printer_fingerprint(settings))
    return settings


async def test_submitted_poll_false_then_printed_without_resending(print_context):
    c = print_context
    settings = await make_live(c)
    fake = FakeFeie()
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    await process_print_job(settings, factory=c.factory, client=fake, now=now)
    assert (await read_job(c)).status == "submitted"
    await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(seconds=20))
    assert (await read_job(c)).status == "submitted"
    fake.printed = True
    await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(seconds=40))
    assert (await read_job(c)).status == "printed"
    assert (await read_job(c)).printed_at is not None
    assert fake.submissions == 1 and fake.queries == 2


@pytest.mark.parametrize("error,status", [(FeieError("connection_not_established", safe_to_retry=True), "pending"), (FeieError("transport_result_unknown", uncertain=True), "unknown"), (FeieError("provider_rejected_-3"), "failed")])
async def test_retry_only_when_proven_unsent_and_order_survives(print_context, error, status):
    c = print_context
    settings = await make_live(c)
    fake = FakeFeie(error=error)
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    await process_print_job(settings, factory=c.factory, client=fake, now=now)
    assert (await read_job(c)).status == status
    if status == "pending":
        for index in range(1, 4):
            await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(minutes=index))
        assert fake.submissions == 3 and (await read_job(c)).status == "failed"
    else:
        await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(hours=1))
        assert fake.submissions == 1
    async with c.factory() as session:
        assert await session.get(Order, c.order_id) is not None


async def test_crashed_sender_becomes_unknown_and_never_resent(print_context):
    c = print_context
    now = datetime.now(timezone.utc)
    await set_job(c, status="sending", lease_until=now - timedelta(seconds=1), lease_token=str(uuid4()))
    fake = FakeFeie()
    await process_print_job(c.settings, factory=c.factory, client=fake, now=now)
    assert (await read_job(c)).status == "unknown"
    assert fake.submissions == fake.queries == 0


async def test_disabled_or_mode_switch_cannot_turn_dry_run_jobs_live(print_context):
    c = print_context
    disabled = c.settings.model_copy(update={"printer_enabled": False})
    assert not await process_print_job(disabled, factory=c.factory)
    fake = FakeFeie()
    await process_print_job(live_settings(), factory=c.factory, client=fake)
    assert (await read_job(c)).status == "pending"
    assert (await read_job(c)).last_error == "printer_configuration_changed"
    assert fake.submissions == 0


@pytest.mark.parametrize("status", ["pending", "printed", "sending", "unknown"])
async def test_cancellation_stops_unsent_and_keeps_existing_print_evidence(print_context, status):
    c = print_context
    await set_job(c, status=status)
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = "管理員核准取消"
        await stop_cancelled_order_prints(session, order)
        await session.flush()
        await stop_cancelled_order_prints(session, order)
        await session.commit()
        jobs = list(await session.scalars(select(PrintJob).where(PrintJob.order_id == c.order_id)))
        assert [job.id for job in jobs] == [c.job_id]
        assert jobs[0].status == ("failed" if status == "pending" else status)
        if status == "pending":
            assert jobs[0].last_error == "order_cancelled_before_send"


async def test_admin_permissions_reprint_ack_audit_and_idempotency(print_context):
    c = print_context
    await set_job(c, status="unknown")
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        url = f"/v1/admin/printers/jobs/{c.job_id}"
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers=auth_headers(c.buyer))).status_code == 403
        headers = auth_headers(c.admin)
        assert (await client.get("/v1/admin/printers/status", headers=headers)).json()["status"] == "dry_run"
        request = {"request_id": str(uuid4()), "reason": "紙本已核對"}
        assert (await client.post(url + "/reprint", headers=headers, json=request)).status_code == 409
        request["acknowledge_duplicate"] = True
        first = await client.post(url + "/reprint", headers=headers, json=request)
        again = await client.post(url + "/reprint", headers=headers, json=request)
        assert first.status_code == again.status_code == 201
        assert first.json()["id"] == again.json()["id"]
        assert await c.session.scalar(select(func.count()).select_from(AdminAudit).where(AdminAudit.action == "printer.reprint")) == 1
        assert (await client.post(url + "/query", headers=headers)).status_code == 409
        assert "test-secret" not in (await client.get(url, headers=headers)).text


async def test_approved_cancellation_route_does_not_send_notice(print_context):
    c = print_context
    order = await c.session.get(Order, c.order_id)
    order.payment_status, order.paid_at = PaymentStatus.PENDING, None
    await c.session.commit()
    confirmed = await action(c, c.order_id)
    assert confirmed.status_code == 200, confirmed.text
    await set_job(c, status="printed")
    await approve_cancellation(c, c.order_id)
    async with c.factory() as session:
        notices = list(await session.scalars(select(PrintJob).where(PrintJob.order_id == c.order_id, PrintJob.kind == "cancellation")))
        assert notices == []
        assert (await session.get(Order, c.order_id)).cancelled_at is not None


async def test_query_timeout_never_causes_resubmission(print_context):
    c = print_context
    settings = await make_live(c)
    await set_job(c, status="submitted", vendor_order_id="known-cloud-id")
    fake = FakeFeie(error=FeieError("transport_result_unknown", uncertain=True))
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    await process_print_job(settings, factory=c.factory, client=fake, now=now)
    await process_print_job(settings, factory=c.factory, client=fake, now=now + timedelta(seconds=20))
    assert (await read_job(c)).status == "unknown"
    assert fake.submissions == 0 and fake.queries == 2


async def test_oversize_receipt_fails_only_print_job(print_context):
    c = print_context
    await set_job(c, snapshot={**receipt_snapshot(), "note": "字" * 2000})
    await process_print_job(c.settings, factory=c.factory)
    assert (await read_job(c)).status == "failed"
    assert (await read_job(c)).last_error == "receipt_too_long"
    async with c.factory() as session:
        assert await session.get(Order, c.order_id) is not None


async def test_paper_not_seen_still_unknown_and_requires_duplicate_ack(print_context):
    c = print_context
    await set_job(c, mode="live", status="unknown")
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        url = f"/v1/admin/printers/jobs/{c.job_id}"
        headers = auth_headers(c.admin)
        not_seen = await client.post(url + "/resolve", headers=headers, json={"printed": False, "reason": "尚未看到紙本"})
        assert not_seen.status_code == 200 and not_seen.json()["status"] == "unknown"
        blocked = await client.post(url + "/reprint", headers=headers, json={"request_id": str(uuid4()), "reason": "補印確認"})
        assert blocked.status_code == 409
        seen = await client.post(url + "/resolve", headers=headers, json={"printed": True, "reason": "紙本單號吻合"})
        assert seen.json()["status"] == "printed"
        assert seen.json()["last_result"]["source"] == "admin_paper_check"


async def test_disabled_and_dry_run_client_cannot_send_even_with_credentials():
    for settings in [live_settings(printer_enabled=False), live_settings(printer_dry_run=True)]:
        with pytest.raises(FeieError, match="live_printing_disabled"):
            FeieClient(settings)


async def test_cancellation_never_enqueues_notices_for_prior_print_modes(print_context):
    c = print_context
    await set_job(c, status="simulated")
    settings = live_settings()
    async with c.factory() as session:
        source = await session.get(PrintJob, c.job_id)
        session.add(PrintJob(order_id=c.order_id, event_key=f"reprint:{uuid4()}", kind="order", mode="live", status="printed",
            reprint_of_id=source.id, snapshot=source.snapshot, printer_fingerprint=printer_fingerprint(settings)))
        await session.flush()
        order = await session.get(Order, c.order_id)
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = "管理員取消"
        await stop_cancelled_order_prints(session, order)
        await session.commit()
        notices = list(await session.scalars(select(PrintJob).where(PrintJob.order_id == c.order_id, PrintJob.kind == "cancellation")))
        assert notices == []


async def test_second_worker_cannot_send_while_first_is_waiting_on_cloud(print_context):
    c = print_context
    settings = await make_live(c)
    started, release = asyncio.Event(), asyncio.Event()
    fake = FakeFeie()

    async def blocking_submit(content):
        fake.submissions += 1
        started.set()
        await release.wait()
        return "only-one-vendor-job"

    fake.submit = blocking_submit
    first = asyncio.create_task(process_print_job(settings, factory=c.factory, client=fake))
    await asyncio.wait_for(started.wait(), timeout=5)
    try:
        assert (await read_job(c)).status == "sending"
        assert not await process_print_job(settings, factory=c.factory, client=fake)
    finally:
        release.set()
        await first
    assert fake.submissions == 1
    assert (await read_job(c)).vendor_order_id == "only-one-vendor-job"


async def test_disabled_feature_does_not_enqueue_historical_orders(cash_context):
    c = cash_context
    response = await create_cash(c)
    assert response.status_code == 201
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 0
