from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .config import get_settings
from .models import (
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    MealEventStatus,
    MembershipType,
    Order,
    OrderKind,
    PaymentStatus,
    Product,
    ProposalStatus,
    SalesChannel,
    ShipmentStatus,
    TaxType,
    VoteProposal,
)


class DomainError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def price_for_membership(
    member_price: int,
    nonmember_price: int,
    membership_type: MembershipType,
) -> int:
    if membership_type in {
        MembershipType.MEMBER,
        MembershipType.TRAINEE,
    }:
        return member_price
    return nonmember_price


def included_tax_amount(subtotal: int, tax_type: TaxType) -> int:
    """Return the 5% tax included in a tax-inclusive TWD subtotal."""
    if tax_type == TaxType.TAX_EXEMPT:
        return 0
    return subtotal * 5 // 105


def product_price(product: Product, membership_type: MembershipType) -> int:
    return price_for_membership(
        product.member_price, product.nonmember_price, membership_type
    )


def live_proposal_status(
    proposal: VoteProposal,
    vote_count: int,
    now: Optional[datetime] = None,
) -> ProposalStatus:
    current = now or utcnow()
    if proposal.status not in {
        ProposalStatus.VOTING,
        ProposalStatus.CONVERSION_PENDING,
    }:
        return proposal.status
    if proposal.deadline is not None and current >= aware(proposal.deadline):
        if vote_count >= proposal.threshold:
            return ProposalStatus.CONVERSION_PENDING
        return ProposalStatus.ENDED_UNMET
    return ProposalStatus.VOTING


def apply_proposal_clock(
    proposal: VoteProposal,
    vote_count: int,
    now: Optional[datetime] = None,
) -> bool:
    current = now or utcnow()
    next_status = live_proposal_status(proposal, vote_count, current)
    changed = next_status != proposal.status
    if next_status == ProposalStatus.CONVERSION_PENDING:
        if proposal.conversion_deadline is None:
            hours = get_settings().proposal_conversion_hours
            proposal.conversion_deadline = aware(
                proposal.deadline or current
            ) + timedelta(hours=hours)
        proposal.frozen_vote_count = vote_count
    elif next_status == ProposalStatus.ENDED_UNMET:
        proposal.frozen_vote_count = vote_count
    proposal.status = next_status
    if (
        proposal.status == ProposalStatus.CONVERSION_PENDING
        and proposal.conversion_deadline is not None
        and current >= aware(proposal.conversion_deadline)
    ):
        proposal.status = ProposalStatus.EXPIRED_UNHANDLED
        changed = True
    return changed


def validate_campaign_schedule(campaign: GroupCampaign, now: datetime) -> None:
    if campaign.min_paid_quantity > campaign.supply_cap:
        raise DomainError("成團門檻不可高於供應上限")
    if aware(campaign.deadline) <= now:
        raise DomainError("團購截止時間必須在未來")
    if aware(campaign.estimated_pickup_start) < aware(campaign.deadline):
        raise DomainError("預估取貨起始時間不可早於團購截止")
    if aware(campaign.estimated_pickup_end) < aware(
        campaign.estimated_pickup_start
    ):
        raise DomainError("預估取貨結束時間不可早於起始時間")


def campaign_available_quantity(campaign: GroupCampaign) -> int:
    return max(
        0,
        campaign.supply_cap
        - campaign.paid_quantity
        - campaign.reserved_quantity,
    )


def validate_group_join(
    campaign: GroupCampaign,
    quantity: int,
    user_committed_quantity: int,
    now: Optional[datetime] = None,
) -> None:
    current = now or utcnow()
    if campaign.intake_status != GroupIntakeStatus.OPEN:
        raise DomainError("此團目前未開放加入")
    if campaign.decision_status not in {
        GroupDecisionStatus.RECRUITING,
        GroupDecisionStatus.CONFIRMED,
    }:
        raise DomainError("此團目前無法加入")
    if current >= aware(campaign.deadline):
        raise DomainError("團購已截止")
    if quantity < 1:
        raise DomainError("數量至少為 1")
    if user_committed_quantity + quantity > campaign.per_user_cap:
        raise DomainError(f"每人最多可購買 {campaign.per_user_cap} 組")
    if quantity > campaign_available_quantity(campaign):
        raise DomainError("剩餘供應數量不足")


