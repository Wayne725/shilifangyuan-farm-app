"""Feie Asia Pacific receipt API. Never log signed forms or vendor response bodies."""
from __future__ import annotations

import hashlib
import time
from urllib.parse import urlsplit

import httpx

from ..config import Settings


class FeieError(Exception):
    def __init__(self, code: str, *, safe_to_retry: bool = False, uncertain: bool = False):
        super().__init__(code)
        self.code = code
        self.safe_to_retry = safe_to_retry
        self.uncertain = uncertain


def validate_endpoint(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise FeieError("invalid_asia_pacific_endpoint") from None
    if (parsed.scheme != "https" or parsed.hostname != "api.jp.feieyun.com"
            or port not in (None, 443) or parsed.path != "/Api/Open/"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise FeieError("invalid_asia_pacific_endpoint")


def validate_live_config(settings: Settings) -> None:
    if not settings.printer_enabled or settings.printer_dry_run:
        raise FeieError("live_printing_disabled")
    validate_endpoint(settings.feie_api_base_url)
    if not (settings.feie_ukey_rotated and settings.feie_ap_endpoint_verified):
        raise FeieError("live_activation_not_confirmed")
    if not all(value.get_secret_value().strip() for value in (
        settings.feie_user, settings.feie_ukey, settings.feie_printer_sn,
    )):
        raise FeieError("missing_printer_credentials")


def configuration_read(settings: Settings) -> dict:
    missing = [name for name, value in (
        ("FEIE_USER", settings.feie_user), ("FEIE_UKEY", settings.feie_ukey),
        ("FEIE_PRINTER_SN", settings.feie_printer_sn),
    ) if not value.get_secret_value().strip()]
    try:
        validate_endpoint(settings.feie_api_base_url)
        endpoint_allowed = True
    except FeieError:
        endpoint_allowed = False
    checks = {
        "printer_enabled": settings.printer_enabled,
        "live_mode_selected": not settings.printer_dry_run,
        "credentials_configured": not missing,
        "endpoint_allowed": endpoint_allowed,
        "ukey_rotation_confirmed": settings.feie_ukey_rotated,
        "asia_pacific_endpoint_confirmed": settings.feie_ap_endpoint_verified,
    }
    return {"checks": checks, "missing_variables": missing,
            "live_config_ready": all(checks.values()),
            "in_app_worker_enabled": settings.printer_worker_enabled}


class FeieClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        validate_live_config(settings)
        self.settings = settings
        self.transport = transport

    async def _call(self, api: str, **parameters: str) -> dict:
        stime = str(int(time.time()))
        user = self.settings.feie_user.get_secret_value()
        signature = hashlib.sha1(
            f"{user}{self.settings.feie_ukey.get_secret_value()}{stime}".encode("utf-8")
        ).hexdigest()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.printer_timeout_seconds,
                follow_redirects=False, transport=self.transport,
            ) as client:
                response = await client.post(self.settings.feie_api_base_url, data={
                    "user": user, "stime": stime, "sig": signature,
                    "apiname": api, **parameters,
                })
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise FeieError("connection_not_established", safe_to_retry=True) from None
        except httpx.HTTPError:
            raise FeieError("transport_result_unknown", uncertain=True) from None
        if response.status_code != 200:
            raise FeieError("http_result_unknown", uncertain=True)
        try:
            result = response.json()
        except ValueError:
            raise FeieError("invalid_response", uncertain=True) from None
        if not isinstance(result, dict) or type(result.get("ret")) is not int:
            raise FeieError("invalid_response", uncertain=True)
        if result["ret"] != 0:
            # Only documented rejection codes prove that no job was accepted.
            code = result["ret"]
            raise FeieError(f"provider_rejected_{code}", uncertain=code not in {-1, -2, -3, 1001, 1002})
        return result

    async def submit(self, content: str) -> str:
        if len(content.encode("utf-8")) > 5000:
            raise FeieError("receipt_too_long")
        result = await self._call("Open_printMsg", sn=self.settings.feie_printer_sn.get_secret_value(), content=content, times="1")
        job_id = result.get("data")
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 120:
            raise FeieError("missing_vendor_order_id", uncertain=True)
        return job_id

    async def query(self, vendor_order_id: str) -> bool:
        result = await self._call("Open_queryOrderState", orderid=vendor_order_id)
        if type(result.get("data")) is not bool:
            raise FeieError("invalid_query_result", uncertain=True)
        return result["data"]

    async def printer_status(self) -> str:
        result = await self._call("Open_queryPrinterStatus", sn=self.settings.feie_printer_sn.get_secret_value())
        value = result.get("data")
        if not isinstance(value, str):
            raise FeieError("invalid_printer_status", uncertain=True)
        if "离线" in value or "離線" in value:
            return "offline"
        if "不正常" in value or "异常" in value or "異常" in value:
            return "abnormal"
        if "在线" in value or "在線" in value:
            return "online"
        return "unknown"
