from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..meal_kitchen import MealKitchenFilters, MealKitchenStatusChange, change_meal_kitchen_status, meal_kitchen_list, meal_kitchen_ticket
from ..models import User


meal_kitchen_router = APIRouter(prefix="/v1/admin/meal-kitchen", tags=["meal-kitchen"])


@meal_kitchen_router.get("")
async def read_meal_kitchen(
    filters: Annotated[MealKitchenFilters, Query()],
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    return await meal_kitchen_list(session, filters, settings)


@meal_kitchen_router.patch("/orders/{order_id}/status")
async def update_meal_kitchen_status(
    order_id: str,
    body: MealKitchenStatusChange,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    return await change_meal_kitchen_status(session, order_id, body.status, admin.id, settings)


@meal_kitchen_router.get("/orders/{order_id}/ticket")
async def read_meal_kitchen_ticket(
    order_id: str,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    return await meal_kitchen_ticket(session, order_id, settings)
