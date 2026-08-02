import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from ..config import Settings
from .common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
    post_json,
)


MAILERSEND_EMAIL_URL = "https://api.mailersend.com/v1/email"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

JsonTransport = Callable[
    [str, Mapping[str, Any], Optional[Mapping[str, str]], float],
    Awaitable[HTTPResponse],
]


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


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    subject: str
    text_content: str
    html_content: Optional[str] = None
    reply_to: Optional[str] = None

    def validate(self) -> None:
        if not EMAIL_PATTERN.fullmatch(self.to_email):
            raise ValueError("Invalid recipient email")
        if not self.subject.strip():
            raise ValueError("Email subject is required")
        if not self.text_content.strip():
            raise ValueError("Email text content is required")
        if self.reply_to and not EMAIL_PATTERN.fullmatch(self.reply_to):
            raise ValueError("Invalid reply-to email")


@dataclass(frozen=True)
class EmailSendResult:
    accepted: bool
    provider_message_id: Optional[str]
    status_code: int


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
        return EmailSendResult(True, message_id, response.status_code)


def mailersend_adapter_from_settings(settings: Settings) -> MailerSendAdapter:
    return MailerSendAdapter(
        MailerSendSettings(
            api_token=settings.mailersend_api_token,
            sender_email=settings.mailersend_from_email,
            sender_name=settings.mailersend_from_name,
            timeout_seconds=settings.integration_timeout_seconds,
        )
    )
