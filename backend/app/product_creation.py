from __future__ import annotations

import re
import unicodedata
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminAudit, Product, Supplier
from .schemas import ProductCreate


def make_product_slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-") or "product"
    return f"{base[:67]}-{uuid4().hex[:12]}"


def is_slug_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "slug" in message and ("unique" in message or "duplicate" in message)


async def create_product_record(
    session: AsyncSession,
    body: ProductCreate,
    actor_id: str,
    *,
    slug: str | None = None,
) -> Product:
    """Create and audit a product inside the caller's transaction."""
    if body.supplier_id is not None:
        supplier = await session.scalar(select(Supplier).where(Supplier.id == body.supplier_id, Supplier.is_active.is_(True)))
        if supplier is None:
            raise HTTPException(status_code=422, detail="找不到已啟用的供應者")
    product = Product(slug=slug or make_product_slug(body.name), **body.model_dump(mode="json"))
    session.add(product)
    await session.flush()
    session.add(AdminAudit(actor_id=actor_id, action="product.create", aggregate_type="product", aggregate_id=product.id,
                           data={"name": product.name, "daily_sales_enabled": product.daily_sales_enabled}))
    return product
