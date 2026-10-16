from fastapi import APIRouter, Depends, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..models import AdminAudit, SystemSetting, User
from ..platform_settings import PLATFORM_KEY, PlatformSettings, platform_settings

platform_router = APIRouter(tags=["platform"])


class PlatformUpdate(PlatformSettings):
    reason: str = Field(min_length=1, max_length=1000)


@platform_router.get("/v1/platform")
async def read_platform(response: Response, session: AsyncSession = Depends(get_session)) -> dict:
    settings = await platform_settings(session)
    response.headers["Cache-Control"] = "no-store"
    return {"orders_open": settings.orders_open, "closed_message": settings.closed_message,
            "meal_cutoff_time": "20:00", "timezone": "Asia/Taipei"}


@platform_router.get("/v1/admin/platform")
async def read_admin_platform(_admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    current = await platform_settings(session)
    return {**current.model_dump(), "meal_cutoff_time": "20:00", "timezone": "Asia/Taipei",
            "invoice_provider": settings.invoice_provider, "payment_provider": settings.payment_provider,
            "printer_status": "browser_print_available", "automatic_print_connected": False}


@platform_router.put("/v1/admin/platform")
async def update_platform(body: PlatformUpdate, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == PLATFORM_KEY).with_for_update())
    previous = PlatformSettings.model_validate(row.value if row else {}).model_dump()
    if row is None:
        row = SystemSetting(key=PLATFORM_KEY)
        session.add(row)
    row.value = body.model_dump(exclude={"reason"})
    row.updated_by_id = admin.id
    session.add(AdminAudit(actor_id=admin.id, action="platform.settings_updated", aggregate_type="system_setting", aggregate_id=PLATFORM_KEY,
                           reason=body.reason, data={"previous": previous, "current": row.value}))
    await session.commit()
    return {**row.value, "meal_cutoff_time": "20:00", "timezone": "Asia/Taipei"}
