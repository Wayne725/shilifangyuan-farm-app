from __future__ import annotations

import hmac
import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .config import Settings, get_settings
from .database import SessionLocal, get_session
from .domain import apply_proposal_clock, remove_paid_quantity
from .meal_schedules import generate_scheduled_meal_events
from .integrations.common import IntegrationError
from .integrations.invoice_service import (
    enqueue_invoice_adjustment_after_refund,
    enqueue_invoice_issue,
    invoice_adapter_from_settings,
    issue_paid_order_invoice,
)
from .integrations.notifications import (
    NotificationCommand,
    NotificationService,
    SQLAlchemyNotificationRepository,
)
from .integrations.payment_service import (
    PaymentApplicationError,
    SQLAlchemyPaymentCallbackRepository,
    payment_adapter_from_settings,
    remaining_subject_payment_status,
    refund_adapter_from_settings,
    release_attempt_reservations,
    reverse_order_purchase_points,
)
from .integrations.pii_crypto import decrypt_auth_outbox_credential
from .integrations.email_sender import (
    EmailMessage,
    email_sender_from_settings,
)
from .models import (
    Activity,
    ActivityRegistrationStatus,
    ActivityStatus,
    FulfillmentState,
    FulfillmentStatus,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InvoiceStatus,
    MealEvent,
    MealEventStatus,
    MembershipCharge,
    MembershipChargeStatus,
    MemberProposal,
    MemberProposalStatus,
    Order,
    OrderFulfillment,
    OutboxEvent,
    OutboxStatus,
    PaymentAttempt,
    PaymentStatus,
    ProposalStatus,
    Refund,
    RefundStatus,
    ReservationStatus,
    User,
    UserRole,
    Vote,
    VoteProposal,
)
from .v2_domain import apply_member_proposal_clock


# Uvicorn configures its own logger, not the root app logger.
logger = logging.getLogger("uvicorn.error").getChild(__name__)


@dataclass
class ReconcileReport:
    payment_attempts: int = 0
    proposals: int = 0
    campaigns: int = 0
    member_proposals: int = 0
    activities: int = 0
    meal_events: int = 0
    meal_events_created: int = 0
    outbox_completed: int = 0
    outbox_failed: int = 0

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


jobs_router = APIRouter(tags=["jobs"])
_lazy_reconcile_lock = asyncio.Lock()
_last_lazy_reconcile_at = 0.0
_background_reconcile_task: Optional["asyncio.Task[None]"] = None


