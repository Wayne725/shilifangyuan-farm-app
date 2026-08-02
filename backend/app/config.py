from __future__ import annotations

import base64
import json
from functools import lru_cache
from typing import List

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_JWT_SECRET = "change-this-sandbox-secret"
DEFAULT_RECONCILE_SECRET = "change-this-reconcile-secret"
MIN_RUNTIME_SECRET_LENGTH = 32
MIN_RESET_CONFIRMATION_LENGTH = 8
SECURE_ENVIRONMENTS = {"sandbox", "production"}
R2_REQUIRED_SETTINGS = (
    "CLOUDFLARE_R2_ACCOUNT_ID",
    "CLOUDFLARE_R2_ACCESS_KEY_ID",
    "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
    "CLOUDFLARE_R2_BUCKET",
)


class Settings(BaseSettings):
    app_name: str = "十里方圓 API"
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    debug: bool = False
    api_v1_prefix: str = "/v1"
    database_url: str = "sqlite+aiosqlite:///./shilifangyuan.db"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:8081"])
    internal_reconcile_secret: str = Field(
        default=DEFAULT_RECONCILE_SECRET,
        validation_alias=AliasChoices(
            "RECONCILE_SECRET", "INTERNAL_RECONCILE_SECRET"
        ),
    )
    app_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:8081"
    ecpay_payment_merchant_id: str = ""
    ecpay_payment_hash_key: str = ""
    ecpay_payment_hash_iv: str = ""
    ecpay_payment_stage: bool = True
    ecpay_payment_aio_url: str = (
        "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
    )
    ecpay_payment_query_url: str = (
        "https://payment-stage.ecpay.com.tw/Cashier/QueryTradeInfo/V5"
    )
    ecpay_invoice_merchant_id: str = ""
    ecpay_invoice_hash_key: str = ""
    ecpay_invoice_hash_iv: str = ""
    ecpay_invoice_stage: bool = True
    ecpay_invoice_issue_url: str = (
        "https://einvoice-stage.ecpay.com.tw/B2CInvoice/Issue"
    )
    ecpay_invoice_query_url: str = (
        "https://einvoice-stage.ecpay.com.tw/B2CInvoice/GetIssue"
    )
    ecpay_invoice_barcode_url: str = (
        "https://einvoice-stage.ecpay.com.tw/B2CInvoice/CheckBarcode"
    )
    ecpay_logistics_merchant_id: str = ""
    ecpay_logistics_hash_key: str = ""
    ecpay_logistics_hash_iv: str = ""
    ecpay_logistics_platform_id: str = ""
    ecpay_logistics_stage: bool = True
    ecpay_logistics_selection_url: str = (
        "https://logistics-stage.ecpay.com.tw/Express/v2/"
        "RedirectToLogisticsSelection"
    )
    ecpay_logistics_update_temp_url: str = (
        "https://logistics-stage.ecpay.com.tw/Express/v2/UpdateTempTrade"
    )
    ecpay_logistics_create_url: str = (
        "https://logistics-stage.ecpay.com.tw/Express/v2/CreateByTempTrade"
    )
    ecpay_logistics_query_url: str = (
        "https://logistics-stage.ecpay.com.tw/Express/v2/"
        "QueryLogisticsTradeInfo"
    )
    ecpay_logistics_print_url: str = (
        "https://logistics-stage.ecpay.com.tw/Express/v2/PrintTradeDocument"
    )
    ecpay_logistics_sender_name: str = "十里方圓"
    ecpay_logistics_sender_zip_code: str = "100"
    ecpay_logistics_sender_address: str = "臺北市中正區測試路一號"
    cloudflare_r2_account_id: str = ""
    cloudflare_r2_access_key_id: str = ""
    cloudflare_r2_secret_access_key: str = ""
    cloudflare_r2_bucket: str = ""
    cloudflare_r2_endpoint_url: str = ""
    r2_presigned_put_seconds: int = 300
    r2_presigned_get_seconds: int = 120
    membership_document_max_bytes: int = 8 * 1024 * 1024
    pii_encryption_keys_json: str = ""
    pii_encryption_current_version: str = "v1"
    mailersend_api_token: str = ""
    mailersend_from_email: str = ""
    mailersend_from_name: str = "十里方圓"
    demo_admin_password: str = "admin123"
    demo_member_password: str = "member123"
    demo_nonmember_password: str = "customer123"
    demo_reset_confirmation: str = ""
    payment_reservation_minutes: int = 15
    integration_timeout_seconds: float = 15.0
    default_vote_threshold: int = 10
    default_vote_days: int = 7
    proposal_conversion_hours: int = 48
    late_confirmation_hours: int = 24
    post_confirmation_cancel_minutes: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def default_development_reset_confirmation(self) -> "Settings":
        if (
            self.environment.strip().lower() == "development"
            and not self.demo_reset_confirmation
        ):
            self.demo_reset_confirmation = "RESET"
        return self

    def validate_runtime_secrets(self) -> None:
        environment = self.environment.strip().lower()
        if environment not in SECURE_ENVIRONMENTS:
            return

        invalid_secrets = [
            f"{name}（至少 {MIN_RUNTIME_SECRET_LENGTH} 字元且不可使用預設值）"
            for name, value, default in (
                ("JWT_SECRET", self.jwt_secret, DEFAULT_JWT_SECRET),
                (
                    "RECONCILE_SECRET",
                    self.internal_reconcile_secret,
                    DEFAULT_RECONCILE_SECRET,
                ),
            )
            if value == default or len(value.strip()) < MIN_RUNTIME_SECRET_LENGTH
        ]
        if (
            environment == "sandbox"
            and len(self.demo_reset_confirmation.strip())
            < MIN_RESET_CONFIRMATION_LENGTH
        ):
            invalid_secrets.append(
                "DEMO_RESET_CONFIRMATION"
                f"（至少 {MIN_RESET_CONFIRMATION_LENGTH} 字元）"
            )
        if environment == "sandbox":
            r2_values = {
                "CLOUDFLARE_R2_ACCOUNT_ID": self.cloudflare_r2_account_id,
                "CLOUDFLARE_R2_ACCESS_KEY_ID": self.cloudflare_r2_access_key_id,
                "CLOUDFLARE_R2_SECRET_ACCESS_KEY": (
                    self.cloudflare_r2_secret_access_key
                ),
                "CLOUDFLARE_R2_BUCKET": self.cloudflare_r2_bucket,
            }
            invalid_secrets.extend(
                name for name in R2_REQUIRED_SETTINGS if not r2_values[name].strip()
            )
            if not self.mailersend_api_token.strip():
                invalid_secrets.append("MAILERSEND_API_TOKEN")
            if (
                not self.mailersend_from_email.strip()
                or "@" not in self.mailersend_from_email
            ):
                invalid_secrets.append("MAILERSEND_FROM_EMAIL")
            if not self.ecpay_payment_merchant_id.strip():
                invalid_secrets.append("ECPAY_PAYMENT_MERCHANT_ID")
            if len(self.ecpay_payment_hash_key.encode("utf-8")) != 16:
                invalid_secrets.append("ECPAY_PAYMENT_HASH_KEY（必須為 16 bytes）")
            if len(self.ecpay_payment_hash_iv.encode("utf-8")) != 16:
                invalid_secrets.append("ECPAY_PAYMENT_HASH_IV（必須為 16 bytes）")
            if not self.ecpay_invoice_merchant_id.strip():
                invalid_secrets.append("ECPAY_INVOICE_MERCHANT_ID")
            if len(self.ecpay_invoice_hash_key.encode("utf-8")) != 16:
                invalid_secrets.append(
                    "ECPAY_INVOICE_HASH_KEY（必須為 16 bytes）"
                )
            if len(self.ecpay_invoice_hash_iv.encode("utf-8")) != 16:
                invalid_secrets.append(
                    "ECPAY_INVOICE_HASH_IV（必須為 16 bytes）"
                )
            if not self.ecpay_logistics_merchant_id.strip():
                invalid_secrets.append("ECPAY_LOGISTICS_MERCHANT_ID")
            if len(self.ecpay_logistics_hash_key.encode("utf-8")) != 16:
                invalid_secrets.append("ECPAY_LOGISTICS_HASH_KEY（必須為 16 bytes）")
            if len(self.ecpay_logistics_hash_iv.encode("utf-8")) != 16:
                invalid_secrets.append("ECPAY_LOGISTICS_HASH_IV（必須為 16 bytes）")
            self._validate_pii_encryption_settings(invalid_secrets)
            if (
                not self.ecpay_payment_stage
                or not self.ecpay_invoice_stage
                or not self.ecpay_logistics_stage
            ):
                invalid_secrets.append(
                    "ECPAY_PAYMENT_STAGE、ECPAY_INVOICE_STAGE 與 "
                    "ECPAY_LOGISTICS_STAGE 必須為 true"
                )
        if invalid_secrets:
            raise RuntimeError(
                f"{environment} 環境拒絕啟動："
                f"{'、'.join(invalid_secrets)}。"
            )

    def _validate_pii_encryption_settings(
        self, invalid_secrets: List[str]
    ) -> None:
        try:
            keys = json.loads(self.pii_encryption_keys_json)
        except json.JSONDecodeError:
            keys = None
        if not isinstance(keys, dict) or not keys:
            invalid_secrets.append("PII_ENCRYPTION_KEYS_JSON")
            return
        invalid_keyring = self.pii_encryption_current_version not in keys
        for version, value in keys.items():
            if not isinstance(version, str) or not version.strip():
                invalid_keyring = True
                continue
            try:
                decoded = base64.b64decode(str(value), validate=True)
            except (ValueError, TypeError):
                decoded = b""
            invalid_keyring = invalid_keyring or len(decoded) != 32
        if invalid_keyring:
            invalid_secrets.append(
                "PII_ENCRYPTION_KEYS_JSON"
                "（每一版本都必須是 Base64 編碼的 32-byte 金鑰，"
                "且必須包含目前版本）"
            )

    @property
    def cloudflare_r2_resolved_endpoint_url(self) -> str:
        if self.cloudflare_r2_endpoint_url:
            return self.cloudflare_r2_endpoint_url.rstrip("/")
        if not self.cloudflare_r2_account_id:
            return ""
        return (
            f"https://{self.cloudflare_r2_account_id}."
            "r2.cloudflarestorage.com"
        )

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
