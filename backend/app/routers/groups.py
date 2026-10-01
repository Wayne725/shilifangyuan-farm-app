from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import (
    get_current_user,
    membership_type_for_user,
    require_admin,
)
from ..database import get_session
from ..config import Settings, get_settings
from ..sales_scope import require_sales_scope_allows
from ..integrations.invoice_service import invoice_context_from_settings
from ..domain import (
    DomainError,
    aware,
    campaign_available_quantity,
    confirm_campaign,
    included_tax_amount,
    order_fulfillment_is_irreversible,
    price_for_membership,
    validate_campaign_schedule,
    validate_group_join,
)
from ..models import (
    AdminAudit,
    FulfillmentMethod,
    FulfillmentState,
    FulfillmentStatus,
    GroupBundle,
    GroupBundleItem,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    InventoryReservation,
    Order,
    OrderFulfillment,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentStatus,
    PickupLocation,
    Product,
    ReservationStatus,
    SalesChannel,
    Shipment,
    ShippingRate,
    ShippingChannel,
    TaxType,
    TargetType,
    User,
)
from ..schemas import (
    CampaignCreate,
    CampaignDecision,
    CampaignRead,
    CampaignReject,
    CampaignUpdate,
    GroupJoinRequest,
    GroupJoinQuoteRead,
    GroupJoinQuoteRequest,
    OrderRead,
)
from .orders import (
    order_read,
    release_active_reservations,
    request_order_refund,
)


groups_router = APIRouter(prefix="/v1/group-campaigns", tags=["groups"])


def make_order_number() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    return f"SLF{timestamp}{secrets.token_hex(2).upper()}"


def campaign_read(campaign: GroupCampaign) -> CampaignRead:
    return CampaignRead(
        id=campaign.id,
        source_proposal_id=campaign.source_proposal_id,
        target_type=campaign.target_type,
        target_id=campaign.target_id,
        title=campaign.title,
        description=campaign.description,
        image_url=campaign.image_url,
        member_price=campaign.member_price,
        nonmember_price=campaign.nonmember_price,
        min_paid_quantity=campaign.min_paid_quantity,
        supply_cap=campaign.supply_cap,
        per_user_cap=campaign.per_user_cap,
        paid_quantity=campaign.paid_quantity,
        reserved_quantity=campaign.reserved_quantity,
        available_quantity=campaign_available_quantity(campaign),
        deadline=campaign.deadline,
        estimated_pickup_start=campaign.estimated_pickup_start,
        estimated_pickup_end=campaign.estimated_pickup_end,
        final_pickup_at=campaign.final_pickup_at,
        decision_status=campaign.decision_status,
        intake_status=campaign.intake_status,
        confirmation_deadline=campaign.confirmation_deadline,
        confirmed_at=campaign.confirmed_at,
        core_locked_at=campaign.core_locked_at,
        can_ship=campaign.can_ship,
        shipping_temperature=campaign.shipping_temperature,
        allowed_shipping_channels=campaign.allowed_shipping_channels or [],
        created_at=campaign.created_at,
    )


async def target_order_snapshot(
    session: AsyncSession,
    target_type: TargetType,
    target_id: str,
) -> Tuple[str, str, TaxType, Optional[str], Optional[str]]:
    if target_type == TargetType.PRODUCT:
        product = await session.scalar(
            select(Product).where(
                Product.id == target_id, Product.is_active.is_(True)
            )
        )
        if product is None:
            raise HTTPException(status_code=422, detail="選擇的商品不存在")
        return (
            product.name,
            product.unit,
            product.tax_type,
            product.id,
            None,
        )
    bundle = await session.scalar(
        select(GroupBundle)
        .where(
            GroupBundle.id == target_id, GroupBundle.is_active.is_(True)
        )
        .options(
            selectinload(GroupBundle.items).selectinload(
                GroupBundleItem.product
            )
        )
    )
    if bundle is None:
        raise HTTPException(status_code=422, detail="選擇的團購套組不存在")
    tax_type = (
        TaxType.TAXABLE
        if any(
            item.product.tax_type == TaxType.TAXABLE for item in bundle.items
        )
        else TaxType.TAX_EXEMPT
    )
    return bundle.name, "組", tax_type, None, bundle.id


