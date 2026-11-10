import hashlib
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol, Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import Notification, OutboxEvent, new_id


EMAIL_EVENT_TYPES = {
    "meal.business_payment_reminder",
    "group.settlement_pending",
    "group.review_ready",
    "group.review_overdue",
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
        self._notification_ids: dict[int, str] = {}

    def _insert(self, model):
        factory = postgres_insert if self.session.get_bind().dialect.name == "postgresql" else sqlite_insert
        return factory(model)

    async def create_notification(
        self, command: NotificationCommand
    ) -> Notification:
        values = dict(
            id=new_id(),
            user_id=command.user_id,
            event_type=command.event_type,
            dedupe_hash=(hashlib.sha256(command.dedupe_key.encode("utf-8")).hexdigest()
                if command.dedupe_key is not None else None),
            delivery_version=1,
            title=command.title,
            body=command.body,
            data={
                **dict(command.data),
                **(
                    {"dedupe_key": command.dedupe_key}
                    if command.dedupe_key is not None
                    else {}
                ),
            },
            created_at=command.created_at,
        )
        if values["dedupe_hash"] is None:
            notification = Notification(**values)
            self.session.add(notification)
            await self.session.flush()
        else:
            notification_id = await self.session.scalar(self._insert(Notification).values(**values)
                .on_conflict_do_nothing(index_elements=["user_id", "event_type", "dedupe_hash"])
                .returning(Notification.id))
            if notification_id:
                notification = await self.session.get(Notification, notification_id)
            else:
                notification = await self.session.scalar(select(Notification).where(
                    Notification.user_id == command.user_id, Notification.event_type == command.event_type,
                    Notification.dedupe_hash == values["dedupe_hash"]))
        self._notification_ids[id(command)] = notification.id
        return notification

    async def enqueue_email_notification(
        self, command: NotificationCommand
    ) -> Optional[OutboxEvent]:
        notification = await self.session.get(Notification, self._notification_ids.pop(id(command)))
        if notification.delivery_version == 0:
            # Historical email jobs have no reliable notification link; never guess or resend.
            return None
        event_id = await self.session.scalar(self._insert(OutboxEvent).values(
            id=new_id(),
            notification_id=notification.id,
            event_type="send_email",
            aggregate_type="user",
            aggregate_id=command.user_id,
            payload={
                "to_email": command.email,
                "subject": notification.title,
                "text_content": notification.body,
                "event_type": command.event_type,
                "data": {key: value for key, value in notification.data.items() if key != "dedupe_key"},
            },
            available_at=command.created_at,
        ).on_conflict_do_nothing(index_elements=["notification_id"]).returning(OutboxEvent.id))
        if event_id:
            return await self.session.get(OutboxEvent, event_id)
        return await self.session.scalar(select(OutboxEvent).where(OutboxEvent.notification_id == notification.id))
