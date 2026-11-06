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
    InvoiceBuyerType,
    InvoiceStatus,
    MealEventStatus,
    MembershipType,
    Order,
    OrderKind,
    PaymentMethod,
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


def member_sale_is_tax_exempt(
    membership_type: MembershipType,
    invoice_buyer_type: InvoiceBuyerType,
) -> bool:
    return (
        membership_type == MembershipType.MEMBER
        and invoice_buyer_type == InvoiceBuyerType.PERSONAL
    )


def sale_tax_type(
    product_tax_type: TaxType,
    membership_type: MembershipType,
    invoice_buyer_type: InvoiceBuyerType,
) -> TaxType:
    if member_sale_is_tax_exempt(membership_type, invoice_buyer_type):
        return TaxType.TAX_EXEMPT
    return product_tax_type


def order_shipping_tax_type(order: Order) -> TaxType:
    return (
        TaxType.TAX_EXEMPT
        if order.member_tax_exempt_snapshot
        else TaxType.TAXABLE
    )


def order_tax_amount(order: Order, shipping_fee: int = 0) -> int:
    return sum(
        included_tax_amount(item.subtotal, item.tax_type)
        for item in order.items
    ) + included_tax_amount(shipping_fee, order_shipping_tax_type(order))


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
    if campaign.confirmation_deadline is None:
        campaign.confirmation_deadline = aware(campaign.deadline) + timedelta(hours=72)
    if aware(campaign.confirmation_deadline) <= aware(campaign.deadline):
        raise DomainError("管理員處理提醒日必須晚於付款截止")
    if aware(campaign.confirmation_deadline) > aware(campaign.estimated_pickup_start):
        raise DomainError("管理員處理提醒日不可晚於預估取貨開始")
    if campaign.min_paid_quantity > campaign.supply_cap:
        raise DomainError("成團門檻不可高於供應上限")
    if aware(campaign.deadline) <= now:
        raise DomainError("共同購買截止時間必須在未來")
    if aware(campaign.estimated_pickup_start) < aware(campaign.deadline):
        raise DomainError("預估取貨起始時間不可早於共同購買截止")
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
        raise DomainError("共同購買已截止")
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
    refresh_campaign_intake(campaign, current)


def refresh_campaign_intake(campaign: GroupCampaign, now: datetime) -> None:
    if campaign.decision_status not in {GroupDecisionStatus.RECRUITING, GroupDecisionStatus.CONFIRMED}:
        return
    if now >= aware(campaign.deadline):
        campaign.intake_status = (GroupIntakeStatus.CLOSED if campaign.decision_status == GroupDecisionStatus.CONFIRMED else GroupIntakeStatus.SETTLING)
    elif campaign.decision_status == GroupDecisionStatus.RECRUITING or campaign.legacy_confirmed_intake:
        campaign.intake_status = (GroupIntakeStatus.FULL if campaign_available_quantity(campaign) == 0 else GroupIntakeStatus.OPEN)


def remove_paid_quantity(campaign: GroupCampaign, quantity: int, now: Optional[datetime] = None) -> None:
    campaign.paid_quantity = max(0, campaign.paid_quantity - quantity)
    campaign.threshold_version = (campaign.threshold_version or 0) + 1
    refresh_campaign_intake(campaign, now or utcnow())


def confirm_campaign(
    campaign: GroupCampaign,
    final_pickup_at: datetime,
    now: Optional[datetime] = None,
    *, expected_threshold_version: int,
    allow_below_threshold: bool = False,
    reason: str = "",
) -> None:
    current = now or utcnow()
    if current < aware(campaign.deadline):
        raise DomainError("付款截止後才可確認共同購買")
    if campaign.decision_status != GroupDecisionStatus.PENDING_CONFIRMATION or campaign.intake_status != GroupIntakeStatus.CLOSED:
        raise DomainError("共同購買尚在付款核對中或已完成決定")
    if expected_threshold_version != (campaign.threshold_version or 0):
        raise DomainError("付款數量已變更，請重新核對後確認")
    if campaign.paid_quantity <= 0:
        raise DomainError("沒有已付款數量，無法確認成團")
    if campaign.paid_quantity < campaign.min_paid_quantity and (not allow_below_threshold or not reason.strip()):
        raise DomainError("未達成團門檻，請勾選例外確認並填寫原因")
    if aware(final_pickup_at) < current:
        raise DomainError("最終取貨時間不可早於現在")
    campaign.final_pickup_at = final_pickup_at
    campaign.confirmed_at = current
    campaign.confirmed_paid_quantity = campaign.paid_quantity
    campaign.confirmed_min_paid_quantity = campaign.min_paid_quantity
    campaign.confirmed_threshold_version = campaign.threshold_version or 0
    campaign.confirmation_exception_reason = reason.strip() if campaign.paid_quantity < campaign.min_paid_quantity else None
    campaign.decision_status = GroupDecisionStatus.CONFIRMED
    campaign.intake_status = GroupIntakeStatus.CLOSED


