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
from .phone_lookup import mask_phone, phone_lookup_digests


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


class CounterPhoneLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: SecretStr = Field(min_length=8, max_length=40)

    @field_validator("phone")
    @classmethod
    def normalize_number(cls, value: SecretStr) -> SecretStr:
        return normalize_counter_phone(value)


def normalize_counter_phone(value: SecretStr) -> SecretStr:
    phone = normalize_phone(value.get_secret_value())
    if not 8 <= len(phone) <= 15 or not phone.isascii():
        raise ValueError("請填寫有效的電話號碼")
    return SecretStr(phone)


class CounterPhoneMatch(BaseModel):
    id: str
    name: str
    member_number: str
    membership_type: MembershipType
    personal_tax_type: TaxType
    company_tax_type: TaxType


class CounterPhoneLookup(BaseModel):
    status: Literal["matched", "inactive", "no_match"]
    phone_masked: str
    matches: list[CounterPhoneMatch]


def counter_member_tax_type(membership_type: MembershipType, buyer_type: InvoiceBuyerType) -> TaxType:
    return sale_tax_type(TaxType.TAXABLE, membership_type, buyer_type)


async def counter_phone_matches(
    session: AsyncSession, settings: Settings, phone: str, *, lock: bool = False,
) -> tuple[list[tuple[CounterPhoneMatch, str]], bool]:
    try:
        cipher = pii_cipher_from_settings(settings)
        digests = list(phone_lookup_digests(phone, cipher).values())
        profile_candidates = (
            select(MemberProfile.user_id).join(User, User.id == MemberProfile.user_id)
            .where(User.meal_contact_phone_encrypted.is_(None), MemberProfile.phone_lookup.in_(digests))
        )
        linked_ids = select(User.id).where(User.meal_contact_phone_lookup.in_(digests)).union(profile_candidates)
        linked_query = (
            select(Membership, User, MemberProfile)
            .join(User, User.id == Membership.user_id)
            .outerjoin(MemberProfile, MemberProfile.user_id == User.id)
            .where(User.id.in_(linked_ids))
            .order_by(User.id)
        )
        roster_query = (
            select(MemberRosterEntry).where(
                MemberRosterEntry.claimed_user_id.is_(None), MemberRosterEntry.phone_lookup.in_(digests),
            ).order_by(MemberRosterEntry.id)
        )
        linked = (await session.execute(linked_query)).all()
        if lock and linked:
            user_ids = [user.id for _, user, _ in linked]
            for model, column in ((User, User.id), (Membership, Membership.user_id), (MemberProfile, MemberProfile.user_id)):
                await session.execute(select(model.id).where(column.in_(user_ids)).order_by(model.id).with_for_update(read=True))
            linked = (await session.execute(linked_query.where(User.id.in_(user_ids)).execution_options(populate_existing=True))).all()
        roster = (await session.scalars(
            roster_query.with_for_update(read=True).execution_options(populate_existing=True) if lock else roster_query,
        )).all()

        def matches_phone(ciphertext: str, aad: str) -> bool:
            stored = normalize_phone(cipher.decrypt_text(ciphertext, associated_data=aad))
            return compare_digest(stored.encode(), phone.encode())

        matches = []
        inactive = False
        for membership, user, profile in linked:
            profile_aad = f"member-profile:{user.id}"
            if user.meal_contact_phone_encrypted:
                matched = matches_phone(user.meal_contact_phone_encrypted, f"meal-contact:{user.id}")
            else:
                matched = profile is not None and matches_phone(profile.phone_encrypted, profile_aad)
            if not matched:
                continue
            if not user.is_active or membership.status not in {MembershipStatus.ACTIVE, MembershipStatus.TRAINEE}:
                inactive = inactive or not user.is_active or membership.status in {
                    MembershipStatus.SUSPENDED, MembershipStatus.RESIGNED, MembershipStatus.TERMINATED,
                }
                continue
            is_active_member = membership.status == MembershipStatus.ACTIVE
            number = membership.member_number if is_active_member else membership.trainee_number
            if not number:
                continue
            name = cipher.decrypt_text(profile.legal_name_encrypted, associated_data=profile_aad) if profile else user.display_name
            membership_type = MembershipType.MEMBER if is_active_member else MembershipType.TRAINEE
            matches.append((_counter_phone_match(f"membership:{membership.id}", name or user.display_name, number, membership_type), name or user.display_name))
        for entry in roster:
            aad = roster_aad(entry.id)
            if not matches_phone(entry.phone_encrypted, aad):
                continue
            if not entry.is_active:
                inactive = True
                continue
            name = cipher.decrypt_text(entry.legal_name_encrypted, associated_data=aad)
            matches.append((_counter_phone_match(f"roster:{entry.id}", name, entry.member_number, MembershipType.MEMBER), name))
    except IntegrationError as exc:
        raise HTTPException(503, "社員資料暫時無法核對，請稍後再試") from exc
    return sorted(matches, key=lambda match: match[0].member_number), inactive


def _counter_phone_match(id: str, name: str, member_number: str, membership_type: MembershipType) -> CounterPhoneMatch:
    return CounterPhoneMatch(
        id=id, name=f"{name[:1]}{'○' * max(1, len(name) - 1)}", member_number=member_number,
        membership_type=membership_type,
        personal_tax_type=counter_member_tax_type(membership_type, InvoiceBuyerType.PERSONAL),
        company_tax_type=counter_member_tax_type(membership_type, InvoiceBuyerType.COMPANY),
    )


async def counter_phone_lookup(session: AsyncSession, settings: Settings, phone: str) -> CounterPhoneLookup:
    matches, inactive = await counter_phone_matches(session, settings, phone)
    return CounterPhoneLookup(
        status="matched" if matches else "inactive" if inactive else "no_match",
        phone_masked=mask_phone(phone), matches=[match for match, _ in matches],
    )


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

    encrypted_phone = user.meal_contact_phone_encrypted
    aad = f"meal-contact:{user.id}"
    if encrypted_phone is None:
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
