from typing import Annotated

from fastapi import HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .audited_changes import audit_changes
from .config import Settings
from .contact_phone import decrypt_contact_phone, normalize_contact_phone
from .integrations.common import IntegrationError
from .integrations.pii_crypto import pii_cipher_from_settings
from .models import MemberProfile, RefreshSession, User, utcnow


Phone = Annotated[str, StringConstraints(max_length=40), AfterValidator(normalize_contact_phone)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class AccountProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: DisplayName | None = None
    phone: Phone | None = None


class AdminAccountUpdate(AccountProfileUpdate):
    is_active: bool | None = None


class AccountProfileRead(BaseModel):
    display_name: str
    phone: str | None
    is_active: bool


async def account_phone(session: AsyncSession, user: User, settings: Settings) -> str | None:
    if user.meal_contact_phone_encrypted:
        phone = decrypt_contact_phone(user.meal_contact_phone_encrypted, f"meal-contact:{user.id}", settings)
    else:
        ciphertext = await session.scalar(select(MemberProfile.phone_encrypted).where(MemberProfile.user_id == user.id))
        phone = decrypt_contact_phone(ciphertext, f"member-profile:{user.id}", settings)
    if phone:
        try:
            return normalize_contact_phone(phone)
        except ValueError:
            return None
    return None


def set_account_phone(user: User, phone: str, settings: Settings) -> None:
    try:
        user.meal_contact_phone_encrypted = pii_cipher_from_settings(settings).encrypt_text(
            phone, associated_data=f"meal-contact:{user.id}",
        )
    except IntegrationError as exc:
        raise HTTPException(503, "聯絡電話目前無法安全儲存，請稍後再試") from exc


async def read_account_profile(session: AsyncSession, user: User, settings: Settings) -> AccountProfileRead:
    return AccountProfileRead(display_name=user.display_name, phone=await account_phone(session, user, settings),
                              is_active=user.is_active)


async def update_account_profile(
    session: AsyncSession, user_id: str, actor: User, body: AccountProfileUpdate, settings: Settings,
) -> AccountProfileRead:
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if user is None:
        raise HTTPException(404, "找不到帳號")
    active = body.is_active if isinstance(body, AdminAccountUpdate) else None
    if actor.id == user.id and not user.is_active:
        raise HTTPException(401, "帳號已停用")
    if actor.id == user.id and active is False:
        raise HTTPException(409, "請由其他管理員停用這個管理員帳號")
    before = {"display_name": user.display_name, "is_active": user.is_active,
              "phone_encrypted": user.meal_contact_phone_encrypted}
    if body.display_name is not None:
        user.display_name = body.display_name
    prior_phone = await account_phone(session, user, settings) if body.phone is not None else None
    if body.phone is not None and body.phone != prior_phone:
        if before["phone_encrypted"] is None:
            if prior_phone is not None:
                set_account_phone(user, prior_phone, settings)
                before["phone_encrypted"] = user.meal_contact_phone_encrypted
        set_account_phone(user, body.phone, settings)
    if active is not None and active != user.is_active:
        user.is_active = active
        user.token_version = (user.token_version or 0) + 1
        await session.execute(update(RefreshSession).where(
            RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None),
        ).values(revoked_at=utcnow()))
    audit_changes(session, actor.id, "user", user.id, "user.profile_update", before,
                  {"display_name": user.display_name, "is_active": user.is_active,
                   "phone_encrypted": user.meal_contact_phone_encrypted})
    await session.commit()
    return await read_account_profile(session, user, settings)
