from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import MembershipAdmissionRound, User


LEGACY_ADMISSION_BLOCKED = "此帳號已使用新版入社流程，請至新版入社審核處理；繳費、核帳及發證尚未開放"


async def has_admission_round(session: AsyncSession, user_id: str) -> bool:
    return await session.scalar(
        select(MembershipAdmissionRound.id)
        .where(MembershipAdmissionRound.user_id == user_id)
        .limit(1)
    ) is not None


async def require_legacy_admission_allowed(session: AsyncSession, user_id: str) -> None:
    await session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    if await has_admission_round(session, user_id):
        raise HTTPException(status_code=409, detail=LEGACY_ADMISSION_BLOCKED)
