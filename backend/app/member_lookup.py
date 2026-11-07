from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .integrations.email_sender import EmailMessage
from .integrations.pii_crypto import decrypt_auth_outbox_credential, encrypt_auth_outbox_credential, pii_cipher_from_settings
from .member_claims import _secure_equal, normalize_email, normalize_phone, roster_aad
from .models import MemberRosterEntry, Membership, MembershipStatus, OutboxEvent, User


MEMBER_LOOKUP_EVENT = "auth.member_lookup_requested"
MEMBER_LOOKUP_MESSAGE = "查詢已受理，請查看填寫的 Email。"


class MemberLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=8, max_length=32)
    email: EmailStr = Field(max_length=320)

    @field_validator("phone")
    @classmethod
    def phone_value(cls, value: str) -> str:
        normalized = normalize_phone(value)
        if not 8 <= len(normalized) <= 15:
            raise ValueError("請填寫有效的手機號碼")
        return normalized

    @field_validator("email", mode="before")
    @classmethod
    def email_value(cls, value: str) -> str:
        return normalize_email(value) if isinstance(value, str) else value


async def lookup_result_text(session: AsyncSession, settings: Settings, body: MemberLookupRequest) -> str:
    cipher = pii_cipher_from_settings(settings)
    entries = await session.scalars(select(MemberRosterEntry))
    matches = []
    for entry in entries:
        aad = roster_aad(entry.id)
        email_matches = _secure_equal(normalize_email(cipher.decrypt_text(entry.email_encrypted, associated_data=aad)), str(body.email))
        phone_matches = _secure_equal(normalize_phone(cipher.decrypt_text(entry.phone_encrypted, associated_data=aad)), body.phone)
        if email_matches and phone_matches:
            matches.append(entry)

    unresolved = "本次無法確認您的社員資格。請核對手機與入社時登記的 Email；資料可能尚未登錄或需要更新，請聯絡合作社管理員協助。"
    if len(matches) != 1 or not matches[0].is_active:
        return unresolved
    entry = matches[0]
    membership = await session.scalar(select(Membership).where(or_(
        Membership.member_number == entry.member_number,
        Membership.user_id == entry.claimed_user_id if entry.claimed_user_id else False,
    )))
    if entry.claimed_user_id:
        user = await session.get(User, entry.claimed_user_id)
        if (membership is None or membership.user_id != entry.claimed_user_id
                or membership.member_number != entry.member_number
                or membership.status != MembershipStatus.ACTIVE or user is None or not user.is_active):
            return "已找到相符名冊，但目前資格需要合作社核對。請聯絡管理員確認，查詢不會恢復或變更會籍。"
        return "已確認您目前具有正式社員資格。請使用原網站帳號登入；不需要再次註冊或重新認領。"
    if membership is not None:
        return unresolved
    if entry.pending_claim_user_id:
        expires = entry.pending_claim_expires_at
        if expires is None or expires.replace(tzinfo=expires.tzinfo or timezone.utc) > datetime.now(timezone.utc):
            return "已找到相符社員名冊，且已有帳號正在認領。請完成先前寄送的 Email 驗證；如果不是您本人操作，請聯絡合作社管理員。"
    return "已找到相符的既有社員名冊。首次使用網站可選擇「我是社員」完成註冊及驗證；已有網站帳號請先登入原帳號，請管理員協助連結資格。"


async def prepare_member_lookup_email(session: AsyncSession, settings: Settings, event: OutboxEvent) -> EmailMessage:
    if event.payload.get("result_encrypted"):
        snapshot = json.loads(decrypt_auth_outbox_credential(
            settings, f"{MEMBER_LOOKUP_EVENT}.result", event.aggregate_id, event.payload["result_encrypted"],
        ))
        return EmailMessage(**snapshot, idempotency_key=f"outbox-{event.id}")
    content = decrypt_auth_outbox_credential(settings, MEMBER_LOOKUP_EVENT, event.aggregate_id, event.payload["credential_encrypted"])
    try:
        body = MemberLookupRequest.model_validate(json.loads(content))
    except ValueError:
        raise ValueError("社員查詢資料格式無效") from None
    result = await lookup_result_text(session, settings, body)
    root = settings.web_base_url.rstrip("/")
    checked_at = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M")
    snapshot = {
        "to_email": str(body.email),
        "subject": "十里方圓社員資格查詢結果",
        "text_content": f"查詢時間：{checked_at}（台灣）\n\n{result}\n\n登入／既有社員入口：{root}/line-consent\n操作說明與聯絡協助：{root}/help\n\n本查詢不會建立帳號或授予社員資格。若不是您提出查詢，可忽略此信。",
    }
    delivery = event.payload.get("email_delivery")
    event.payload = {"result_encrypted": encrypt_auth_outbox_credential(
        settings, f"{MEMBER_LOOKUP_EVENT}.result", event.aggregate_id, json.dumps(snapshot, ensure_ascii=False),
    ), **({"email_delivery": delivery} if isinstance(delivery, dict) else {})}
    # Persist the exact message before sending so retries keep the same provider idempotency payload.
    await session.commit()
    return EmailMessage(**snapshot, idempotency_key=f"outbox-{event.id}")
