from __future__ import annotations

import asyncio
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from .auth import require_admin
from .database import get_session
from .models import AdminAudit, Meal, MealOptionGroup, MealPhotoAsset, User, new_id, utcnow
from .schemas import MealRead

router = APIRouter(tags=["meals"])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PHOTO_BYTES = 1024 * 1024
MAX_PIXELS = 12_000_000
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
_processing = asyncio.Semaphore(1)


def normalize_photo(content: bytes) -> bytes:
    try:
        with Image.open(BytesIO(content), formats=["JPEG", "PNG", "WEBP"]) as source:
            if source.width * source.height > MAX_PIXELS or max(source.size) > 8000:
                raise HTTPException(422, "照片解析度過大，請縮小至 1200 萬像素以下")
            if getattr(source, "is_animated", False):
                raise HTTPException(422, "請上傳靜態照片")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1600, 1600))
            clean = Image.new("RGB", image.size, "white")
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                clean.paste(rgba, mask=rgba.getchannel("A"))
            else:
                clean.paste(image.convert("RGB"))
            output = BytesIO()
            clean.save(output, format="WEBP", quality=85, method=4)
            result = output.getvalue()
            if len(result) > MAX_PHOTO_BYTES:
                raise HTTPException(422, "照片內容過大，請降低解析度後重新上傳")
            return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "照片無法讀取，請使用有效的 JPG、PNG 或 WebP") from exc


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
    if request.headers.get("content-type", "").split(";")[0] not in ALLOWED_TYPES:
        raise HTTPException(415, "僅支援 JPG、PNG 或 WebP 照片")
    length = request.headers.get("content-length", "0")
    if not length.isdecimal():
        raise HTTPException(400, "照片上傳長度無效")
    if int(length) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "照片不可超過 5 MB")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "照片不可超過 5 MB")
        content.extend(chunk)
    async with _processing:
        normalized = await run_in_threadpool(normalize_photo, bytes(content))
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
