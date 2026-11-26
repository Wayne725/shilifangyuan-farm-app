"""Delete master data that was created by mistake and never used.

Anything already referenced by orders, events, campaigns or proposals stays and
must be archived or disabled instead, so history and reports keep their links.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_admin
from .config import Settings, get_settings
from .controlled_product_payments import meals_test_product_pairs
from .database import get_session
from .models import (
    AdminAudit,
    GroupBundleItem,
    GroupCampaign,
    InventoryReservation,
    Meal,
    MealEvent,
    MealEventOffering,
    MealOption,
    MealOptionGroup,
    MealPhotoAsset,
    MealScheduleTemplate,
    Order,
    OrderFulfillment,
    OrderItem,
    PickupLocation,
    Product,
    ProfilePhotoAsset,
    ProposalTargetType,
    TargetType,
    User,
    VoteProposal,
    Wish,
)

router = APIRouter(tags=["admin"])


def _snapshot(record, fields: tuple[str, ...]) -> dict:
    return jsonable_encoder({field: getattr(record, field) for field in fields})


def _record_deletion(session: AsyncSession, admin: User, kind: str, record_id: str, data: dict) -> None:
    session.add(AdminAudit(actor_id=admin.id, action=f"{kind}.delete", aggregate_type=kind,
                           aggregate_id=record_id, data=data))


async def _in_use(session: AsyncSession, *queries) -> bool:
    for query in queries:
        if await session.scalar(query.limit(1)) is not None:
            return True
    return False


async def _listed_in_pickup_limits(session: AsyncSession, location_id: str) -> bool:
    for model in (GroupCampaign, MealEvent, MealScheduleTemplate):
        for ids in await session.scalars(select(model.pickup_location_ids)):
            if location_id in (ids or []):
                return True
    return False


@router.delete("/v1/admin/pickup-locations/{location_id}", status_code=204)
async def delete_unused_pickup_location(
    location_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> Response:
    location = await session.scalar(select(PickupLocation).where(PickupLocation.id == location_id).with_for_update())
    if location is None:
        raise HTTPException(404, "找不到取貨點")
    if await _in_use(session, select(OrderFulfillment.id).where(OrderFulfillment.pickup_location_id == location_id)):
        raise HTTPException(409, "已有訂單使用這個取貨點，不能刪除；不再使用請改為停用")
    if await _listed_in_pickup_limits(session, location_id):
        raise HTTPException(409, "共同購買、便當場次或排程仍指定這個取貨點，請先從那些設定移除，或改為停用")
    _record_deletion(session, admin, "pickup_location", location_id, {"pickup_location": _snapshot(location, (
        "code", "name", "address", "instructions", "sort_order", "is_active",
        "business_meal_only", "requires_pickup_note", "retail_pickup"))})
    await session.execute(delete(PickupLocation).where(PickupLocation.id == location_id))
    await session.commit()
    return Response(status_code=204)


@router.delete("/v1/admin/meals/{meal_id}", status_code=204)
async def delete_unused_meal(
    meal_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> Response:
    meal = await session.scalar(select(Meal).where(Meal.id == meal_id).with_for_update())
    if meal is None:
        raise HTTPException(404, "找不到餐點")
    if await _in_use(session, select(MealEventOffering.id).where(MealEventOffering.meal_id == meal_id)):
        raise HTTPException(409, "這個餐點已排進便當場次，不能刪除；不再供應請改為停售")
    for offerings in await session.scalars(select(MealScheduleTemplate.offerings)):
        if any(item.get("meal_id") == meal_id for item in offerings or []):
            raise HTTPException(409, "每日排程的菜單仍包含這個餐點，請先從排程移除，或改為停售")
    _record_deletion(session, admin, "meal", meal_id, {"meal": _snapshot(meal, (
        "slug", "name", "description", "price", "tax_type", "is_active"))})
    group_ids = select(MealOptionGroup.id).where(MealOptionGroup.meal_id == meal_id).scalar_subquery()
    await session.execute(delete(MealPhotoAsset).where(MealPhotoAsset.meal_id == meal_id))
    await session.execute(delete(MealOption).where(MealOption.group_id.in_(group_ids)))
    await session.execute(delete(MealOptionGroup).where(MealOptionGroup.meal_id == meal_id))
    await session.execute(delete(Meal).where(Meal.id == meal_id))
    await session.commit()
    return Response(status_code=204)


@router.delete("/v1/admin/meal-schedules/{template_id}", status_code=204)
async def delete_unused_meal_schedule(
    template_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
) -> Response:
    template = await session.scalar(select(MealScheduleTemplate).where(MealScheduleTemplate.id == template_id)
                                    .with_for_update())
    if template is None:
        raise HTTPException(404, "找不到每日排程")
    # Checkout locks the event row before adding an order, so these locks keep orders out until we finish.
    event_ids = list(await session.scalars(select(MealEvent.id).where(MealEvent.schedule_template_id == template_id)
                                           .order_by(MealEvent.id).with_for_update()))
    offerings = select(MealEventOffering.id).where(MealEventOffering.meal_event_id.in_(event_ids))
    if event_ids and await _in_use(
        session,
        select(Order.id).where(Order.meal_event_id.in_(event_ids)),
        offerings.where(or_(MealEventOffering.reserved_quantity > 0, MealEventOffering.paid_quantity > 0)),
        select(InventoryReservation.id).where(InventoryReservation.source_meal_offering_id.in_(offerings.scalar_subquery())),
    ):
        raise HTTPException(409, "這個排程的場次已有訂單，不能刪除；不再使用請改為停用排程")
    _record_deletion(session, admin, "meal_schedule", template_id, {
        "meal_schedule": _snapshot(template, (
            "title", "location", "meal_period", "pickup_start_time", "pickup_end_time", "weekdays",
            "saturday_business_only", "sunday_business_only", "enabled", "auto_publish", "offerings",
            "pickup_location_ids")),
        "deleted_event_ids": event_ids,
    })
    if event_ids:
        await session.execute(delete(MealEventOffering).where(MealEventOffering.meal_event_id.in_(event_ids)))
        await session.execute(delete(MealEvent).where(MealEvent.id.in_(event_ids)))
    await session.execute(delete(MealScheduleTemplate).where(MealScheduleTemplate.id == template_id))
    await session.commit()
    return Response(status_code=204)


@router.delete("/v1/admin/products/{product_id}", status_code=204)
async def delete_unused_product(
    product_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    product = await session.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise HTTPException(404, "找不到商品")
    if product_id in meals_test_product_pairs(settings):
        raise HTTPException(409, "這個商品是系統設定的測試商品，不能刪除")
    if await _in_use(
        session,
        select(OrderItem.id).where(OrderItem.source_product_id == product_id),
        select(InventoryReservation.id).where(InventoryReservation.source_product_id == product_id),
        select(GroupBundleItem.id).where(GroupBundleItem.product_id == product_id),
        select(GroupCampaign.id).where(GroupCampaign.target_type == TargetType.PRODUCT, GroupCampaign.target_id == product_id),
        select(VoteProposal.id).where(VoteProposal.target_type == ProposalTargetType.PRODUCT, VoteProposal.target_id == product_id),
        select(Wish.id).where(Wish.launched_product_id == product_id),
    ):
        raise HTTPException(409, "這個商品已有訂單、共同購買或提案紀錄，不能刪除；不再販售請改用封存")
    _record_deletion(session, admin, "product", product_id, {"product": _snapshot(product, (
        "name", "slug", "sku", "product_number", "supplier_id", "category", "unit",
        "member_price", "nonmember_price", "stock_quantity", "is_active", "archived_at"))})
    await session.execute(delete(ProfilePhotoAsset).where(ProfilePhotoAsset.product_id == product_id))
    await session.execute(delete(Product).where(Product.id == product_id))
    await session.commit()
    return Response(status_code=204)
