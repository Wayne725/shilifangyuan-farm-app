from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
    require_provider_mutation_enabled,
    post_json_no_redirect,
)
from .invoice import (
    BarcodeValidation,
    InvoiceIssueRequest,
    InvoiceIssueResult,
    JsonTransport,
    PreparedInvoice,
    PreparedInvoiceLine,
    is_mobile_barcode_format,
    normalize_mobile_barcode,
)
from .fanyu_endpoints import (
    FANYU_TEST_BASE_URL,
    FANYU_PRODUCTION_BASE_URL,
    FANYU_HOSTS,
    is_fanyu_api_base_url,
)
from .invoice_context import fanyu_account_context


TAIPEI = ZoneInfo("Asia/Taipei")
INVOICE_NUMBER_PATTERN = re.compile(r"^[A-Z]{2}[0-9]{8}$")


MAX_CERTIFICATE_PDF_BYTES = 2 * 1024 * 1024
PRINT_BARCODE_PATTERN = re.compile(r"[0-9]{3}(?:02|04|06|08|10|12)[A-Z]{2}[0-9]{12}")


@dataclass(frozen=True)
class FanyuInvoicePrintCodes:
    invoice_number: str
    barcode: str = field(repr=False)
    left_qrcode: str = field(repr=False)
    right_qrcode: str = field(repr=False)


def _validate_invoice_identity(invoice_number: str, invoice_date: str) -> datetime:
    if not INVOICE_NUMBER_PATTERN.fullmatch(invoice_number) or not re.fullmatch(r"[0-9]{8}", invoice_date):
        raise ValueError("Invalid invoice number or date")
    return datetime.strptime(invoice_date, "%Y%m%d")


