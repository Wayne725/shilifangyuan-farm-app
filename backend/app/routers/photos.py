from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_active_member, require_admin
from ..database import get_session
from ..models import AdminAudit, GroupBundle, GroupCampaign, MemberDirectoryEntry, Product, ProfilePhotoAsset, User, new_id
from ..photo_uploads import read_photo_upload

photos_router = APIRouter(tags=["photos"])


async def product_target(session: AsyncSession, product_id: str) -> Product:
    product = await session.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise HTTPException(404, "找不到商品")
    return product


async def sync_shared_photo(session: AsyncSession, old_url: str | None, new_url: str | None):
    if old_url and old_url.startswith("/v1/product-photos/"):
        for model in (GroupBundle, GroupCampaign):
            await session.execute(update(model).where(model.image_url == old_url).values(image_url=new_url))


@photos_router.post("/v1/admin/products/{product_id}/photo")
async def upload_product_photo(product_id: str, request: Request, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    content = await read_photo_upload(request)
    product = await product_target(session, product_id)
    await session.execute(delete(ProfilePhotoAsset).where(ProfilePhotoAsset.product_id == product_id))
    photo = ProfilePhotoAsset(id=new_id(), product_id=product_id, content=content)
    session.add(photo)
    new_url = f"/v1/product-photos/{photo.id}"
    await sync_shared_photo(session, product.image_url, new_url)
    product.image_url = new_url
    session.add(AdminAudit(actor_id=admin.id, action="product.photo_upload", aggregate_type="product", aggregate_id=product_id, data={"size_bytes": len(content)}))
    await session.commit()
    return {"url": product.image_url}


@photos_router.delete("/v1/admin/products/{product_id}/photo")
async def remove_product_photo(product_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    product = await product_target(session, product_id)
    await session.execute(delete(ProfilePhotoAsset).where(ProfilePhotoAsset.product_id == product_id))
    await sync_shared_photo(session, product.image_url, None)
    product.image_url = None
    session.add(AdminAudit(actor_id=admin.id, action="product.photo_remove", aggregate_type="product", aggregate_id=product_id, data={}))
    await session.commit()
    return {"url": None}


@photos_router.get("/v1/product-photos/{photo_id}")
async def read_product_photo(photo_id: UUID, session: AsyncSession = Depends(get_session)):
    photo = await session.get(ProfilePhotoAsset, str(photo_id))
    if photo is None or photo.product_id is None:
        raise HTTPException(404, "找不到照片")
    return Response(photo.content, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400, immutable", "X-Content-Type-Options": "nosniff"})


async def own_directory(session: AsyncSession, user: User) -> MemberDirectoryEntry:
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    entry = await session.scalar(select(MemberDirectoryEntry).where(MemberDirectoryEntry.user_id == user.id))
    if entry is None:
        entry = MemberDirectoryEntry(user_id=user.id, nickname=user.display_name, is_public=False)
        session.add(entry)
    return entry


@photos_router.post("/v1/members/me/photo")
async def upload_member_photo(request: Request, user: User = Depends(require_active_member), session: AsyncSession = Depends(get_session)):
    content = await read_photo_upload(request)
    entry = await own_directory(session, user)
    await session.execute(delete(ProfilePhotoAsset).where(ProfilePhotoAsset.user_id == user.id))
    photo = ProfilePhotoAsset(id=new_id(), user_id=user.id, content=content)
    session.add(photo)
    entry.avatar_url = f"/v1/member-photos/{photo.id}"
    await session.commit()
    return {"url": entry.avatar_url}


@photos_router.delete("/v1/members/me/photo")
async def remove_member_photo(user: User = Depends(require_active_member), session: AsyncSession = Depends(get_session)):
    entry = await own_directory(session, user)
    await session.execute(delete(ProfilePhotoAsset).where(ProfilePhotoAsset.user_id == user.id))
    entry.avatar_url = None
    await session.commit()
    return {"url": None}


@photos_router.get("/v1/member-photos/{photo_id}")
async def read_member_photo(photo_id: UUID, user: User = Depends(require_active_member), session: AsyncSession = Depends(get_session)):
    photo = await session.get(ProfilePhotoAsset, str(photo_id))
    entry = await session.scalar(select(MemberDirectoryEntry).where(MemberDirectoryEntry.user_id == photo.user_id)) if photo and photo.user_id else None
    if entry is None or (entry.user_id != user.id and not entry.is_public):
        raise HTTPException(404, "找不到照片")
    return Response(photo.content, media_type="image/webp", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
