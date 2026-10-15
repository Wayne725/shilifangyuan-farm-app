import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Notification, OutboxEvent


EMAIL_EVENT_TYPES = {
    "proposal_approved",
    "proposal_rejected",
    "group_opened",
    "payment_succeeded",
    "refund_completed",
    "refund_fulfillment_intervention",
    "refund_manual_review_required",
    "group_confirmed",
    "group_rejected",
    "group_failed",
    "pickup_finalized",
    "invoice_issued",
    "membership_charge_paid",
    "membership_refund_completed",
    "membership.activated",
    "membership.trainee_started",
    "proposal.approved",
    "proposal.rejected",
    "group.opened",
    "group.confirmed",
    "group.rejected",
    "group.failed_unmet",
    "group.expired_unconfirmed",
    "group.cancelled",
    "order.cancelled",
}


@dataclass(frozen=True)
class NotificationCommand:
    user_id: str
    event_type: str
    title: str
    body: str
    data: Mapping[str, Any] = field(default_factory=dict)
    email: Optional[str] = None
    dedupe_key: Optional[str] = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class NotificationRepository(Protocol):
    async def create_notification(
        self, command: NotificationCommand
    ) -> Any:
        """Persist an in-app notification, deduplicated by dedupe_key."""

    async def enqueue_email_notification(
        self, command: NotificationCommand
    ) -> Any:
        """Persist an email outbox item in the same transaction."""


class NotificationService:
    """Persists in-app notifications and durable email outbox jobs."""

    def __init__(
        self,
        repository: NotificationRepository,
        email_event_types: Optional[Sequence[str]] = None,
    ) -> None:
        self.repository = repository
        self.email_event_types = set(email_event_types or EMAIL_EVENT_TYPES)

    async def publish(self, command: NotificationCommand) -> Any:
        if not command.user_id:
            raise ValueError("Notification user_id is required")
        if not command.event_type.strip():
            raise ValueError("Notification event_type is required")
        if not command.title.strip() or not command.body.strip():
            raise ValueError("Notification title and body are required")

        result = self.repository.create_notification(command)
        if inspect.isawaitable(result):
            result = await result
        if command.email and command.event_type in self.email_event_types:
            queued = self.repository.enqueue_email_notification(command)
            if inspect.isawaitable(queued):
                await queued
        return result

    async def publish_many(
        self, commands: Sequence[NotificationCommand]
    ) -> int:
        count = 0
        for command in commands:
            await self.publish(command)
            count += 1
        return count


class SQLAlchemyNotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_notification(
        self, command: NotificationCommand
    ) -> Notification:
        notification = Notification(
            user_id=command.user_id,
            event_type=command.event_type,
            title=command.title,
            body=command.body,
            data={
                **dict(command.data),
                **(
                    {"dedupe_key": command.dedupe_key}
                    if command.dedupe_key
                    else {}
                ),
            },
            created_at=command.created_at,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def enqueue_email_notification(
        self, command: NotificationCommand
    ) -> OutboxEvent:
        event = OutboxEvent(
            event_type="send_email",
            aggregate_type="user",
            aggregate_id=command.user_id,
            payload={
                "to_email": command.email,
                "subject": command.title,
                "text_content": command.body,
                "event_type": command.event_type,
                "data": dict(command.data),
            },
            available_at=command.created_at,
        )
        self.session.add(event)
        await self.session.flush()
        return event