@jobs_router.post("/internal/reconcile")
async def reconcile_endpoint(
    x_reconcile_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Dict[str, int]:
    if not hmac.compare_digest(
        x_reconcile_secret, settings.internal_reconcile_secret
    ):
        raise HTTPException(status_code=401, detail="Reconcile secret 無效")
    return (await reconcile_once(session, settings)).to_dict()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def reconcile_once(
    session: AsyncSession,
    settings: Optional[Settings] = None,
    limit: int = 100,
    now: Optional[datetime] = None,
) -> ReconcileReport:
    active_settings = settings or get_settings()
    current = now or datetime.now(timezone.utc)
    report = ReconcileReport()
    report.payment_attempts = await _reconcile_expired_payments(
        session, active_settings, current, limit
    )
    report.proposals = await _reconcile_proposals(session, current, limit)
    report.campaigns = await _reconcile_campaigns(
        session, active_settings, current, limit
    )
    report.member_proposals = await _reconcile_member_proposals(
        session,
        current,
        limit,
    )
    report.activities = await _reconcile_activities(
        session,
        current,
        limit,
    )
    report.meal_events = await _reconcile_meal_events(
        session,
        current,
        limit,
    )
    report.meal_events_created = len(await generate_scheduled_meal_events(session, current, limit=limit))
    completed, failed = await _process_outbox(
        session, active_settings, current, limit
    )
    report.outbox_completed = completed
    report.outbox_failed = failed
    return report


async def run_reconcile_job(
    settings: Optional[Settings] = None,
    limit: int = 100,
) -> Dict[str, int]:
    async with SessionLocal() as session:
        report = await reconcile_once(session, settings, limit)
        return report.to_dict()


async def lazy_reconcile(
    session: AsyncSession,
    settings: Optional[Settings] = None,
    minimum_interval_seconds: float = 60.0,
) -> Optional[Dict[str, int]]:
    """Request-time fallback when the external scheduler or Render was asleep."""
    global _last_lazy_reconcile_at
    current_monotonic = time.monotonic()
    if current_monotonic - _last_lazy_reconcile_at < minimum_interval_seconds:
        return None
    async with _lazy_reconcile_lock:
        current_monotonic = time.monotonic()
        if current_monotonic - _last_lazy_reconcile_at < minimum_interval_seconds:
            return None
        report = await reconcile_once(session, settings)
        _last_lazy_reconcile_at = current_monotonic
        logger.info("reconcile_completed counts=%s", report.to_dict())
        return report.to_dict()


def should_reconcile_now(minimum_interval_seconds: float = 60.0) -> bool:
    """Cheap, non-blocking throttle check for the request middleware."""
    if (
        _background_reconcile_task is not None
        and not _background_reconcile_task.done()
    ):
        return False
    return (
        time.monotonic() - _last_lazy_reconcile_at >= minimum_interval_seconds
    )


def schedule_background_reconcile(
    settings: Optional[Settings] = None,
    minimum_interval_seconds: float = 60.0,
) -> "asyncio.Task[None]":
    """Runs reconciliation off the request path.

    The task reference is held until completion so the event loop cannot
    garbage-collect a still-running reconciliation.
    """

    global _background_reconcile_task
    if (
        _background_reconcile_task is not None
        and not _background_reconcile_task.done()
    ):
        if minimum_interval_seconds <= 0:
            previous_task = _background_reconcile_task

            async def _run_after_current() -> None:
                global _background_reconcile_task
                try:
                    await previous_task
                    async with SessionLocal() as session:
                        try:
                            await lazy_reconcile(
                                session,
                                settings,
                                minimum_interval_seconds=0,
                            )
                        except Exception as exc:
                            await session.rollback()
                            logger.error("reconcile_failed error_type=%s", type(exc).__name__)
                finally:
                    if _background_reconcile_task is asyncio.current_task():
                        _background_reconcile_task = None

            follow_up = asyncio.create_task(_run_after_current())
            _background_reconcile_task = follow_up
            return follow_up
        return _background_reconcile_task

    async def _run() -> None:
        global _background_reconcile_task
        try:
            async with SessionLocal() as session:
                try:
                    await lazy_reconcile(
                        session,
                        settings,
                        minimum_interval_seconds=minimum_interval_seconds,
                    )
                except Exception as exc:
                    await session.rollback()
                    logger.error("reconcile_failed error_type=%s", type(exc).__name__)
        finally:
            if _background_reconcile_task is asyncio.current_task():
                _background_reconcile_task = None

    task = asyncio.create_task(_run())
    _background_reconcile_task = task
    return task


@asynccontextmanager
async def periodic_reconciliation(settings: Settings):
    if not settings.reconciliation_enabled:
        yield
        return
    stop = asyncio.Event()

    async def run():
        logger.info("reconcile_scheduler_started interval_seconds=%s", settings.reconciliation_interval_seconds)
        while not stop.is_set():
            try:
                await schedule_background_reconcile(
                    settings, minimum_interval_seconds=settings.reconciliation_interval_seconds,
                )
            except Exception as exc:
                logger.error("reconcile_scheduler_failed error_type=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.reconciliation_interval_seconds)
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(run(), name="periodic-reconciliation")
    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=settings.integration_timeout_seconds + 5)
        except asyncio.TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        logger.info("reconcile_scheduler_stopped")


async def _reconcile_expired_payments(
    session: AsyncSession,
    settings: Settings,
    now: datetime,
    limit: int,
) -> int:
    raygate_late_payment_cutoff = now - timedelta(
        hours=settings.raygate_payment_reconcile_hours
    )
    due = or_(PaymentAttempt.next_reconcile_at.is_(None), PaymentAttempt.next_reconcile_at <= now)
    attempt_ids = list(
        await session.scalars(
            select(PaymentAttempt.id)
            .where(
                due,
                or_(
                    (
                        (PaymentAttempt.status == PaymentStatus.PENDING)
                        & (
                            (PaymentAttempt.provider == "raygate")
                            | (PaymentAttempt.expires_at <= now)
                        )
                    ),
                    (
                        (PaymentAttempt.provider == "raygate")
                        & (
                            PaymentAttempt.status.in_(
                                {
                                    PaymentStatus.EXPIRED,
                                    PaymentStatus.FAILED,
                                }
                            )
                        )
                        & (
                            PaymentAttempt.expires_at
                            >= raygate_late_payment_cutoff
                        )
                    ),
                )
            )
            .order_by(
                func.coalesce(PaymentAttempt.next_reconcile_at, PaymentAttempt.created_at),
                case(
                    (PaymentAttempt.status == PaymentStatus.PENDING, 0),
                    else_=1,
                ),
                PaymentAttempt.expires_at,
                PaymentAttempt.id,
            )
            .limit(limit)
        )
    )
    if not attempt_ids:
        return 0
    processed = 0
    for attempt_id in attempt_ids:
        claim_time = max(now, datetime.now(timezone.utc))
        claimed = await session.execute(
            update(PaymentAttempt)
            .where(
                PaymentAttempt.id == attempt_id,
                PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED}),
                due,
            )
            .values(next_reconcile_at=claim_time + timedelta(seconds=settings.reconciliation_interval_seconds))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if not claimed.rowcount:
            continue
        attempt = await session.get(PaymentAttempt, attempt_id, populate_existing=True)
        if attempt is None or attempt.status not in {
            PaymentStatus.PENDING,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }:
            continue
        attempt_status = attempt.status
        attempt_provider = attempt.provider
        attempt_expires_at = _aware(attempt.expires_at)
        try:
            adapter = (
                payment_adapter_from_settings(settings)
                if attempt_provider == settings.payment_provider
                else payment_adapter_from_settings(settings, attempt_provider)
            )
            query_result = await adapter.query_order(attempt.merchant_trade_no)
        except (IntegrationError, ValueError):
            await session.rollback()
            if (
                attempt_status == PaymentStatus.PENDING
                and attempt_expires_at <= now
            ):
                locked_attempt = await session.scalar(
                    select(PaymentAttempt)
                    .where(
                        PaymentAttempt.id == attempt_id,
                        PaymentAttempt.status == PaymentStatus.PENDING,
                    )
                    .options(selectinload(PaymentAttempt.reservations))
                    .with_for_update()
                )
                if locked_attempt is not None:
                    await release_attempt_reservations(session, locked_attempt)
                    locked_attempt.status = PaymentStatus.EXPIRED
                    await session.commit()
                    processed += 1
            continue
        if (
            query_result.get("TradeStatus") == "1"
            or (
                attempt_provider == "raygate"
                and query_result.get("PaymentDisposition") == "refunded"
            )
        ):
            repository = SQLAlchemyPaymentCallbackRepository(session)
            try:
                await repository.apply_query_result(attempt, query_result)
            except (
                IntegrationError,
                PaymentApplicationError,
                ValueError,
                IntegrityError,
            ):
                await session.rollback()
                logger.exception(
                    "payment_reconcile_apply_failed attempt_id=%s provider=%s",
                    attempt_id,
                    attempt_provider,
                )
                continue
            processed += 1
            continue

        if attempt_provider == "raygate" and attempt_expires_at > now:
            repository = SQLAlchemyPaymentCallbackRepository(session)
            try:
                await repository.apply_query_result(attempt, query_result)
            except (
                IntegrationError,
                PaymentApplicationError,
                ValueError,
                IntegrityError,
            ):
                await session.rollback()
                logger.exception(
                    "payment_reconcile_apply_failed attempt_id=%s provider=%s",
                    attempt_id,
                    attempt_provider,
                )
                continue
            processed += 1
            continue

        if attempt_status in {PaymentStatus.EXPIRED, PaymentStatus.FAILED}:
            continue

        locked_attempt = await session.scalar(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.id == attempt_id,
                PaymentAttempt.status == PaymentStatus.PENDING,
            )
            .options(selectinload(PaymentAttempt.reservations))
            .with_for_update()
        )
        if locked_attempt is None:
            continue
        await release_attempt_reservations(session, locked_attempt)
        locked_attempt.status = PaymentStatus.EXPIRED
        locked_attempt.provider_response = dict(query_result)
        await session.commit()
        processed += 1
    return processed


