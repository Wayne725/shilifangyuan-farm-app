from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_session
from ..membership_education_settings import (
    MembershipEducationSettingsConflict,
    MembershipEducationSettingsRead,
    MembershipEducationSettingsUpdate,
    read_membership_education_settings,
    update_membership_education_settings,
)
from ..models import User


membership_education_admin_router = APIRouter(tags=["membership"])


@membership_education_admin_router.get(
    "/v1/admin/membership-education/settings", response_model=MembershipEducationSettingsRead,
)
async def read_education_settings(
    response: Response,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MembershipEducationSettingsRead:
    response.headers["Cache-Control"] = "no-store"
    return await read_membership_education_settings(session)


@membership_education_admin_router.put(
    "/v1/admin/membership-education/settings", response_model=MembershipEducationSettingsRead,
)
async def update_education_settings(
    body: MembershipEducationSettingsUpdate,
    response: Response,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MembershipEducationSettingsRead:
    response.headers["Cache-Control"] = "no-store"
    try:
        current = await update_membership_education_settings(session, body, actor_id=admin.id)
    except MembershipEducationSettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"Cache-Control": "no-store"}) from exc
    await session.commit()
    return current
