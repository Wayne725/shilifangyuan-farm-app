import hashlib
import hmac
import html
import inspect
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Protocol, Union
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from .common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
    parse_form_body,
    post_form,
)


ECPAY_STAGE_CHECKOUT_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
ECPAY_STAGE_QUERY_URL = (
    "https://payment-stage.ecpay.com.tw/Cashier/QueryTradeInfo/V5"
)

FormTransport = Callable[
    [str, Mapping[str, Any], Optional[Mapping[str, str]], float],
    Awaitable[HTTPResponse],
]


@dataclass(frozen=True)
class ECPayAIOSettings:
    merchant_id: str
    hash_key: str
    hash_iv: str
    checkout_url: str = ECPAY_STAGE_CHECKOUT_URL
    query_url: str = ECPAY_STAGE_QUERY_URL
    timeout_seconds: float = 15.0

    def validate(self) -> None:
        if not self.merchant_id or not self.hash_key or not self.hash_iv:
            raise IntegrationConfigurationError(
                "ECPay MerchantID, HashKey and HashIV are required"
            )
        if not self.checkout_url.startswith("https://"):
            raise IntegrationConfigurationError("ECPay checkout URL must use HTTPS")


@dataclass(frozen=True)
class CheckoutForm:
    action_url: str
    fields: Mapping[str, str]

    def to_html(self, title: str = "前往綠界付款") -> str:
        escaped_inputs = "\n".join(
            (
                '<input type="hidden" name="{}" value="{}">'.format(
                    html.escape(str(key), quote=True),
                    html.escape(str(value), quote=True),
                )
            )
            for key, value in self.fields.items()
        )
        return """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
</head>
<body onload="document.getElementById('ecpay-checkout').submit()">
  <main>
    <p>正在安全地前往綠界付款頁面…</p>
    <form id="ecpay-checkout" method="post" action="{action}">
      {inputs}
      <noscript><button type="submit">繼續付款</button></noscript>
    </form>
  </main>
</body>
</html>""".format(
            title=html.escape(title),
            action=html.escape(self.action_url, quote=True),
            inputs=escaped_inputs,
        )


@dataclass(frozen=True)
class RefundResult:
    refund_id: str
    status: str
    provider_refund_performed: bool
    amount: int
    reason: str
    created_at: datetime


class CallbackRepository(Protocol):
    async def apply_ecpay_payment_callback(
        self, event_key: str, payload: Mapping[str, str]
    ) -> str:
        """Atomically record the event and update the payment attempt once."""


def _stringify_parameters(parameters: Mapping[str, Any]) -> Dict[str, str]:
    return {
        str(key): "" if value is None else str(value)
        for key, value in parameters.items()
        if str(key).lower() != "checkmacvalue"
    }


def _ecpay_url_encode(value: str) -> str:
    encoded = quote_plus(value, safe="").lower()
    replacements = {
        "%2d": "-",
        "%5f": "_",
        "%2e": ".",
        "%21": "!",
        "%2a": "*",
        "%28": "(",
        "%29": ")",
    }
    for source, replacement in replacements.items():
        encoded = encoded.replace(source, replacement)
    return encoded


def build_check_mac_value(
    parameters: Mapping[str, Any],
    hash_key: str,
    hash_iv: str,
    algorithm: str = "sha256",
) -> str:
    """Build ECPay CheckMacValue using the documented sort/encode/hash rules."""
    if not hash_key or not hash_iv:
        raise IntegrationConfigurationError("ECPay HashKey and HashIV are required")

    values = _stringify_parameters(parameters)
    sorted_pairs = sorted(values.items(), key=lambda item: item[0].lower())
    query = "&".join("{}={}".format(key, value) for key, value in sorted_pairs)
    raw = "HashKey={}&{}&HashIV={}".format(hash_key, query, hash_iv)
    encoded = _ecpay_url_encode(raw)

    algorithm_name = algorithm.lower()
    if algorithm_name == "sha256":
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    elif algorithm_name == "md5":
        digest = hashlib.md5(encoded.encode("utf-8")).hexdigest()
    else:
        raise ValueError("Unsupported CheckMacValue algorithm")
    return digest.upper()


def verify_check_mac_value(
    parameters: Mapping[str, Any],
    hash_key: str,
    hash_iv: str,
    algorithm: str = "sha256",
) -> bool:
    provided = str(parameters.get("CheckMacValue", ""))
    if not provided:
        provided = str(parameters.get("checkmacvalue", ""))
    expected = build_check_mac_value(parameters, hash_key, hash_iv, algorithm)
    return hmac.compare_digest(provided.upper(), expected)


def create_merchant_trade_no(
    order_reference: str,
    attempt_number: int,
    now: Optional[datetime] = None,
) -> str:
    """Create a unique ASCII trade number within ECPay's 20-char limit."""
    timestamp = (now or datetime.now(timezone.utc)).strftime("%y%m%d%H%M%S")
    entropy = "{}:{}:{}:{}".format(
        order_reference, attempt_number, timestamp, secrets.token_hex(4)
    )
    suffix = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:8].upper()
    return "{}{}".format(timestamp, suffix)


def callback_event_key(parameters: Mapping[str, Any]) -> str:
    components = (
        str(parameters.get("MerchantTradeNo", "")),
        str(parameters.get("TradeNo", "")),
        str(parameters.get("RtnCode", "")),
        str(parameters.get("PaymentDate", "")),
    )
    return hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()


