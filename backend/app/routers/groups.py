from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, require_admin
from ..database import get_session
from ..domain import (
    DomainError,
    campaign_available_quantity,
    confirm_campaign,
    price_for_membership,
    validate_campaign_schedule,
    validate_group_join,
)
from ..models import (
    AdminAudit,
    FulfillmentStatus,
    GroupBundle,
    GroupBundleItem,
    GroupCampaign,
    GroupDecisionStatus,
    GroupIntakeStatus,
    Order,
    OrderItem,
    OrderKind,
    OutboxEvent,
    PaymentStatus,
    Product,
    TaxType,
    TargetType,
    User,
)
from ..schemas import (
    CampaignCreate,
    CampaignDecision,
    CampaignRead,
    CampaignReject,
    GroupJoinRequest,
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
) -> OrderRead:
    campaign = await load_campaign(session, campaign_id, lock=True)
    if campaign is None:
        raise HTTPException(status_code=404, detail="找不到團購")
    existing_orders = list(
        await session.scalars(
            select(Order)
            .where(
                Order.group_campaign_id == campaign.id,
                Order.user_id == user.id,
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
    committed_quantity = sum(
        item.quantity
        for order in existing_orders
        for item in order.items
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
    unit_price = price_for_membership(
        campaign.member_price,
        campaign.nonmember_price,
        user.membership_type,
    )
    order = Order(
        order_number=make_order_number(),
        order_kind=OrderKind.GROUP,
        user_id=user.id,
        group_campaign=campaign,
        membership_type_snapshot=user.membership_type,
        amount_total=unit_price * body.quantity,
        contact_email=body.contact_email.lower(),
        invoice_carrier_type=body.invoice_carrier_type,
        invoice_carrier_value=body.invoice_carrier_value,
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
    )
    session.add(order)
    await session.commit()
    order = await session.scalar(
        select(Order)
        .where(Order.id == order.id)
        .options(
            selectinload(Order.items),
            selectinload(Order.group_campaign),
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
            )
        )
    )
    if any(
        order.fulfillment_status == FulfillmentStatus.PICKED_UP
        for order in orders
    ):
        raise HTTPException(
            status_code=409, detail="已有訂單完成取貨，不能取消整團"
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
