from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional
from urllib.parse import quote, urlencode, urlsplit

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
    is_public_https_origin,
    is_public_https_url,
    post_json_no_redirect,
)


RAYGATE_IDENTIFIER = "RayGate"
RAYGATE_PAYMENT_PROVIDER = "raygate"


class RayGateTransactionNotFoundError(IntegrationResponseError):
    """The provider has no transaction for the requested POS order number."""


RAYGATE_PAID_STATUS = 2
RAYGATE_CALLBACK_URL_MAX_LENGTH = 120
SUPPORTED_PAYMENT_TYPES = {
    "easywallet",
    "gamapay",
    "icashpay",
    "jkopay",
    "linepay",
    "newebpay",
    "piwallet",
    "pluspay",
    "pxpay",
    "taiwanpay",
}
RAYGATE_DOCUMENT_EXAMPLE_CREDENTIAL_FINGERPRINTS = frozenset(
    {
        "a7f388035f44be57e50e2c4d6f2e6e637f64d44fe231cbacdffcccb841101a5b",
        "54a37d8827f7e4441f461820dc336c9b07e1d6bf70b82e88e0775830245354da",
        "7be229d8a0d1010898021fb1ad121cb7abe55cfb9d0666e8efcca221078a18c7",
        "ef3416f6724de9c1dbb0dd1b86106b0ea1d087f0d91ca1c4f21ff28dec6df491",
        "23fee3be72c43ab0ace45c45a3d5af19d45f137e3f3cd1d9d05723c985562fc2",
    }
)

JsonTransport = Callable[
    [str, Mapping[str, Any], Optional[Mapping[str, str]], float],
    Awaitable[HTTPResponse],
]


@dataclass(frozen=True)
class RayGateSettings:
    store_identifier: str
    key_hex: str
    iv_hex: str
    base_url: str
    allowed_hostname: str
    merchant_id: str = ""
    terminal_id: str = ""
    device_type: str = ""
    timeout_seconds: float = 15.0

    def validate(self) -> None:
        store_identifier = self.store_identifier.strip()
        if not store_identifier or len(store_identifier) > 50:
            raise IntegrationConfigurationError(
                "RayGate store identifier is required and must be at most 50 characters"
            )
        parsed = urlsplit(self.base_url.strip())
        hostname = (parsed.hostname or "").rstrip(".").lower()
        allowed_hostname = self.allowed_hostname.strip().rstrip(".").lower()
        if (
            not is_public_https_origin(self.base_url)
            or not allowed_hostname
            or hostname != allowed_hostname
        ):
            raise IntegrationConfigurationError(
                "RayGate base URL must be the explicitly allowed public HTTPS origin"
            )
        _decode_hex_secret(self.key_hex, 32, "RayGate AES key")
        _decode_hex_secret(self.iv_hex, 16, "RayGate AES IV")

    def validate_server_api(self) -> None:
        self.validate()
        merchant_id = self.merchant_id.strip()
        if not merchant_id or len(merchant_id) > 15:
            raise IntegrationConfigurationError(
                "RayGate merchant ID is required and must be at most 15 characters"
            )
        terminal_id = self.terminal_id.strip()
        if not terminal_id or len(terminal_id) > 8:
            raise IntegrationConfigurationError(
                "RayGate terminal ID is required and must be at most 8 characters"
            )
        if len(self.device_type.strip()) > 50:
            raise IntegrationConfigurationError(
                "RayGate device type must be at most 50 characters"
            )


@dataclass(frozen=True)
class RayGatePaymentResult:
    order_id: str
    amount: int
    pay_type: str
    return_code: str
    message: str
    transaction_time: str
    store_name: str
    store_code: str
    status: int
    associated_order_id: str = ""
    pos_id: str = ""
    pos_order_number: str = ""

    @property
    def disposition(self) -> str:
        if self.return_code == "0000" and self.status == RAYGATE_PAID_STATUS:
            return "paid"
        if (
            self.status == 1
            and self.return_code in {"0000", "RG000001"}
        ) or (
            self.status == 5
            and self.return_code in {"0000", "RG000005"}
        ):
            return "pending"
        if self.return_code == "0000" and self.status == 3:
            return "refunded"
        if self.status == 4 and self.return_code != "0000":
            return "refund_failed"
        if self.status == 0:
            return "cancelled"
        return "failed"

    def to_canonical_payload(self) -> Dict[str, str]:
        payload = {
            "Provider": RAYGATE_PAYMENT_PROVIDER,
            "MerchantTradeNo": self.pos_order_number,
            "TradeAmt": str(self.amount),
            "TradeNo": self.order_id,
            "RtnCode": "1" if self.disposition == "paid" else self.return_code,
            "RtnMsg": self.message,
            "PaymentDate": self.transaction_time,
            "PaymentType": self.pay_type,
            "TradeStatus": "1" if self.disposition == "paid" else "0",
            "PaymentDisposition": self.disposition,
            "RayGateReturnCode": self.return_code,
            "RayGateStatus": str(self.status),
            "RayGateStoreCode": self.store_code,
            "RayGateStoreName": self.store_name,
            "RayGateAssociatedOrderID": self.associated_order_id,
            "RayGatePOSID": self.pos_id,
        }
        return payload


