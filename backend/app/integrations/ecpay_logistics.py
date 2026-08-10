from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Sequence
from urllib.parse import quote, unquote

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..config import Settings
from .common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
    post_json,
)


ECPAY_STAGE_LOGISTICS_BASE_URL = (
    "https://logistics-stage.ecpay.com.tw/Express/v2"
)
ECPAY_STAGE_LOGISTICS_SELECTION_URL = (
    f"{ECPAY_STAGE_LOGISTICS_BASE_URL}/RedirectToLogisticsSelection"
)
ECPAY_STAGE_LOGISTICS_UPDATE_TEMP_URL = (
    f"{ECPAY_STAGE_LOGISTICS_BASE_URL}/UpdateTempTrade"
)
ECPAY_STAGE_LOGISTICS_CREATE_URL = (
    f"{ECPAY_STAGE_LOGISTICS_BASE_URL}/CreateByTempTrade"
)
ECPAY_STAGE_LOGISTICS_QUERY_URL = (
    f"{ECPAY_STAGE_LOGISTICS_BASE_URL}/QueryLogisticsTradeInfo"
)
ECPAY_STAGE_LOGISTICS_PRINT_URL = (
    f"{ECPAY_STAGE_LOGISTICS_BASE_URL}/PrintTradeDocument"
)
ECPAY_CALLBACK_MAX_AGE_SECONDS = 300
GOODS_NAME_FORBIDDEN_PATTERN = re.compile(r"[\^'‘`!@#%&*+\\”<>|_\[\]]")
MERCHANT_TRADE_NO_PATTERN = re.compile(r"^[A-Za-z0-9]{1,20}$")

JsonTransport = Callable[
    [str, Mapping[str, Any], Optional[Mapping[str, str]], float],
    Awaitable[HTTPResponse],
]


@dataclass(frozen=True)
class ECPayLogisticsSettings:
    merchant_id: str
    hash_key: str
    hash_iv: str
    platform_id: str = ""
    selection_url: str = ECPAY_STAGE_LOGISTICS_SELECTION_URL
    update_temp_url: str = ECPAY_STAGE_LOGISTICS_UPDATE_TEMP_URL
    create_url: str = ECPAY_STAGE_LOGISTICS_CREATE_URL
    query_url: str = ECPAY_STAGE_LOGISTICS_QUERY_URL
    print_url: str = ECPAY_STAGE_LOGISTICS_PRINT_URL
    timeout_seconds: float = 15.0

    def validate(self) -> None:
        if not self.merchant_id.strip():
            raise IntegrationConfigurationError(
                "ECPay logistics MerchantID is required"
            )
        if len(self.hash_key.encode("utf-8")) != 16:
            raise IntegrationConfigurationError(
                "ECPay logistics HashKey must be 16 bytes"
            )
        if len(self.hash_iv.encode("utf-8")) != 16:
            raise IntegrationConfigurationError(
                "ECPay logistics HashIV must be 16 bytes"
            )
        for url in (
            self.selection_url,
            self.update_temp_url,
            self.create_url,
            self.query_url,
            self.print_url,
        ):
            if not url.startswith("https://"):
                raise IntegrationConfigurationError(
                    "ECPay logistics endpoints must use HTTPS"
                )
        if self.timeout_seconds <= 0:
            raise IntegrationConfigurationError(
                "ECPay logistics timeout must be positive"
            )