async def _reconcile_proposals(
    session: AsyncSession,
    now: datetime,
    limit: int,
) -> int:
    proposals = list(
        await session.scalars(
            select(VoteProposal)
            .where(
                or_(
                    (
                        (VoteProposal.status == ProposalStatus.VOTING)
                        & (VoteProposal.deadline <= now)
                    ),
                    (
                        (
                            VoteProposal.status
                            == ProposalStatus.CONVERSION_PENDING
                        )
                        & (VoteProposal.conversion_deadline <= now)
                    ),
                )
            )
            .order_by(VoteProposal.deadline)
            .limit(limit)
            .with_for_update()
        )
    )
    changed = 0
    notification_service = NotificationService(
        SQLAlchemyNotificationRepository(session)
    )
    for proposal in proposals:
        vote_count = int(
            await session.scalar(
                select(func.count(Vote.id)).where(
                    Vote.proposal_id == proposal.id
                )
            )
            or 0
        )
        previous_status = proposal.status
        if not apply_proposal_clock(proposal, vote_count, now):
            continue
        changed += 1
        if proposal.status == ProposalStatus.CONVERSION_PENDING:
            title = "團購投票已達門檻"
            body = "{} 已結束投票，等待管理員正式開團。".format(
                proposal.target_name_snapshot
            )
        elif proposal.status == ProposalStatus.ENDED_UNMET:
            title = "團購投票未達門檻"
            body = "{} 本次未達成投票門檻。".format(
                proposal.target_name_snapshot
            )
        else:
            title = "團購提案已逾開團期限"
            body = "{} 未在期限內轉成正式團購。".format(
                proposal.target_name_snapshot
            )
        user = await session.get(User, proposal.proposer_id)
        await notification_service.publish(
            NotificationCommand(
                user_id=proposal.proposer_id,
                event_type="proposal.clock_changed",
                title=title,
                body=body,
                data={
                    "proposal_id": proposal.id,
                    "status": proposal.status.value,
                    "previous_status": previous_status.value,
                },
                email=user.email if user else None,
                dedupe_key="proposal-clock:{}:{}".format(
                    proposal.id, proposal.status.value
                ),
            )
        )
    await session.commit()
    return changed


async def _reconcile_member_proposals(
    session: AsyncSession,
    now: datetime,
    limit: int,
) -> int:
    proposals = list(
        await session.scalars(
            select(MemberProposal)
            .where(
                or_(
                    (
                        MemberProposal.status
                        == MemberProposalStatus.DISCUSSION
                    )
                    & (MemberProposal.discussion_ends_at <= now),
                    (
                        MemberProposal.status == MemberProposalStatus.VOTING
                    )
                    & (MemberProposal.voting_ends_at <= now),
                )
            )
            .options(selectinload(MemberProposal.votes))
            .order_by(MemberProposal.created_at)
            .limit(limit)
            .with_for_update()
        )
    )
    changed = 0
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    for proposal in proposals:
        previous = proposal.status
        if not apply_member_proposal_clock(proposal, proposal.votes, now):
            continue
        changed += 1
        if proposal.status in {
            MemberProposalStatus.PASSED,
            MemberProposalStatus.REJECTED,
        }:
            user = await session.get(User, proposal.created_by_id)
            if user is not None:
                await service.publish(
                    NotificationCommand(
                        user_id=user.id,
                        event_type="member_proposal.result",
                        title=(
                            "社員提案表決通過"
                            if proposal.status
                            == MemberProposalStatus.PASSED
                            else "社員提案表決未通過"
                        ),
                        body=f"「{proposal.title}」表決已結束。",
                        data={
                            "member_proposal_id": proposal.id,
                            "status": proposal.status.value,
                            "previous_status": previous.value,
                        },
                        email=user.email,
                        dedupe_key=(
                            f"member-proposal:{proposal.id}:"
                            f"{proposal.status.value}"
                        ),
                    )
                )
    await session.commit()
    return changed


async def _reconcile_activities(
    session: AsyncSession,
    now: datetime,
    limit: int,
) -> int:
    activities = list(
        await session.scalars(
            select(Activity)
            .where(
                Activity.status == ActivityStatus.PUBLISHED,
                Activity.ends_at <= now,
            )
            .options(selectinload(Activity.registrations))
            .order_by(Activity.ends_at)
            .limit(limit)
            .with_for_update()
        )
    )
    for activity in activities:
        activity.status = ActivityStatus.COMPLETED
        for registration in activity.registrations:
            if (
                registration.status
                == ActivityRegistrationStatus.REGISTERED
            ):
                registration.status = ActivityRegistrationStatus.NO_SHOW
    await session.commit()
    return len(activities)


