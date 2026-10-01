from dataclasses import dataclass
from typing import Any, Dict

from ..config import Settings
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


MAILERSEND_EMAIL_URL = "https://api.mailersend.com/v1/email"


@dataclass(frozen=True)
class MailerSendSettings:
    api_token: str
    sender_email: str
    sender_name: str = "十里方圓"
    email_url: str = MAILERSEND_EMAIL_URL
    timeout_seconds: float = 15.0

    def validate(self) -> None:
        if not self.api_token:
            raise IntegrationConfigurationError("MailerSend API token is required")
        if not EMAIL_PATTERN.fullmatch(self.sender_email):
            raise IntegrationConfigurationError("Invalid MailerSend sender email")
        if not self.sender_name.strip():
            raise IntegrationConfigurationError("MailerSend sender name is required")
        if not self.email_url.startswith("https://"):
            raise IntegrationConfigurationError("MailerSend URL must use HTTPS")


class MailerSendAdapter:
    def __init__(
        self,
        settings: MailerSendSettings,
        transport: JsonTransport = post_json,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport

    def build_payload(self, message: EmailMessage) -> Dict[str, Any]:
        message.validate()
        payload: Dict[str, Any] = {
            "from": {
                "email": self.settings.sender_email,
                "name": self.settings.sender_name,
            },
            "to": [{"email": message.to_email}],
            "subject": message.subject[:998],
            "text": message.text_content,
        }
        if message.html_content:
            payload["html"] = message.html_content
        if message.reply_to:
            payload["reply_to"] = {"email": message.reply_to}
        return payload

    async def send(self, message: EmailMessage) -> EmailSendResult:
        response = await self.transport(
            self.settings.email_url,
            self.build_payload(message),
            {"Authorization": f"Bearer {self.settings.api_token}"},
            self.settings.timeout_seconds,
        )
        if response.status_code != 202:
            raise IntegrationResponseError(
                f"MailerSend rejected email with HTTP {response.status_code}"
            )
        message_id = next(
            (
                value
                for key, value in response.headers.items()
                if key.lower() == "x-message-id"
            ),
            None,
        )
        return EmailSendResult(
            True,
            message_id,
            response.status_code,
            provider="mailersend",
        )


def mailersend_adapter_from_settings(settings: Settings) -> MailerSendAdapter:
    return MailerSendAdapter(
        MailerSendSettings(
            api_token=settings.mailersend_api_token,
            sender_email=settings.mailersend_from_email,
            sender_name=settings.mailersend_from_name,
            timeout_seconds=settings.integration_timeout_seconds,
        )
    )
