from secrets import compare_digest
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .domain import member_sale_is_tax_exempt, product_price, sale_tax_type
from .integrations.common import IntegrationError
from .integrations.pii_crypto import pii_cipher_from_settings
from .member_claims import normalize_member_number, normalize_phone, roster_aad
from .models import (
    InvoiceBuyerType, Meal, MemberProfile, MemberRosterEntry, Membership, MembershipStatus,
    MembershipType, Product, TaxType, User,
)


class CounterMemberIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_number: str = Field(min_length=1, max_length=32)
    phone: SecretStr = Field(min_length=8, max_length=32)

    @field_validator("member_number")
    @classmethod
    def normalize_number(cls, value: str) -> str:
        number = normalize_member_number(value)
        if not 1 <= len(number) <= 32:
            raise ValueError("請填寫有效的社員編號")
        return number

    @field_validator("phone")
    @classmethod
    def normalize_mobile(cls, value: SecretStr) -> SecretStr:
        phone = normalize_phone(value.get_secret_value())
        if not 8 <= len(phone) <= 15:
            raise ValueError("請填寫有效的手機號碼")
        return SecretStr(phone)


class CounterMemberQuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member: CounterMemberIdentity
    sale_category: Literal["meal", "product"]
    catalog_id: UUID
    invoice_buyer_type: InvoiceBuyerType


class CounterMemberQuote(BaseModel):
    product_name: str
    unit_price: int
    tax_type: TaxType
    member_tax_exempt: bool
    membership_type: MembershipType


async def counter_member_quote(
    session: AsyncSession, settings: Settings, body: CounterMemberQuoteInput, *, lock: bool = False,
) -> tuple[Membership, CounterMemberQuote]:
    number = body.member.member_number
    number_matches = or_(Membership.member_number == number, Membership.trainee_number == number)
    rows = (await session.execute(
        select(Membership, User).join(User, User.id == Membership.user_id).where(number_matches).limit(2),
    )).all()
    mismatch = "無法核對社員資格，請確認社員編號及手機號碼"
    if len(rows) != 1:
        raise HTTPException(409, mismatch)
    membership, user = rows[0]
    if lock:
        # Match the user-before-membership lock order used by membership changes.
        user = await session.scalar(
            select(User).where(User.id == user.id).with_for_update(read=True).execution_options(populate_existing=True)
        )
        membership = await session.scalar(
            select(Membership).where(Membership.id == membership.id, number_matches)
            .with_for_update(read=True).execution_options(populate_existing=True)
        )
    if not user or not user.is_active or not membership or membership.status not in {
        MembershipStatus.ACTIVE, MembershipStatus.TRAINEE,
    }:
        raise HTTPException(409, mismatch)

    phone_query = select(MemberProfile.phone_encrypted).where(MemberProfile.user_id == user.id)
    encrypted_phone = await session.scalar(phone_query.with_for_update(read=True) if lock else phone_query)
    aad = f"member-profile:{user.id}"
    if encrypted_phone is None and membership.member_number:
        roster_query = select(MemberRosterEntry.id, MemberRosterEntry.phone_encrypted).where(
            MemberRosterEntry.claimed_user_id == user.id,
            MemberRosterEntry.member_number == membership.member_number,
            MemberRosterEntry.is_active.is_(True),
        )
        roster = (await session.execute(roster_query.with_for_update(read=True) if lock else roster_query)).one_or_none()
        if roster:
            encrypted_phone, aad = roster.phone_encrypted, roster_aad(roster.id)
    if encrypted_phone is None:
        raise HTTPException(409, mismatch)
    try:
        stored_phone = normalize_phone(
            pii_cipher_from_settings(settings).decrypt_text(encrypted_phone, associated_data=aad)
        )
    except IntegrationError as exc:
        raise HTTPException(503, "社員資料暫時無法核對，請稍後再試") from exc
    if not compare_digest(stored_phone.encode(), body.member.phone.get_secret_value().encode()):
        raise HTTPException(409, mismatch)

    membership_type = MembershipType.MEMBER if membership.status == MembershipStatus.ACTIVE else MembershipType.TRAINEE
    catalog_model = Meal if body.sale_category == "meal" else Product
    catalog_query = select(catalog_model).where(catalog_model.id == str(body.catalog_id), catalog_model.is_active.is_(True))
    if lock:
        catalog_query = catalog_query.with_for_update(read=True).execution_options(populate_existing=True)
    item = await session.scalar(catalog_query)
    if item is None:
        raise HTTPException(409, "此品項已停用或不存在，請重新選擇")
    price = item.price if isinstance(item, Meal) else product_price(item, membership_type)
    product_name = item.name.strip()
    if not product_name or not 1 <= price <= 1_000_000:
        raise HTTPException(409, "此品項的售價無法用於現場收款")
    return membership, CounterMemberQuote(
        product_name=product_name, unit_price=price,
        tax_type=sale_tax_type(item.tax_type, membership_type, body.invoice_buyer_type),
        member_tax_exempt=member_sale_is_tax_exempt(membership_type, body.invoice_buyer_type),
        membership_type=membership_type,
    )