@dataclass(frozen=True)
class RayGateRefundResult:
    refund_id: str
    status: str
    provider_refund_performed: bool
    amount: int
    reason: str
    created_at: datetime
    provider_response: Mapping[str, Any]


def _decode_hex_secret(value: str, length: int, label: str) -> bytes:
    expected_characters = length * 2
    if re.fullmatch(rf"[0-9a-fA-F]{{{expected_characters}}}", value) is None:
        raise IntegrationConfigurationError(
            f"{label} must be exactly {expected_characters} hexadecimal characters"
        )
    return bytes.fromhex(value)


def hash_digest(transaction_data: str) -> str:
    return hashlib.sha256(transaction_data.encode("utf-8")).hexdigest()


def verify_hash_digest(transaction_data: str, provided_digest: str) -> bool:
    if len(provided_digest) != 64:
        return False
    return hmac.compare_digest(hash_digest(transaction_data), provided_digest.lower())


def uses_document_example_credentials(settings: RayGateSettings) -> bool:
    credential_values = (
        settings.store_identifier.strip(),
        settings.key_hex.strip().lower(),
        settings.iv_hex.strip().lower(),
        settings.merchant_id.strip(),
        settings.terminal_id.strip(),
    )
    return any(
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        in RAYGATE_DOCUMENT_EXAMPLE_CREDENTIAL_FINGERPRINTS
        for value in credential_values
    )


def callback_event_key(result: RayGatePaymentResult) -> str:
    return canonical_event_key(result.to_canonical_payload())


def canonical_event_key(payload: Mapping[str, str]) -> str:
    values = (
        payload.get("TradeNo", ""),
        payload.get("MerchantTradeNo", ""),
        payload.get("RayGateReturnCode", ""),
        payload.get("RayGateStatus", ""),
        payload.get("PaymentDate", ""),
    )
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


