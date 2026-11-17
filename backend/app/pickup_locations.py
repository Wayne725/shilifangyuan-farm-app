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
