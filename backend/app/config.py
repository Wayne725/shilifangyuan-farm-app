from __future__ import annotations

import base64
import json
import re
from functools import lru_cache
from typing import List
from urllib.parse import urlparse
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_JWT_SECRET = "change-this-development-jwt-secret"
DEFAULT_RECONCILE_SECRET = "change-this-development-reconcile-secret"
DEFAULT_DEMO_ADMIN_PASSWORD = "admin123"
DEFAULT_DEMO_MEMBER_PASSWORD = "member123"
DEFAULT_DEMO_NONMEMBER_PASSWORD = "customer123"
MIN_RUNTIME_SECRET_LENGTH = 32
MIN_RESET_CONFIRMATION_LENGTH = 8
KNOWN_RUNTIME_SECRET_PLACEHOLDERS = {
    DEFAULT_JWT_SECRET,
    DEFAULT_RECONCILE_SECRET,
    "replace-with-a-long-random-string",
}
SECURE_ENVIRONMENTS = {"sandbox", "production"}
REMOTE_ENVIRONMENTS = {"preview", *SECURE_ENVIRONMENTS}
DEMO_ENVIRONMENTS = {"preview", "sandbox"}
KNOWN_ENVIRONMENTS = {"development", "test", *REMOTE_ENVIRONMENTS}
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
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=5, ge=0, le=20)
    database_pool_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    database_pool_recycle_seconds: int = Field(default=300, ge=30, le=3600)
    database_connect_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    database_readiness_timeout_seconds: float = Field(default=3.0, ge=1, le=15)
    startup_migration_attempts: int = Field(default=5, ge=1, le=10)
    startup_migration_retry_seconds: float = Field(default=2.0, ge=0.1, le=30)
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=30, ge=5, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    refresh_cookie_name: str = Field(
        default="slf_refresh",
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
    )
    cors_origins: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "http://localhost:8081",
        ]
    )
    internal_reconcile_secret: str = Field(
        default=DEFAULT_RECONCILE_SECRET,
        validation_alias=AliasChoices(
            "RECONCILE_SECRET", "INTERNAL_RECONCILE_SECRET"
        ),
    )
    app_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://127.0.0.1:4173"
    payment_provider: str = "ecpay"
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
    raygate_payment_store_identifier: str = ""
    raygate_payment_key_hex: str = ""
    raygate_payment_iv_hex: str = ""
    raygate_payment_merchant_id: str = ""
    raygate_payment_terminal_id: str = ""
    raygate_payment_device_type: str = ""
    raygate_payment_base_url: str = ""
    raygate_payment_allowed_hostname: str = ""
    raygate_payment_stage: bool = True
    raygate_payment_contract_verified: bool = False
    raygate_payment_reconcile_hours: int = Field(default=24, ge=1, le=168)
    raygate_payment_acceptance_order_id: str = ""
    raygate_payment_acceptance_sku: str = Field(default="", max_length=80)
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
    invoice_provider: str = "ecpay"
    fanyu_invoice_company_id: str = ""
    fanyu_invoice_user_id: str = ""
    fanyu_invoice_auth_password: str = ""
    fanyu_invoice_api_key: str = ""
    fanyu_invoice_seller_id: str = ""
    fanyu_invoice_stage: bool = True
    fanyu_invoice_base_url: str = "https://webtest.einvoice.com.tw/einv"
    fanyu_invoice_signature_verified: bool = False
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
    r2_presigned_put_seconds: int = Field(default=300, ge=60, le=300)
    r2_presigned_get_seconds: int = Field(default=120, ge=30, le=120)
    membership_document_max_bytes: int = 8 * 1024 * 1024
    pii_encryption_keys_json: str = ""
    pii_encryption_current_version: str = "v1"
    resend_api_key: str = ""
    email_from_email: str = ""
    email_from_name: str = "十里方圓"
    mailersend_api_token: str = ""
    mailersend_from_email: str = ""
    mailersend_from_name: str = "十里方圓"
    demo_admin_password: str = DEFAULT_DEMO_ADMIN_PASSWORD
    demo_member_password: str = DEFAULT_DEMO_MEMBER_PASSWORD
    demo_nonmember_password: str = DEFAULT_DEMO_NONMEMBER_PASSWORD
    demo_reset_confirmation: str = ""
    payment_reservation_minutes: int = 15
    reconciliation_enabled: bool = False
    reconciliation_interval_seconds: float = Field(default=60.0, ge=30, le=3600)
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

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        environment = value.strip().lower()
        if environment not in KNOWN_ENVIRONMENTS:
            raise ValueError(
                "APP_ENV 必須是 development、test、preview、sandbox 或 production"
            )
        return environment

    @field_validator("invoice_provider")
    @classmethod
    def validate_invoice_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in {"ecpay", "fanyu"}:
            raise ValueError("INVOICE_PROVIDER 必須是 ecpay 或 fanyu")
        return provider

    @field_validator("payment_provider")
    @classmethod
    def validate_payment_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in {"ecpay", "raygate"}:
            raise ValueError("PAYMENT_PROVIDER 必須是 ecpay 或 raygate")
        return provider

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
        if environment not in REMOTE_ENVIRONMENTS:
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
            if (
                value.strip() in KNOWN_RUNTIME_SECRET_PLACEHOLDERS
                or value == default
                or len(value.strip()) < MIN_RUNTIME_SECRET_LENGTH
            )
        ]
        if self.debug:
            invalid_secrets.append("DEBUG（遠端環境必須為 false）")
        if self.jwt_algorithm != "HS256":
            invalid_secrets.append("JWT_ALGORITHM（目前僅允許 HS256）")
        if self.jwt_secret.strip() == self.internal_reconcile_secret.strip():
            invalid_secrets.append(
                "JWT_SECRET/RECONCILE_SECRET（必須使用兩組不同的隨機值）"
            )
        if environment in DEMO_ENVIRONMENTS:
            demo_passwords = (
                self.demo_admin_password.strip(),
                self.demo_member_password.strip(),
                self.demo_nonmember_password.strip(),
            )
            for name, value, default in (
                (
                    "DEMO_ADMIN_PASSWORD",
                    self.demo_admin_password,
                    DEFAULT_DEMO_ADMIN_PASSWORD,
                ),
                (
                    "DEMO_MEMBER_PASSWORD",
                    self.demo_member_password,
                    DEFAULT_DEMO_MEMBER_PASSWORD,
                ),
                (
                    "DEMO_NONMEMBER_PASSWORD",
                    self.demo_nonmember_password,
                    DEFAULT_DEMO_NONMEMBER_PASSWORD,
                ),
            ):
                if value == default or len(value.strip()) < 12:
                    invalid_secrets.append(
                        f"{name}（至少 12 字元且不可使用預設值）"
                    )
            if len(set(demo_passwords)) != len(demo_passwords):
                invalid_secrets.append(
                    "DEMO_ADMIN_PASSWORD/DEMO_MEMBER_PASSWORD/"
                    "DEMO_NONMEMBER_PASSWORD（必須使用三組不同的密碼）"
                )
        if (
            environment == "sandbox"
            and len(self.demo_reset_confirmation.strip())
            < MIN_RESET_CONFIRMATION_LENGTH
        ):
            invalid_secrets.append(
                "DEMO_RESET_CONFIRMATION"
                f"（至少 {MIN_RESET_CONFIRMATION_LENGTH} 字元）"
            )
        if (
            environment == "sandbox"
            and self.demo_reset_confirmation.strip() in demo_passwords
        ):
            invalid_secrets.append(
                "DEMO_RESET_CONFIRMATION（不可與任何展示登入密碼相同）"
            )
        if environment in REMOTE_ENVIRONMENTS:
            from .integrations.common import (
                IntegrationConfigurationError,
                is_public_https_origin,
            )
            from .integrations.email_sender import email_sender_from_settings
            from .integrations.resend import ResendSettings

            try:
                email_sender_from_settings(self)
            except IntegrationConfigurationError:
                invalid_secrets.append(
                    "RESEND_API_KEY/EMAIL_FROM_EMAIL 或 "
                    "MAILERSEND_API_TOKEN/MAILERSEND_FROM_EMAIL"
                    "（至少一組設定必須完整有效）"
                )
            if environment in {"preview", "production"}:
                try:
                    ResendSettings(
                        api_key=self.resend_api_key.strip(),
                        sender_email=self.email_from_email.strip(),
                        sender_name=self.email_from_name.strip(),
                        timeout_seconds=self.integration_timeout_seconds,
                    ).validate()
                except IntegrationConfigurationError:
                    invalid_secrets.append(
                        "RESEND_API_KEY/EMAIL_FROM_EMAIL"
                        "（Preview／正式環境必須設定可用的 Resend 寄件者）"
                    )
            for name, value in (
                ("APP_BASE_URL", self.app_base_url),
                ("WEB_BASE_URL", self.web_base_url),
            ):
                if not is_public_https_origin(value):
                    invalid_secrets.append(f"{name}（必須為公開 HTTPS 網址）")
        acceptance_order_id = self.raygate_payment_acceptance_order_id.strip()
        acceptance_sku = self.raygate_payment_acceptance_sku.strip()
        if acceptance_order_id and acceptance_sku:
            invalid_secrets.append(
                "RAYGATE_PAYMENT_ACCEPTANCE_ORDER_ID/"
                "RAYGATE_PAYMENT_ACCEPTANCE_SKU（只能擇一）"
            )
        if acceptance_sku and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", acceptance_sku
        ):
            invalid_secrets.append(
                "RAYGATE_PAYMENT_ACCEPTANCE_SKU（格式不正確）"
            )
        if acceptance_order_id or acceptance_sku:
            if environment != "preview":
                invalid_secrets.append(
                    "RAYGATE_PAYMENT_ACCEPTANCE_*"
                    "（小額驗收只允許用於 Preview）"
                )
            if acceptance_order_id:
                try:
                    UUID(acceptance_order_id)
                except ValueError:
                    invalid_secrets.append(
                        "RAYGATE_PAYMENT_ACCEPTANCE_ORDER_ID（必須是訂單 UUID）"
                    )
            if self.payment_provider != "raygate":
                invalid_secrets.append(
                    "PAYMENT_PROVIDER（小額驗收必須使用 raygate）"
                )
            if self.raygate_payment_stage:
                invalid_secrets.append(
                    "RAYGATE_PAYMENT_STAGE（正式小額驗收必須為 false）"
                )
            self._validate_raygate_payment_settings(invalid_secrets)
            from .integrations.raygate import (
                RayGateSettings,
                uses_document_example_credentials,
            )

            if uses_document_example_credentials(
                RayGateSettings(
                    store_identifier=self.raygate_payment_store_identifier,
                    key_hex=self.raygate_payment_key_hex,
                    iv_hex=self.raygate_payment_iv_hex,
                    base_url=self.raygate_payment_base_url,
                    allowed_hostname=self.raygate_payment_allowed_hostname,
                    merchant_id=self.raygate_payment_merchant_id,
                    terminal_id=self.raygate_payment_terminal_id,
                    device_type=self.raygate_payment_device_type,
                    timeout_seconds=self.integration_timeout_seconds,
                )
            ):
                invalid_secrets.append(
                    "RAYGATE_PAYMENT_*（正式小額驗收不可使用規格書範例憑證）"
                )
        if environment in SECURE_ENVIRONMENTS:
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
            if self.payment_provider == "ecpay":
                if not self.ecpay_payment_merchant_id.strip():
                    invalid_secrets.append("ECPAY_PAYMENT_MERCHANT_ID")
                if len(self.ecpay_payment_hash_key.encode("utf-8")) != 16:
                    invalid_secrets.append("ECPAY_PAYMENT_HASH_KEY（必須為 16 bytes）")
                if len(self.ecpay_payment_hash_iv.encode("utf-8")) != 16:
                    invalid_secrets.append("ECPAY_PAYMENT_HASH_IV（必須為 16 bytes）")
            else:
                self._validate_raygate_payment_settings(invalid_secrets)
            if self.invoice_provider == "ecpay":
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
            else:
                for name, value in (
                    ("FANYU_INVOICE_COMPANY_ID", self.fanyu_invoice_company_id),
                    ("FANYU_INVOICE_USER_ID", self.fanyu_invoice_user_id),
                    (
                        "FANYU_INVOICE_AUTH_PASSWORD",
                        self.fanyu_invoice_auth_password,
                    ),
                    ("FANYU_INVOICE_API_KEY", self.fanyu_invoice_api_key),
                    ("FANYU_INVOICE_SELLER_ID", self.fanyu_invoice_seller_id),
                ):
                    if not value.strip():
                        invalid_secrets.append(name)
                if not self.fanyu_invoice_signature_verified:
                    invalid_secrets.append(
                        "FANYU_INVOICE_SIGNATURE_VERIFIED"
                        "（須先用汎宇官方測試向量驗證）"
                    )
            if not self.ecpay_logistics_merchant_id.strip():
                invalid_secrets.append("ECPAY_LOGISTICS_MERCHANT_ID")
            if len(self.ecpay_logistics_hash_key.encode("utf-8")) != 16:
                invalid_secrets.append("ECPAY_LOGISTICS_HASH_KEY（必須為 16 bytes）")
            if len(self.ecpay_logistics_hash_iv.encode("utf-8")) != 16:
                invalid_secrets.append("ECPAY_LOGISTICS_HASH_IV（必須為 16 bytes）")
            self._validate_pii_encryption_settings(invalid_secrets)
            invoice_stage = (
                self.ecpay_invoice_stage
                if self.invoice_provider == "ecpay"
                else self.fanyu_invoice_stage
            )
            payment_stage = (
                self.ecpay_payment_stage
                if self.payment_provider == "ecpay"
                else self.raygate_payment_stage
            )
            payment_stage_name = (
                "ECPAY_PAYMENT_STAGE"
                if self.payment_provider == "ecpay"
                else "RAYGATE_PAYMENT_STAGE"
            )
            invoice_stage_name = (
                "ECPAY_INVOICE_STAGE"
                if self.invoice_provider == "ecpay"
                else "FANYU_INVOICE_STAGE"
            )
            stage_values = (payment_stage, invoice_stage, self.ecpay_logistics_stage)
            if environment == "sandbox" and not all(stage_values):
                invalid_secrets.append(
                    f"{payment_stage_name}、{invoice_stage_name} 與 "
                    "ECPAY_LOGISTICS_STAGE 必須為 true"
                )
            if environment == "production" and any(stage_values):
                invalid_secrets.append(
                    f"{payment_stage_name}、{invoice_stage_name} 與 "
                    "ECPAY_LOGISTICS_STAGE 必須為 false"
                )
            if environment == "production":
                provider_urls = [
                    (
                        "ECPAY_LOGISTICS_SELECTION_URL",
                        self.ecpay_logistics_selection_url,
                    ),
                    (
                        "ECPAY_LOGISTICS_UPDATE_TEMP_URL",
                        self.ecpay_logistics_update_temp_url,
                    ),
                    (
                        "ECPAY_LOGISTICS_CREATE_URL",
                        self.ecpay_logistics_create_url,
                    ),
                    (
                        "ECPAY_LOGISTICS_QUERY_URL",
                        self.ecpay_logistics_query_url,
                    ),
                    (
                        "ECPAY_LOGISTICS_PRINT_URL",
                        self.ecpay_logistics_print_url,
                    ),
                ]
                if self.payment_provider == "ecpay":
                    provider_urls.extend(
                        [
                            ("ECPAY_PAYMENT_AIO_URL", self.ecpay_payment_aio_url),
                            ("ECPAY_PAYMENT_QUERY_URL", self.ecpay_payment_query_url),
                        ]
                    )
                if self.invoice_provider == "ecpay":
                    provider_urls.extend(
                        [
                            (
                                "ECPAY_INVOICE_ISSUE_URL",
                                self.ecpay_invoice_issue_url,
                            ),
                            (
                                "ECPAY_INVOICE_QUERY_URL",
                                self.ecpay_invoice_query_url,
                            ),
                            (
                                "ECPAY_INVOICE_BARCODE_URL",
                                self.ecpay_invoice_barcode_url,
                            ),
                        ]
                    )
                for name, value in provider_urls:
                    parsed = urlparse(value.strip())
                    if (
                        parsed.scheme != "https"
                        or not parsed.netloc
                        or "stage" in parsed.netloc.lower()
                        or not (
                            parsed.hostname == "ecpay.com.tw"
                            or str(parsed.hostname).endswith(".ecpay.com.tw")
                        )
                    ):
                        invalid_secrets.append(
                            f"{name}（正式環境必須使用綠界正式 HTTPS 網址）"
                        )
                if self.payment_provider == "raygate":
                    parsed = urlparse(self.raygate_payment_base_url.strip())
                    if (
                        parsed.scheme != "https"
                        or not parsed.netloc
                        or parsed.path not in {"", "/"}
                        or parsed.query
                        or parsed.fragment
                        or (
                            (parsed.hostname or "").rstrip(".").lower()
                            != self.raygate_payment_allowed_hostname
                            .strip()
                            .rstrip(".")
                            .lower()
                        )
                    ):
                        invalid_secrets.append(
                            "RAYGATE_PAYMENT_BASE_URL"
                            "（正式環境必須使用雷門提供的正式 HTTPS 根網址）"
                        )
                    if not self.raygate_payment_contract_verified:
                        invalid_secrets.append(
                            "RAYGATE_PAYMENT_CONTRACT_VERIFIED"
                            "（須先確認回跳查單、定時補查、狀態與退款契約）"
                        )
                if self.invoice_provider == "fanyu":
                    parsed = urlparse(self.fanyu_invoice_base_url.strip())
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname != "web.einvoice.com.tw"
                        or parsed.query
                        or parsed.fragment
                    ):
                        invalid_secrets.append(
                            "FANYU_INVOICE_BASE_URL"
                            "（正式環境必須使用汎宇正式 HTTPS 網址）"
                        )
                if "測試" in self.ecpay_logistics_sender_address:
                    invalid_secrets.append(
                        "ECPAY_LOGISTICS_SENDER_ADDRESS（不可使用測試地址）"
                    )
            if environment == "production" and not self.async_database_url.startswith(
                "postgresql+asyncpg://"
            ):
                invalid_secrets.append(
                    "DATABASE_URL（正式環境必須使用 PostgreSQL）"
                )
        if invalid_secrets:
            raise RuntimeError(
                f"{environment} 環境拒絕啟動："
                f"{'、'.join(invalid_secrets)}。"
            )

    def _validate_raygate_payment_settings(
        self, invalid_secrets: List[str]
    ) -> None:
        from .integrations.common import is_public_https_origin

        store_identifier = self.raygate_payment_store_identifier.strip()
        if not store_identifier or len(store_identifier) > 50:
            invalid_secrets.append(
                "RAYGATE_PAYMENT_STORE_IDENTIFIER（必填且最多 50 字元）"
            )
        if not self._is_hex_secret(self.raygate_payment_key_hex, 32):
            invalid_secrets.append(
                "RAYGATE_PAYMENT_KEY_HEX（必須為 64 位十六進位字元）"
            )
        if not self._is_hex_secret(self.raygate_payment_iv_hex, 16):
            invalid_secrets.append(
                "RAYGATE_PAYMENT_IV_HEX（必須為 32 位十六進位字元）"
            )
        merchant_id = self.raygate_payment_merchant_id.strip()
        if not merchant_id or len(merchant_id) > 15:
            invalid_secrets.append(
                "RAYGATE_PAYMENT_MERCHANT_ID（必填且最多 15 字元）"
            )
        terminal_id = self.raygate_payment_terminal_id.strip()
        if not terminal_id or len(terminal_id) > 8:
            invalid_secrets.append(
                "RAYGATE_PAYMENT_TERMINAL_ID（必填且最多 8 字元）"
            )
        if len(self.raygate_payment_device_type.strip()) > 50:
            invalid_secrets.append(
                "RAYGATE_PAYMENT_DEVICE_TYPE（最多 50 字元）"
            )
        parsed = urlparse(self.raygate_payment_base_url.strip())
        hostname = (parsed.hostname or "").rstrip(".").lower()
        allowed_hostname = (
            self.raygate_payment_allowed_hostname.strip().rstrip(".").lower()
        )
        if (
            not is_public_https_origin(self.raygate_payment_base_url)
            or not allowed_hostname
            or hostname != allowed_hostname
        ):
            invalid_secrets.append(
                "RAYGATE_PAYMENT_BASE_URL/RAYGATE_PAYMENT_ALLOWED_HOSTNAME"
                "（必須為雷門確認的同一個公開 HTTPS 根網域）"
            )
        from .integrations.raygate import RAYGATE_CALLBACK_URL_MAX_LENGTH

        if self.environment.strip().lower() == "production":
            from .integrations.raygate import (
                RayGateSettings,
                uses_document_example_credentials,
            )

            if uses_document_example_credentials(
                RayGateSettings(
                    store_identifier=store_identifier,
                    key_hex=self.raygate_payment_key_hex,
                    iv_hex=self.raygate_payment_iv_hex,
                    base_url=self.raygate_payment_base_url,
                    allowed_hostname=allowed_hostname,
                    merchant_id=merchant_id,
                    terminal_id=terminal_id,
                    device_type=self.raygate_payment_device_type,
                    timeout_seconds=self.integration_timeout_seconds,
                )
            ):
                invalid_secrets.append(
                    "RAYGATE_PAYMENT_*（正式環境不可使用規格書範例憑證）"
                )

        app_base_url = self.app_base_url.rstrip("/")
        derived_urls = (
            f"{app_base_url}/webhooks/raygate/payment",
            (
                f"{app_base_url}/payments/raygate/result?attempt_id="
                f"{'0' * 36}"
            ),
        )
        if any(
            len(url) > RAYGATE_CALLBACK_URL_MAX_LENGTH
            for url in derived_urls
        ):
            invalid_secrets.append(
                "APP_BASE_URL（組成雷門 callback／return URL 後不可超過 "
                f"{RAYGATE_CALLBACK_URL_MAX_LENGTH} 字元）"
            )

    @staticmethod
    def _is_hex_secret(value: str, expected_bytes: int) -> bool:
        expected_characters = expected_bytes * 2
        return re.fullmatch(
            rf"[0-9a-fA-F]{{{expected_characters}}}", value
        ) is not None

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

    @property
    def refresh_cookie_secure(self) -> bool:
        return self.environment.strip().lower() in REMOTE_ENVIRONMENTS

    @property
    def refresh_cookie_samesite(self) -> str:
        return "none" if self.refresh_cookie_secure else "lax"


@lru_cache
def get_settings() -> Settings:
    return Settings()