class ECPayAIOAdapter:
    def __init__(
        self,
        settings: ECPayAIOSettings,
        transport: FormTransport = post_form,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport

    def create_checkout_form(
        self,
        merchant_trade_no: str,
        amount: int,
        item_name: str,
        return_url: str,
        order_result_url: str,
        trade_description: str = "十里方圓訂單",
        client_back_url: Optional[str] = None,
        now: Optional[datetime] = None,
        custom_fields: Optional[Mapping[str, str]] = None,
    ) -> CheckoutForm:
        if not re.fullmatch(r"[A-Za-z0-9]{1,20}", merchant_trade_no):
            raise ValueError("MerchantTradeNo must be 1-20 ASCII letters or digits")
        if amount <= 0:
            raise ValueError("TradeAmt must be greater than zero")
        if not item_name.strip():
            raise ValueError("ItemName is required")
        if not return_url.startswith("https://"):
            raise ValueError("ReturnURL must use public HTTPS")
        if not order_result_url.startswith("https://"):
            raise ValueError("OrderResultURL must use public HTTPS")

        taipei_now = (now or datetime.now(timezone.utc)).astimezone(
            ZoneInfo("Asia/Taipei")
        )
        fields: Dict[str, str] = {
            "MerchantID": self.settings.merchant_id,
            "MerchantTradeNo": merchant_trade_no,
            "MerchantTradeDate": taipei_now.strftime("%Y/%m/%d %H:%M:%S"),
            "PaymentType": "aio",
            "TotalAmount": str(amount),
            "TradeDesc": trade_description[:200],
            "ItemName": item_name[:400],
            "ReturnURL": return_url,
            "ChoosePayment": "Credit",
            "EncryptType": "1",
            "OrderResultURL": order_result_url,
            "NeedExtraPaidInfo": "N",
        }
        if client_back_url:
            fields["ClientBackURL"] = client_back_url
        for key, value in (custom_fields or {}).items():
            if key in {"CustomField1", "CustomField2", "CustomField3", "CustomField4"}:
                fields[key] = str(value)[:50]
        fields["CheckMacValue"] = build_check_mac_value(
            fields,
            self.settings.hash_key,
            self.settings.hash_iv,
        )
        return CheckoutForm(action_url=self.settings.checkout_url, fields=fields)

    def verify_callback(self, parameters: Mapping[str, Any]) -> Dict[str, str]:
        payload = {str(key): str(value) for key, value in parameters.items()}
        if payload.get("MerchantID") != self.settings.merchant_id:
            raise IntegrationResponseError("Unexpected ECPay MerchantID")
        if not verify_check_mac_value(
            payload, self.settings.hash_key, self.settings.hash_iv
        ):
            raise IntegrationResponseError("Invalid ECPay CheckMacValue")
        if not payload.get("MerchantTradeNo"):
            raise IntegrationResponseError("Missing ECPay MerchantTradeNo")
        return payload

    async def process_callback(
        self,
        parameters: Mapping[str, Any],
        repository: CallbackRepository,
    ) -> str:
        payload = self.verify_callback(parameters)
        event_key = callback_event_key(payload)
        outcome = repository.apply_ecpay_payment_callback(event_key, payload)
        if inspect.isawaitable(outcome):
            await outcome
        return "1|OK"

    async def query_order(self, merchant_trade_no: str) -> Dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9]{1,20}", merchant_trade_no):
            raise ValueError("Invalid MerchantTradeNo")
        fields: Dict[str, str] = {
            "MerchantID": self.settings.merchant_id,
            "MerchantTradeNo": merchant_trade_no,
            "TimeStamp": str(int(datetime.now(timezone.utc).timestamp())),
        }
        fields["CheckMacValue"] = build_check_mac_value(
            fields,
            self.settings.hash_key,
            self.settings.hash_iv,
        )
        response = await self.transport(
            self.settings.query_url,
            fields,
            None,
            self.settings.timeout_seconds,
        )
        if response.status_code != 200:
            raise IntegrationResponseError(
                "ECPay query returned HTTP {}".format(response.status_code)
            )
        parsed = parse_form_body(response.body)
        if parsed.get("MerchantID") not in {None, "", self.settings.merchant_id}:
            raise IntegrationResponseError("Unexpected query MerchantID")
        if "CheckMacValue" in parsed and not verify_check_mac_value(
            parsed, self.settings.hash_key, self.settings.hash_iv
        ):
            raise IntegrationResponseError("Invalid query response CheckMacValue")
        return parsed


class LocalSandboxRefundAdapter:
    """Records a local refund without claiming ECPay performed a card reversal."""

    async def refund(
        self,
        order_id: Union[int, str],
        amount: int,
        reason: str,
        idempotency_key: str,
    ) -> RefundResult:
        if amount <= 0:
            raise ValueError("Refund amount must be greater than zero")
        if not reason.strip():
            raise ValueError("Refund reason is required")
        if not idempotency_key.strip():
            raise ValueError("Refund idempotency key is required")
        digest = hashlib.sha256(
            "{}:{}:{}".format(order_id, amount, idempotency_key).encode("utf-8")
        ).hexdigest()[:20]
        return RefundResult(
            refund_id="sandbox_{}".format(digest),
            status="refunded",
            provider_refund_performed=False,
            amount=amount,
            reason=reason.strip(),
            created_at=datetime.now(timezone.utc),
        )
