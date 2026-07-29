import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode
from urllib.request import Request, urlopen


class IntegrationError(RuntimeError):
    """A remote integration failed in a way callers may retry."""


class IntegrationConfigurationError(IntegrationError):
    """Required credentials or cryptographic dependencies are missing."""


class IntegrationResponseError(IntegrationError):
    """The provider returned an invalid or unsuccessful response."""


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    body: str
    headers: Mapping[str, str]

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except json.JSONDecodeError as exc:
            raise IntegrationResponseError("Provider returned invalid JSON") from exc


async def post_form(
    url: str,
    values: Mapping[str, Any],
    headers: Optional[Mapping[str, str]] = None,
    timeout_seconds: float = 15.0,
) -> HTTPResponse:
    encoded = urlencode(
        {key: "" if value is None else str(value) for key, value in values.items()}
    ).encode("utf-8")
    request_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "shilifangyuan-sandbox/1.0",
    }
    request_headers.update(dict(headers or {}))
    return await _urlopen(request_headers, encoded, timeout_seconds, url)


async def post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Optional[Mapping[str, str]] = None,
    timeout_seconds: float = 15.0,
) -> HTTPResponse:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    request_headers = {
        "Content-Type": "application/json",
        "User-Agent": "shilifangyuan-sandbox/1.0",
    }
    request_headers.update(dict(headers or {}))
    return await _urlopen(request_headers, encoded, timeout_seconds, url)


async def _urlopen(
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float,
    url: str,
) -> HTTPResponse:
    def send() -> HTTPResponse:
        request = Request(url=url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
                return HTTPResponse(
                    status_code=response.status,
                    body=response_body,
                    headers=dict(response.headers.items()),
                )
        except HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            return HTTPResponse(
                status_code=exc.code,
                body=response_body,
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except (URLError, TimeoutError) as exc:
            raise IntegrationError("Provider request could not be completed") from exc

    return await asyncio.to_thread(send)


def parse_form_body(body: str) -> Dict[str, str]:
    return dict(parse_qsl(body, keep_blank_values=True))


def json_dumps_stable(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