def apply_paid_quantity(
    campaign: GroupCampaign,
    quantity: int,
    now: Optional[datetime] = None,
) -> None:
    current = now or utcnow()
    campaign.paid_quantity += quantity
    campaign.threshold_version = (campaign.threshold_version or 0) + 1
    campaign.reserved_quantity = max(0, campaign.reserved_quantity - quantity)
    if campaign.core_locked_at is None:
        campaign.core_locked_at = current
    if campaign.paid_quantity >= campaign.supply_cap:
        campaign.intake_status = GroupIntakeStatus.FULL
    if (
        campaign.decision_status == GroupDecisionStatus.RECRUITING
        and campaign.paid_quantity >= campaign.min_paid_quantity
    ):
        campaign.decision_status = GroupDecisionStatus.PENDING_CONFIRMATION
        campaign.intake_status = GroupIntakeStatus.PAUSED
        if current <= aware(campaign.deadline):
            campaign.confirmation_deadline = aware(campaign.deadline)
        else:
            campaign.confirmation_deadline = current + timedelta(
                hours=get_settings().late_confirmation_hours
            )


def remove_paid_quantity(
    campaign: GroupCampaign,
    quantity: int,
    now: Optional[datetime] = None,
) -> None:
    current = now or utcnow()
    campaign.paid_quantity = max(0, campaign.paid_quantity - quantity)
    campaign.threshold_version = (campaign.threshold_version or 0) + 1
    if (
        campaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION
        and campaign.paid_quantity < campaign.min_paid_quantity
    ):
        if current < aware(campaign.deadline):
            campaign.decision_status = GroupDecisionStatus.RECRUITING
            campaign.intake_status = GroupIntakeStatus.OPEN
            campaign.confirmation_deadline = None
        else:
            campaign.decision_status = GroupDecisionStatus.RECRUITING
            campaign.intake_status = GroupIntakeStatus.SETTLING
            campaign.confirmation_deadline = None


def confirm_campaign(
    campaign: GroupCampaign,
    final_pickup_at: datetime,
    now: Optional[datetime] = None,
) -> None:
    current = now or utcnow()
    if campaign.decision_status != GroupDecisionStatus.PENDING_CONFIRMATION:
        raise DomainError("只有待確認成團的團購可以確認")
    if campaign.paid_quantity < campaign.min_paid_quantity:
        raise DomainError("已付款數量尚未達成團門檻")
    if (
        campaign.confirmation_deadline is not None
        and current > aware(campaign.confirmation_deadline)
    ):
        raise DomainError("管理員確認期限已過")
    if aware(final_pickup_at) < current:
        raise DomainError("最終取貨時間不可早於現在")
    campaign.final_pickup_at = final_pickup_at
    campaign.confirmed_at = current
    campaign.decision_status = GroupDecisionStatus.CONFIRMED
    if current < aware(campaign.deadline) and campaign.paid_quantity < campaign.supply_cap:
        campaign.intake_status = GroupIntakeStatus.OPEN
    elif campaign.paid_quantity >= campaign.supply_cap:
        campaign.intake_status = GroupIntakeStatus.FULL
    else:
        campaign.intake_status = GroupIntakeStatus.CLOSED


def meal_payment_window_open(order: Order, now: datetime) -> bool:
    event = order.__dict__.get("meal_event")
    return (
        event is not None
        and event.status in {MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN}
        and aware(event.ordering_starts_at) <= now < aware(event.ordering_ends_at)
    )