async def _committed_group_quantity(
    session: AsyncSession,
    campaign_id: str,
    user_id: str,
) -> int:
    existing_orders = list(
        await session.scalars(
            select(Order)
            .where(
                Order.group_campaign_id == campaign_id,
                Order.user_id == user_id,
                Order.fulfillment_status != FulfillmentStatus.CANCELLED,
                Order.payment_status.notin_(
                    [
                        PaymentStatus.FAILED,
                        PaymentStatus.EXPIRED,
                        PaymentStatus.LATE_PAID_REFUND_REQUIRED,
                        PaymentStatus.REFUND_PENDING,
                        PaymentStatus.REFUNDED,
                    ]
                ),
            )
            .options(selectinload(Order.items))
        )
    )
    return sum(
        item.quantity
        for order in existing_orders
        for item in order.items
    )


async def load_campaign(
    session: AsyncSession, campaign_id: str, lock: bool = False
) -> Optional[GroupCampaign]:
    query = select(GroupCampaign).where(GroupCampaign.id == campaign_id)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


@groups_router.get("", response_model=List[CampaignRead])
async def list_campaigns(
    session: AsyncSession = Depends(get_session),
) -> List[CampaignRead]:
    campaigns = await session.scalars(
        select(GroupCampaign).order_by(GroupCampaign.created_at.desc())
    )
    return [campaign_read(campaign) for campaign in campaigns]


@groups_router.get("/{campaign_id}", response_model=CampaignRead)
async def get_campaign(
    campaign_id: str,
    session: AsyncSession = Depends(get_session),
) -> CampaignRead:
    campaign = await load_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")
    return campaign_read(campaign)


@groups_router.post(
    "",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign(
    body: CampaignCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> CampaignRead:
    await target_order_snapshot(session, body.target_type, body.target_id)
    if body.source_proposal_id is not None:
        raise HTTPException(
            status_code=422,
            detail="由投票轉團請使用提案的 convert API",
        )
    campaign = GroupCampaign(
        **body.model_dump(),
        created_by_id=admin.id,
    )
    try:
        validate_campaign_schedule(campaign, datetime.now(timezone.utc))
    except DomainError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.add(campaign)
    await session.flush()
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="campaign.create",
            aggregate_type="group_campaign",
            aggregate_id=campaign.id,
            data={"title": campaign.title},
        )
    )
    await session.commit()
    await session.refresh(campaign)
    return campaign_read(campaign)


CAMPAIGN_CORE_FIELDS = {
    "target_type",
    "target_id",
    "member_price",
    "nonmember_price",
    "min_paid_quantity",
    "supply_cap",
    "per_user_cap",
    "deadline",
    "can_ship",
    "shipping_temperature",
    "allowed_shipping_channels",
}


