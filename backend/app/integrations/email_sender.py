import re
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
)

from ..config import Settings
from .common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationError,
    IntegrationResponseError,
)


EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?$",
    re.IGNORECASE,
)
JsonTransport = Callable[
    [str, Mapping[str, Any], Optional[Mapping[str, str]], float],
    Awaitable[HTTPResponse],
]


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    subject: str
    text_content: str
    html_content: Optional[str] = None
    reply_to: Optional[str] = None
    idempotency_key: Optional[str] = None

    def validate(self) -> None:
        if not EMAIL_PATTERN.fullmatch(self.to_email):
            raise ValueError("Invalid recipient email")
        if not self.subject.strip():
            raise ValueError("Email subject is required")
        if not self.text_content.strip():
            raise ValueError("Email text content is required")
        if self.reply_to and not EMAIL_PATTERN.fullmatch(self.reply_to):
            raise ValueError("Invalid reply-to email")
        if self.idempotency_key is not None and not (
            1 <= len(self.idempotency_key) <= 256
        ):
            raise ValueError("Invalid email idempotency key")


@dataclass(frozen=True)
class EmailSendResult:
    accepted: bool
    provider_message_id: Optional[str]
    status_code: int
    provider: str = ""


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> EmailSendResult: ...


class FailoverEmailSender:
    def __init__(self, senders: Sequence[EmailSender]) -> None:
        if not senders:
            raise IntegrationConfigurationError(
                "At least one email provider is required"
            )
        self.senders = tuple(senders)

    async def send(self, message: EmailMessage) -> EmailSendResult:
        errors = []
        for sender in self.senders:
            try:
                return await sender.send(message)
            except IntegrationError as exc:
                errors.append(str(exc))
        raise IntegrationResponseError(
            f"All email providers failed: {'; '.join(errors)}"
        )


def email_sender_from_settings(settings: Settings) -> EmailSender:
    from .mailersend import MailerSendAdapter, MailerSendSettings
    from .resend import ResendAdapter, ResendSettings

    sender_email = (
        settings.email_from_email.strip()
        or settings.mailersend_from_email.strip()
    )
    sender_name = settings.email_from_name.strip() or "十里方圓"
    senders: list[EmailSender] = []
    configuration_errors: list[str] = []
    if settings.resend_api_key.strip():
        try:
            senders.append(
                ResendAdapter(
                    ResendSettings(
                        api_key=settings.resend_api_key,
                        sender_email=sender_email,
                        sender_name=sender_name,
                        timeout_seconds=settings.integration_timeout_seconds,
                    )
                )
            )
        except IntegrationConfigurationError as exc:
            configuration_errors.append(f"Resend: {exc}")
    if settings.mailersend_api_token.strip():
        try:
            senders.append(
                MailerSendAdapter(
                    MailerSendSettings(
                        api_token=settings.mailersend_api_token,
                        sender_email=(
                            settings.mailersend_from_email.strip()
                            or sender_email
                        ),
                        sender_name=(
                            settings.mailersend_from_name.strip()
                            or sender_name
                        ),
                        timeout_seconds=settings.integration_timeout_seconds,
                    )
                )
            )
        except IntegrationConfigurationError as exc:
            configuration_errors.append(f"MailerSend: {exc}")
    if not senders:
        detail = "; ".join(configuration_errors)
        raise IntegrationConfigurationError(
            "No usable email provider is configured"
            + (f": {detail}" if detail else "")
        )
    if len(senders) == 1:
        return senders[0]
    return FailoverEmailSender(senders)