def fanyu_signature(create_datetime: str, api_key: str) -> str:
    digest = hmac.new(
        api_key.encode("utf-8"),
        create_datetime.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


@dataclass(frozen=True)
class FanyuInvoiceSettings:
    company_id: str
    user_id: str
    auth_password: str
    api_key: str
    seller_id: str
    base_url: str = FANYU_TEST_BASE_URL
    signature_verified: bool = False
    timeout_seconds: float = 15.0
    recovery_mode: bool = False

    def validate(self) -> None:
        required = {
            "companyID": self.company_id,
            "userID": self.user_id,
            "auth": self.auth_password,
            "APIKey": self.api_key,
            "sellerID": self.seller_id,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise IntegrationConfigurationError(
                "汎宇電子發票缺少設定：{}".format("、".join(missing))
            )
        if not re.fullmatch(r"[0-9]{8,10}", self.company_id):
            raise IntegrationConfigurationError("汎宇 companyID 格式不正確")
        if not re.fullmatch(r"[0-9]{8,10}", self.seller_id):
            raise IntegrationConfigurationError("汎宇 sellerID 格式不正確")
        if not is_fanyu_api_base_url(self.base_url):
            raise IntegrationConfigurationError(
                "汎宇 API 必須使用官方 HTTPS /einv 根網址，不可包含登入路徑或網址參數"
            )
        if not self.signature_verified:
            raise IntegrationConfigurationError(
                "汎宇簽章測試向量尚未驗證，拒絕傳送財務資料"
            )


def _decimal_text(value: Decimal | int) -> str:
    decimal_value = Decimal(value)
    if decimal_value == decimal_value.to_integral():
        return str(int(decimal_value))
    return format(decimal_value.normalize(), "f")


def _round_integer(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _personal_identifier(request: InvoiceIssueRequest) -> str:
    email_prefix = request.customer_email.split("@", 1)[0]
    cleaned = re.sub(r"[^A-Za-z0-9]", "", email_prefix)
    if cleaned and cleaned not in {"0", "00", "000", "0000"}:
        return cleaned[:4]
    cleaned_customer = re.sub(r"[^A-Za-z0-9]", "", request.customer_id)
    if cleaned_customer:
        return cleaned_customer[-4:]
    return "客戶"


class FanyuInvoiceAdapter:
    provider_name = "fanyu"

    def __init__(
        self,
        settings: FanyuInvoiceSettings,
        transport: JsonTransport = post_json_no_redirect,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport

    @property
    def binding_context(self) -> dict:
        return fanyu_account_context(
            base_url=self.settings.base_url,
            company_id=self.settings.company_id,
            seller_id=self.settings.seller_id,
            user_id=self.settings.user_id,
        )

    def build_envelope(
        self,
        request_data: Mapping[str, Any],
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        timestamp = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
        create_datetime = timestamp.strftime("%Y%m%d%H%M%S")
        return {
            "companyID": self.settings.company_id,
            "userID": self.settings.user_id,
            "auth": base64.b64encode(
                self.settings.auth_password.encode("utf-8")
            ).decode("ascii"),
            "createDateTime": create_datetime,
            "signatureValue": fanyu_signature(
                create_datetime,
                self.settings.api_key,
            ),
            "reqData": dict(request_data),
        }

    def build_issue_data(
        self, request: InvoiceIssueRequest
    ) -> Dict[str, Any]:
        request.validate(allow_mobile_delivery_without_contact=True)
        company_invoice = request.buyer_type == "company"
        taxable_lines = [item for item in request.items if item.tax_type == "1"]
        exempt_lines = [item for item in request.items if item.tax_type == "3"]
        taxable_gross = sum(item.amount for item in taxable_lines)
        exempt_amount = sum(item.amount for item in exempt_lines)

        taxable_sales_amount = (
            _round_integer(
                sum(
                    (
                        Decimal(item.amount) / Decimal("1.05")
                        for item in taxable_lines
                    ),
                    Decimal("0"),
                )
            )
            if company_invoice
            else taxable_gross
        )
        tax_amount = (
            taxable_gross - taxable_sales_amount if company_invoice else 0
        )
        total_amount = taxable_gross + exempt_amount

        if taxable_lines and exempt_lines:
            tax_type = "9"
        elif exempt_lines:
            tax_type = "3"
        else:
            tax_type = "1"

        # api01 沿用成功實測的 001 補零；僅 web2 已證實最多 3 碼。
        host = urlparse(self.settings.base_url).hostname
        sequence_width = (
            3 if host in {"api01.einvoice.com.tw", "web2.einvoice.com.tw"} else 4
        )
        sequence_limit = 999 if host == "web2.einvoice.com.tw" else 9999
        if len(request.items) > sequence_limit:
            raise ValueError(f"汎宇目前主機的發票明細不可超過 {sequence_limit} 筆")
        details = []
        for index, item in enumerate(request.items, start=1):
            if company_invoice and item.tax_type == "1":
                unit_price = (
                    Decimal(item.unit_price) / Decimal("1.05")
                ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
                amount = (
                    Decimal(item.amount) / Decimal("1.05")
                ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            else:
                unit_price = Decimal(item.unit_price)
                amount = item.amount
            details.append(
                {
                    "description": item.name[:500],
                    "quantity": str(item.quantity),
                    "unit": item.unit[:6],
                    "unitprice": _decimal_text(unit_price),
                    "amount": _decimal_text(amount),
                    "sequenceNumber": f"{index:0{sequence_width}d}",
                    "remark": item.remark[:120],
                    "taxType": item.tax_type,
                }
            )

        carrier_type = ""
        carrier_number = ""
        if request.carrier_type == "mobile_barcode":
            carrier_type = "3J0002"
            carrier_number = normalize_mobile_barcode(request.carrier_number)
        else:
            carrier_number = request.customer_email.strip()
            if not carrier_number:
                raise ValueError("汎宇 Email 會員載具必須提供客人 Email")
            if len(carrier_number) > 64:
                raise ValueError("汎宇 Email 會員載具不可超過 64 字元")
            carrier_type = "EG0478"
        return {
            "orderID": request.relate_number,
            "process_type": "B" if company_invoice else "C",
            "sellerID": self.settings.seller_id,
            "buyerID": (
                request.buyer_tax_id
                if company_invoice
                else "0000000000"
            ),
            "buyerName": (
                request.buyer_name.strip()
                if company_invoice
                else _personal_identifier(request)
            )[:60],
            "buyerAddress": "",
            "mainRemark": request.remark[:200],
            "invoiceType": "07",
            "donateMark": "0",
            "carrierType": carrier_type,
            "carrierID1": carrier_number,
            "carrierID2": carrier_number,
            "printMark": "N",
            "npoban": "",
            "salesAmount": str(taxable_sales_amount),
            "freetaxSalesamount": str(exempt_amount),
            "zerotaxSalesamount": "0",
            "taxType": tax_type,
            "taxrate": "0.05" if taxable_lines else "0",
            "taxAmount": str(tax_amount),
            "totalAmount": str(total_amount),
            "notifyEmail": request.customer_email[:80],
            "customsClearanceMark": "",
            "unitCode": "",
            "zeroTaxRateReason": "",
            "Details": details,
        }

    def prepare_invoice(self, request: InvoiceIssueRequest) -> PreparedInvoice:
        data = self.build_issue_data(request)
        details = data["Details"]
        return PreparedInvoice(
            provider=self.provider_name,
            relate_number=request.relate_number,
            buyer_type=request.buyer_type,
            provider_request=data,
            sales_amount=Decimal(str(data["salesAmount"])),
            tax_amount=Decimal(str(data["taxAmount"])),
            total_amount=Decimal(str(data["totalAmount"])),
            items=tuple(
                PreparedInvoiceLine(
                    name=str(detail["description"]),
                    quantity=int(detail["quantity"]),
                    unit=str(detail["unit"]),
                    unit_price=Decimal(str(detail["unitprice"])),
                    amount=Decimal(str(detail["amount"])),
                    tax_type=str(detail["taxType"]),
                    sequence_number=int(detail["sequenceNumber"]),
                    remark=str(detail["remark"]),
                )
                for detail in details
            ),
        )

    async def issue_prepared_invoice(
        self,
        prepared: PreparedInvoice,
    ) -> InvoiceIssueResult:
        if prepared.provider != self.provider_name:
            raise ValueError("Prepared invoice provider does not match Fanyu")
        response = await self._request(
            "/openInvoice",
            prepared.provider_request,
        )
        invoice_number = str(response.get("invNo", "")).strip().upper()
        if not INVOICE_NUMBER_PATTERN.fullmatch(invoice_number):
            raise IntegrationResponseError(
                "汎宇電子發票成功回應缺少有效發票號碼"
            )
        return InvoiceIssueResult(
            relate_number=prepared.relate_number,
            invoice_number=invoice_number,
            invoice_date=self._invoice_datetime(response),
            random_number=str(response.get("randomNumber", "")),
            raw=response,
        )

    async def issue_invoice(
        self, request: InvoiceIssueRequest
    ) -> InvoiceIssueResult:
        return await self.issue_prepared_invoice(
            self.prepare_invoice(request)
        )

    async def query_invoice(
        self,
        relate_number: str,
        *,
        buyer_type: str = "personal",
    ) -> Dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9]{1,50}", relate_number):
            raise ValueError("Invalid invoice sales order number")
        if buyer_type not in {"personal", "company"}:
            raise ValueError("Invalid invoice buyer type")
        response = await self._request(
            "/queryInvoice",
            {
                "orderID": relate_number,
                "process_type": "B" if buyer_type == "company" else "C",
                "sellerID": self.settings.seller_id,
            },
            allow_no_data=True,
            read_only=True,
        )
        if not response:
            return {
                "RtnCode": 0,
                "RtnMsg": "查無電子發票",
                "InvoiceNo": "",
                "InvoiceDate": "",
                "RandomNumber": "",
                "ProviderStatus": "",
                "ProviderResponse": {},
            }
        invoice_number = str(response.get("invNo", "")).strip().upper()
        if not INVOICE_NUMBER_PATTERN.fullmatch(invoice_number):
            raise IntegrationResponseError(
                "汎宇銷貨單查詢缺少有效發票號碼"
            )
        provider_status = str(response.get("status", ""))
        if provider_status not in {"", "0", "1", "3"}:
            raise IntegrationResponseError("汎宇銷貨單查詢回傳未知發票狀態")
        return {
            "RtnCode": 1,
            "RtnMsg": "",
            "InvoiceNo": invoice_number,
            "InvoiceDate": self._invoice_datetime(response),
            "RandomNumber": str(response.get("randomNumber", "")),
            "ProviderStatus": provider_status,
            "ProviderResponse": response,
        }

    async def query_invoice_detail(
        self, invoice_number: str, invoice_date: str, *, buyer_type: str,
    ) -> Mapping[str, Any]:
        _validate_invoice_identity(invoice_number, invoice_date)
        if buyer_type not in {"personal", "company"}:
            raise ValueError("Invalid invoice buyer type")
        return await self._request(
            "/queryInvoiceDetail",
            {"invNo": invoice_number, "invDate": invoice_date,
             "process_type": "B" if buyer_type == "company" else "C", "sellerID": self.settings.seller_id},
            read_only=True,
        )

    async def get_invoice_print_codes(
        self, invoice_number: str, invoice_date: str, *, buyer_type: str,
    ) -> FanyuInvoicePrintCodes:
        date = _validate_invoice_identity(invoice_number, invoice_date)
        if buyer_type not in {"personal", "company"}:
            raise ValueError("Invalid invoice buyer type")
        response = await self._request(
            "/getInvoicePrintCode",
            {"invNo": invoice_number, "invDate": invoice_date,
             "processType": "B" if buyer_type == "company" else "C", "sellerID": self.settings.seller_id},
            read_only=True,
        )
        if response.get("invNo") != invoice_number:
            raise IntegrationResponseError("汎宇列印資料的發票號碼不符")
        barcode = response.get("barcode")
        if isinstance(barcode, str) and barcode.startswith("*") and barcode.endswith("*"):
            barcode = barcode[1:-1]  # Strip the optional Code 39 start and stop symbols.
        period_end_month = date.month + date.month % 2
        barcode_prefix = f"{date.year - 1911:03d}{period_end_month:02d}{invoice_number}"
        if (
            not isinstance(barcode, str)
            or not PRINT_BARCODE_PATTERN.fullmatch(barcode)
            or not barcode.startswith(barcode_prefix)
        ):
            raise IntegrationResponseError("汎宇列印資料缺少相符的一維條碼")
        left, right = response.get("leftQrcode"), response.get("rightQrcode")
        if not all(
            isinstance(value, str) and not re.search(r"[\x00\ud800-\udfff]", value) and len(value.encode("utf-8")) <= 8192
            for value in (left, right)
        ):
            raise IntegrationResponseError("汎宇列印資料缺少完整的左右二維條碼")
        roc_date = f"{date.year - 1911:03d}{date:%m%d}"
        if (
            len(left) < 77 or not left.startswith(f"{invoice_number}{roc_date}{barcode[-4:]}")
            or left[45:53] != self.settings.seller_id or not right.startswith("**")
        ):
            raise IntegrationResponseError("汎宇左右二維條碼與原發票資料不符")
        return FanyuInvoicePrintCodes(invoice_number, barcode, left, right)

    async def get_invoice_b2c_pdf(
        self, invoice_number: str, invoice_date: str, *, random_number: str = "",
    ) -> bytes:
        _validate_invoice_identity(invoice_number, invoice_date)
        if random_number and not re.fullmatch(r"[0-9]{4}", random_number):
            raise ValueError("發票隨機碼必須為 4 碼數字")
        request = {"invNo": invoice_number, "invDate": invoice_date, "sellerID": self.settings.seller_id}
        if random_number:
            request["ranNo"] = random_number
        response = await self._request("/getInvoiceB2CPDF", request, read_only=True)
        if response.get("invNo", invoice_number) != invoice_number or response.get("invDate", invoice_date) != invoice_date:
            raise IntegrationResponseError("汎宇證明聯 PDF 與原發票資料不符")
        encoded = response.get("b2c_PDF")
        if not isinstance(encoded, str) or len(encoded) > ((MAX_CERTIFICATE_PDF_BYTES + 2) // 3) * 4:
            raise IntegrationResponseError("汎宇證明聯 PDF 缺少內容或超過大小限制")
        try:
            document = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise IntegrationResponseError("汎宇證明聯 PDF 編碼不正確") from exc
        if (
            len(document) > MAX_CERTIFICATE_PDF_BYTES
            or not document.startswith(b"%PDF-")
            or not document.rstrip().endswith(b"%%EOF")
        ):
            raise IntegrationResponseError("汎宇證明聯不是有效的 PDF 資料")
        return document

    async def cancel_invoice(
        self,
        *,
        order_id: str,
        buyer_type: str,
        buyer_tax_id: str,
        invoice_number: str,
        invoice_date: str,
        reason: str,
        notify_email: str = "",
        automatic_refund: bool = False,
    ) -> Mapping[str, Any]:
        cancel_reason = reason.strip() or ("全額退款自動作廢" if automatic_refund else "管理員作廢")
        if len(cancel_reason) > 20:
            raise ValueError("發票作廢原因不可超過 20 字")
        return await self._request(
            "/cancelInvoice",
            {
                "orderID": order_id[:50],
                "process_type": "B" if buyer_type == "company" else "C",
                "sellerID": self.settings.seller_id,
                "buyerID": buyer_tax_id if buyer_type == "company" else "00000000",
                "invNo": invoice_number,
                "invDate": invoice_date,
                "cancelReason": cancel_reason,
                "remark": "",
                "notifyEmail": notify_email[:80],
            },
        )

    async def open_allowance(
        self, request_data: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        return await self._request("/openAllowance", request_data)

    async def validate_mobile_barcode(
        self, barcode: str
    ) -> BarcodeValidation:
        normalized = normalize_mobile_barcode(barcode)
        valid = is_mobile_barcode_format(normalized)
        return BarcodeValidation(
            barcode=normalized,
            valid=valid,
            provider_checked=False,
            message=(
                "手機條碼格式正確；汎宇文件未提供載具查詢 API"
                if valid
                else "手機條碼格式不正確"
            ),
        )

    async def _request(
        self,
        path: str,
        request_data: Mapping[str, Any],
        *,
        allow_no_data: bool = False,
        read_only: bool = False,
    ) -> Mapping[str, Any]:
        if not read_only:
            require_provider_mutation_enabled(self.settings.recovery_mode)
        response: HTTPResponse = await self.transport(
            f"{self.settings.base_url.rstrip('/')}{path}",
            self.build_envelope(request_data),
            {"Content-Type": "application/json"},
            self.settings.timeout_seconds,
        )
        if response.status_code != 200:
            raise IntegrationResponseError(
                f"汎宇電子發票回傳 HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise IntegrationResponseError("汎宇電子發票回傳格式不正確")
        status_code = str(payload.get("statusCode", ""))
        status_desc = str(payload.get("statusDesc", ""))
        live_not_issued = (
            status_code == "2"
            and "尚未用此銷貨單號碼開立發票" in status_desc
        )
        if allow_no_data and (status_code == "3" or live_not_issued):
            return {}
        if status_code != "0":
            raise IntegrationResponseError(
                "汎宇電子發票失敗：{} {}".format(
                    status_code,
                    self._safe_provider_text(status_desc),
                ).strip()
            )
        data = payload.get("respData")
        if not isinstance(data, dict):
            raise IntegrationResponseError("汎宇電子發票缺少 respData")
        return data

    def _safe_provider_text(self, value: object) -> str:
        message = str(value)[:500]
        secrets = (
            self.settings.auth_password,
            self.settings.api_key,
            base64.b64encode(
                self.settings.auth_password.encode("utf-8")
            ).decode("ascii"),
        )
        for secret in secrets:
            if secret:
                message = message.replace(secret, "[REDACTED]")
        return message

    @staticmethod
    def _invoice_datetime(response: Mapping[str, Any]) -> str:
        date_value = str(response.get("invDate", ""))
        time_value = str(response.get("invTime", ""))
        if re.fullmatch(r"[0-9]{8}", date_value):
            date_value = (
                f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:8]}"
            )
        return " ".join(value for value in (date_value, time_value) if value)