@dataclass(frozen=True)
class LogisticsSelectionRequest:
    goods_amount: int
    goods_name: str
    sender_name: str
    sender_zip_code: str
    sender_address: str
    server_reply_url: str
    client_reply_url: str
    temp_logistics_id: str = "0"
    remark: str = ""
    temperature: str = "0001"
    specification: str = "0001"
    scheduled_pickup_time: str = "4"
    receiver_address: str = ""
    receiver_cell_phone: str = ""
    receiver_phone: str = ""
    receiver_name: str = ""
    enable_select_delivery_time: str = "N"
    eshop_member_id: str = ""

    def to_data(self) -> Dict[str, Any]:
        _validate_goods(self.goods_amount, self.goods_name)
        _validate_https_url(self.server_reply_url, "ServerReplyURL")
        _validate_https_url(self.client_reply_url, "ClientReplyURL")
        if not self.sender_name.strip():
            raise ValueError("SenderName is required")
        if not self.sender_zip_code.strip():
            raise ValueError("SenderZipCode is required")
        if not self.sender_address.strip():
            raise ValueError("SenderAddress is required")
        if self.temperature not in {"0001", "0002", "0003"}:
            raise ValueError("Invalid logistics Temperature")
        if self.specification not in {"0001", "0002", "0003", "0004"}:
            raise ValueError("Invalid logistics Specification")
        if self.temperature in {"0002", "0003"} and self.specification == "0004":
            raise ValueError("Chilled or frozen delivery cannot use 150cm")
        if self.scheduled_pickup_time not in {"1", "2", "4"}:
            raise ValueError("Invalid ScheduledPickupTime")
        if self.enable_select_delivery_time not in {"Y", "N"}:
            raise ValueError("Invalid EnableSelectDeliveryTime")
        if self.receiver_cell_phone and not re.fullmatch(
            r"09\d{8}", self.receiver_cell_phone
        ):
            raise ValueError("ReceiverCellPhone must be a Taiwan mobile number")
        return {
            "TempLogisticsID": self.temp_logistics_id,
            "GoodsAmount": self.goods_amount,
            "IsCollection": "N",
            "GoodsName": self.goods_name.strip(),
            "SenderName": self.sender_name.strip(),
            "SenderZipCode": self.sender_zip_code.strip(),
            "SenderAddress": self.sender_address.strip(),
            "Remark": self.remark.strip(),
            "ServerReplyURL": self.server_reply_url,
            "ClientReplyURL": self.client_reply_url,
            "Temperature": self.temperature,
            "Specification": self.specification,
            "ScheduledPickupTime": self.scheduled_pickup_time,
            "ReceiverAddress": self.receiver_address.strip(),
            "ReceiverCellPhone": self.receiver_cell_phone.strip(),
            "ReceiverPhone": self.receiver_phone.strip(),
            "ReceiverName": self.receiver_name.strip(),
            "EnableSelectDeliveryTime": self.enable_select_delivery_time,
            "EshopMemberID": self.eshop_member_id.strip(),
        }


@dataclass(frozen=True)
class UpdateTempLogisticsRequest:
    temp_logistics_id: str
    goods_amount: Optional[int] = None
    goods_name: Optional[str] = None
    sender_name: Optional[str] = None
    sender_zip_code: Optional[str] = None
    sender_address: Optional[str] = None
    remark: Optional[str] = None
    server_reply_url: Optional[str] = None
    specification: Optional[str] = None
    receiver_address: Optional[str] = None
    receiver_zip_code: Optional[str] = None
    receiver_cell_phone: Optional[str] = None
    receiver_phone: Optional[str] = None
    receiver_name: Optional[str] = None

    def to_data(self) -> Dict[str, Any]:
        if not self.temp_logistics_id.strip():
            raise ValueError("TempLogisticsID is required")
        if (self.goods_amount is None) != (self.goods_name is None):
            raise ValueError(
                "GoodsAmount and GoodsName must be updated together"
            )
        if self.goods_amount is not None and self.goods_name is not None:
            _validate_goods(self.goods_amount, self.goods_name)
        if self.server_reply_url is not None:
            _validate_https_url(self.server_reply_url, "ServerReplyURL")
        if self.specification is not None and self.specification not in {
            "0001",
            "0002",
            "0003",
            "0004",
        }:
            raise ValueError("Invalid logistics Specification")
        if self.receiver_cell_phone and not re.fullmatch(
            r"09\d{8}", self.receiver_cell_phone
        ):
            raise ValueError("ReceiverCellPhone must be a Taiwan mobile number")
        values = {
            "GoodsAmount": self.goods_amount,
            "GoodsName": (
                self.goods_name.strip()
                if self.goods_name is not None
                else None
            ),
            "SenderName": self.sender_name,
            "SenderZipCode": self.sender_zip_code,
            "SenderAddress": self.sender_address,
            "Remark": self.remark,
            "ServerReplyURL": self.server_reply_url,
            "Specification": self.specification,
            "ReceiverAddress": self.receiver_address,
            "ReceiverZipCode": self.receiver_zip_code,
            "ReceiverCellPhone": self.receiver_cell_phone,
            "ReceiverPhone": self.receiver_phone,
            "ReceiverName": self.receiver_name,
        }
        return {
            "TempLogisticsID": self.temp_logistics_id.strip(),
            **{
                key: value.strip() if isinstance(value, str) else value
                for key, value in values.items()
                if value is not None
            },
        }


