from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PickupLocation


def available_pickup_locations(*, business_meal: bool = False) -> Select[tuple[PickupLocation]]:
    query = select(PickupLocation).where(PickupLocation.is_active.is_(True))
    if not business_meal:
        query = query.where(PickupLocation.business_meal_only.is_(False))
    return query


async def checkout_pickup_location(
    session: AsyncSession, location_id: str, *, business_meal: bool = False,
) -> PickupLocation | None:
    query = available_pickup_locations(business_meal=business_meal).where(PickupLocation.id == location_id)
    return await session.scalar(query.with_for_update(read=True).execution_options(populate_existing=True))


async def selectable_pickup_location_ids(session: AsyncSession, location_ids: list[str]) -> list[str]:
    if not location_ids:
        return []
    found = set(await session.scalars(
        available_pickup_locations().where(PickupLocation.id.in_(location_ids)).with_only_columns(PickupLocation.id)
    ))
    if found != set(location_ids):
        raise HTTPException(422, "開放的取貨地點包含已停用或不存在的地點，請重新選擇")
    return location_ids


def pickup_location_allowed(allowed_ids: list[str] | None, location_id: str | None) -> bool:
    return not allowed_ids or location_id in allowed_ids