async def _reconcile_meal_events(
    session: AsyncSession,
    now: datetime,
    limit: int,
) -> int:
    events = list(
        await session.scalars(
            select(MealEvent)
            .where(
                or_(
                    MealEvent.status.in_([
                        MealEventStatus.PUBLISHED, MealEventStatus.ORDERING_CLOSED,
                    ]) & (MealEvent.pickup_starts_at <= now),
                    (
                        MealEvent.status == MealEventStatus.PUBLISHED
                    )
                    & (MealEvent.ordering_ends_at <= now),
                    (
                        MealEvent.status.in_(
                            [
                                MealEventStatus.ORDERING_CLOSED,
                                MealEventStatus.PICKUP_OPEN,
                            ]
                        )
                    )
                    & (MealEvent.pickup_ends_at <= now),
                )
            )
            .order_by(MealEvent.pickup_ends_at)
            .limit(limit)
            .with_for_update()
        )
    )
    changed = 0
    for event in events:
        if _aware(event.pickup_ends_at) > now:
            if _aware(event.pickup_starts_at) <= now:
                event.status = MealEventStatus.PICKUP_OPEN
                ready_orders = list(await session.scalars(
                    select(Order).where(
                        Order.meal_event_id == event.id,
                        Order.payment_status == PaymentStatus.PAID,
                        Order.cancelled_at.is_(None),
                    ).options(selectinload(Order.fulfillment)).with_for_update()
                ))
                for order in ready_orders:
                    if order.fulfillment is not None and order.fulfillment.status in {
                        FulfillmentState.PENDING_CONFIRMATION, FulfillmentState.PREPARING,
                    }:
                        order.fulfillment.status = FulfillmentState.READY_FOR_PICKUP
                        order.fulfillment_status = FulfillmentStatus.READY_FOR_PICKUP
            elif event.status == MealEventStatus.PUBLISHED and _aware(event.ordering_ends_at) <= now:
                event.status = MealEventStatus.ORDERING_CLOSED
            changed += 1
            continue
        orders = list(
            await session.scalars(
                select(Order)
                .where(
                    Order.meal_event_id == event.id,
                    Order.payment_status == PaymentStatus.PAID,
                )
                .options(selectinload(Order.fulfillment))
                .with_for_update()
            )
        )
        for order in orders:
            if order.fulfillment is None or order.fulfillment.status in {
                FulfillmentState.PICKED_UP,
                FulfillmentState.NO_SHOW,
                FulfillmentState.CANCELLED,
            }:
                continue
            order.fulfillment.status = FulfillmentState.NO_SHOW
            order.fulfillment.fulfilled_at = now
            order.fulfillment_status = FulfillmentStatus.PICKED_UP
            enqueue_invoice_issue(
                session,
                order,
                trigger="legacy_meal_no_show",
            )
        event.status = MealEventStatus.COMPLETED
        changed += 1
    await session.commit()
    return changed


async def _reconcile_campaigns(
    session: AsyncSession,
    settings: Settings,
    now: datetime,
    limit: int,
) -> int:
    campaigns = list(
        await session.scalars(
            select(GroupCampaign)
            .where(
                or_(
                    (
                        GroupCampaign.decision_status
                        == GroupDecisionStatus.RECRUITING
                    )
                    & (GroupCampaign.deadline <= now),
                    (
                        GroupCampaign.decision_status
                        == GroupDecisionStatus.PENDING_CONFIRMATION
                    )
                    & (GroupCampaign.confirmation_deadline <= now),
                    (
                        GroupCampaign.decision_status
                        == GroupDecisionStatus.CONFIRMED
                    )
                    & (GroupCampaign.deadline <= now)
                    & (GroupCampaign.intake_status == GroupIntakeStatus.OPEN),
                    GroupCampaign.intake_status == GroupIntakeStatus.SETTLING,
                )
            )
            .order_by(GroupCampaign.deadline)
            .limit(limit)
            .with_for_update()
        )
    )
    changed = 0
    notification_service = NotificationService(
        SQLAlchemyNotificationRepository(session)
    )
    for campaign in campaigns:
        if (
            campaign.decision_status == GroupDecisionStatus.CONFIRMED
            and _aware(campaign.deadline) <= now
        ):
            campaign.intake_status = GroupIntakeStatus.CLOSED
            changed += 1
            continue
        if (
            campaign.decision_status
            == GroupDecisionStatus.PENDING_CONFIRMATION
            and campaign.confirmation_deadline is not None
            and _aware(campaign.confirmation_deadline) <= now
        ):
            campaign.decision_status = GroupDecisionStatus.EXPIRED_UNCONFIRMED
            campaign.intake_status = GroupIntakeStatus.CLOSED
            await _enqueue_campaign_refunds(session, campaign)
            await _notify_campaign_outcome(
                session,
                notification_service,
                campaign,
                "group.expired_unconfirmed",
                "團購未在期限內確認",
            )
            changed += 1
            continue
        if (
            campaign.decision_status == GroupDecisionStatus.RECRUITING
            and _aware(campaign.deadline) <= now
        ) or campaign.intake_status == GroupIntakeStatus.SETTLING:
            if campaign.reserved_quantity > 0:
                campaign.intake_status = GroupIntakeStatus.SETTLING
                continue
            if campaign.paid_quantity >= campaign.min_paid_quantity:
                campaign.decision_status = (
                    GroupDecisionStatus.PENDING_CONFIRMATION
                )
                campaign.intake_status = GroupIntakeStatus.PAUSED
                campaign.confirmation_deadline = now + timedelta(
                    hours=settings.late_confirmation_hours
                )
            else:
                campaign.decision_status = GroupDecisionStatus.FAILED_UNMET
                campaign.intake_status = GroupIntakeStatus.CLOSED
                await _enqueue_campaign_refunds(session, campaign)
                await _notify_campaign_outcome(
                    session,
                    notification_service,
                    campaign,
                    "group.failed_unmet",
                    "團購未達成團門檻",
                )
            changed += 1
    await session.commit()
    return changed


async def _notify_campaign_outcome(
    session: AsyncSession,
    service: NotificationService,
    campaign: GroupCampaign,
    event_type: str,
    title: str,
) -> None:
    users = list(
        await session.scalars(
            select(User)
            .join(Order, Order.user_id == User.id)
            .where(Order.group_campaign_id == campaign.id)
            .distinct()
        )
    )
    for user in users:
        await service.publish(
            NotificationCommand(
                user_id=user.id,
                event_type=event_type,
                title=title,
                body="「{}」{}，已付款訂單將送出退款處理。".format(
                    campaign.title, title
                ),
                data={"campaign_id": campaign.id},
                email=user.email,
                dedupe_key="campaign-outcome:{}:{}".format(
                    campaign.id, event_type
                ),
            )
        )


