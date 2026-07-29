from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_JWT_SECRET = "change-this-sandbox-secret"
DEFAULT_RECONCILE_SECRET = "change-this-reconcile-secret"
MIN_RUNTIME_SECRET_LENGTH = 32
MIN_RESET_CONFIRMATION_LENGTH = 8
SECURE_ENVIRONMENTS = {"sandbox", "production"}


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
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = ""
    sendgrid_from_name: str = "十里方圓"
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
        if invalid_secrets:
            raise RuntimeError(
                f"{environment} 環境拒絕啟動："
                f"{'、'.join(invalid_secrets)}。"
            )
        if environment == "sandbox" and (
            not self.ecpay_payment_stage or not self.ecpay_invoice_stage
        ):
            raise RuntimeError(
                "sandbox 環境拒絕啟動："
                "ECPAY_PAYMENT_STAGE 與 ECPAY_INVOICE_STAGE 必須為 true。"
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
