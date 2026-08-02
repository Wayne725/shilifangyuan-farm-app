from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.integrations.common import HTTPResponse, IntegrationResponseError
from app.integrations.ecpay_logistics import (
    ECPayLogisticsAdapter,
    ECPayLogisticsSettings,
    LogisticsSelectionRequest,
    UpdateTempLogisticsRequest,
    decrypt_ecpay_logistics_data,
    encrypt_ecpay_logistics_data,
)
from app.integrations.pii_crypto import VersionedPIICipher
from app.integrations.r2_storage import (
    MAX_MEMBERSHIP_DOCUMENT_BYTES,
    R2DocumentStorage,
    R2StorageSettings,
)


LOGISTICS_MERCHANT_ID = "2000132"
LOGISTICS_HASH_KEY = "5294y06JbISpM5x9"
LOGISTICS_HASH_IV = "v77hoKGq4kWxNNIS"
NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)


def logistics_settings() -> ECPayLogisticsSettings:
    return ECPayLogisticsSettings(
        merchant_id=LOGISTICS_MERCHANT_ID,
        hash_key=LOGISTICS_HASH_KEY,
        hash_iv=LOGISTICS_HASH_IV,
    )


def logistics_response(
    data,
    *,
    now: datetime = NOW,
) -> HTTPResponse:
    body = {
        "MerchantID": LOGISTICS_MERCHANT_ID,
        "RpHeader": {"Timestamp": str(int(now.timestamp()))},
        "TransCode": 1,
        "TransMsg": "",
        "Data": encrypt_ecpay_logistics_data(
            data,
            LOGISTICS_HASH_KEY,
            LOGISTICS_HASH_IV,
        ),
    }
    return HTTPResponse(
        status_code=200,
        body=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )


def test_logistics_aes_matches_official_vector_and_round_trips() -> None:
    encrypted = encrypt_ecpay_logistics_data(
        {"Name": "Test", "ID": "A123456789"},
        LOGISTICS_HASH_KEY,
        LOGISTICS_HASH_IV,
    )

    assert encrypted == (
        "0FKSa0j4InjlU0ewoWpzd9FmU9LVR/8z9Zmh8d+shjJ8fuvlmNxs"
        "xyOQfC2BB4VVPEA/MyAHNjzV6HcAGYXgCw=="
    )
    assert decrypt_ecpay_logistics_data(
        encrypted,
        LOGISTICS_HASH_KEY,
        LOGISTICS_HASH_IV,
    ) == {"Name": "Test", "ID": "A123456789"}


@pytest.mark.asyncio
async def test_logistics_selection_returns_html_and_forces_prepaid() -> None:
    captured = {}

    async def transport(url, payload, headers, timeout):
        captured["url"] = url
        captured["data"] = decrypt_ecpay_logistics_data(
            payload["Data"],
            LOGISTICS_HASH_KEY,
            LOGISTICS_HASH_IV,
        )
        return HTTPResponse(
            status_code=200,
            body="<html><form action='https://logistics-stage.ecpay.com.tw'></form></html>",
            headers={"Content-Type": "text/html"},
        )

    adapter = ECPayLogisticsAdapter(
        logistics_settings(),
        transport=transport,
    )
    html = await adapter.create_selection_page(
        LogisticsSelectionRequest(
            goods_amount=1200,
            goods_name="十里方圓農產",
            sender_name="十里方圓",
            sender_zip_code="100",
            sender_address="臺北市測試路一段一號",
            server_reply_url="https://api.example.test/webhooks/logistics",
            client_reply_url="https://api.example.test/logistics/result",
            temperature="0001",
        ),
        now=NOW,
    )

    assert "<form" in html
    assert captured["url"].endswith("/RedirectToLogisticsSelection")
    assert captured["data"]["IsCollection"] == "N"
    assert captured["data"]["GoodsAmount"] == 1200


