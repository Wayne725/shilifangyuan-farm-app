from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .models import SystemSetting

PLATFORM_KEY = "storefront_operations"


class PlatformSettings(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    orders_open: bool = True
    closed_message: str = Field(default="目前暫停接受新訂單", min_length=1, max_length=200)
    printer_brand: str = Field(default="", max_length=80)
    printer_model: str = Field(default="", max_length=80)
    printer_connection: Literal["unknown", "usb", "network", "bluetooth", "browser"] = "unknown"
    kitchen_sound: bool = False


async def platform_settings(session: AsyncSession, *, lock: bool = False) -> PlatformSettings:
    query = select(SystemSetting).where(SystemSetting.key == PLATFORM_KEY)
    if lock:
        query = query.with_for_update(read=True)
    row = await session.scalar(query)
    return PlatformSettings.model_validate(row.value if row else {})


async def require_accepting_orders(session: AsyncSession) -> None:
    settings = await platform_settings(session, lock=True)
    if not settings.orders_open:
        raise HTTPException(status_code=409, detail=settings.closed_message)
