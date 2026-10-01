from dataclasses import dataclass
from typing import Any, Dict

from .common import (
    IntegrationConfigurationError,
    IntegrationResponseError,
    post_json,
)
from .email_sender import (
    EMAIL_PATTERN,
    EmailMessage,
    EmailSendResult,
    JsonTransport,
)


RESEND_EMAIL_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class ResendSettings:
    api_key: str
    sender_email: str
    sender_name: str = "十里方圓"
    email_url: str = RESEND_EMAIL_URL
    timeout_seconds: float = 15.0

    def validate(self) -> None:
        if not self.api_key:
            raise IntegrationConfigurationError("Resend API key is required")
        if not EMAIL_PATTERN.fullmatch(self.sender_email):
            raise IntegrationConfigurationError("Invalid Resend sender email")
        if not self.sender_name.strip():
            raise IntegrationConfigurationError("Resend sender name is required")
        if not self.email_url.startswith("https://"):
            raise IntegrationConfigurationError("Resend URL must use HTTPS")


class ResendAdapter:
    def __init__(
        self,
        settings: ResendSettings,
        transport: JsonTransport = post_json,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport

    def build_payload(self, message: EmailMessage) -> Dict[str, Any]:
        message.validate()
        payload: Dict[str, Any] = {
            "from": (
                f"{self.settings.sender_name} <{self.settings.sender_email}>"
            ),
            "to": [message.to_email],
            "subject": message.subject[:998],
            "text": message.text_content,
        }
        if message.html_content:
            payload["html"] = message.html_content
        if message.reply_to:
            payload["reply_to"] = message.reply_to
        return payload

    async def send(self, message: EmailMessage) -> EmailSendResult:
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key
        response = await self.transport(
            self.settings.email_url,
            self.build_payload(message),
            headers,
            self.settings.timeout_seconds,
        )
        if not 200 <= response.status_code < 300:
            raise IntegrationResponseError(
                f"Resend rejected email with HTTP {response.status_code}"
            )
        body = response.json()
        message_id = body.get("id") if isinstance(body, dict) else None
        if not message_id:
            raise IntegrationResponseError(
                "Resend accepted email without a message id"
            )
        return EmailSendResult(
            True,
            str(message_id),
            response.status_code,
            provider="resend",
        )