@pytest.mark.asyncio
async def test_logistics_temp_formal_query_and_print_use_mocked_network() -> None:
    calls = []

    async def transport(url, payload, headers, timeout):
        data = decrypt_ecpay_logistics_data(
            payload["Data"],
            LOGISTICS_HASH_KEY,
            LOGISTICS_HASH_IV,
        )
        calls.append((url, data))
        if url.endswith("/CreateByTempTrade"):
            return logistics_response(
                {"RtnCode": 1, "RtnMsg": "成功", "LogisticsID": "90001"}
            )
        if url.endswith("/QueryLogisticsTradeInfo"):
            return logistics_response(
                {
                    "RtnCode": 1,
                    "RtnMsg": "成功",
                    "MerchantID": LOGISTICS_MERCHANT_ID,
                    "MerchantTradeNo": "SLF260730000001",
                    "LogisticsID": "90001",
                    "LogisticsStatus": "300",
                }
            )
        if url.endswith("/PrintTradeDocument"):
            return HTTPResponse(
                status_code=200,
                body="<html><body>label</body></html>",
                headers={"Content-Type": "text/html"},
            )
        return logistics_response({"RtnCode": 1, "RtnMsg": "成功"})

    adapter = ECPayLogisticsAdapter(
        logistics_settings(),
        transport=transport,
    )
    updated = await adapter.update_temp_order(
        UpdateTempLogisticsRequest(
            temp_logistics_id="80001",
            goods_amount=1300,
            goods_name="十里方圓農產",
        ),
        now=NOW,
    )
    created = await adapter.create_order(
        temp_logistics_id="80001",
        merchant_trade_no="SLF260730000001",
        now=NOW,
    )
    queried = await adapter.query_order(
        logistics_id="90001",
        now=NOW,
    )
    print_html = await adapter.create_print_document_page(
        logistics_ids=["90001"],
        logistics_sub_type="UNIMART",
        now=NOW,
    )

    assert updated["RtnCode"] == 1
    assert created["LogisticsID"] == "90001"
    assert queried["LogisticsStatus"] == "300"
    assert "label" in print_html
    assert calls[0][1]["TempLogisticsID"] == "80001"
    assert calls[1][1]["MerchantTradeNo"] == "SLF260730000001"
    assert calls[2][1] == {
        "MerchantID": LOGISTICS_MERCHANT_ID,
        "LogisticsID": "90001",
    }


def test_logistics_callback_validates_encryption_merchant_and_age() -> None:
    adapter = ECPayLogisticsAdapter(logistics_settings())
    callback_data = {
        "RtnCode": 1,
        "RtnMsg": "成功",
        "MerchantID": LOGISTICS_MERCHANT_ID,
        "MerchantTradeNo": "SLF260730000001",
        "LogisticsID": "90001",
        "LogisticsStatus": "2063",
    }
    callback = json.loads(logistics_response(callback_data).body)

    assert adapter.verify_callback(callback, now=NOW) == callback_data
    assert adapter.callback_acknowledgement() == {"RtnCode": 1}

    with pytest.raises(IntegrationResponseError, match="timestamp"):
        adapter.verify_callback(
            callback,
            now=NOW + timedelta(minutes=6),
        )

    callback["MerchantID"] = "unexpected"
    with pytest.raises(IntegrationResponseError, match="MerchantID"):
        adapter.verify_callback(callback, now=NOW)


def test_logistics_selection_result_validates_temp_order_and_age() -> None:
    adapter = ECPayLogisticsAdapter(logistics_settings())
    data = {
        "RtnCode": 1,
        "TempLogisticsID": "80001",
        "LogisticsType": "HOME",
        "LogisticsSubType": "TCAT",
    }
    envelope = json.loads(logistics_response(data).body)

    assert adapter.decode_selection_result(envelope, now=NOW) == data
    with pytest.raises(IntegrationResponseError, match="timestamp"):
        adapter.decode_selection_result(
            envelope,
            now=NOW + timedelta(minutes=6),
        )


def test_versioned_pii_cipher_rotates_keys_and_authenticates_context() -> None:
    v1 = base64.b64encode(b"1" * 32).decode("ascii")
    v2 = base64.b64encode(b"2" * 32).decode("ascii")
    original_cipher = VersionedPIICipher({"v1": v1}, "v1")
    encrypted_v1 = original_cipher.encrypt_text(
        "臺北市測試路一號",
        associated_data="member-profile:address:user-1",
    )
    rotated_cipher = VersionedPIICipher({"v1": v1, "v2": v2}, "v2")
    encrypted_v2 = rotated_cipher.encrypt_json(
        {"phone": "0912345678"},
        associated_data="member-profile:contact:user-1",
    )

    assert encrypted_v1.startswith("pii:1:v1:")
    assert rotated_cipher.decrypt_text(
        encrypted_v1,
        associated_data="member-profile:address:user-1",
    ) == "臺北市測試路一號"
    assert rotated_cipher.decrypt_json(
        encrypted_v2,
        associated_data="member-profile:contact:user-1",
    ) == {"phone": "0912345678"}
    with pytest.raises(IntegrationResponseError, match="authenticated"):
        rotated_cipher.decrypt_text(
            encrypted_v1,
            associated_data="another-user",
        )


class FakeR2Client:
    def __init__(self) -> None:
        self.presigned_calls = []
        self.head_response = {}
        self.deleted = []

    def generate_presigned_url(
        self,
        client_method,
        Params,
        ExpiresIn,
        HttpMethod,
    ):
        self.presigned_calls.append(
            (client_method, Params, ExpiresIn, HttpMethod)
        )
        return f"https://r2.example.test/{Params['Key']}?signed=1"

    def head_object(self, **kwargs):
        return self.head_response

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs["Key"])
        return {}

    def delete_objects(self, **kwargs):
        self.deleted.extend(
            item["Key"] for item in kwargs["Delete"]["Objects"]
        )
        return {"Errors": []}