def meal_payment_window_open(order: Order, now: datetime) -> bool:
    event = order.__dict__.get("meal_event")
    fulfillment = order.__dict__.get("fulfillment")
    booking_deadline = fulfillment.meal_ordering_ends_at if fulfillment is not None else None
    return (
        event is not None
        and event.status in {MealEventStatus.PUBLISHED, MealEventStatus.PICKUP_OPEN}
        and aware(event.ordering_starts_at) <= now < aware(event.ordering_ends_at)
        and (booking_deadline is None or now < aware(booking_deadline))
    )


def meal_cancellation_request_allowed(order: Order, now: Optional[datetime] = None) -> bool:
    current = now or utcnow()
    event = order.__dict__.get("meal_event")
    fulfillment = order.__dict__.get("fulfillment")
    return (
        order.sales_channel == SalesChannel.MEAL_PREORDER
        and order.payment_status in {PaymentStatus.PENDING, PaymentStatus.PAID}
        and order.cancelled_at is None
        and order.fulfillment_status not in {FulfillmentStatus.PICKED_UP, FulfillmentStatus.CANCELLED}
        and fulfillment is not None
        and fulfillment.status in {FulfillmentState.PENDING_CONFIRMATION, FulfillmentState.PREPARING, FulfillmentState.READY_FOR_PICKUP}
        and event is not None
        and event.status not in {MealEventStatus.CANCELLED, MealEventStatus.COMPLETED}
        and current < aware(event.pickup_ends_at)
    )


def admin_order_cancellation_allowed(order: Order, now: Optional[datetime] = None) -> bool:
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        return meal_cancellation_request_allowed(order, now) and not order_fulfillment_is_irreversible(order)
    fulfillment = order.__dict__.get("fulfillment")
    return (
        order.payment_status in {PaymentStatus.PENDING, PaymentStatus.PAID}
        and order.cancelled_at is None
        and order.fulfillment_status != FulfillmentStatus.CANCELLED
        and (fulfillment is None or fulfillment.status not in {FulfillmentState.CANCELLED, FulfillmentState.NO_SHOW})
        and not order_fulfillment_is_irreversible(order)
    )


def order_amendment_blocked_reason(order: Order, now: Optional[datetime] = None) -> Optional[str]:
    if getattr(order, "customer_kind", "account") == "walk_in":
        return "現場交易不適用網站商品數量修改"
    current = now or utcnow()
    if order.payment_status != PaymentStatus.PENDING or order.paid_at is not None:
        return "僅可修改未付款訂單；已付款請由管理員整單退款後重新下單"
    fulfillment = order.__dict__.get("fulfillment")
    if (order.cancelled_at is not None or order.fulfillment_status != FulfillmentStatus.PENDING_CONFIRMATION
        or (fulfillment is not None and fulfillment.status != FulfillmentState.PENDING_CONFIRMATION)):
        return "訂單已進入備貨或已結束，無法修改數量"
    if order.invoice_status not in {InvoiceStatus.NOT_ELIGIBLE, InvoiceStatus.EXTERNAL} or order.__dict__.get("invoice") is not None:
        return "訂單已有發票紀錄，請由管理員查核"
    if any(attempt.provider_response.get("payment_correction") for attempt in order.__dict__.get("payment_attempts", [])):
        return "訂單有收款更正紀錄，請由管理員處理"
    if any(request.status == "pending" for request in order.__dict__.get("cancellation_requests", [])):
        return "退單申請審核中，無法修改數量"
    if order.sales_channel == SalesChannel.MEAL_PREORDER:
        if not meal_payment_window_open(order, current):
            return "已超過餐點訂購期限，無法修改數量"
        from .meal_booking import validate_meal_booking_day
        try:
            validate_meal_booking_day(order.meal_event, current)
        except ValueError as exc:
            return str(exc)
    elif order.group_campaign_id is not None:
        from .group_review import order_has_uncertain_group_payment, order_matches_group_contract
        if order_has_uncertain_group_payment(order, current):
            return "付款結果仍待核對，暫時無法修改數量"
        campaign = order.__dict__.get("group_campaign")
        if not order_matches_group_contract(order, campaign):
            return "共同購買核心條件已更新，請回共同購買頁重新確認"
        if (campaign is None or campaign.intake_status not in {GroupIntakeStatus.OPEN, GroupIntakeStatus.FULL}
            or campaign.decision_status not in {GroupDecisionStatus.RECRUITING, GroupDecisionStatus.CONFIRMED}
            or current >= aware(campaign.deadline)):
            return "共同購買已截止或停止接單，無法修改數量"
    if fulfillment is not None:
        shipment = fulfillment.__dict__.get("shipment")
        if shipment is not None and shipment.status not in {ShipmentStatus.DRAFT, ShipmentStatus.SELECTION_PENDING, ShipmentStatus.READY_TO_CREATE}:
            return "物流單已成立，請由管理員處理"
    return None


