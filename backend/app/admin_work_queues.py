from datetime import datetime, timezone

from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .business_meals import TAIPEI
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
    PaymentStatus,
    ProposalStatus,
    SalesChannel,
    VoteProposal,
)


def pending_order_cancellation():
    return Order.cancellation_requests.any(MealCancellationRequest.status == "pending")


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
        count(business_invoice_followup_orders(session, now).subquery()).label("invoice_followups"),
    )
    return dict((await session.execute(statement)).mappings().one())
