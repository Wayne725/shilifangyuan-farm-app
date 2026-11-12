from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import Date, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .business_meals import TAIPEI
from .failed_jobs import JOB_KINDS, job_order_id_expression
from .models import (
    Activity,
    ActivityRefundRequest,
    ActivityRegistration,
    ActivityRegistrationStatus,
    ActivityStatus,
    GroupCampaign,
    GroupDecisionStatus,
    Invoice,
    InvoiceStatus,
    MealCancellationRequest,
    MealEvent,
    MemberProposal,
    MemberProposalStatus,
    MembershipAdmissionRound,
    MembershipApplication,
    MembershipApplicationStatus,
    Order,
    OrderFulfillment,
    OutboxEvent,
    OutboxStatus,
    PaymentMethod,
    PaymentStatus,
    ProposalStatus,
    SalesChannel,
    VoteProposal,
)

INVOICE_ADJUSTMENT_JOB = "invoice.adjustment_required"
INVOICE_ISSUE_JOBS = JOB_KINDS["invoice"] - {INVOICE_ADJUSTMENT_JOB}
ACTIVE_JOB_STATUSES = {OutboxStatus.PENDING, OutboxStatus.PROCESSING}
InvoiceFollowupReason = Literal["business_date", "issuance", "void"]


def pending_order_cancellation():
    return and_(Order.payment_status.not_in({PaymentStatus.EXPIRED, PaymentStatus.FAILED}),
        Order.cancellation_requests.any(MealCancellationRequest.status == "pending"))


def unpaid_business_order():
    return and_(
        Order.sales_channel == SalesChannel.MEAL_PREORDER,
        Order.meal_booking_type == "business",
        Order.cancelled_at.is_(None),
        Order.payment_status.in_({PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED}),
    )


def business_invoice_followup_orders(session: AsyncSession, now: datetime):
    # Use the same Taipei day boundaries as business_invoice_followup_required.
    def taipei_day(timestamp):
        if session.get_bind().dialect.name == "sqlite":
            # SQLite's date function rounds fractional seconds, which can cross midnight.
            return func.date(func.substr(timestamp, 1, 19), "+08:00")
        return cast(func.timezone(TAIPEI.key, timestamp), Date)

    pickup_at = func.coalesce(OrderFulfillment.pickup_at, OrderFulfillment.pickup_starts_at, MealEvent.pickup_starts_at)
    pickup_day = taipei_day(pickup_at)
    paid_day = taipei_day(Order.paid_at)
    issued_day = taipei_day(Invoice.invoice_date)
    today = now.astimezone(TAIPEI).date()
    return (
        select(Order.id)
        .outerjoin(OrderFulfillment, OrderFulfillment.order_id == Order.id)
        .outerjoin(MealEvent, MealEvent.id == Order.meal_event_id)
        .outerjoin(Invoice, Invoice.order_id == Order.id)
        .where(
            Order.sales_channel == SalesChannel.MEAL_PREORDER,
            Order.meal_booking_type == "business",
            Order.cancelled_at.is_(None),
            Order.payment_status == PaymentStatus.PAID,
            pickup_at.is_not(None),
            or_(
                paid_day != pickup_day,
                and_(Invoice.status == InvoiceStatus.ISSUED, or_(Invoice.invoice_date.is_(None), issued_day != pickup_day)),
                and_(or_(Invoice.status.is_(None), Invoice.status != InvoiceStatus.ISSUED), pickup_day < today),
            ),
        )
    )