def order_available_actions(
    order: Order,
    viewer_is_admin: bool = False,
    now: Optional[datetime] = None,
) -> List[str]:
    current = now or utcnow()
    actions: List[str] = []
    if order_amendment_blocked_reason(order, current) is None:
        actions.append("amend_quantities")
    fulfillment = order.__dict__.get("fulfillment")
    is_meal = order.sales_channel == SalesChannel.MEAL_PREORDER
    meal_fulfillment_closed = is_meal and (
        order.cancelled_at is not None
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
        campaign = order.__dict__.get("group_campaign")
        from .group_review import order_has_uncertain_group_payment, current_order_attempt, order_matches_group_contract
        has_open_group_checkout = any(
            current_order_attempt(attempt, order) and attempt.status == PaymentStatus.PENDING
            and aware(attempt.expires_at) > current and bool(attempt.checkout_payload)
            for attempt in order.__dict__.get("payment_attempts", []))
        group_payment_ready = not order.group_campaign_id or (
            campaign is not None and order_matches_group_contract(order, campaign) and current < aware(campaign.deadline)
            and campaign.decision_status in {GroupDecisionStatus.RECRUITING, GroupDecisionStatus.CONFIRMED}
            and (campaign.intake_status == GroupIntakeStatus.OPEN
                 or (campaign.intake_status == GroupIntakeStatus.FULL and has_open_group_checkout))
            and not order_has_uncertain_group_payment(order, current))
        if payment_ready and group_payment_ready and order.payment_method in {None, PaymentMethod.ONLINE} and (
            not is_meal
            or (not meal_fulfillment_closed and meal_payment_window_open(order, current))
        ):
            actions.append("pay")
    if viewer_is_admin:
        if getattr(order, "customer_kind", "account") == "walk_in" and order.payment_status == PaymentStatus.PAID:
            actions.append("refund_walk_in")
        if admin_order_cancellation_allowed(order, current):
            actions.append("cancel")
        event = order.__dict__.get("meal_event")
        if (order.payment_method == PaymentMethod.CASH_ON_PICKUP
            and order.payment_status == PaymentStatus.PENDING and not meal_fulfillment_closed
            and event is not None and event.status not in {MealEventStatus.CANCELLED, MealEventStatus.COMPLETED, MealEventStatus.DRAFT}
            and current < aware(event.pickup_ends_at)):
            actions.append("confirm_cash_payment")
        if (order.payment_method == PaymentMethod.CASH_ON_PICKUP
            and order.payment_status == PaymentStatus.PAID and not meal_fulfillment_closed
            and getattr(order, "customer_kind", "account") != "walk_in"
            and not order_fulfillment_is_irreversible(order)):
            actions.append("mark_cash_unpaid")
        if order.payment_method == PaymentMethod.CASH_ON_PICKUP and order.payment_status == PaymentStatus.REFUND_PENDING:
            actions.append("confirm_cash_refund")
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


def ensure_admin_cancel_allowed(
    order: Order, now: Optional[datetime] = None
) -> None:
    if not admin_order_cancellation_allowed(order, now):
        raise DomainError("訂單已取消、交付或結束履約，目前不可退單")