def order_available_actions(
    order: Order,
    viewer_is_admin: bool = False,
    now: Optional[datetime] = None,
) -> List[str]:
    current = now or utcnow()
    actions: List[str] = []
    fulfillment = order.__dict__.get("fulfillment")
    is_meal = order.sales_channel == SalesChannel.MEAL_PREORDER
    meal_fulfillment_closed = is_meal and (
        order.cancelled_at is not None
        or order_fulfillment_is_irreversible(order)
        or order.fulfillment_status in {FulfillmentStatus.PICKED_UP, FulfillmentStatus.CANCELLED}
        or (
            fulfillment is not None
            and fulfillment.status in {
                FulfillmentState.PICKED_UP, FulfillmentState.NO_SHOW, FulfillmentState.CANCELLED,
            }
        )
    )
    if order.payment_status == PaymentStatus.PENDING:
        shipment = (
            fulfillment.__dict__.get("shipment")
            if fulfillment is not None
            else None
        )
        payment_ready = (
            order.fulfillment_method != FulfillmentMethod.ECPAY_LOGISTICS
            or (
                shipment is not None
                and shipment.status
                in {ShipmentStatus.READY_TO_CREATE, ShipmentStatus.CREATED}
            )
        )
        if payment_ready and (
            not is_meal
            or (not meal_fulfillment_closed and meal_payment_window_open(order, current))
        ):
            actions.append("pay")
        if not meal_fulfillment_closed:
            actions.append("cancel")
    elif order.payment_status == PaymentStatus.PAID:
        if order.sales_channel == SalesChannel.MEAL_PREORDER:
            if (
                order.meal_event is not None
                and order.meal_event.status not in {MealEventStatus.CANCELLED, MealEventStatus.COMPLETED}
                and not meal_fulfillment_closed
                and order.paid_at is not None
                and current
                <= min(
                    aware(order.paid_at)
                    + timedelta(
                        minutes=get_settings().post_confirmation_cancel_minutes
                    ),
                    aware(order.meal_event.ordering_ends_at),
                )
            ):
                actions.append("cancel")
        elif order.order_kind == OrderKind.REGULAR:
            if order.fulfillment_status == FulfillmentStatus.PENDING_CONFIRMATION:
                actions.append("cancel")
        elif order.group_campaign is not None:
            campaign = order.group_campaign
            if campaign.decision_status != GroupDecisionStatus.CONFIRMED:
                actions.append("cancel")
            elif (
                campaign.confirmed_at is not None
                and order.paid_at is not None
                and aware(order.paid_at) >= aware(campaign.confirmed_at)
                and current
                <= aware(order.paid_at)
                + timedelta(
                    minutes=get_settings().post_confirmation_cancel_minutes
                )
            ):
                actions.append("cancel")
    if viewer_is_admin:
        if (
            order.payment_status == PaymentStatus.PAID
            and not order_fulfillment_is_irreversible(order)
        ):
            actions.append("refund")
        campaign_ready = (
            order.order_kind != OrderKind.GROUP
            or (
                order.group_campaign is not None
                and order.group_campaign.decision_status
                == GroupDecisionStatus.CONFIRMED
            )
        )
        fulfillment_allowed = (
            order.sales_channel != SalesChannel.MEAL_PREORDER
            and campaign_ready
        )
        manual_fulfillment_allowed = (
            fulfillment_allowed
            and order.fulfillment_method != FulfillmentMethod.ECPAY_LOGISTICS
        )
        if (
            order.payment_status == PaymentStatus.PAID
            and fulfillment_allowed
            and order.fulfillment_status
            == FulfillmentStatus.PENDING_CONFIRMATION
        ):
            actions.append("start_preparing")
        if (
            order.payment_status == PaymentStatus.PAID
            and manual_fulfillment_allowed
        ):
            if order.fulfillment_status == FulfillmentStatus.PREPARING:
                actions.append("mark_ready")
            elif order.fulfillment_status == FulfillmentStatus.READY_FOR_PICKUP:
                actions.append("mark_picked_up")
        fulfillment = order.__dict__.get("fulfillment")
        shipment = (
            fulfillment.__dict__.get("shipment")
            if fulfillment is not None
            else None
        )
        if (
            order.payment_status == PaymentStatus.PAID
            and order.fulfillment_method == FulfillmentMethod.ECPAY_LOGISTICS
            and campaign_ready
            and order.fulfillment_status == FulfillmentStatus.PREPARING
            and fulfillment is not None
            and fulfillment.status == FulfillmentState.PREPARING
            and shipment is not None
            and shipment.status == ShipmentStatus.READY_TO_CREATE
        ):
            actions.append("create_shipment")
        if (
            get_settings().environment.strip().lower()
            in {"development", "sandbox", "test"}
            and shipment is not None
            and shipment.status
            in {
                ShipmentStatus.CREATED,
                ShipmentStatus.IN_TRANSIT,
                ShipmentStatus.EXCEPTION,
            }
        ):
            actions.append("advance_shipment")
    return list(dict.fromkeys(actions))


def order_fulfillment_is_irreversible(order: Order) -> bool:
    fulfillment = order.__dict__.get("fulfillment")
    shipment = (
        fulfillment.__dict__.get("shipment")
        if fulfillment is not None
        else None
    )
    return (
        order.fulfillment_status == FulfillmentStatus.PICKED_UP
        or (
            fulfillment is not None
            and fulfillment.status
            in {
                FulfillmentState.PICKED_UP,
                FulfillmentState.SHIPPED,
                FulfillmentState.DELIVERED,
            }
        )
        or (
            shipment is not None
            and shipment.status
            in {
                ShipmentStatus.CREATED,
                ShipmentStatus.IN_TRANSIT,
                ShipmentStatus.DELIVERED,
                ShipmentStatus.EXCEPTION,
            }
        )
    )


def ensure_self_cancel_allowed(
    order: Order, now: Optional[datetime] = None
) -> None:
    if "cancel" not in order_available_actions(order, False, now):
        raise DomainError("此訂單目前無法由買家取消")
