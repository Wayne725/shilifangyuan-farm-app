from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .integrations.common import IntegrationError
from .integrations.pii_crypto import pii_cipher_from_settings
from .member_claims import normalize_phone, roster_aad
from .models import MemberRosterEntry, Membership


class MembershipPhoneCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=32)

    @field_validator("phone")
    @classmethod
    def normalized_phone(cls, value: str) -> str:
        phone = normalize_phone(value)
        if not 8 <= len(phone) <= 15:
            raise ValueError("請填寫有效的手機號碼")
        return phone


async def verify_offline_roster_phone(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    phone: str,
    current_number: str | None,
    now: datetime,
    expected_entry_id: str | None = None,
    lock: bool = False,
) -> tuple[MemberRosterEntry, str]:
    try:
        cipher = pii_cipher_from_settings(settings)

        async def matching_ids() -> list[str]:
            entries = await session.scalars(select(MemberRosterEntry).execution_options(populate_existing=True))
            return [entry.id for entry in entries if normalize_phone(cipher.decrypt_text(
                entry.phone_encrypted, associated_data=roster_aad(entry.id),
            )) == phone]

        matches = await matching_ids()
        if not matches:
            raise HTTPException(status_code=409, detail="查無此手機號碼的社員名冊，請先確認或補齊名冊資料")
        if len(matches) != 1:
            raise HTTPException(status_code=409, detail="此手機號碼對應多筆社員名冊，請先整理重複資料")
        entry_id = matches[0]
        if expected_entry_id is not None and expected_entry_id != entry_id:
            raise HTTPException(status_code=409, detail="社員名冊核對結果已變更，請重新核對手機號碼")

        conditions = [MemberRosterEntry.id == entry_id, MemberRosterEntry.claimed_user_id == user_id,
                      MemberRosterEntry.pending_claim_user_id == user_id]
        if current_number:
            conditions.append(MemberRosterEntry.member_number == current_number)
        query = select(MemberRosterEntry).where(or_(*conditions)).order_by(MemberRosterEntry.id).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        entries = list(await session.scalars(query))
        if len(entries) != 1 or entries[0].id != entry_id:
            raise HTTPException(status_code=409, detail="此帳號已有其他社員名冊連結，請先核對原會籍資料")
        entry = entries[0]
        # A concurrent claim may have changed the row while this request waited for its lock.
        if lock and await matching_ids() != [entry_id]:
            raise HTTPException(status_code=409, detail="社員名冊核對結果已變更，請重新核對手機號碼")
        if current_number and current_number != entry.member_number:
            raise HTTPException(status_code=409, detail="手機號碼對應的社員編號與既有會籍不同，請先核對原會籍資料")
        if not entry.is_active or entry.claimed_user_id not in {None, user_id}:
            raise HTTPException(status_code=409, detail="此社員名冊已停用或屬於其他帳號")
        pending_until = entry.pending_claim_expires_at
        if entry.pending_claim_user_id not in {None, user_id} and (pending_until is None or pending_until.replace(tzinfo=pending_until.tzinfo or timezone.utc) > now):
            raise HTTPException(status_code=409, detail="此社員名冊正由其他帳號認領")
        conflict = await session.scalar(select(Membership.id).where(Membership.member_number == entry.member_number, Membership.user_id != user_id))
        if conflict:
            raise HTTPException(status_code=409, detail="此社員編號已屬於其他帳號")
        legal_name = cipher.decrypt_text(entry.legal_name_encrypted, associated_data=roster_aad(entry.id))
        return entry, legal_name
    except IntegrationError as exc:
        raise HTTPException(status_code=503, detail="社員名冊私密資料暫時無法核對，請稍後再試") from exc
