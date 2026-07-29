import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence
from urllib.parse import quote, unquote

from .common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
    post_json,
)


ECPAY_STAGE_INVOICE_ISSUE_URL = (
    "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Issue"
)
ECPAY_STAGE_BARCODE_URL = (
    "https://einvoice-stage.ecpay.com.tw/B2CInvoice/CheckBarcode"
)
ECPAY_STAGE_INVOICE_QUERY_URL = (
    "https://einvoice-stage.ecpay.com.tw/B2CInvoice/GetIssue"
)
MOBILE_BARCODE_PATTERN = re.compile(r"^/[0-9A-Z+\-.]{7}$")

JsonTransport = Callable[
    [str, Mapping[str, Any], Optional[Mapping[str, str]], float],
    Awaitable[HTTPResponse],
]


@dataclass(frozen=True)
class ECPayInvoiceSettings:
    merchant_id: str
    hash_key: str
    hash_iv: str
    issue_url: str = ECPAY_STAGE_INVOICE_ISSUE_URL
    barcode_url: str = ECPAY_STAGE_BARCODE_URL
    query_url: str = ECPAY_STAGE_INVOICE_QUERY_URL
    timeout_seconds: float = 15.0

    def validate(self) -> None:
        if not self.merchant_id:
            raise IntegrationConfigurationError("Invoice MerchantID is required")
        if len(self.hash_key.encode("utf-8")) != 16:
            raise IntegrationConfigurationError(
                "ECPay B2C invoice HashKey must be 16 bytes"
            )
        if len(self.hash_iv.encode("utf-8")) != 16:
            raise IntegrationConfigurationError(
                "ECPay B2C invoice HashIV must be 16 bytes"
            )
        for url in (self.issue_url, self.barcode_url, self.query_url):
            if not url.startswith("https://"):
                raise IntegrationConfigurationError(
                    "ECPay invoice endpoints must use HTTPS"
                )


@dataclass(frozen=True)
class InvoiceLine:
    name: str
    quantity: int
    unit_price: int
    unit: str = "件"
    tax_type: str = "1"
    remark: str = ""

    @property
    def amount(self) -> int:
        return self.quantity * self.unit_price

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Invoice item name is required")
        if self.quantity <= 0:
            raise ValueError("Invoice item quantity must be greater than zero")
        if self.unit_price < 0:
            raise ValueError("Invoice item unit price cannot be negative")
        if self.tax_type not in {"1", "3"}:
            raise ValueError("Sandbox invoices support taxable or tax-exempt items")


@dataclass(frozen=True)
class InvoiceIssueRequest:
    relate_number: str
    customer_email: str
    items: Sequence[InvoiceLine]
    carrier_type: str = "1"
    carrier_number: str = ""
    customer_id: str = ""
    customer_phone: str = ""
    remark: str = ""

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9]{1,30}", self.relate_number):
            raise ValueError(
                "Invoice RelateNumber must be 1-30 ASCII letters or digits"
            )
        if not self.customer_email and not self.customer_phone:
            raise ValueError("Invoice requires a customer email or phone")
        if self.carrier_type not in {"1", "3"}:
            raise ValueError("Sandbox supports ECPay or mobile barcode carriers")
        if self.carrier_type == "3" and not is_mobile_barcode_format(
            self.carrier_number
        ):
            raise ValueError("Invalid mobile barcode format")
        if not self.items:
            raise ValueError("Invoice requires at least one item")
        for item in self.items:
            item.validate()


@dataclass(frozen=True)
class BarcodeValidation:
    barcode: str
    valid: bool
    provider_checked: bool
    message: str = ""


@dataclass(frozen=True)
class InvoiceIssueResult:
    relate_number: str
    invoice_number: str
    invoice_date: str
    random_number: str
    raw: Mapping[str, Any]


def normalize_mobile_barcode(barcode: str) -> str:
    return barcode.strip().upper()


def is_mobile_barcode_format(barcode: str) -> bool:
    return bool(MOBILE_BARCODE_PATTERN.fullmatch(normalize_mobile_barcode(barcode)))


def _pkcs7_pad(value: bytes, block_size: int = 16) -> bytes:
    padding_length = block_size - (len(value) % block_size)
    return value + bytes([padding_length]) * padding_length


