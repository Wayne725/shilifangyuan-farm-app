from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin_explanations import AdminExplanation
from ..auth import require_admin
from ..config import Settings, get_settings
from ..database import get_session
from ..models import AdminAudit, SystemSetting, User
from ..platform_settings import PLATFORM_KEY, PlatformSettings, platform_settings
from ..public_information import PublicInformation, preserve_published_policy
from ..sales_scope import sales_capabilities

platform_router = APIRouter(tags=["platform"])


class PlatformUpdate(PlatformSettings):
    reason: AdminExplanation = Field(default="", max_length=1000)
    expected_version: int | None = Field(default=None, ge=0)


@platform_router.get("/v1/platform")
async def read_platform(response: Response, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    current = await platform_settings(session)
    response.headers["Cache-Control"] = "no-store"
    return {"orders_open": current.orders_open, "closed_message": current.closed_message,
            "support_phone": current.support_phone,
            "public_information": current.public_information.model_dump() if current.public_information.published else None,
            **sales_capabilities(settings),
            "meal_cutoff_time": "20:00", "timezone": "Asia/Taipei"}


@platform_router.get("/v1/admin/platform")
async def read_admin_platform(response: Response, _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == PLATFORM_KEY).execution_options(populate_existing=True))
    current = PlatformSettings.model_validate(row.value if row else {})
    return {**current.model_dump(), "version": row.value.get("_version", 0) if row else 0,
            "meal_cutoff_time": "20:00", "timezone": "Asia/Taipei",
            **sales_capabilities(settings),
            "invoice_provider": settings.invoice_provider, "payment_provider": settings.payment_provider,
            "printer_status": "browser_print_available", "automatic_print_connected": False}


@platform_router.put("/v1/admin/platform")
async def update_platform(body: PlatformUpdate, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    lock = insert(SystemSetting).values(key=PLATFORM_KEY, value={})
    # Lock even the first update, when SELECT FOR UPDATE would find no row.
    await session.execute(lock.on_conflict_do_update(index_elements=[SystemSetting.key], set_={"key": lock.excluded.key}))
    row = await session.scalar(select(SystemSetting).where(SystemSetting.key == PLATFORM_KEY).with_for_update().execution_options(populate_existing=True))
    version = row.value.get("_version", 0)
    if body.expected_version is not None and body.expected_version != version:
        raise HTTPException(status_code=409, detail="設定已由其他操作更新，請重新讀取後再儲存")
    previous = PlatformSettings.model_validate(row.value).model_dump()
    await preserve_published_policy(session, PublicInformation.model_validate(previous['public_information']), admin.id)
    if 'public_information' in body.model_fields_set:
        await preserve_published_policy(session, body.public_information, admin.id)
    row.value = {**body.model_dump(exclude={"reason", "expected_version"}), "_version": version + 1}
    for field in ('support_phone', 'public_information'):
        if field not in body.model_fields_set:
            row.value = {**row.value, field: previous[field]}
    row.updated_by_id = admin.id
    session.add(AdminAudit(actor_id=admin.id, action="platform.settings_updated", aggregate_type="system_setting", aggregate_id=PLATFORM_KEY,
                           reason=body.reason, data={"previous": previous, "current": row.value}))
    await session.commit()
    return {**PlatformSettings.model_validate(row.value).model_dump(), "version": version + 1,
            **sales_capabilities(settings), "meal_cutoff_time": "20:00", "timezone": "Asia/Taipei"}
