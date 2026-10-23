import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SystemSetting

PLATFORM_KEY = "storefront_operations"


class PlatformSettings(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    orders_open: bool = True
    closed_message: str = Field(default="目前暫停接受新訂單", min_length=1, max_length=200)
    support_phone: str = Field(default="", max_length=40)
    printer_brand: str = Field(default="", max_length=80)
    printer_model: str = Field(default="", max_length=80)
    printer_connection: Literal["unknown", "usb", "network", "bluetooth", "browser"] = "unknown"
    kitchen_sound: bool = False

    @field_validator("support_phone")
    @classmethod
    def validate_support_phone(cls, value: str) -> str:
        if not value:
            return value
        digits = re.sub(r"[^0-9]", "", value)
        if not re.fullmatch(r"\+?[0-9 ()-]+", value) or not 7 <= len(digits) <= 15:
            raise ValueError("請輸入含區碼的聯絡電話，或留空")
        return value


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