def _pkcs7_unpad(value: bytes, block_size: int = 16) -> bytes:
    if not value:
        raise IntegrationResponseError("Encrypted invoice response was empty")
    padding_length = value[-1]
    if padding_length < 1 or padding_length > block_size:
        raise IntegrationResponseError("Invalid invoice response padding")
    if value[-padding_length:] != bytes([padding_length]) * padding_length:
        raise IntegrationResponseError("Invalid invoice response padding")
    return value[:-padding_length]


def _aes_cbc_encrypt(value: bytes, key: bytes, iv: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return encryptor.update(value) + encryptor.finalize()
    except ImportError:
        try:
            from Crypto.Cipher import AES

            return AES.new(key, AES.MODE_CBC, iv).encrypt(value)
        except ImportError as exc:
            raise IntegrationConfigurationError(
                "Install cryptography or pycryptodome for ECPay invoice AES"
            ) from exc


def _aes_cbc_decrypt(value: bytes, key: bytes, iv: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return decryptor.update(value) + decryptor.finalize()
    except ImportError:
        try:
            from Crypto.Cipher import AES

            return AES.new(key, AES.MODE_CBC, iv).decrypt(value)
        except ImportError as exc:
            raise IntegrationConfigurationError(
                "Install cryptography or pycryptodome for ECPay invoice AES"
            ) from exc


def encrypt_ecpay_invoice_data(
    data: Mapping[str, Any],
    hash_key: str,
    hash_iv: str,
) -> str:
    """URL-encode JSON, then encrypt with AES-128-CBC/PKCS7."""
    key = hash_key.encode("utf-8")
    iv = hash_iv.encode("utf-8")
    if len(key) != 16 or len(iv) != 16:
        raise IntegrationConfigurationError(
            "ECPay invoice HashKey and HashIV must each be 16 bytes"
        )
    serialized = json.dumps(
        data, ensure_ascii=False, separators=(",", ":")
    )
    url_encoded = quote(serialized, safe="").encode("utf-8")
    encrypted = _aes_cbc_encrypt(_pkcs7_pad(url_encoded), key, iv)
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_ecpay_invoice_data(
    encrypted_data: str,
    hash_key: str,
    hash_iv: str,
) -> Dict[str, Any]:
    key = hash_key.encode("utf-8")
    iv = hash_iv.encode("utf-8")
    if len(key) != 16 or len(iv) != 16:
        raise IntegrationConfigurationError(
            "ECPay invoice HashKey and HashIV must each be 16 bytes"
        )
    try:
        encrypted = base64.b64decode(encrypted_data, validate=True)
        decrypted = _aes_cbc_decrypt(encrypted, key, iv)
        decoded = unquote(_pkcs7_unpad(decrypted).decode("utf-8"))
        parsed = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationResponseError(
            "Could not decrypt ECPay invoice response"
        ) from exc
    if not isinstance(parsed, dict):
        raise IntegrationResponseError("Invoice Data must decode to a JSON object")
    return parsed


class ECPayInvoiceAdapter:
    def __init__(
        self,
        settings: ECPayInvoiceSettings,
        transport: JsonTransport = post_json,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport

    def build_issue_data(
        self, request: InvoiceIssueRequest
    ) -> Dict[str, Any]:
        request.validate()
        tax_types = {item.tax_type for item in request.items}
        if tax_types == {"1"}:
            invoice_tax_type = "1"
        elif tax_types == {"3"}:
            invoice_tax_type = "3"
        else:
            invoice_tax_type = "9"

        items: List[Dict[str, Any]] = []
        for index, item in enumerate(request.items, start=1):
            items.append(
                {
                    "ItemSeq": index,
                    "ItemName": item.name[:100],
                    "ItemCount": item.quantity,
                    "ItemWord": item.unit[:6],
                    "ItemPrice": item.unit_price,
                    "ItemTaxType": item.tax_type,
                    "ItemAmount": item.amount,
                    "ItemRemark": item.remark[:100],
                }
            )

        result: Dict[str, Any] = {
            "MerchantID": self.settings.merchant_id,
            "RelateNumber": request.relate_number,
            "CustomerID": request.customer_id[:20],
            "CustomerIdentifier": "",
            "CustomerName": "",
            "CustomerAddr": "",
            "CustomerPhone": request.customer_phone[:20],
            "CustomerEmail": request.customer_email[:80],
            "ClearanceMark": "",
            "Print": "0",
            "Donation": "0",
            "LoveCode": "",
            "CarrierType": request.carrier_type,
            "CarrierNum": (
                normalize_mobile_barcode(request.carrier_number)
                if request.carrier_type == "3"
                else ""
            ),
            "TaxType": invoice_tax_type,
            "SpecialTaxType": 8 if invoice_tax_type == "3" else 0,
            "SalesAmount": sum(item.amount for item in request.items),
            "InvoiceRemark": request.remark[:200],
            "Items": items,
            "InvType": "07",
            "vat": "1",
        }
        return result

    async def issue_invoice(
        self, request: InvoiceIssueRequest
    ) -> InvoiceIssueResult:
        data = self.build_issue_data(request)
        response = await self._request(self.settings.issue_url, data)
        if int(response.get("RtnCode", 0)) != 1:
            raise IntegrationResponseError(
                "ECPay invoice issue failed: {} {}".format(
                    response.get("RtnCode", ""),
                    response.get("RtnMsg", ""),
                ).strip()
            )
        return InvoiceIssueResult(
            relate_number=request.relate_number,
            invoice_number=str(response.get("InvoiceNo", "")),
            invoice_date=str(response.get("InvoiceDate", "")),
            random_number=str(response.get("RandomNumber", "")),
            raw=response,
        )

    async def query_invoice(self, relate_number: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9]{1,30}", relate_number):
            raise ValueError("Invalid invoice RelateNumber")
        return await self._request(
            self.settings.query_url,
            {
                "MerchantID": self.settings.merchant_id,
                "RelateNumber": relate_number,
            },
        )

    async def validate_mobile_barcode(
        self, barcode: str
    ) -> BarcodeValidation:
        normalized = normalize_mobile_barcode(barcode)
        if not is_mobile_barcode_format(normalized):
            return BarcodeValidation(
                barcode=normalized,
                valid=False,
                provider_checked=False,
                message="手機條碼格式不正確",
            )
        try:
            response = await self._request(
                self.settings.barcode_url,
                {
                    "MerchantID": self.settings.merchant_id,
                    "BarCode": normalized,
                },
            )
        except IntegrationResponseError:
            return BarcodeValidation(
                barcode=normalized,
                valid=True,
                provider_checked=False,
                message="格式正確，但目前無法向財政部確認",
            )
        if int(response.get("RtnCode", 0)) != 1:
            return BarcodeValidation(
                barcode=normalized,
                valid=True,
                provider_checked=False,
                message=str(response.get("RtnMsg", "暫時無法確認手機條碼")),
            )
        exists = response.get("IsExist") == "Y"
        return BarcodeValidation(
            barcode=normalized,
            valid=exists,
            provider_checked=True,
            message="" if exists else "查無此手機條碼",
        )

    async def _request(
        self, endpoint: str, data: Mapping[str, Any]
    ) -> Dict[str, Any]:
        envelope = {
            "MerchantID": self.settings.merchant_id,
            "RqHeader": {
                "Timestamp": int(datetime.now(timezone.utc).timestamp()),
            },
            "Data": encrypt_ecpay_invoice_data(
                data,
                self.settings.hash_key,
                self.settings.hash_iv,
            ),
        }
        response = await self.transport(
            endpoint,
            envelope,
            None,
            self.settings.timeout_seconds,
        )
        if response.status_code != 200:
            raise IntegrationResponseError(
                "ECPay invoice returned HTTP {}".format(response.status_code)
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise IntegrationResponseError("Invalid ECPay invoice envelope")
        if str(payload.get("MerchantID", "")) != self.settings.merchant_id:
            raise IntegrationResponseError("Unexpected invoice MerchantID")
        if int(payload.get("TransCode", 0)) != 1:
            raise IntegrationResponseError(
                "ECPay invoice transport failed: {} {}".format(
                    payload.get("TransCode", ""),
                    payload.get("TransMsg", ""),
                ).strip()
            )
        encrypted_data = payload.get("Data")
        if not isinstance(encrypted_data, str) or not encrypted_data:
            raise IntegrationResponseError("ECPay invoice response Data is missing")
        return decrypt_ecpay_invoice_data(
            encrypted_data,
            self.settings.hash_key,
            self.settings.hash_iv,
        )