def _pkcs7_pad(value: bytes, block_size: int = 16) -> bytes:
    padding_length = block_size - (len(value) % block_size)
    return value + bytes([padding_length]) * padding_length


def _pkcs7_unpad(value: bytes, block_size: int = 16) -> bytes:
    if not value:
        raise IntegrationResponseError(
            "Encrypted logistics response was empty"
        )
    padding_length = value[-1]
    if padding_length < 1 or padding_length > block_size:
        raise IntegrationResponseError(
            "Invalid logistics response padding"
        )
    if value[-padding_length:] != bytes([padding_length]) * padding_length:
        raise IntegrationResponseError(
            "Invalid logistics response padding"
        )
    return value[:-padding_length]


def encrypt_ecpay_logistics_data(
    data: Mapping[str, Any],
    hash_key: str,
    hash_iv: str,
) -> str:
    key, iv = _validate_aes_key_iv(hash_key, hash_iv)
    serialized = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    url_encoded = quote(serialized, safe="").encode("utf-8")
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = (
        encryptor.update(_pkcs7_pad(url_encoded))
        + encryptor.finalize()
    )
    return base64.b64encode(encrypted).decode("ascii")


def decrypt_ecpay_logistics_data(
    encrypted_data: str,
    hash_key: str,
    hash_iv: str,
) -> Dict[str, Any]:
    key, iv = _validate_aes_key_iv(hash_key, hash_iv)
    try:
        encrypted = base64.b64decode(encrypted_data, validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
        decoded = unquote(_pkcs7_unpad(decrypted).decode("utf-8"))
        parsed = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationResponseError(
            "Could not decrypt ECPay logistics response"
        ) from exc
    if not isinstance(parsed, dict):
        raise IntegrationResponseError(
            "Logistics Data must decode to a JSON object"
        )
    return parsed


class ECPayLogisticsAdapter:
    def __init__(
        self,
        settings: ECPayLogisticsSettings,
        transport: JsonTransport = post_json,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport

    def build_envelope(
        self,
        data: Mapping[str, Any],
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        envelope: Dict[str, Any] = {
            "MerchantID": self.settings.merchant_id,
            "RqHeader": {
                "Timestamp": str(int(current.timestamp())),
            },
            "Data": encrypt_ecpay_logistics_data(
                data,
                self.settings.hash_key,
                self.settings.hash_iv,
            ),
        }
        if self.settings.platform_id:
            envelope["PlatformID"] = self.settings.platform_id
        return envelope

    async def create_selection_page(
        self,
        request: LogisticsSelectionRequest,
        *,
        now: Optional[datetime] = None,
    ) -> str:
        response = await self.transport(
            self.settings.selection_url,
            self.build_envelope(request.to_data(), now=now),
            None,
            self.settings.timeout_seconds,
        )
        self._validate_http_response(response)
        body = response.body.strip()
        if body.startswith("{"):
            outer = response.json()
            data = self._decode_envelope(outer)
            body = str(
                data.get("HtmlContent")
                or data.get("HTML")
                or data.get("Html")
                or ""
            ).strip()
        if not body or "<" not in body or ">" not in body:
            raise IntegrationResponseError(
                "ECPay logistics selection page returned invalid HTML"
            )
        return body

    async def update_temp_order(
        self,
        request: UpdateTempLogisticsRequest,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        return await self._post_encrypted(
            self.settings.update_temp_url,
            request.to_data(),
            now=now,
        )

    async def create_order(
        self,
        *,
        temp_logistics_id: str,
        merchant_trade_no: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if not temp_logistics_id.strip():
            raise ValueError("TempLogisticsID is required")
        if not MERCHANT_TRADE_NO_PATTERN.fullmatch(merchant_trade_no):
            raise ValueError(
                "MerchantTradeNo must be 1-20 ASCII letters or digits"
            )
        return await self._post_encrypted(
            self.settings.create_url,
            {
                "TempLogisticsID": temp_logistics_id.strip(),
                "MerchantTradeNo": merchant_trade_no,
            },
            now=now,
        )

    async def query_order(
        self,
        *,
        logistics_id: Optional[str] = None,
        merchant_trade_no: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if bool(logistics_id) == bool(merchant_trade_no):
            raise ValueError(
                "Provide exactly one LogisticsID or MerchantTradeNo"
            )
        if merchant_trade_no and not MERCHANT_TRADE_NO_PATTERN.fullmatch(
            merchant_trade_no
        ):
            raise ValueError("Invalid MerchantTradeNo")
        data = {
            "MerchantID": self.settings.merchant_id,
            **(
                {"LogisticsID": logistics_id.strip()}
                if logistics_id
                else {"MerchantTradeNo": merchant_trade_no}
            ),
        }
        return await self._post_encrypted(
            self.settings.query_url,
            data,
            now=now,
        )

    async def create_print_document_page(
        self,
        *,
        logistics_ids: Sequence[str],
        logistics_sub_type: str,
        print_mode: int = 1,
        now: Optional[datetime] = None,
    ) -> str:
        unique_ids = list(dict.fromkeys(logistics_ids))
        if not unique_ids or any(not value.strip() for value in unique_ids):
            raise ValueError("At least one LogisticsID is required")
        if not logistics_sub_type.strip():
            raise ValueError("LogisticsSubType is required")
        if print_mode not in {1, 2}:
            raise ValueError("PrintMode must be 1 or 2")
        response = await self.transport(
            self.settings.print_url,
            self.build_envelope(
                {
                    "MerchantID": self.settings.merchant_id,
                    "LogisticsID": unique_ids,
                    "LogisticsSubType": logistics_sub_type.strip(),
                    "PrintMode": print_mode,
                },
                now=now,
            ),
            None,
            self.settings.timeout_seconds,
        )
        self._validate_http_response(response)
        body = response.body.strip()
        if not body or "<" not in body or ">" not in body:
            raise IntegrationResponseError(
                "ECPay logistics print endpoint returned invalid HTML"
            )
        return body

    def verify_callback(
        self,
        envelope: Mapping[str, Any],
        *,
        now: Optional[datetime] = None,
        max_age_seconds: int = ECPAY_CALLBACK_MAX_AGE_SECONDS,
    ) -> Dict[str, Any]:
        data = self._decode_envelope(
            envelope,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        if str(data.get("MerchantID", "")) != self.settings.merchant_id:
            raise IntegrationResponseError(
                "Unexpected callback Data MerchantID"
            )
        if not data.get("MerchantTradeNo") or not data.get("LogisticsID"):
            raise IntegrationResponseError(
                "Logistics callback is missing order identifiers"
            )
        if data.get("LogisticsStatus") in {None, ""}:
            raise IntegrationResponseError(
                "Logistics callback is missing LogisticsStatus"
            )
        return data

    def decode_selection_result(
        self,
        envelope: Mapping[str, Any],
        *,
        now: Optional[datetime] = None,
        max_age_seconds: int = ECPAY_CALLBACK_MAX_AGE_SECONDS,
    ) -> Dict[str, Any]:
        data = self._decode_envelope(
            envelope,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        if not data.get("TempLogisticsID"):
            raise IntegrationResponseError(
                "Logistics selection result is missing TempLogisticsID"
            )
        if not data.get("LogisticsType") or not data.get("LogisticsSubType"):
            raise IntegrationResponseError(
                "Logistics selection result is missing logistics type"
            )
        return data

    def callback_acknowledgement(
        self,
        *,
        success: bool = True,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        envelope: Dict[str, Any] = {
            "MerchantID": self.settings.merchant_id,
            "RpHeader": {
                "Timestamp": str(int(current.timestamp())),
            },
            "TransCode": 1,
            "TransMsg": "",
            "Data": encrypt_ecpay_logistics_data(
                {
                    "RtnCode": 1 if success else 0,
                    "RtnMsg": "成功" if success else "失敗",
                },
                self.settings.hash_key,
                self.settings.hash_iv,
            ),
        }
        if self.settings.platform_id:
            envelope["PlatformID"] = self.settings.platform_id
        return envelope

    async def _post_encrypted(
        self,
        url: str,
        data: Mapping[str, Any],
        *,
        now: Optional[datetime],
    ) -> Dict[str, Any]:
        response = await self.transport(
            url,
            self.build_envelope(data, now=now),
            None,
            self.settings.timeout_seconds,
        )
        self._validate_http_response(response)
        return self._decode_envelope(response.json())

    def _decode_envelope(
        self,
        envelope: Mapping[str, Any],
        *,
        now: Optional[datetime] = None,
        max_age_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        if str(envelope.get("MerchantID", "")) != self.settings.merchant_id:
            raise IntegrationResponseError(
                "Unexpected ECPay logistics MerchantID"
            )
        try:
            trans_code = int(envelope["TransCode"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrationResponseError(
                "ECPay logistics response has invalid TransCode"
            ) from exc
        if trans_code != 1:
            raise IntegrationResponseError(
                "ECPay logistics rejected the transport"
            )
        if max_age_seconds is not None:
            header = envelope.get("RpHeader") or envelope.get("RqHeader")
            if not isinstance(header, Mapping):
                raise IntegrationResponseError(
                    "Logistics callback is missing timestamp header"
                )
            try:
                timestamp = int(header.get("Timestamp", 0))
            except (TypeError, ValueError) as exc:
                raise IntegrationResponseError(
                    "Logistics callback timestamp is invalid"
                ) from exc
            current = now or datetime.now(timezone.utc)
            if abs(int(current.timestamp()) - timestamp) > max_age_seconds:
                raise IntegrationResponseError(
                    "Logistics callback timestamp is outside the allowed window"
                )
        encrypted_data = envelope.get("Data")
        if not isinstance(encrypted_data, str) or not encrypted_data:
            raise IntegrationResponseError(
                "ECPay logistics response is missing encrypted Data"
            )
        data = decrypt_ecpay_logistics_data(
            encrypted_data,
            self.settings.hash_key,
            self.settings.hash_iv,
        )
        if "RtnCode" in data:
            try:
                rtn_code = int(data["RtnCode"])
            except (TypeError, ValueError) as exc:
                raise IntegrationResponseError(
                    "ECPay logistics Data has invalid RtnCode"
                ) from exc
            if rtn_code != 1:
                raise IntegrationResponseError(
                    "ECPay logistics request failed: {}".format(
                        str(data.get("RtnMsg", ""))[:200]
                    )
                )
        return data

    @staticmethod
    def _validate_http_response(response: HTTPResponse) -> None:
        if response.status_code != 200:
            raise IntegrationResponseError(
                "ECPay logistics returned HTTP {}".format(
                    response.status_code
                )
            )


def _validate_aes_key_iv(hash_key: str, hash_iv: str) -> tuple[bytes, bytes]:
    key = hash_key.encode("utf-8")
    iv = hash_iv.encode("utf-8")
    if len(key) != 16 or len(iv) != 16:
        raise IntegrationConfigurationError(
            "ECPay logistics HashKey and HashIV must each be 16 bytes"
        )
    return key, iv


def _validate_goods(goods_amount: int, goods_name: str) -> None:
    if not 1 <= goods_amount <= 20000:
        raise ValueError("GoodsAmount must be between 1 and 20000")
    if not goods_name.strip():
        raise ValueError("GoodsName is required")
    if GOODS_NAME_FORBIDDEN_PATTERN.search(goods_name):
        raise ValueError("GoodsName contains unsupported characters")
    weighted_length = sum(1 if ord(character) < 128 else 2 for character in goods_name)
    if weighted_length > 50:
        raise ValueError("GoodsName exceeds ECPay's 50-character limit")


def _validate_https_url(value: str, field_name: str) -> None:
    if not value.startswith("https://"):
        raise ValueError(f"{field_name} must use public HTTPS")


def ecpay_logistics_adapter_from_settings(
    settings: Settings,
    transport: JsonTransport = post_json,
) -> ECPayLogisticsAdapter:
    return ECPayLogisticsAdapter(
        ECPayLogisticsSettings(
            merchant_id=settings.ecpay_logistics_merchant_id,
            hash_key=settings.ecpay_logistics_hash_key,
            hash_iv=settings.ecpay_logistics_hash_iv,
            platform_id=settings.ecpay_logistics_platform_id,
            selection_url=settings.ecpay_logistics_selection_url,
            update_temp_url=settings.ecpay_logistics_update_temp_url,
            create_url=settings.ecpay_logistics_create_url,
            query_url=settings.ecpay_logistics_query_url,
            print_url=settings.ecpay_logistics_print_url,
            timeout_seconds=settings.integration_timeout_seconds,
        ),
        transport=transport,
    )
