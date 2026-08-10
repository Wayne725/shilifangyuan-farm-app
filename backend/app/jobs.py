from __future__ import annotations

import hmac
import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import Settings, get_settings
from .database import SessionLocal, get_session
from .domain import apply_proposal_clock, remove_paid_quantity
from .integrations.common import IntegrationError
from .integrations.ecpay import LocalSandboxRefundAdapter
from .integrations.invoice_service import (
    invoice_adapter_from_settings,
    issue_picked_up_order_invoice,
)
from .integrations.notifications import (
    NotificationCommand,
    NotificationService,
    SQLAlchemyNotificationRepository,
)
from .integrations.payment_service import (
    SQLAlchemyPaymentCallbackRepository,
    payment_adapter_from_settings,
    release_attempt_reservations,
)
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


@dataclass
class ReconcileReport:
    payment_attempts: int = 0
    proposals: int = 0
    campaigns: int = 0
    member_proposals: int = 0
    activities: int = 0
    meal_events: int = 0
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
                        except Exception:
                            await session.rollback()
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
                except Exception:
                    await session.rollback()
        finally:
            if _background_reconcile_task is asyncio.current_task():
                _background_reconcile_task = None

    task = asyncio.create_task(_run())
    _background_reconcile_task = task
    return task


async def _reconcile_expired_payments(
    session: AsyncSession,
    settings: Settings,
    now: datetime,
    limit: int,
) -> int:
    attempt_ids = list(
        await session.scalars(
            select(PaymentAttempt.id)
            .where(
                PaymentAttempt.status == PaymentStatus.PENDING,
                PaymentAttempt.expires_at <= now,
            )
            .order_by(PaymentAttempt.expires_at)
            .limit(limit)
        )
    )
    if not attempt_ids:
        return 0
    adapter = payment_adapter_from_settings(settings)
    processed = 0
    for attempt_id in attempt_ids:
        attempt = await session.get(PaymentAttempt, attempt_id)
        if attempt is None or attempt.status != PaymentStatus.PENDING:
            continue
        try:
            query_result = await adapter.query_order(attempt.merchant_trade_no)
        except IntegrationError:
            await session.rollback()
            continue
        if query_result.get("TradeStatus") == "1":
            repository = SQLAlchemyPaymentCallbackRepository(session)
            await repository.apply_query_result(attempt, query_result)
            processed += 1
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
        if (
            event.status == MealEventStatus.PUBLISHED
            and _aware(event.ordering_ends_at) <= now
            and _aware(event.pickup_ends_at) > now
        ):
            event.status = MealEventStatus.ORDERING_CLOSED
            changed += 1
            continue
        if _aware(event.pickup_ends_at) > now:
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
            if order.invoice_status != InvoiceStatus.ISSUED:
                order.invoice_status = InvoiceStatus.PENDING
            session.add(
                OutboxEvent(
                    event_type="invoice.issue_requested",
                    aggregate_type="order",
                    aggregate_id=order.id,
                    payload={"order_id": order.id},
                )
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
                body="「{}」{}，已付款訂單將進行 Sandbox 退款。".format(
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
            reason="團購未成立，自動 Sandbox 退款",
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
        await session.commit()
        try:
            await _dispatch_outbox_event(session, settings, event_id)
            event = await session.get(OutboxEvent, event_id)
            if event is not None:
                event.status = OutboxStatus.COMPLETED
                event.processed_at = datetime.now(timezone.utc)
                event.last_error = None
                await session.commit()
            completed += 1
        except Exception as exc:
            await session.rollback()
            event = await session.get(OutboxEvent, event_id)
            if event is not None:
                event.last_error = str(exc)[:500]
                if event.attempts >= 8:
                    event.status = OutboxStatus.FAILED
                else:
                    event.status = OutboxStatus.PENDING
                    event.available_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(3600, 30 * (2 ** event.attempts))
                    )
                await session.commit()
            failed += 1
    return completed, failed


async def _dispatch_outbox_event(
    session: AsyncSession,
    settings: Settings,
    event_id: str,
) -> None:
    event = await session.get(OutboxEvent, event_id)
    if event is None:
        return
    if event.event_type in {"refund.requested", "sandbox_refund"}:
        await _process_refund_event(session, event)
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
    session: AsyncSession, event: OutboxEvent
) -> None:
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
        )
        .with_for_update()
    )
    if refund is None:
        raise ValueError("找不到退款資料")
    if refund.status == RefundStatus.COMPLETED:
        return
    order = refund.order
    result = await LocalSandboxRefundAdapter().refund(
        order_id=order.id,
        amount=refund.amount,
        reason=refund.reason,
        idempotency_key=refund.id,
    )
    refund.status = RefundStatus.COMPLETED
    refund.completed_at = result.created_at
    order.payment_status = PaymentStatus.REFUNDED
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    await service.publish(
        NotificationCommand(
            user_id=order.user_id,
            event_type="refund_completed",
            title="Sandbox 退款完成",
            body="訂單 {} 已完成系統內退款紀錄；綠界測試環境未執行真實退刷。".format(
                order.order_number
            ),
            data={
                "order_id": order.id,
                "provider_refund_performed": result.provider_refund_performed,
            },
            email=order.contact_email,
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
    result = await issue_picked_up_order_invoice(
        session,
        order_id,
        invoice_adapter_from_settings(settings),
    )
    order = await session.get(Order, order_id)
    if order is None:
        raise ValueError("找不到發票訂單")
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
            email=order.contact_email,
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
        )
    )


async def _process_auth_email_event(
    settings: Settings,
    event: OutboxEvent,
) -> None:
    payload = event.payload
    if event.event_type == "auth.email_verification_requested":
        token = str(payload["verification_token"])
        subject = "十里方圓 Email 驗證"
        text = f"您的 Email 驗證碼是：{token}\n\n驗證碼將於 10 分鐘後失效。"
    else:
        token = str(payload["reset_token"])
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
