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


SENDGRID_MAIL_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

JsonTransport = Callable[
    [str, Mapping[str, Any], Optional[Mapping[str, str]], float],
    Awaitable[HTTPResponse],
]


@dataclass(frozen=True)
class SendGridSettings:
    api_key: str
    sender_email: str
    sender_name: str = "十里方圓"
    mail_send_url: str = SENDGRID_MAIL_SEND_URL
    timeout_seconds: float = 15.0
    sandbox_mode: bool = False

    def validate(self) -> None:
        if not self.api_key:
            raise IntegrationConfigurationError("SendGrid API key is required")
        if not EMAIL_PATTERN.fullmatch(self.sender_email):
            raise IntegrationConfigurationError("Invalid SendGrid sender email")
        if not self.sender_name.strip():
            raise IntegrationConfigurationError("SendGrid sender name is required")
        if not self.mail_send_url.startswith("https://"):
            raise IntegrationConfigurationError("SendGrid URL must use HTTPS")


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


class SendGridAdapter:
    def __init__(
        self,
        settings: SendGridSettings,
        transport: JsonTransport = post_json,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.transport = transport

    def build_payload(self, message: EmailMessage) -> Dict[str, Any]:
        message.validate()
        content = [
            {
                "type": "text/plain",
                "value": message.text_content,
            }
        ]
        if message.html_content:
            content.append(
                {
                    "type": "text/html",
                    "value": message.html_content,
                }
            )
        payload: Dict[str, Any] = {
            "personalizations": [
                {
                    "to": [{"email": message.to_email}],
                    "subject": message.subject[:998],
                }
            ],
            "from": {
                "email": self.settings.sender_email,
                "name": self.settings.sender_name,
            },
            "content": content,
        }
        if message.reply_to:
            payload["reply_to"] = {"email": message.reply_to}
        if self.settings.sandbox_mode:
            payload["mail_settings"] = {"sandbox_mode": {"enable": True}}
        return payload

    async def send(self, message: EmailMessage) -> EmailSendResult:
        payload = self.build_payload(message)
        response = await self.transport(
            self.settings.mail_send_url,
            payload,
            {"Authorization": "Bearer {}".format(self.settings.api_key)},
            self.settings.timeout_seconds,
        )
        if response.status_code != 202:
            raise IntegrationResponseError(
                "SendGrid rejected email with HTTP {}".format(response.status_code)
            )
        message_id = None
        for key, value in response.headers.items():
            if key.lower() == "x-message-id":
                message_id = value
                break
        return EmailSendResult(
            accepted=True,
            provider_message_id=message_id,
            status_code=response.status_code,
        )


def sendgrid_adapter_from_settings(settings: Settings) -> SendGridAdapter:
    return SendGridAdapter(
        SendGridSettings(
            api_key=settings.sendgrid_api_key,
            sender_email=settings.sendgrid_from_email,
            sender_name=settings.sendgrid_from_name,
            timeout_seconds=settings.integration_timeout_seconds,
        )
    )