@groups_router.patch("/{campaign_id}", response_model=CampaignRead)
async def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> CampaignRead:
    campaign = await load_campaign(session, campaign_id, lock=True)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return campaign_read(campaign)

    candidate_values = {
        "source_proposal_id": campaign.source_proposal_id,
        "target_type": campaign.target_type,
        "target_id": campaign.target_id,
        "title": campaign.title,
        "description": campaign.description,
        "image_url": campaign.image_url,
        "member_price": campaign.member_price,
        "nonmember_price": campaign.nonmember_price,
        "min_paid_quantity": campaign.min_paid_quantity,
        "supply_cap": campaign.supply_cap,
        "per_user_cap": campaign.per_user_cap,
        "deadline": aware(campaign.deadline),
        "estimated_pickup_start": aware(campaign.estimated_pickup_start),
        "estimated_pickup_end": aware(campaign.estimated_pickup_end),
        "can_ship": campaign.can_ship,
        "shipping_temperature": campaign.shipping_temperature,
        "allowed_shipping_channels": campaign.allowed_shipping_channels or [],
        **updates,
    }
    try:
        candidate = CampaignCreate(**candidate_values)
        validate_campaign_schedule(candidate, datetime.now(timezone.utc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    candidate_dump = candidate.model_dump(exclude={"source_proposal_id"})
    fields_to_consider = set(updates)
    if "can_ship" in updates and not candidate.can_ship:
        fields_to_consider.update(
            {"shipping_temperature", "allowed_shipping_channels"}
        )
    changed_fields = {
        field
        for field in fields_to_consider
        if getattr(campaign, field) != candidate_dump[field]
    }
    changed_core_fields = changed_fields & CAMPAIGN_CORE_FIELDS
    if changed_core_fields and campaign.core_locked_at is not None:
        raise HTTPException(
            status_code=409,
            detail="首筆付款成功後，團購核心條件不可修改",
        )
    if changed_core_fields:
        now = datetime.now(timezone.utc)
        active_reservation_id = await session.scalar(
            select(InventoryReservation.id)
            .where(
                InventoryReservation.group_campaign_id == campaign.id,
                InventoryReservation.status == ReservationStatus.ACTIVE,
                InventoryReservation.expires_at > now,
            )
            .limit(1)
        )
        if active_reservation_id is not None:
            raise HTTPException(
                status_code=409,
                detail="尚有有效付款保留，暫時無法修改團購核心條件",
            )

    if (
        candidate.target_type != campaign.target_type
        or candidate.target_id != campaign.target_id
    ):
        await target_order_snapshot(
            session, candidate.target_type, candidate.target_id
        )

    for field in changed_fields:
        setattr(campaign, field, candidate_dump[field])
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="campaign.update",
            aggregate_type="group_campaign",
            aggregate_id=campaign.id,
            data={"changed_fields": sorted(changed_fields)},
        )
    )
    await session.commit()
    await session.refresh(campaign)
    return campaign_read(campaign)