def invoice_followup_conditions(
    session: AsyncSession, now: datetime,
) -> dict[InvoiceFollowupReason, ColumnElement[bool]]:
    job_order_id = job_order_id_expression()
    issue_job = OutboxEvent.event_type.in_(INVOICE_ISSUE_JOBS)
    void_job = and_(
        OutboxEvent.event_type == INVOICE_ADJUSTMENT_JOB,
        OutboxEvent.payload["adjustment"].as_string() == "void",
    )

    def matching_job(job, statuses):
        return Order.id.in_(select(job_order_id).where(
            job_order_id.is_not(None), job, OutboxEvent.status.in_(statuses),
        ))

    handling = func.coalesce(
        Order.invoice_handling,
        case((Order.payment_method == PaymentMethod.CASH_ON_PICKUP, "external"), else_="on_payment"),
    )
    due = or_(handling == "on_payment", and_(handling == "pickup_day", Order.invoice_issue_after <= now))
    issue_finished = {InvoiceStatus.ISSUED, InvoiceStatus.VOID_PENDING, InvoiceStatus.VOIDED}
    issuance_attention = and_(
        Order.payment_status == PaymentStatus.PAID,
        Order.cancelled_at.is_(None),
        due,
        Order.invoice_status.not_in(issue_finished | {InvoiceStatus.EXTERNAL}),
        ~Order.invoice.has(Invoice.status.in_(issue_finished)),
        ~matching_job(issue_job, ACTIVE_JOB_STATUSES),
        or_(
            Order.invoice_status == InvoiceStatus.FAILED,
            Order.invoice.has(Invoice.status == InvoiceStatus.FAILED),
            matching_job(issue_job, {OutboxStatus.FAILED}),
        ),
    )

    auto_void_job = and_(void_job, OutboxEvent.payload["auto_void"].as_boolean().is_(True))
    void_attention = and_(
        Order.payment_status == PaymentStatus.REFUNDED,
        Order.invoice_status != InvoiceStatus.VOIDED,
        Order.invoice.has(Invoice.status == InvoiceStatus.VOID_PENDING),
        ~matching_job(auto_void_job, ACTIVE_JOB_STATUSES),
        or_(
            matching_job(void_job, {OutboxStatus.FAILED}),
            Order.invoice.has(or_(
                and_(Invoice.provider != "fanyu", Invoice.void_source == "refund"),
                and_(Invoice.void_source == "api_submission_started", Invoice.error_message != ""),
            )),
        ),
    )
    # The business date rule still applies when the same order also has an issue failure.
    return {
        "business_date": Order.id.in_(business_invoice_followup_orders(session, now)),
        "issuance": issuance_attention,
        "void": void_attention,
    }


async def admin_today_counts(session: AsyncSession) -> dict[str, int]:
    now = datetime.now(timezone.utc)

    def count(table, *criteria):
        return select(func.count()).select_from(table).where(*criteria).scalar_subquery()

    active_activity = (
        Activity.status == ActivityStatus.PUBLISHED, Activity.is_public.is_(True), Activity.ends_at > now,
    )
    active_activity_ids = select(Activity.id).where(*active_activity)
    statement = select(
        (
            count(MembershipAdmissionRound, MembershipAdmissionRound.status.in_({"submitted", "needs_supplement"}))
            + count(MembershipApplication, MembershipApplication.status.in_({
                MembershipApplicationStatus.SUBMITTED, MembershipApplicationStatus.NEEDS_SUPPLEMENT,
            }))
        ).label("pending_admissions"),
        count(Order, pending_order_cancellation()).label("pending_order_cancellations"),
        count(Order, unpaid_business_order()).label("unpaid_business_orders"),
        count(ActivityRefundRequest, ActivityRefundRequest.status == "pending_review").label("pending_activity_refunds"),
        count(Activity, *active_activity).label("active_activities"),
        count(
            ActivityRegistration,
            ActivityRegistration.activity_id.in_(active_activity_ids),
            ActivityRegistration.status.in_({
                ActivityRegistrationStatus.PENDING_PAYMENT, ActivityRegistrationStatus.REGISTERED,
                ActivityRegistrationStatus.ATTENDED, ActivityRegistrationStatus.NO_SHOW,
            }),
        ).label("active_activity_registrations"),
        count(
            GroupCampaign, GroupCampaign.is_public.is_(True), GroupCampaign.deadline > now,
            GroupCampaign.decision_status.in_({GroupDecisionStatus.RECRUITING, GroupDecisionStatus.CONFIRMED}),
        ).label("active_campaigns"),
        count(GroupCampaign, GroupCampaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION).label("pending_campaign_decisions"),
        count(VoteProposal, VoteProposal.status == ProposalStatus.VOTING, VoteProposal.deadline > now).label("voting_group_proposals"),
        count(MemberProposal, MemberProposal.status == MemberProposalStatus.VOTING, MemberProposal.voting_ends_at > now).label("voting_member_proposals"),
        count(Order, or_(*invoice_followup_conditions(session, now).values())).label("invoice_followups"),
    )
    return dict((await session.execute(statement)).mappings().one())