async def _enqueue_campaign_refunds(
    session: AsyncSession, campaign: GroupCampaign
) -> None:
    orders = list(
        await session.scalars(
            select(Order)
            .where(
                Order.group_campaign_id == campaign.id,
                Order.payment_status == PaymentStatus.PAID,
            )
            .options(
                selectinload(Order.refunds),
                selectinload(Order.reservations),
                selectinload(Order.items),
            )
        )
    )
    for order in orders:
        if any(
            refund.status in {RefundStatus.PENDING, RefundStatus.COMPLETED}
            for refund in order.refunds
        ):
            continue
        refund = Refund(
            order=order,
            amount=order.amount_total,
            status=RefundStatus.PENDING,
            reason="團購未成立，自動退款",
            requested_by_id=campaign.created_by_id,
        )
        session.add(refund)
        await session.flush()
        consumed_group_reservations = [
            reservation
            for reservation in order.reservations
            if (
                reservation.status == ReservationStatus.CONSUMED
                and reservation.group_campaign_id == campaign.id
            )
        ]
        remove_paid_quantity(
            campaign,
            sum(
                reservation.quantity
                for reservation in consumed_group_reservations
            ),
        )
        for reservation in consumed_group_reservations:
            reservation.status = ReservationStatus.RELEASED
            reservation.released_at = datetime.now(timezone.utc)
        order.payment_status = PaymentStatus.REFUND_PENDING
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        session.add(
            OutboxEvent(
                event_type="refund.requested",
                aggregate_type="refund",
                aggregate_id=refund.id,
                payload={"refund_id": refund.id, "order_id": order.id},
            )
        )


async def _process_outbox(
    session: AsyncSession,
    settings: Settings,
    now: datetime,
    limit: int,
) -> tuple[int, int]:
    stale_processing_cutoff = now - timedelta(minutes=10)
    stale = list(
        await session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.status == OutboxStatus.PROCESSING,
                OutboxEvent.available_at <= stale_processing_cutoff,
            )
        )
    )
    for event in stale:
        event.status = OutboxStatus.PENDING
    await session.commit()

    event_ids = list(
        await session.scalars(
            select(OutboxEvent.id)
            .where(
                OutboxEvent.status == OutboxStatus.PENDING,
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.created_at)
            .limit(limit)
        )
    )
    completed = 0
    failed = 0
    for event_id in event_ids:
        event = await session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.status == OutboxStatus.PENDING,
            )
            .with_for_update()
        )
        if event is None:
            continue
        event.status = OutboxStatus.PROCESSING
        event.attempts += 1
        event.available_at = now
        await session.commit()
        try:
            await _dispatch_outbox_event(session, settings, event_id)
            event = await session.get(OutboxEvent, event_id)
            if event is not None:
                event.status = OutboxStatus.COMPLETED
                event.processed_at = datetime.now(timezone.utc)
                event.last_error = None
                _redact_auth_credential(event)
                await session.commit()
            completed += 1
        except Exception as exc:
            await session.rollback()
            event = await session.get(OutboxEvent, event_id)
            if event is not None:
                event.last_error = str(exc)[:500]
                if event.attempts >= 8:
                    event.status = OutboxStatus.FAILED
                    _redact_auth_credential(event)
                    await _notify_refund_terminal_failure(session, event)
                else:
                    event.status = OutboxStatus.PENDING
                    event.available_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(3600, 30 * (2 ** event.attempts))
                    )
                await session.commit()
            failed += 1
    return completed, failed


async def _notify_refund_terminal_failure(
    session: AsyncSession,
    event: OutboxEvent,
) -> None:
    if event.event_type != "refund.requested":
        return
    result_uncertain = bool(event.payload.get("provider_refund_query_only"))
    refund_id = str(event.payload.get("refund_id") or event.aggregate_id)
    refund = await session.get(Refund, refund_id)
    if refund is None or refund.status == RefundStatus.COMPLETED:
        return
    admins = list(
        await session.scalars(
            select(User).where(User.user_role == UserRole.ADMIN)
        )
    )
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    for admin in admins:
        await service.publish(
            NotificationCommand(
                user_id=admin.id,
                event_type="refund_manual_review_required",
                title=(
                    "退款結果需要人工確認"
                    if result_uncertain
                    else "退款處理失敗，需要人工介入"
                ),
                body=(
                    f"退款 {refund.id} 已送出或可能尚未送達，"
                    "多次查單仍無法確認；請先向雷門核對，勿直接重送退款。"
                    if result_uncertain
                    else f"退款 {refund.id} 多次處理仍未完成；"
                    "請查核原付款與金流狀態後人工處理。"
                ),
                data={
                    "refund_id": refund.id,
                    "order_id": refund.order_id,
                    "membership_charge_id": refund.membership_charge_id,
                    "outbox_event_id": event.id,
                },
                email=admin.email,
                dedupe_key=f"refund-manual-review:{refund.id}:{admin.id}",
            )
        )


def _redact_auth_credential(event: OutboxEvent) -> None:
    if event.event_type not in {
        "auth.email_verification_requested",
        "auth.password_reset_requested",
    }:
        return
    payload = dict(event.payload)
    payload.pop("verification_token", None)
    payload.pop("reset_token", None)
    payload.pop("credential_encrypted", None)
    payload["credential_redacted"] = True
    event.payload = payload


async def _dispatch_outbox_event(
    session: AsyncSession,
    settings: Settings,
    event_id: str,
) -> None:
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        return
    if event.event_type in {"refund.requested", "sandbox_refund"}:
        await _process_refund_event(session, event, settings)
    elif event.event_type in {
        "invoice.issue",
        "invoice.issue_requested",
        "issue_invoice",
    }:
        await _process_invoice_event(session, settings, event)
    elif event.event_type in {
        "auth.email_verification_requested",
        "auth.password_reset_requested",
    }:
        await _process_auth_email_event(settings, event)
    elif event.event_type == "send_email":
        await _process_email_event(settings, event)
    else:
        await _process_domain_event(session, event)


