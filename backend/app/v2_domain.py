from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from .domain import DomainError
from .models import (
    Activity,
    ActivityRegistration,
    ActivityRegistrationStatus,
    ActivityStatus,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    MemberProposal,
    MemberProposalStatus,
    MemberProposalType,
    MemberProposalVote,
    MemberVoteChoice,
    Membership,
    MembershipStatus,
    MembershipType,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def derived_membership_type(
    membership: Optional[Membership],
) -> MembershipType:
    if membership is not None and membership.status == MembershipStatus.ACTIVE:
        return MembershipType.MEMBER
    return MembershipType.NONMEMBER


def require_active_membership(
    membership: Optional[Membership],
) -> Membership:
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise DomainError("此功能僅限有效社員使用")
    return membership


def next_activity_registration_status(
    activity: Activity,
    active_registration_count: int,
    now: Optional[datetime] = None,
) -> ActivityRegistrationStatus:
    current = now or utcnow()
    if activity.status != ActivityStatus.PUBLISHED:
        raise DomainError("活動尚未開放報名")
    if current >= aware(activity.registration_deadline):
        raise DomainError("活動報名已截止")
    if active_registration_count < activity.capacity:
        return ActivityRegistrationStatus.REGISTERED
    if activity.waitlist_enabled is not False:
        return ActivityRegistrationStatus.WAITLISTED
    raise DomainError("活動名額已滿")


def promote_activity_waitlist(
    registrations: Sequence[ActivityRegistration],
) -> Optional[ActivityRegistration]:
    candidate = min(
        (
            registration
            for registration in registrations
            if registration.status == ActivityRegistrationStatus.WAITLISTED
        ),
        key=lambda registration: (
            registration.queue_position,
            aware(registration.registered_at),
        ),
        default=None,
    )
    if candidate is not None:
        candidate.status = ActivityRegistrationStatus.REGISTERED
    return candidate


@dataclass(frozen=True)
class MemberVoteTally:
    yes: int
    no: int
    abstain: int
    total: int
    quorum_met: bool
    passed: bool


def tally_member_votes(
    votes: Iterable[MemberProposalVote],
    minimum_voters: int,
) -> MemberVoteTally:
    choices = [vote.choice for vote in votes]
    yes = choices.count(MemberVoteChoice.YES)
    no = choices.count(MemberVoteChoice.NO)
    abstain = choices.count(MemberVoteChoice.ABSTAIN)
    total = yes + no + abstain
    quorum_met = total >= minimum_voters
    return MemberVoteTally(
        yes=yes,
        no=no,
        abstain=abstain,
        total=total,
        quorum_met=quorum_met,
        passed=quorum_met and yes > no,
    )


def apply_member_proposal_clock(
    proposal: MemberProposal,
    votes: Iterable[MemberProposalVote],
    now: Optional[datetime] = None,
) -> bool:
    current = now or utcnow()
    previous = proposal.status
    if (
        proposal.status == MemberProposalStatus.DISCUSSION
        and proposal.discussion_ends_at is not None
        and current >= aware(proposal.discussion_ends_at)
    ):
        proposal.status = MemberProposalStatus.VOTING
    if (
        proposal.status == MemberProposalStatus.VOTING
        and proposal.voting_ends_at is not None
        and current >= aware(proposal.voting_ends_at)
    ):
        if proposal.proposal_type == MemberProposalType.MULTIPLE_CHOICE:
            option_votes = [vote.option_id for vote in votes if vote.option_id]
            proposal.status = MemberProposalStatus.PASSED if len(option_votes) >= proposal.minimum_voters else MemberProposalStatus.REJECTED
            if option_votes:
                winner_id = max(set(option_votes), key=lambda value: (option_votes.count(value), value))
                winner = next((item for item in proposal.options if item.id == winner_id), None)
                proposal.result_summary = f"最高票選項：{winner.label}" if winner else None
        else:
            tally = tally_member_votes(votes, proposal.minimum_voters)
            proposal.status = MemberProposalStatus.PASSED if tally.passed else MemberProposalStatus.REJECTED
        proposal.closed_at = current
    return proposal.status != previous


def meal_available_quantity(offering: MealEventOffering) -> int:
    return max(
        0,
        offering.capacity
        - offering.reserved_quantity
        - offering.paid_quantity,
    )


def validate_meal_preorder(
    event: MealEvent,
    offering: MealEventOffering,
    quantity: int,
    now: Optional[datetime] = None,
) -> None:
    current = now or utcnow()
    if event.status != MealEventStatus.PUBLISHED:
        raise DomainError("此便當場次目前未開放預購")
    if not (
        aware(event.ordering_starts_at)
        <= current
        < aware(event.ordering_ends_at)
    ):
        raise DomainError("目前不在便當預購時間內")
    if not offering.is_active:
        raise DomainError("此便當已停止預購")
    if quantity < 1:
        raise DomainError("數量至少為 1")
    if quantity > meal_available_quantity(offering):
        raise DomainError("便當剩餘數量不足")


def meal_cancel_deadline(
    paid_at: datetime,
    ordering_ends_at: datetime,
) -> datetime:
    return min(
        aware(paid_at) + timedelta(minutes=30),
        aware(ordering_ends_at),
    )


def can_cancel_meal_order(
    paid_at: Optional[datetime],
    ordering_ends_at: datetime,
    now: Optional[datetime] = None,
) -> bool:
    if paid_at is None:
        return True
    return (now or utcnow()) <= meal_cancel_deadline(
        paid_at,
        ordering_ends_at,
    )


def validate_shipping_selection(
    temperatures: Iterable[ShippingTemperature],
    allowed_channel_sets: Iterable[Iterable[str]],
    selected_channel: ShippingChannel,
) -> ShippingTemperature:
    unique_temperatures = set(temperatures)
    if len(unique_temperatures) != 1:
        raise DomainError("同一張物流訂單只能包含單一溫層")
    channel = selected_channel.value
    if any(channel not in set(channels) for channels in allowed_channel_sets):
        raise DomainError("購物車中有商品不支援所選物流通路")
    return unique_temperatures.pop()


def shipping_fee_for_rate(
    subtotal: int,
    rate: ShippingRate,
) -> int:
    if subtotal < 0:
        raise DomainError("商品小計不可為負數")
    if subtotal >= rate.free_shipping_threshold:
        return 0
    return rate.fee
