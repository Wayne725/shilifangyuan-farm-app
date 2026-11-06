from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Optional

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import aware
from .models import (GroupCampaign, GroupDecisionStatus, GroupIntakeStatus, InventoryReservation,
                     Notification, Order, PaymentAttempt, PaymentStatus, ReservationStatus, User, UserRole, FulfillmentStatus)
from .models import TargetType
from .integrations.notifications import NotificationCommand, NotificationService, SQLAlchemyNotificationRepository


UNSETTLED_STATUSES = {PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED}
TERMINAL_DECISIONS = {GroupDecisionStatus.CONFIRMED, GroupDecisionStatus.REJECTED,
                     GroupDecisionStatus.CANCELLED, GroupDecisionStatus.FAILED_UNMET,
                     GroupDecisionStatus.EXPIRED_UNCONFIRMED}


def group_contract_fingerprint(campaign: GroupCampaign) -> str:
    core = {name: getattr(campaign, name) for name in (
        "target_type", "target_id", "member_price", "nonmember_price", "min_paid_quantity",
        "supply_cap", "per_user_cap", "can_ship", "shipping_temperature",
    )}
    core["deadline"] = aware(campaign.deadline).astimezone(timezone.utc).isoformat()
    core["allowed_shipping_channels"] = sorted(campaign.allowed_shipping_channels or [])
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def order_matches_group_contract(order: Order, campaign: GroupCampaign | None) -> bool:
    if campaign is None or order.group_campaign_id != campaign.id:
        return False
    saved = getattr(order, "group_contract_fingerprint", None)
    if saved is not None:
        return saved == group_contract_fingerprint(campaign)
    # Historical orders have no full contract; only their saved target identity is provable.
    items = order.__dict__.get("items", [])
    if campaign.target_type == TargetType.PRODUCT:
        return bool(items) and all(item.source_product_id == campaign.target_id and item.source_bundle_id is None for item in items)
    return bool(items) and all(item.source_bundle_id == campaign.target_id and item.source_product_id is None for item in items)


def current_order_attempt(attempt: PaymentAttempt, order: Order) -> bool:
    return (attempt.superseded_at is None and (attempt.order_revision or 1) == (order.revision or 1)
            and attempt.amount == order.amount_total)


def order_accepts_group_payment(order: Order) -> bool:
    return (order.cancelled_at is None and order.fulfillment_status != FulfillmentStatus.CANCELLED
            and order.payment_status not in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED})


def unsettled_attempt(attempt: PaymentAttempt, order: Order) -> bool:
    return (order_accepts_group_payment(order) and current_order_attempt(attempt, order) and attempt.status in UNSETTLED_STATUSES
            and attempt.unpaid_verified_at is None)


def order_has_uncertain_group_payment(order: Order, now: datetime) -> bool:
    return bool(order.group_campaign_id and any(
        unsettled_attempt(attempt, order)
        and (aware(attempt.expires_at) <= now or attempt.settlement_unknown_since is not None)
        for attempt in order.__dict__.get("payment_attempts", [])))


async def campaign_has_unsettled_payments(session: AsyncSession, campaign: GroupCampaign) -> bool:
    rows = (await session.execute(select(PaymentAttempt, Order).join(Order, PaymentAttempt.order_id == Order.id)
        .where(Order.group_campaign_id == campaign.id).execution_options(populate_existing=True))).all()
    if any(unsettled_attempt(attempt, order) for attempt, order in rows):
        return True
    attempts = {attempt.id: (attempt, order) for attempt, order in rows}
    reservations = await session.scalars(select(InventoryReservation).where(
        InventoryReservation.group_campaign_id == campaign.id,
        InventoryReservation.status == ReservationStatus.ACTIVE))
    for reservation in reservations:
        pair = attempts.get(reservation.payment_attempt_id)
        if pair is not None:
            if current_order_attempt(*pair) and order_accepts_group_payment(pair[1]):
                return True
        else:
            order = await session.get(Order, reservation.order_id)
            if order is not None and order_accepts_group_payment(order):
                return True
    return False


async def reconcile_campaign_state(session: AsyncSession, campaign: GroupCampaign, now: datetime) -> None:
    if campaign.decision_status in TERMINAL_DECISIONS:
        if campaign.decision_status == GroupDecisionStatus.CONFIRMED and now >= aware(campaign.deadline):
            campaign.intake_status = GroupIntakeStatus.CLOSED
        return
    if now < aware(campaign.deadline):
        return
    if await campaign_has_unsettled_payments(session, campaign):
        campaign.intake_status = GroupIntakeStatus.SETTLING
    else:
        campaign.decision_status = GroupDecisionStatus.PENDING_CONFIRMATION
        campaign.intake_status = GroupIntakeStatus.CLOSED


def review_blocked_reason(campaign: GroupCampaign, now: datetime) -> Optional[str]:
    if campaign.decision_status in TERMINAL_DECISIONS:
        return "此共同購買已完成決定"
    if now < aware(campaign.deadline):
        return "付款截止後才可確認共同購買"
    if campaign.intake_status != GroupIntakeStatus.CLOSED or campaign.decision_status != GroupDecisionStatus.PENDING_CONFIRMATION:
        return "付款結果仍待核對"
    if campaign.paid_quantity <= 0:
        return "沒有已付款數量，無法確認成團"
    return None


def valid_notification_email(*candidates: Optional[str]) -> Optional[str]:
    for candidate in candidates:
        if not candidate or not candidate.strip():
            continue
        try:
            return validate_email(candidate.strip(), check_deliverability=False).normalized
        except EmailNotValidError:
            continue
    return None


async def publish_campaign_notification_once(session: AsyncSession, command: NotificationCommand) -> bool:
    # Callers hold the campaign row lock; notification and email are committed together.
    existing = await session.scalar(select(Notification.id).where(
        Notification.user_id == command.user_id,
        Notification.event_type == command.event_type,
        Notification.data["dedupe_key"].as_string() == command.dedupe_key).limit(1))
    if existing is not None:
        return False
    await NotificationService(SQLAlchemyNotificationRepository(session)).publish(command)
    return True


async def remind_campaign_review(session: AsyncSession, campaign: GroupCampaign, now: datetime) -> None:
    if campaign.decision_status in TERMINAL_DECISIONS:
        return
    overdue = campaign.confirmation_deadline is not None and now >= aware(campaign.confirmation_deadline)
    settling = campaign.intake_status == GroupIntakeStatus.SETTLING
    if not overdue and not settling and campaign.decision_status != GroupDecisionStatus.PENDING_CONFIRMATION:
        return
    event_type = "group.review_overdue" if overdue else "group.settlement_pending" if settling else "group.review_ready"
    title = "共同購買審核已逾期" if overdue else "共同購買付款待核對" if settling else "共同購買已截止，請審核"
    detail = "仍有付款交易等待金流核對，核對完成後才能確認成案。" if settling else ""

    admins = await session.scalars(select(User).where(User.user_role == UserRole.ADMIN))
    for admin in admins:
        await publish_campaign_notification_once(session, NotificationCommand(
            user_id=admin.id, event_type=event_type, title=title,
            body=f"「{campaign.title}」已付款 {campaign.paid_quantity} 組，成案門檻 {campaign.min_paid_quantity} 組。{detail}逾期不會自動退單或退款。",
            email=valid_notification_email(admin.notification_email, admin.email),
            data={"campaign_id": campaign.id},
            dedupe_key=f"{event_type}:{campaign.id}:{aware(campaign.confirmation_deadline).isoformat() if overdue else 'ready'}"))
