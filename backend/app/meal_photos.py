from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .auth import require_admin
from .database import get_session
from .models import AdminAudit, Meal, MealOptionGroup, MealPhotoAsset, User, new_id, utcnow
from .schemas import MealRead
from .photo_uploads import read_photo_upload

router = APIRouter(tags=["meals"])


async def photo_target(session: AsyncSession, meal_id: str, option_id: str | None):
    meal = await session.scalar(select(Meal).where(Meal.id == meal_id).options(
        selectinload(Meal.option_groups).selectinload(MealOptionGroup.options)
    ).with_for_update())
    if meal is None:
        raise HTTPException(404, "找不到餐點")
    target = meal
    if option_id is not None:
        target = next((option for group in meal.option_groups for option in group.options if option.id == option_id), None)
        if target is None:
            raise HTTPException(422, "附餐選項不屬於此餐點，請重新載入")
    return meal, target


async def clear_slot(session: AsyncSession, meal_id: str, option_id: str | None):
    await session.execute(delete(MealPhotoAsset).where(
        MealPhotoAsset.meal_id == meal_id, MealPhotoAsset.option_id == option_id,
    ))


@router.post("/v1/admin/meals/{meal_id}/photo", response_model=MealRead)
async def upload_photo(
    meal_id: str, request: Request, option_id: str | None = None,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    # No File/Body dependency: authenticate before consuming any upload bytes.
    normalized = await read_photo_upload(request)
    meal, target = await photo_target(session, meal_id, option_id)
    await clear_slot(session, meal_id, option_id)
    photo = MealPhotoAsset(id=new_id(), meal_id=meal_id, option_id=option_id, content=normalized)
    session.add(photo)
    target.image_url = f"/v1/meal-photos/{photo.id}"
    meal.updated_at = utcnow()
    session.add(AdminAudit(actor_id=admin.id, action="meal.photo_upload", aggregate_type="meal", aggregate_id=meal.id,
                           data={"option_id": option_id, "size_bytes": len(normalized)}))
    await session.commit()
    return MealRead.model_validate(meal)


@router.delete("/v1/admin/meals/{meal_id}/photo", response_model=MealRead)
async def remove_photo(
    meal_id: str, option_id: str | None = None,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    meal, target = await photo_target(session, meal_id, option_id)
    await clear_slot(session, meal_id, option_id)
    target.image_url = None
    meal.updated_at = utcnow()
    session.add(AdminAudit(actor_id=admin.id, action="meal.photo_remove", aggregate_type="meal", aggregate_id=meal.id,
                           data={"option_id": option_id}))
    await session.commit()
    return MealRead.model_validate(meal)


@router.get("/v1/meal-photos/{photo_id}")
async def read_photo(photo_id: UUID, session: AsyncSession = Depends(get_session)):
    photo = await session.get(MealPhotoAsset, str(photo_id))
    if photo is None:
        raise HTTPException(404, "找不到照片")
    return Response(photo.content, media_type="image/webp", headers={
        "Cache-Control": "public, max-age=86400, immutable",
        "X-Content-Type-Options": "nosniff",
    })