@groups_router.post(
    "/{campaign_id}/quote",
    response_model=GroupJoinQuoteRead,
)
async def quote_group_join(
    campaign_id: str,
    body: GroupJoinQuoteRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GroupJoinQuoteRead:
    campaign = await load_campaign(session, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")
    committed_quantity = await _committed_group_quantity(
        session,
        campaign.id,
        user.id,
    )
    try:
        validate_group_join(
            campaign,
            body.quantity,
            committed_quantity,
            datetime.now(timezone.utc),
        )
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    membership_type = membership_type_for_user(user)
    unit_price = price_for_membership(
        campaign.member_price,
        campaign.nonmember_price,
        membership_type,
    )
    product_subtotal = unit_price * body.quantity
    shipping_fee = 0
    if body.fulfillment_method == FulfillmentMethod.ECPAY_LOGISTICS:
        channel = body.shipping_channel
        if (
            not campaign.can_ship
            or campaign.shipping_temperature is None
            or channel not in (campaign.allowed_shipping_channels or [])
        ):
            raise HTTPException(status_code=409, detail="此團購不支援選擇的物流通路")
        today = datetime.now(timezone.utc).date()
        rate = await session.scalar(
            select(ShippingRate)
            .where(
                ShippingRate.channel == channel,
                ShippingRate.temperature == campaign.shipping_temperature,
                ShippingRate.is_active.is_(True),
                ShippingRate.effective_from <= today,
                or_(
                    ShippingRate.effective_to.is_(None),
                    ShippingRate.effective_to >= today,
                ),
            )
            .order_by(ShippingRate.effective_from.desc())
            .limit(1)
        )
        if rate is None:
            raise HTTPException(status_code=422, detail="找不到適用的物流費率")
        if product_subtotal < rate.free_shipping_threshold:
            shipping_fee = rate.fee
    return GroupJoinQuoteRead(
        membership_type=membership_type,
        quantity=body.quantity,
        unit_price=unit_price,
        product_subtotal=product_subtotal,
        shipping_fee=shipping_fee,
        amount_total=product_subtotal + shipping_fee,
    )


@groups_router.post(
    "/{campaign_id}/join",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
)
async def join_campaign(
    campaign_id: str,
    body: GroupJoinRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> OrderRead:
    require_sales_scope_allows(settings)
    campaign = await load_campaign(session, campaign_id, lock=True)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")
    committed_quantity = await _committed_group_quantity(
        session,
        campaign.id,
        user.id,
    )
    try:
        validate_group_join(
            campaign,
            body.quantity,
            committed_quantity,
            datetime.now(timezone.utc),
        )
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    name, unit, tax_type, product_id, bundle_id = await target_order_snapshot(
        session, campaign.target_type, campaign.target_id
    )
    membership_type = membership_type_for_user(user)
    unit_price = price_for_membership(
        campaign.member_price,
        campaign.nonmember_price,
        membership_type,
    )
    if body.fulfillment_method == FulfillmentMethod.ECPAY_LOGISTICS and (
        not campaign.can_ship
        or campaign.shipping_temperature is None
        or body.shipping_channel not in (campaign.allowed_shipping_channels or [])
    ):
        raise HTTPException(status_code=409, detail="此團購不支援選擇的物流通路")
    pickup_location = None
    if body.pickup_location_id is not None:
        if body.fulfillment_method != FulfillmentMethod.COOPERATIVE_PICKUP:
            raise HTTPException(
                status_code=422,
                detail="只有合作社取貨可選擇領取地點",
            )
        pickup_location = await session.scalar(
            select(PickupLocation).where(
                PickupLocation.id == body.pickup_location_id,
                PickupLocation.is_active.is_(True),
            )
        )
        if pickup_location is None:
            raise HTTPException(status_code=422, detail="找不到可用的領取地點")
    order = Order(
        order_number=make_order_number(),
        order_kind=OrderKind.GROUP,
        invoice_provider_context=invoice_context_from_settings(settings),
        sales_channel=SalesChannel.GROUP,
        fulfillment_method=body.fulfillment_method,
        user_id=user.id,
        group_campaign=campaign,
        membership_type_snapshot=membership_type,
        amount_total=unit_price * body.quantity,
        tax_amount=included_tax_amount(unit_price * body.quantity, tax_type),
        contact_email=body.contact_email.lower(),
        invoice_buyer_type=body.invoice_buyer_type,
        invoice_buyer_tax_id=(
            body.invoice_buyer_tax_id.strip()
            if body.invoice_buyer_tax_id
            else None
        ),
        invoice_buyer_name=(
            body.invoice_buyer_name.strip()
            if body.invoice_buyer_name
            else None
        ),
        invoice_buyer_email=(
            str(body.invoice_buyer_email).lower()
            if body.invoice_buyer_email
            else None
        ),
        invoice_carrier_type=body.invoice_carrier_type,
        invoice_carrier_value=(
            body.invoice_carrier_value.strip().upper()
            if body.invoice_carrier_value
            else None
        ),
        items=[
            OrderItem(
                source_product_id=product_id,
                source_bundle_id=bundle_id,
                product_name=name,
                unit_label=unit,
                quantity=body.quantity,
                unit_price=unit_price,
                subtotal=unit_price * body.quantity,
                tax_type=tax_type,
            )
        ],
        fulfillment=OrderFulfillment(
            method=body.fulfillment_method,
            status=FulfillmentState.PENDING_CONFIRMATION,
            pickup_location_id=(
                pickup_location.id if pickup_location is not None else None
            ),
            pickup_location=(
                pickup_location.name if pickup_location is not None else None
            ),
        ),
    )
    session.add(order)
    await session.commit()
    order = await session.scalar(
        select(Order)
        .where(Order.id == order.id)
        .options(
            selectinload(Order.items),
            selectinload(Order.group_campaign),
            selectinload(Order.invoice),
            selectinload(Order.fulfillment).selectinload(
                OrderFulfillment.shipment
            ),
        )
    )
    return order_read(order, False)


@groups_router.post(
    "/{campaign_id}/admin/confirm", response_model=CampaignRead
)
async def confirm_group(
    campaign_id: str,
    body: CampaignDecision,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> CampaignRead:
    campaign = await load_campaign(session, campaign_id, lock=True)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")
    try:
        confirm_campaign(
            campaign, body.final_pickup_at, datetime.now(timezone.utc)
        )
    except DomainError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    paid_orders = list(
        await session.scalars(
            select(Order).where(
                Order.group_campaign_id == campaign.id,
                Order.payment_status == PaymentStatus.PAID,
            )
        )
    )
    notified_users: Set[str] = set()
    for order in paid_orders:
        if order.user_id in notified_users:
            continue
        notified_users.add(order.user_id)
    session.add_all(
        [
            AdminAudit(
                actor_id=admin.id,
                action="campaign.confirm",
                aggregate_type="group_campaign",
                aggregate_id=campaign.id,
                data={"threshold_version": campaign.threshold_version},
            ),
            OutboxEvent(
                event_type="group.confirmed",
                aggregate_type="group_campaign",
                aggregate_id=campaign.id,
                payload={
                    "user_ids": list(notified_users),
                    "final_pickup_at": body.final_pickup_at.isoformat(),
                },
            ),
        ]
    )
    await session.commit()
    return campaign_read(campaign)


async def close_campaign_with_refunds(
    session: AsyncSession,
    campaign: GroupCampaign,
    admin: User,
    decision_status: GroupDecisionStatus,
    reason: str,
) -> None:
    orders = list(
        await session.scalars(
            select(Order)
            .where(Order.group_campaign_id == campaign.id)
            .options(
                selectinload(Order.items),
                selectinload(Order.reservations),
                selectinload(Order.group_campaign),
                selectinload(Order.fulfillment).selectinload(
                    OrderFulfillment.shipment
                ),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    )
    for order in orders:
        fulfillment = order.__dict__.get("fulfillment")
        shipment = (
            fulfillment.__dict__.get("shipment")
            if fulfillment is not None
            else None
        )
        if shipment is not None:
            await session.scalar(
                select(Shipment)
                .where(Shipment.id == shipment.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
    if any(order_fulfillment_is_irreversible(order) for order in orders):
        raise HTTPException(
            status_code=409,
            detail="已有訂單建立物流或完成交付，不能取消整團",
        )
    campaign.decision_status = decision_status
    campaign.intake_status = GroupIntakeStatus.CLOSED
    campaign.rejected_reason = reason
    for order in orders:
        if order.payment_status == PaymentStatus.PAID:
            await request_order_refund(
                session, order, admin, reason
            )
        elif order.payment_status == PaymentStatus.PENDING:
            await release_active_reservations(session, order)
            order.payment_status = PaymentStatus.EXPIRED
            order.fulfillment_status = FulfillmentStatus.CANCELLED
    session.add_all(
        [
            AdminAudit(
                actor_id=admin.id,
                action=f"campaign.{decision_status.value}",
                aggregate_type="group_campaign",
                aggregate_id=campaign.id,
                reason=reason,
            ),
            OutboxEvent(
                event_type=f"group.{decision_status.value}",
                aggregate_type="group_campaign",
                aggregate_id=campaign.id,
                payload={"reason": reason},
            ),
        ]
    )


@groups_router.post(
    "/{campaign_id}/admin/reject", response_model=CampaignRead
)
async def reject_group(
    campaign_id: str,
    body: CampaignReject,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> CampaignRead:
    campaign = await load_campaign(session, campaign_id, lock=True)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")
    if campaign.decision_status != GroupDecisionStatus.PENDING_CONFIRMATION:
        raise HTTPException(status_code=409, detail="此團目前無法拒絕成團")
    await close_campaign_with_refunds(
        session,
        campaign,
        admin,
        GroupDecisionStatus.REJECTED,
        body.reason,
    )
    await session.commit()
    return campaign_read(campaign)


@groups_router.post(
    "/{campaign_id}/admin/cancel", response_model=CampaignRead
)
async def cancel_group(
    campaign_id: str,
    body: CampaignReject,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> CampaignRead:
    campaign = await load_campaign(session, campaign_id, lock=True)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")
    if campaign.decision_status in {
        GroupDecisionStatus.REJECTED,
        GroupDecisionStatus.CANCELLED,
        GroupDecisionStatus.FAILED_UNMET,
        GroupDecisionStatus.EXPIRED_UNCONFIRMED,
    }:
        raise HTTPException(status_code=409, detail="此團已關閉")
    await close_campaign_with_refunds(
        session,
        campaign,
        admin,
        GroupDecisionStatus.CANCELLED,
        body.reason,
    )
    await session.commit()
    return campaign_read(campaign)


router = groups_router
