from __future__ import annotations

import hmac
import re
import unicodedata
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_optional_user, require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..models import GroupBundle, GroupBundleItem, Product, Supplier, User, UserRole
from ..schemas import (
    BundleCreate,
    BundleItemRead,
    BundleRead,
    DemoResetRequest,
    Message,
    ProductCreate,
    ProductRead,
    ProductUpdate,
)
from ..seed import reset_demo_data


catalog_router = APIRouter(tags=["catalog"])


def make_product_slug(name: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "product"
    return f"{base[:67]}-{uuid4().hex[:12]}"


def is_slug_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "slug" in message and (
        "unique" in message or "duplicate" in message
    )


def bundle_read(bundle: GroupBundle) -> BundleRead:
    return BundleRead(
        id=bundle.id,
        name=bundle.name,
        description=bundle.description,
        image_url=bundle.image_url,
        member_price=bundle.member_price,
        nonmember_price=bundle.nonmember_price,
        is_active=bundle.is_active,
        items=[
            BundleItemRead(
                product_id=item.product_id,
                product_name=item.product.name,
                quantity=item.quantity,
            )
            for item in bundle.items
        ],
    )


@catalog_router.get("/v1/products", response_model=List[ProductRead])
async def list_products(
    current_user: Optional[User] = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> List[Product]:
    query = select(Product).options(selectinload(Product.supplier))
    if current_user is None or current_user.user_role != UserRole.ADMIN:
        query = query.where(Product.is_active.is_(True))
    result = await session.scalars(
        query.order_by(
            Product.is_active.desc(), Product.category, Product.name
        )
    )
    return list(result)


@catalog_router.get("/v1/products/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: str,
    session: AsyncSession = Depends(get_session),
) -> Product:
    product = await session.scalar(
        select(Product)
        .where(Product.id == product_id, Product.is_active.is_(True))
        .options(selectinload(Product.supplier))
    )
    if product is None:
        raise HTTPException(status_code=404, detail="找不到商品")
    return product


@catalog_router.post(
    "/v1/products",
    response_model=ProductRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    body: ProductCreate,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Product:
    values = body.model_dump(mode="json")
    if body.supplier_id is not None:
        supplier = await session.scalar(
            select(Supplier).where(
                Supplier.id == body.supplier_id,
                Supplier.is_active.is_(True),
            )
        )
        if supplier is None:
            raise HTTPException(status_code=422, detail="找不到已啟用的供應者")
    for _attempt in range(3):
        product = Product(slug=make_product_slug(body.name), **values)
        session.add(product)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if not is_slug_conflict(exc):
                raise HTTPException(
                    status_code=409,
                    detail="產品編號或 SKU 已存在",
                ) from exc
            continue
        return await session.scalar(
            select(Product)
            .where(Product.id == product.id)
            .options(selectinload(Product.supplier))
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="無法建立唯一商品代碼，請重試",
    )


@catalog_router.patch("/v1/products/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: str,
    body: ProductUpdate,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Product:
    product = await session.scalar(
        select(Product).where(Product.id == product_id).with_for_update()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="找不到商品")
    updates = body.model_dump(exclude_unset=True, mode="json")
    if "supplier_id" in updates and updates["supplier_id"] is not None:
        supplier = await session.scalar(
            select(Supplier).where(
                Supplier.id == updates["supplier_id"],
                Supplier.is_active.is_(True),
            )
        )
        if supplier is None:
            raise HTTPException(status_code=422, detail="找不到已啟用的供應者")
    member_price = updates.get("member_price", product.member_price)
    nonmember_price = updates.get(
        "nonmember_price", product.nonmember_price
    )
    if member_price > nonmember_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="社員價不可高於非社員價",
        )
    for field, value in updates.items():
        setattr(product, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="產品編號或 SKU 已存在",
        ) from exc
    return await session.scalar(
        select(Product)
        .where(Product.id == product.id)
        .options(selectinload(Product.supplier))
    )


@catalog_router.get("/v1/group-bundles", response_model=List[BundleRead])
async def list_bundles(
    session: AsyncSession = Depends(get_session),
) -> List[BundleRead]:
    bundles = await session.scalars(
        select(GroupBundle)
        .where(GroupBundle.is_active.is_(True))
        .options(
            selectinload(GroupBundle.items).selectinload(
                GroupBundleItem.product
            )
        )
        .order_by(GroupBundle.name)
    )
    return [bundle_read(bundle) for bundle in bundles]


@catalog_router.get(
    "/v1/group-bundles/{bundle_id}", response_model=BundleRead
)
async def get_bundle(
    bundle_id: str,
    session: AsyncSession = Depends(get_session),
) -> BundleRead:
    bundle = await session.scalar(
        select(GroupBundle)
        .where(
            GroupBundle.id == bundle_id, GroupBundle.is_active.is_(True)
        )
        .options(
            selectinload(GroupBundle.items).selectinload(
                GroupBundleItem.product
            )
        )
    )
    if bundle is None:
        raise HTTPException(status_code=404, detail="找不到團購套組")
    return bundle_read(bundle)


@catalog_router.post(
    "/v1/group-bundles",
    response_model=BundleRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_bundle(
    body: BundleCreate,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> BundleRead:
    product_ids = list(dict.fromkeys(item.product_id for item in body.items))
    products = list(
        await session.scalars(
            select(Product).where(
                Product.id.in_(product_ids), Product.is_active.is_(True)
            )
        )
    )
    if len(products) != len(product_ids):
        raise HTTPException(status_code=422, detail="套組包含不存在的商品")
    bundle = GroupBundle(
        **body.model_dump(exclude={"items"}),
        items=[
            GroupBundleItem(
                product_id=item.product_id,
                quantity=item.quantity,
                position=position,
            )
            for position, item in enumerate(body.items)
        ],
    )
    session.add(bundle)
    await session.commit()
    bundle = await session.scalar(
        select(GroupBundle)
        .where(GroupBundle.id == bundle.id)
        .options(
            selectinload(GroupBundle.items).selectinload(
                GroupBundleItem.product
            )
        )
    )
    return bundle_read(bundle)


router = catalog_router


@catalog_router.post("/v1/admin/demo/reset", response_model=Message)
async def reset_demo(
    body: DemoResetRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Message:
    if settings.environment.strip().lower() not in {
        "development",
        "sandbox",
        "test",
    }:
        raise HTTPException(status_code=403, detail="此環境不允許重設資料")
    if not hmac.compare_digest(
        body.confirmation, settings.demo_reset_confirmation
    ):
        raise HTTPException(status_code=403, detail="重設確認碼不正確")
    counts = await reset_demo_data(session)
    return Message(
        message=(
            f"展示資料已重設：{counts['products']} 項商品、"
            f"{counts['campaigns']} 個正式團購"
        )
    )