class RayGateAdapter:
    def __init__(
        self,
        settings: RayGateSettings,
        transport: JsonTransport = post_json_no_redirect,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport
        self._key = _decode_hex_secret(settings.key_hex, 32, "RayGate AES key")
        self._iv = _decode_hex_secret(settings.iv_hex, 16, "RayGate AES IV")

    def encrypt_transaction(self, payload: Mapping[str, Any]) -> str:
        plaintext = json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = Cipher(
            algorithms.AES(self._key),
            modes.CBC(self._iv),
        ).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode("ascii")

    def decrypt_transaction(self, transaction_data: str) -> Dict[str, Any]:
        try:
            ciphertext = base64.b64decode(transaction_data, validate=True)
            if not ciphertext or len(ciphertext) % 16:
                raise ValueError("invalid AES block length")
            decryptor = Cipher(
                algorithms.AES(self._key),
                modes.CBC(self._iv),
            ).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
            payload = json.loads(plaintext.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise IntegrationResponseError(
                "RayGate TransactionData could not be decrypted"
            ) from exc
        if not isinstance(payload, dict):
            raise IntegrationResponseError(
                "RayGate TransactionData must contain a JSON object"
            )
        return payload

    def create_checkout_url(
        self,
        *,
        pos_order_number: str,
        amount: int,
        callback_url: str,
        return_url: str,
        pos_id: str = "",
    ) -> str:
        if (
            not isinstance(pos_order_number, str)
            or not pos_order_number
            or len(pos_order_number) > 50
        ):
            raise ValueError("RayGate pos_order_number must be 1-50 characters")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount <= 0
            or len(str(amount)) > 10
        ):
            raise ValueError("RayGate set_price must be a positive value up to 10 digits")
        _validate_callback_url(callback_url, "callback_url", required=True)
        _validate_callback_url(return_url, "return_url", required=False)
        if len(pos_id) > 8:
            raise ValueError("RayGate pos_id must be at most 8 characters")
        transaction: Dict[str, str] = {
            "set_price": str(amount),
            "pos_order_number": pos_order_number,
            "callback_url": callback_url,
            "return_url": return_url,
        }
        if pos_id:
            transaction["pos_id"] = pos_id
        transaction_data = self.encrypt_transaction(transaction)
        endpoint = "{}/calc/pay_encrypt/{}".format(
            self.settings.base_url.strip().rstrip("/"),
            quote(self.settings.store_identifier.strip(), safe=""),
        )
        return "{}?{}".format(
            endpoint,
            urlencode(
                {
                    "TransactionData": transaction_data,
                    "HashDigest": hash_digest(transaction_data),
                }
            ),
        )

    def verify_callback(
        self,
        envelope: Mapping[str, Any],
        identifier: str,
    ) -> RayGatePaymentResult:
        if not hmac.compare_digest(identifier, RAYGATE_IDENTIFIER):
            raise IntegrationResponseError("Unexpected RayGate callback identifier")
        transaction_data = str(envelope.get("TransactionData", ""))
        provided_digest = str(envelope.get("HashDigest", ""))
        if not transaction_data or not verify_hash_digest(
            transaction_data, provided_digest
        ):
            raise IntegrationResponseError("Invalid RayGate HashDigest")
        return self._parse_payment_result(
            self.decrypt_transaction(transaction_data),
            require_pos_order_number=True,
        )

    async def query_order(self, pos_order_number: str) -> Dict[str, str]:
        if not pos_order_number or len(pos_order_number) > 50:
            raise ValueError("RayGate pos_order_number must be 1-50 characters")
        response_payload = await self._post_server_api(
            "query",
            {"pos_order_number": pos_order_number},
        )
        error_code = _required_string(
            response_payload,
            "ErrorCode",
            8,
            "query response",
        )
        if error_code == "RG999922":
            raise RayGateTransactionNotFoundError(
                "RayGate query found no matching transaction"
            )
        if error_code != "0000":
            raise IntegrationResponseError(
                "RayGate query failed: {} {}".format(
                    error_code or "missing ErrorCode",
                    str(response_payload.get("Message", ""))[:100],
                )
            )
        data = response_payload.get("Data")
        if isinstance(data, Mapping):
            records = [data]
        elif isinstance(data, list):
            records = data
        else:
            raise IntegrationResponseError(
                "RayGate query Data must be an object or array"
            )
        matches = [
            self._parse_payment_result(item, require_pos_order_number=True)
            for item in records
            if isinstance(item, Mapping)
            and str(item.get("pos_order_number", "")) == pos_order_number
        ]
        if len(matches) != 1:
            raise IntegrationResponseError(
                "RayGate query must return exactly one matching transaction"
            )
        return matches[0].to_canonical_payload()

    async def refund(
        self,
        *,
        provider_order_id: str,
        payment_type: str,
        amount: int,
        reason: str,
        idempotency_key: str,
    ) -> RayGateRefundResult:
        if not provider_order_id or len(provider_order_id) > 30:
            raise ValueError("RayGate order_id must be 1-30 characters")
        if payment_type not in SUPPORTED_PAYMENT_TYPES:
            raise ValueError("Unsupported RayGate refund_type")
        if amount <= 0:
            raise ValueError("Refund amount must be greater than zero")
        if not reason.strip() or not idempotency_key.strip():
            raise ValueError("Refund reason and idempotency key are required")
        response_payload = await self._post_server_api(
            "refund",
            {
                "order_id": provider_order_id,
                "refund_type": payment_type,
            },
        )
        error_code = _required_string(
            response_payload,
            "ErrorCode",
            8,
            "refund response",
        )
        refund_id = _required_string(
            response_payload,
            "refund_order_id",
            30,
            "refund response",
        )
        status = _strict_integer(
            response_payload.get("status"),
            "RayGate refund status",
        )
        if error_code != "0000" or status != 3 or not refund_id:
            raise IntegrationResponseError(
                "RayGate refund failed: {} {}".format(
                    error_code or "missing ErrorCode",
                    str(response_payload.get("Message", ""))[:100],
                )
            )
        return RayGateRefundResult(
            refund_id=refund_id,
            status="refunded",
            provider_refund_performed=True,
            amount=amount,
            reason=reason.strip(),
            created_at=datetime.now(timezone.utc),
            provider_response=dict(response_payload),
        )

    async def _post_server_api(
        self,
        operation: str,
        transaction: Mapping[str, Any],
    ) -> Dict[str, Any]:
        self.settings.validate_server_api()
        transaction_data = self.encrypt_transaction(transaction)
        response = await self.transport(
            "{}/api/{}/{}".format(
                self.settings.base_url.strip().rstrip("/"),
                operation,
                quote(self.settings.store_identifier.strip(), safe=""),
            ),
            {
                "TransactionData": transaction_data,
                "HashDigest": hash_digest(transaction_data),
            },
            self._server_headers(),
            self.settings.timeout_seconds,
        )
        if response.status_code != 200:
            raise IntegrationResponseError(
                "RayGate {} returned HTTP {}".format(
                    operation, response.status_code
                )
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise IntegrationResponseError(
                "RayGate server API response must be a JSON object"
            )
        return payload

    def _server_headers(self) -> Dict[str, str]:
        headers = {
            "X-ePay-MerchantID": self.settings.merchant_id.strip(),
            "X-ePay-TerminalID": self.settings.terminal_id.strip(),
        }
        if self.settings.device_type.strip():
            headers["X-Merchant-DeviceType"] = self.settings.device_type.strip()
        return headers

    def _parse_payment_result(
        self,
        payload: Mapping[str, Any],
        *,
        require_pos_order_number: bool,
    ) -> RayGatePaymentResult:
        required = {
            "order_id",
            "amount",
            "pay_type",
            "return_code",
            "message",
            "transaction_time",
            "store_name",
            "store_code",
            "status",
        }
        missing = sorted(key for key in required if key not in payload)
        if missing:
            raise IntegrationResponseError(
                "RayGate payment result is missing {}".format(", ".join(missing))
            )
        amount = _strict_integer(payload["amount"], "RayGate payment amount")
        status = _strict_integer(payload["status"], "RayGate payment status")
        result = RayGatePaymentResult(
            order_id=_required_string(payload, "order_id", 30, "payment result"),
            amount=amount,
            pay_type=_required_string(payload, "pay_type", 20, "payment result"),
            return_code=_required_string(
                payload, "return_code", 8, "payment result"
            ),
            message=_required_string(payload, "message", 100, "payment result"),
            transaction_time=_required_string(
                payload, "transaction_time", 20, "payment result"
            ),
            store_name=_required_string(
                payload, "store_name", 50, "payment result"
            ),
            store_code=_required_string(
                payload, "store_code", 50, "payment result"
            ),
            status=status,
            associated_order_id=_optional_string(
                payload, "associated_order_id", 30, "payment result"
            ),
            pos_id=_optional_string(payload, "pos_id", 8, "payment result"),
            pos_order_number=_optional_string(
                payload, "pos_order_number", 50, "payment result"
            ),
        )
        if not hmac.compare_digest(
            result.store_code,
            self.settings.store_identifier.strip(),
        ):
            raise IntegrationResponseError("Unexpected RayGate store_code")
        if require_pos_order_number and not result.pos_order_number:
            raise IntegrationResponseError("Missing RayGate pos_order_number")
        if amount <= 0 or len(str(amount)) > 10:
            raise IntegrationResponseError("Invalid RayGate payment amount")
        if status not in {0, 1, 2, 3, 4, 5}:
            raise IntegrationResponseError("Invalid RayGate payment status")
        if result.pay_type not in SUPPORTED_PAYMENT_TYPES:
            raise IntegrationResponseError("Unsupported RayGate payment type")
        if not _is_supported_transaction_time(result.transaction_time):
            raise IntegrationResponseError("Invalid RayGate transaction_time")
        if status == 3 and not result.associated_order_id:
            raise IntegrationResponseError(
                "Refunded RayGate payment is missing associated_order_id"
            )
        return result


def _validate_callback_url(value: str, label: str, *, required: bool) -> None:
    if not value and not required:
        return
    if (
        not is_public_https_url(value)
        or len(value) > RAYGATE_CALLBACK_URL_MAX_LENGTH
    ):
        raise ValueError(f"RayGate {label} must be a public HTTPS URL up to 120 characters")


def _strict_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntegrationResponseError(f"{label} must be an integer")
    return value


def _is_supported_transaction_time(value: str) -> bool:
    for date_format in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            datetime.strptime(value, date_format)
            return True
        except ValueError:
            continue
    return False


def _required_string(
    payload: Mapping[str, Any],
    key: str,
    maximum_length: int,
    context: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum_length:
        raise IntegrationResponseError(
            f"RayGate {context} field {key} must be a non-empty string "
            f"up to {maximum_length} characters"
        )
    return value


def _optional_string(
    payload: Mapping[str, Any],
    key: str,
    maximum_length: int,
    context: str,
) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum_length:
        raise IntegrationResponseError(
            f"RayGate {context} field {key} must be a string "
            f"up to {maximum_length} characters"
        )
    return value
