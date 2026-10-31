from datetime import datetime, timedelta, timezone
from hashlib import sha1
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.feie import FeieClient
from app.models import PrintJob
from app.printer_worker import process_print_job
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_cash_payments import cash_context, create_cash, approve_cancellation
from tests.test_feie_printing import live_settings


@pytest.mark.parametrize("mode", ["disabled", "dry_run", "live"])
async def test_configuration_api_needs_no_credentials_or_network_and_never_returns_secrets(cash_context, monkeypatch, mode):
    c = cash_context
    settings = live_settings() if mode == "live" else make_test_settings(printer_enabled=mode == "dry_run")

    def forbidden(*args, **kwargs):
        raise AssertionError("configuration must not connect to provider")

    monkeypatch.setattr("app.routers.printers.FeieClient", forbidden)
    async with api_test_context(c.session, [printers_router], settings=settings) as client:
        url = "/v1/admin/printers/configuration"
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers=auth_headers(c.buyer))).status_code == 403
        response = await client.get(url, headers=auth_headers(c.admin))
        assert response.status_code == 200
        result = response.json()
        assert result["live_config_ready"] is (mode == "live")
        assert result["missing_variables"] == ([] if mode == "live" else ["FEIE_USER", "FEIE_UKEY", "FEIE_PRINTER_SN"])
        for value in ["test-user", "test-secret", "test-device"]:
            assert value not in response.text


async def test_configuration_reports_bad_endpoint_without_echoing_it(cash_context):
    settings = live_settings(feie_api_base_url="https://private-user:private-password@evil.test/Api/Open/?key=private-value")
    c = cash_context
    async with api_test_context(c.session, [printers_router], settings=settings) as client:
        response = await client.get("/v1/admin/printers/configuration", headers=auth_headers(c.admin))
        assert response.status_code == 200
        assert not response.json()["live_config_ready"]
        assert response.json()["checks"]["endpoint_allowed"] is False
        assert "private-" not in response.text and "evil.test" not in response.text


async def test_order_to_real_client_mock_cloud_admin_query_reprint_and_cancel(cash_context, monkeypatch):
    c = cash_context
    settings = live_settings()
    for name in ("printer_enabled", "printer_dry_run", "feie_user", "feie_ukey", "feie_printer_sn", "feie_ukey_rotated", "feie_ap_endpoint_verified"):
        setattr(c.settings, name, getattr(settings, name))
    factory = async_sessionmaker(c.session.bind, expire_on_commit=False)
    cloud = {"printed": False, "contents": [], "calls": []}

    def forbid_real_network(*args, **kwargs):
        raise AssertionError("real network forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_real_network)

    def provider(request):
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        assert form["sig"] == sha1(f"test-usertest-secret{form['stime']}".encode()).hexdigest()
        api = form["apiname"]
        cloud["calls"].append(api)
        if api == "Open_printMsg":
            assert str(request.url) == "https://api.jp.feieyun.com/Api/Open/printMsg"
            assert form["sn"] == "test-device" and form["times"] == "1"
            cloud["contents"].append(form["content"])
            return httpx.Response(200, json={"ret": 0, "data": f"mock-job-{len(cloud['contents'])}"})
        if api == "Open_queryOrderState":
            assert str(request.url) == "https://api.jp.feieyun.com/Api/Open/queryOrderState"
            assert form["orderid"].startswith("mock-job-")
            return httpx.Response(200, json={"ret": 0, "data": cloud["printed"]})
        assert api == "Open_queryPrinterStatus"
        assert str(request.url) == "https://api.jp.feieyun.com/Api/Open/queryPrinterStatus"
        return httpx.Response(200, json={"ret": 0, "data": "离线"})

    transport = httpx.MockTransport(provider)
    client_factory = lambda config: FeieClient(config, transport=transport)
    monkeypatch.setattr("app.printer_worker.FeieClient", client_factory)
    monkeypatch.setattr("app.routers.printers.FeieClient", client_factory)

    order_response = await create_cash(c, client_request_id=str(uuid4()), kitchen_note="飯少，不要辣")
    assert order_response.status_code == 201
    order_id = order_response.json()["id"]
    job_id = await c.session.scalar(select(PrintJob.id).where(PrintJob.order_id == order_id))
    assert cloud["calls"] == [], "order request must not call external printer API"
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    async with api_test_context(c.session, [printers_router], settings=c.settings) as client:
        headers = auth_headers(c.admin)
        root = "/v1/admin/printers"
        job_url = f"{root}/jobs/{job_id}"
        status = await client.get(f"{root}/status", headers=headers)
        assert status.json()["status"] == "offline"
        await process_print_job(c.settings, factory=factory, now=now)
        detail = await client.get(job_url, headers=headers)
        assert detail.json()["status"] == "submitted" and detail.json()["can_query"]
        assert "付款狀態：未付款" in detail.json()["preview"]
        assert "飯少，不要辣" in cloud["contents"][0]
        await process_print_job(c.settings, factory=factory, now=now + timedelta(seconds=20))
        assert (await client.get(job_url, headers=headers)).json()["status"] == "submitted"
        cloud["printed"] = True
        assert (await client.post(f"{job_url}/query", headers=headers)).status_code == 200
        await process_print_job(c.settings, factory=factory, now=now + timedelta(seconds=40))
        assert (await client.get(job_url, headers=headers)).json()["status"] == "printed"
        assert len(cloud["contents"]) == 1

        body = {"request_id": str(uuid4()), "reason": "測試補印完整接口"}
        reprint = await client.post(f"{job_url}/reprint", headers=headers, json=body)
        replay = await client.post(f"{job_url}/reprint", headers=headers, json=body)
        assert reprint.status_code == replay.status_code == 201
        assert reprint.json()["id"] == replay.json()["id"]
        await process_print_job(c.settings, factory=factory, now=now + timedelta(seconds=60))
        assert len(cloud["contents"]) == 2 and "補印" in cloud["contents"][1]
        await process_print_job(c.settings, factory=factory, now=now + timedelta(seconds=80))

        await approve_cancellation(c, order_id)
        await process_print_job(c.settings, factory=factory, now=now + timedelta(seconds=100))
        assert len(cloud["contents"]) == 3 and "取消通知" in cloud["contents"][2]
        listing = await client.get(f"{root}/jobs", params={"order_id": order_id}, headers=headers)
        assert listing.json()["total"] == 3
        assert len([job for job in listing.json()["items"] if job["kind"] == "cancellation"]) == 1
        rejected = await client.post(f"{job_url}/reprint", headers=headers, json={"request_id": str(uuid4()), "reason": "已取消訂單不能補印製作單"})
        assert rejected.status_code == 409