async def _process_refund_event(
    session: AsyncSession,
    event: OutboxEvent,
    settings: Optional[Settings] = None,
) -> None:
    resolved_settings = settings or get_settings()
    raw_refund_id = event.payload.get("refund_id")
    if raw_refund_id:
        refund_filter = Refund.id == str(raw_refund_id)
    elif event.aggregate_type == "order":
        refund_filter = (
            (Refund.order_id == event.aggregate_id)
            & (Refund.status == RefundStatus.PENDING)
        )
    else:
        refund_filter = Refund.id == event.aggregate_id
    refund = await session.scalar(
        select(Refund)
        .where(refund_filter)
        .order_by(Refund.created_at.desc())
        .options(
            selectinload(Refund.order).selectinload(Order.reservations),
            selectinload(Refund.order).selectinload(Order.user),
            selectinload(Refund.order).selectinload(Order.invoice),
            selectinload(Refund.payment_attempt),
            selectinload(Refund.membership_charge),
        )
        .with_for_update()
    )
    if refund is None:
        raise ValueError("找不到退款資料")
    if refund.status == RefundStatus.COMPLETED:
        return
    if refund.status != RefundStatus.PENDING:
        raise ValueError("退款目前不可處理")
    order = refund.order
    charge = refund.membership_charge
    if order is None and charge is None:
        raise ValueError("退款缺少訂單或入社款項")
    if order is not None:
        await session.execute(
            select(Order.id).where(Order.id == order.id).with_for_update()
        )
    else:
        await session.execute(
            select(MembershipCharge.id)
            .where(MembershipCharge.id == charge.id)
            .with_for_update()
        )
    subject_amount = order.amount_total if order is not None else charge.amount
    if refund.amount != subject_amount:
        raise ValueError("雷門規格只允許全額退款")
    attempt = refund.payment_attempt
    if attempt is None:
        subject_filter = (
            PaymentAttempt.order_id == order.id
            if order is not None
            else PaymentAttempt.membership_charge_id == charge.id
        )
        attempt = await session.scalar(
            select(PaymentAttempt)
            .where(
                subject_filter,
                PaymentAttempt.status.in_(
                    {
                        PaymentStatus.PAID,
                        PaymentStatus.LATE_PAID_REFUND_REQUIRED,
                        PaymentStatus.REFUND_PENDING,
                    }
                ),
                ~exists().where(
                    Refund.payment_attempt_id == PaymentAttempt.id
                ),
            )
            .order_by(
                case(
                    (PaymentAttempt.status == PaymentStatus.PAID, 0),
                    (
                        PaymentAttempt.status
                        == PaymentStatus.LATE_PAID_REFUND_REQUIRED,
                        1,
                    ),
                    else_=2,
                ),
                PaymentAttempt.paid_at.desc(),
                PaymentAttempt.created_at.desc(),
            )
            .limit(1)
            .with_for_update()
        )
    provider = (
        attempt.provider
        if attempt is not None
        else refund.provider or resolved_settings.payment_provider
    )
    if attempt is None and resolved_settings.environment == "production":
        raise ValueError("正式環境找不到可退款的原付款交易")
    if provider == "raygate" and (
        attempt is None or not attempt.provider_trade_no
    ):
        raise ValueError("找不到已付款的雷門交易")
    if refund.provider and refund.provider != provider:
        raise ValueError("退款服務與原付款服務不符")
    if attempt is not None:
        refund.payment_attempt = attempt
    refund.provider = provider
    await session.flush()
    adapter = refund_adapter_from_settings(resolved_settings, provider)
    if provider == "raygate":
        query_result = await adapter.query_order(attempt.merchant_trade_no)
        if (
            query_result.get("MerchantTradeNo")
            != attempt.merchant_trade_no
            or query_result.get("TradeNo") != attempt.provider_trade_no
            or query_result.get("TradeAmt") != str(attempt.amount)
        ):
            raise ValueError("雷門查單結果與原付款不符")
        if query_result.get("RayGateStatus") == "3":
            provider_refund_id = query_result.get("RayGateAssociatedOrderID", "")
            if not provider_refund_id:
                raise ValueError("雷門已退款但未回傳退款訂單編號")
            result = None
            refund.provider_refund_id = provider_refund_id
            refund.provider_response = dict(query_result)
            completed_at = datetime.now(timezone.utc)
            provider_refund_performed = True
        elif (
            query_result.get("RayGateStatus") == "4"
            or query_result.get("PaymentDisposition") == "refund_failed"
        ):
            refund.status = RefundStatus.FAILED
            refund.provider_response = dict(query_result)
            user = (
                order.user
                if order is not None
                else await session.get(User, charge.user_id)
            )
            if user is not None:
                service = NotificationService(
                    SQLAlchemyNotificationRepository(session)
                )
                await service.publish(
                    NotificationCommand(
                        user_id=user.id,
                        event_type="refund_failed",
                        title="退款尚未完成",
                        body="金流回報退款失敗，工作人員將協助確認。",
                        data={"refund_id": refund.id},
                        email=user.email,
                        dedupe_key="refund-failed:{}".format(refund.id),
                    )
                )
            await session.commit()
            await _notify_refund_terminal_failure(session, event)
            await session.commit()
            return
        else:
            if query_result.get("PaymentDisposition") != "paid":
                raise ValueError("原雷門交易尚未達可退款狀態")
            if event.payload.get("provider_refund_query_only"):
                raise ValueError(
                    "退款送出結果仍不明；目前只查單，不會自動重送退款"
                )
            payment_type = str(
                query_result.get("PaymentType")
                or attempt.provider_response.get("PaymentType", "")
            )
            event.payload = {
                **dict(event.payload),
                "provider_refund_query_only": True,
                "provider_refund_submission_started_at": (
                    datetime.now(timezone.utc).isoformat()
                ),
            }
            refund.provider_response = {
                **dict(query_result),
                "platform_refund_state": "submission_started",
            }
            await session.commit()
            refund = await session.scalar(
                select(Refund)
                .where(Refund.id == refund.id)
                .options(
                    selectinload(Refund.order).selectinload(Order.reservations),
                    selectinload(Refund.order).selectinload(Order.user),
                    selectinload(Refund.order).selectinload(Order.invoice),
                    selectinload(Refund.payment_attempt),
                    selectinload(Refund.membership_charge),
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if refund is None:
                raise ValueError("找不到退款資料")
            if refund.status == RefundStatus.COMPLETED:
                return
            if refund.status != RefundStatus.PENDING:
                return
            order = refund.order
            charge = refund.membership_charge
            attempt = refund.payment_attempt
            if order is not None:
                order = await session.scalar(
                    select(Order)
                    .where(Order.id == order.id)
                    .options(
                        selectinload(Order.reservations),
                        selectinload(Order.user),
                        selectinload(Order.invoice),
                    )
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
                if order is None:
                    raise ValueError("找不到退款訂單")
            elif charge is not None:
                charge = await session.scalar(
                    select(MembershipCharge)
                    .where(MembershipCharge.id == charge.id)
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
                if charge is None:
                    raise ValueError("找不到退款入社款項")
            if attempt is not None:
                attempt = await session.scalar(
                    select(PaymentAttempt)
                    .where(PaymentAttempt.id == attempt.id)
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            if attempt is None or not attempt.provider_trade_no:
                raise ValueError("找不到已付款的雷門交易")
            result = await adapter.refund(
                provider_order_id=attempt.provider_trade_no,
                payment_type=payment_type,
                amount=refund.amount,
                reason=refund.reason,
                idempotency_key=refund.id,
            )
            refund.provider_refund_id = result.refund_id
            refund.provider_response = dict(result.provider_response)
            completed_at = result.created_at
            provider_refund_performed = result.provider_refund_performed
    else:
        result = await adapter.refund(
            order_id=order.id if order is not None else charge.id,
            amount=refund.amount,
            reason=refund.reason,
            idempotency_key=refund.id,
        )
        refund.provider_refund_id = result.refund_id
        refund.provider_response = {}
        completed_at = result.created_at
        provider_refund_performed = result.provider_refund_performed
    refund.status = RefundStatus.COMPLETED
    refund.completed_at = completed_at
    remaining_payment_status = None
    if attempt is not None:
        remaining_payment_status = await remaining_subject_payment_status(
            session,
            attempt,
        )
        attempt.status = PaymentStatus.REFUNDED
    if order is not None:
        order.payment_status = remaining_payment_status or PaymentStatus.REFUNDED
        if remaining_payment_status is None:
            order.fulfillment_status = FulfillmentStatus.CANCELLED
            enqueue_invoice_adjustment_after_refund(session, order, refund)
            await reverse_order_purchase_points(
                session,
                order,
                refund.id,
            )
    else:
        if remaining_payment_status is None:
            charge.status = MembershipChargeStatus.REFUNDED
            charge.refunded_at = completed_at
        elif remaining_payment_status == PaymentStatus.PAID:
            charge.status = MembershipChargeStatus.PAID
        else:
            charge.status = MembershipChargeStatus.REFUND_PENDING
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    if order is not None:
        await service.publish(
            NotificationCommand(
                user_id=order.user_id,
                event_type="refund_completed",
                title="退款完成" if provider_refund_performed else "Sandbox 退款完成",
                body=(
                    "訂單 {} 已完成退款。".format(order.order_number)
                    if provider_refund_performed
                    else "訂單 {} 已完成系統內退款紀錄；測試環境未執行真實退刷。".format(
                        order.order_number
                    )
                ),
                data={
                    "order_id": order.id,
                    "provider_refund_performed": provider_refund_performed,
                },
                email=order.contact_email,
                dedupe_key="refund:{}".format(refund.id),
            )
        )
    else:
        charge_user = await session.get(User, charge.user_id)
        await service.publish(
            NotificationCommand(
                user_id=charge.user_id,
                event_type="membership_refund_completed",
                title="入社款項退款完成",
                body="入社款項 NT${} 已完成退款。".format(refund.amount),
                data={"membership_charge_id": charge.id},
                email=charge_user.email if charge_user else None,
                dedupe_key="refund:{}".format(refund.id),
            )
        )
    await session.commit()


async def _process_invoice_event(
    session: AsyncSession,
    settings: Settings,
    event: OutboxEvent,
) -> None:
    order_id = str(event.payload.get("order_id") or event.aggregate_id)
    adapter = invoice_adapter_from_settings(settings)
    result = await issue_paid_order_invoice(
        session,
        order_id,
        adapter,
    )
    order = await session.get(Order, order_id)
    if order is None:
        raise ValueError("找不到發票訂單")
    if order.invoice_status != InvoiceStatus.ISSUED:
        await session.commit()
        return
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    await service.publish(
        NotificationCommand(
            user_id=order.user_id,
            event_type="invoice_issued",
            title="電子發票已開立",
            body="訂單 {} 的電子發票 {} 已開立。".format(
                order.order_number, result.invoice_number
            ),
            data={
                "order_id": order.id,
                "invoice_number": result.invoice_number,
            },
            # Fanyu delivers the invoice via notifyEmail; keep this notice in-app.
            email=(
                None
                if adapter.provider_name == "fanyu"
                else order.invoice_buyer_email or order.contact_email
            ),
            dedupe_key="invoice:{}".format(result.relate_number),
        )
    )
    await session.commit()


async def _process_email_event(
    settings: Settings, event: OutboxEvent
) -> None:
    payload = event.payload
    await email_sender_from_settings(settings).send(
        EmailMessage(
            to_email=str(payload["to_email"]),
            subject=str(payload["subject"]),
            text_content=str(payload["text_content"]),
            html_content=(
                str(payload["html_content"])
                if payload.get("html_content")
                else None
            ),
            idempotency_key=f"outbox-{event.id}",
        )
    )


async def _process_auth_email_event(
    settings: Settings,
    event: OutboxEvent,
) -> None:
    payload = event.payload
    encrypted = payload.get("credential_encrypted")
    credential = (
        decrypt_auth_outbox_credential(
            settings,
            event.event_type,
            event.aggregate_id,
            str(encrypted),
        )
        if encrypted
        else None
    )
    if event.event_type == "auth.email_verification_requested":
        token = credential or str(payload["verification_token"])
        subject = "十里方圓 Email 驗證"
        text = f"您的 Email 驗證碼是：{token}\n\n驗證碼將於 10 分鐘後失效。"
    else:
        token = credential or str(payload["reset_token"])
        subject = "十里方圓密碼重設"
        action_url = (
            f"{settings.web_base_url.rstrip('/')}/reset-password?token={token}"
        )
        text = f"請使用以下連結重設密碼：{action_url}"
    await email_sender_from_settings(settings).send(
        EmailMessage(
            to_email=str(payload["recipient"]),
            subject=subject,
            text_content=text,
            idempotency_key=f"outbox-{event.id}",
        )
    )


async def _process_domain_event(
    session: AsyncSession, event: OutboxEvent
) -> None:
    """Convert durable domain events to in-app/email notifications."""
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    if event.event_type == "proposal.review_requested":
        admins = list(
            await session.scalars(
                select(User).where(User.user_role == UserRole.ADMIN)
            )
        )
        for admin in admins:
            await service.publish(
                NotificationCommand(
                    user_id=admin.id,
                    event_type=event.event_type,
                    title="有新的團購提案待審核",
                    body="{} 已送出團購投票提案。".format(
                        event.payload.get("target_name", "商品")
                    ),
                    data={"proposal_id": event.aggregate_id},
                    dedupe_key="outbox:{}".format(event.id),
                )
            )
    elif event.event_type == "invoice.adjustment_required":
        admins = list(
            await session.scalars(
                select(User).where(User.user_role == UserRole.ADMIN)
            )
        )
        adjustment = str(event.payload.get("adjustment", "allowance"))
        title = (
            "退款完成，發票待作廢"
            if adjustment == "void"
            else "退款完成，發票待開折讓"
        )
        for admin in admins:
            await service.publish(
                NotificationCommand(
                    user_id=admin.id,
                    event_type=event.event_type,
                    title=title,
                    body="訂單退款已完成，請至原發票平台處理後再同步狀態。",
                    data={
                        "invoice_id": event.payload.get("invoice_id"),
                        "order_id": event.payload.get("order_id"),
                        "refund_id": event.payload.get("refund_id"),
                        "adjustment": adjustment,
                    },
                    dedupe_key="outbox:{}".format(event.id),
                )
            )
    elif event.event_type in {"proposal.approved", "proposal.rejected"}:
        user_id = str(event.payload["user_id"])
        user = await session.get(User, user_id)
        if user is not None:
            title = (
                "團購提案已通過"
                if event.event_type.endswith("approved")
                else "團購提案未通過"
            )
            await service.publish(
                NotificationCommand(
                    user_id=user.id,
                    event_type=event.event_type,
                    title=title,
                    body=str(event.payload.get("reason") or title),
                    data={"proposal_id": event.aggregate_id},
                    email=user.email,
                    dedupe_key="outbox:{}".format(event.id),
                )
            )
    elif event.event_type == "proposal.threshold_reached":
        admins = list(
            await session.scalars(
                select(User).where(User.user_role == UserRole.ADMIN)
            )
        )
        for admin in admins:
            await service.publish(
                NotificationCommand(
                    user_id=admin.id,
                    event_type=event.event_type,
                    title="團購投票已達門檻",
                    body="提案已達 {} 票。".format(
                        event.payload.get("vote_count", "")
                    ),
                    data={"proposal_id": event.aggregate_id},
                    dedupe_key="outbox:{}".format(event.id),
                )
            )
    elif event.event_type == "group.opened":
        proposal_id = event.payload.get("proposal_id")
        if proposal_id:
            voter_ids = list(
                await session.scalars(
                    select(Vote.user_id).where(Vote.proposal_id == proposal_id)
                )
            )
            for user_id in set(voter_ids):
                user = await session.get(User, user_id)
                if user is not None:
                    await service.publish(
                        NotificationCommand(
                            user_id=user.id,
                            event_type=event.event_type,
                            title="正式團購已開放",
                            body="你參與投票的商品已開始正式團購。",
                            data={"campaign_id": event.aggregate_id},
                            email=user.email,
                            dedupe_key="outbox:{}:{}".format(event.id, user.id),
                        )
                    )
    elif event.event_type.startswith("group."):
        user_ids = {
            str(user_id) for user_id in event.payload.get("user_ids", [])
        }
        if not user_ids:
            user_ids = set(
                await session.scalars(
                    select(Order.user_id).where(
                        Order.group_campaign_id == event.aggregate_id
                    )
                )
            )
        titles = {
            "group.confirmed": "團購已確認成團",
            "group.rejected": "團購未確認成團",
            "group.cancelled": "團購已取消",
        }
        title = titles.get(event.event_type, "團購狀態已更新")
        for user_id in user_ids:
            user = await session.get(User, user_id)
            if user is not None:
                await service.publish(
                    NotificationCommand(
                        user_id=user.id,
                        event_type=event.event_type,
                        title=title,
                        body=str(event.payload.get("reason") or title),
                        data={"campaign_id": event.aggregate_id},
                        email=user.email,
                        dedupe_key="outbox:{}:{}".format(event.id, user.id),
                    )
                )
    elif event.event_type == "order.cancelled":
        order = await session.get(Order, event.aggregate_id)
        if order is not None:
            await service.publish(
                NotificationCommand(
                    user_id=order.user_id,
                    event_type=event.event_type,
                    title="訂單已取消",
                    body=str(event.payload.get("reason") or "訂單已取消"),
                    data={"order_id": order.id},
                    email=order.contact_email,
                    dedupe_key="outbox:{}".format(event.id),
                )
            )
    await session.commit()


router = jobs_router