def r2_storage(client: FakeR2Client) -> R2DocumentStorage:
    return R2DocumentStorage(
        R2StorageSettings(
            endpoint_url="https://account.r2.cloudflarestorage.com",
            access_key_id="access-key",
            secret_access_key="secret-key",
            bucket="private-documents",
        ),
        client=client,
    )


@pytest.mark.asyncio
async def test_r2_presigns_random_private_key_and_confirms_head() -> None:
    client = FakeR2Client()
    storage = r2_storage(client)
    checksum = "a" * 64
    ticket = storage.create_upload_ticket(
        content_type="image/jpeg",
        content_length=1234,
        sha256=checksum,
    )
    client.head_response = {
        "ContentType": "image/jpeg",
        "ContentLength": 1234,
        "Metadata": {"sha256": checksum},
        "ETag": '"etag-123"',
    }

    confirmed = await storage.confirm_upload(
        object_key=ticket.object_key,
        expected_content_type="image/jpeg",
        expected_content_length=1234,
        expected_sha256=checksum,
    )
    download_url = storage.create_download_url(ticket.object_key)

    assert ticket.object_key.startswith("membership-documents/")
    assert ticket.object_key.endswith(".jpg")
    assert ticket.expires_in_seconds == 300
    assert ticket.required_headers["x-amz-meta-sha256"] == checksum
    assert "Content-Length" not in ticket.required_headers
    assert "ContentLength" not in client.presigned_calls[0][1]
    assert "signed=1" in ticket.upload_url
    assert confirmed.etag == "etag-123"
    assert client.presigned_calls[0][2:] == (300, "PUT")
    assert client.presigned_calls[1][2:] == (120, "GET")
    assert "signed=1" in download_url


@pytest.mark.asyncio
async def test_r2_rejects_oversize_and_deletes_in_batches() -> None:
    client = FakeR2Client()
    storage = r2_storage(client)

    with pytest.raises(ValueError, match="8 MB"):
        storage.create_upload_ticket(
            content_type="application/pdf",
            content_length=MAX_MEMBERSHIP_DOCUMENT_BYTES + 1,
        )
    with pytest.raises(ValueError, match="JPEG"):
        storage.create_upload_ticket(
            content_type="text/plain",
            content_length=10,
        )

    ticket_a = storage.create_upload_ticket(
        content_type="application/pdf",
        content_length=10,
    )
    ticket_b = storage.create_upload_ticket(
        content_type="image/png",
        content_length=20,
    )
    deleted = await storage.delete_documents(
        [ticket_a.object_key, ticket_b.object_key]
    )

    assert deleted == 2
    assert client.deleted == [ticket_a.object_key, ticket_b.object_key]


def sandbox_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "environment": "sandbox",
        "jwt_secret": "j" * 32,
        "internal_reconcile_secret": "r" * 32,
        "demo_reset_confirmation": "reset-code-strong",
        "ecpay_payment_merchant_id": "3002607",
        "ecpay_payment_hash_key": "pwFHCqoQZGmho4w6",
        "ecpay_payment_hash_iv": "EkRm7iFT261dpevs",
        "ecpay_invoice_merchant_id": "2000132",
        "ecpay_invoice_hash_key": "ejCk326UnaZWKisg",
        "ecpay_invoice_hash_iv": "q9jcZX8Ib9LM8wYk",
        "ecpay_logistics_merchant_id": LOGISTICS_MERCHANT_ID,
        "ecpay_logistics_hash_key": LOGISTICS_HASH_KEY,
        "ecpay_logistics_hash_iv": LOGISTICS_HASH_IV,
        "cloudflare_r2_account_id": "account-id",
        "cloudflare_r2_access_key_id": "access-key",
        "cloudflare_r2_secret_access_key": "secret-key",
        "cloudflare_r2_bucket": "private-documents",
        "sendgrid_api_key": "SG.test-secret",
        "sendgrid_from_email": "verified@example.test",
        "pii_encryption_keys_json": json.dumps(
            {"v1": base64.b64encode(b"p" * 32).decode("ascii")}
        ),
    }
    values.update(overrides)
    return Settings(**values)


def test_sandbox_fails_fast_for_v2_integration_secrets() -> None:
    sandbox_settings().validate_runtime_secrets()

    with pytest.raises(RuntimeError, match="CLOUDFLARE_R2_BUCKET"):
        sandbox_settings(cloudflare_r2_bucket="").validate_runtime_secrets()
    with pytest.raises(RuntimeError, match="ECPAY_LOGISTICS_HASH_KEY"):
        sandbox_settings(
            ecpay_logistics_hash_key="too-short"
        ).validate_runtime_secrets()
    with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEYS_JSON"):
        sandbox_settings(
            pii_encryption_keys_json="{}"
        ).validate_runtime_secrets()
    with pytest.raises(RuntimeError, match="ECPAY_LOGISTICS_STAGE"):
        sandbox_settings(
            ecpay_logistics_stage=False
        ).validate_runtime_secrets()
