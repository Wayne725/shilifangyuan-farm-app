from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain import DomainError, price_for_membership
from app.models import (
    Activity,
    ActivityRegistration,
    ActivityRegistrationStatus,
    ActivityStatus,
    MealEvent,
    MealEventOffering,
    MealEventStatus,
    MemberProposal,
    MemberProposalStatus,
    MemberProposalVote,
    MemberVoteChoice,
    Membership,
    MembershipStatus,
    MembershipType,
    ShippingChannel,
    ShippingRate,
    ShippingTemperature,
)
from app.v2_domain import (
    apply_member_proposal_clock,
    can_cancel_meal_order,
    derived_membership_type,
    meal_available_quantity,
    next_activity_registration_status,
    promote_activity_waitlist,
    require_active_membership,
    shipping_fee_for_rate,
    tally_member_votes,
    validate_meal_preorder,
    validate_shipping_selection,
)


NOW = datetime(2026, 7, 30, 8, tzinfo=timezone.utc)


def make_activity(**overrides) -> Activity:
    values = {
        "title": "社員步道健行",
        "location": "觀音山",
        "starts_at": NOW + timedelta(days=2),
        "ends_at": NOW + timedelta(days=2, hours=3),
        "registration_deadline": NOW + timedelta(days=1),
        "capacity": 2,
        "status": ActivityStatus.PUBLISHED,
        "created_by_id": "member-1",
    }
    values.update(overrides)
    return Activity(**values)


def test_active_membership_is_the_only_membership_truth() -> None:
    assert (
        derived_membership_type(Membership(status=MembershipStatus.ACTIVE))
        == MembershipType.MEMBER
    )
    assert (
        derived_membership_type(Membership(status=MembershipStatus.SUSPENDED))
        == MembershipType.NONMEMBER
    )
    assert derived_membership_type(None) == MembershipType.NONMEMBER


def test_trainee_gets_member_pricing_without_active_member_rights() -> None:
    trainee = Membership(status=MembershipStatus.TRAINEE)

    assert derived_membership_type(trainee) == MembershipType.TRAINEE
    assert price_for_membership(90, 120, MembershipType.TRAINEE) == 90
    with pytest.raises(DomainError, match="僅限有效社員"):
        require_active_membership(trainee)


def test_activity_capacity_uses_fifo_waitlist_and_promotes_first() -> None:
    activity = make_activity()
    assert (
        next_activity_registration_status(activity, 1, NOW)
        == ActivityRegistrationStatus.REGISTERED
    )
    assert (
        next_activity_registration_status(activity, 2, NOW)
        == ActivityRegistrationStatus.WAITLISTED
    )
    registrations = [
        ActivityRegistration(
            status=ActivityRegistrationStatus.WAITLISTED,
            queue_position=3,
            registered_at=NOW + timedelta(minutes=2),
        ),
        ActivityRegistration(
            status=ActivityRegistrationStatus.WAITLISTED,
            queue_position=2,
            registered_at=NOW + timedelta(minutes=1),
        ),
    ]
    promoted = promote_activity_waitlist(registrations)
    assert promoted is registrations[1]
    assert promoted.status == ActivityRegistrationStatus.REGISTERED


def test_activity_without_waitlist_rejects_when_full() -> None:
    activity = make_activity(waitlist_enabled=False)
    with pytest.raises(DomainError, match="名額已滿"):
        next_activity_registration_status(activity, 2, NOW)


def test_governance_abstention_counts_for_quorum_but_not_majority() -> None:
    votes = [
        *[
            MemberProposalVote(choice=MemberVoteChoice.YES)
            for _ in range(4)
        ],
        *[MemberProposalVote(choice=MemberVoteChoice.NO) for _ in range(3)],
        *[
            MemberProposalVote(choice=MemberVoteChoice.ABSTAIN)
            for _ in range(3)
        ],
    ]
    tally = tally_member_votes(votes, minimum_voters=10)
    assert tally.total == 10
    assert tally.quorum_met is True
    assert tally.passed is True


def test_governance_tie_is_rejected_at_deadline() -> None:
    proposal = MemberProposal(
        title="共同廚房排班",
        body="討論共同廚房排班方式",
        created_by_id="member-1",
        status=MemberProposalStatus.VOTING,
        minimum_voters=4,
        voting_ends_at=NOW,
    )
    votes = [
        MemberProposalVote(choice=MemberVoteChoice.YES),
        MemberProposalVote(choice=MemberVoteChoice.YES),
        MemberProposalVote(choice=MemberVoteChoice.NO),
        MemberProposalVote(choice=MemberVoteChoice.NO),
    ]
    assert apply_member_proposal_clock(proposal, votes, NOW) is True
    assert proposal.status == MemberProposalStatus.REJECTED


def test_meal_capacity_and_cancel_deadline() -> None:
    event = MealEvent(
        title="校園午餐預購",
        location="設計學院前廣場",
        ordering_starts_at=NOW - timedelta(hours=1),
        ordering_ends_at=NOW + timedelta(hours=1),
        pickup_starts_at=NOW + timedelta(hours=2),
        pickup_ends_at=NOW + timedelta(hours=4),
        status=MealEventStatus.PUBLISHED,
        created_by_id="admin-1",
    )
    offering = MealEventOffering(
        price=120,
        capacity=20,
        reserved_quantity=3,
        paid_quantity=15,
        is_active=True,
    )
    assert meal_available_quantity(offering) == 2
    validate_meal_preorder(event, offering, 2, NOW)
    with pytest.raises(DomainError, match="剩餘數量不足"):
        validate_meal_preorder(event, offering, 3, NOW)
    paid_at = NOW
    assert can_cancel_meal_order(
        paid_at,
        event.ordering_ends_at,
        NOW + timedelta(minutes=30),
    )
    assert not can_cancel_meal_order(
        paid_at,
        event.ordering_ends_at,
        NOW + timedelta(minutes=31),
    )


def test_shipping_rejects_mixed_temperature_and_applies_free_threshold() -> None:
    with pytest.raises(DomainError, match="單一溫層"):
        validate_shipping_selection(
            [ShippingTemperature.AMBIENT, ShippingTemperature.CHILLED],
            [["home_delivery"], ["home_delivery"]],
            ShippingChannel.HOME_DELIVERY,
        )
    assert (
        validate_shipping_selection(
            [ShippingTemperature.AMBIENT, ShippingTemperature.AMBIENT],
            [
                ["home_delivery", "seven_eleven"],
                ["home_delivery"],
            ],
            ShippingChannel.HOME_DELIVERY,
        )
        == ShippingTemperature.AMBIENT
    )
    rate = ShippingRate(
        channel=ShippingChannel.HOME_DELIVERY,
        temperature=ShippingTemperature.AMBIENT,
        fee=160,
        free_shipping_threshold=1500,
        effective_from=date(2026, 1, 1),
    )
    assert shipping_fee_for_rate(1499, rate) == 160
    assert shipping_fee_for_rate(1500, rate) == 0
