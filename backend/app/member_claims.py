from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .integrations.pii_crypto import VersionedPIICipher
from .models import (
    MemberRosterEntry,
    Membership,
    MembershipStatus,
    MembershipType,
    User,
)


class MemberClaimError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def roster_aad(entry_id: str) -> str:
    return f"member-roster:{entry_id}"


def normalize_member_number(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().upper()


def normalize_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def normalize_email(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().lower()


def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", unicodedata.normalize("NFKC", value))


def _secure_equal(left: str, right: str) -> bool:
    left_hash = hashlib.sha256(left.encode("utf-8")).digest()
    right_hash = hashlib.sha256(right.encode("utf-8")).digest()
    return hmac.compare_digest(left_hash, right_hash)


async def verified_roster_entry(
    session: AsyncSession,
    cipher: VersionedPIICipher,
    *,
    member_number: str,
    legal_name: str,
    email: str,
    phone: str,
) -> MemberRosterEntry:
    entry = await session.scalar(
        select(MemberRosterEntry)
        .where(
            MemberRosterEntry.member_number
            == normalize_member_number(member_number)
        )
        .with_for_update()
    )
    if entry is None or not entry.is_active:
        raise MemberClaimError("mismatch")

    aad = roster_aad(entry.id)
    matches = (
        _secure_equal(
            normalize_name(
                cipher.decrypt_text(
                    entry.legal_name_encrypted,
                    associated_data=aad,
                )
            ),
            normalize_name(legal_name),
        ),
        _secure_equal(
            normalize_email(
                cipher.decrypt_text(
                    entry.email_encrypted,
                    associated_data=aad,
                )
            ),
            normalize_email(email),
        ),
        _secure_equal(
            normalize_phone(
                cipher.decrypt_text(
                    entry.phone_encrypted,
                    associated_data=aad,
                )
            ),
            normalize_phone(phone),
        ),
    )
    if not all(matches):
        raise MemberClaimError("mismatch")
    if entry.claimed_user_id is not None:
        raise MemberClaimError("claimed")
    return entry


async def attach_roster_membership(
    session: AsyncSession,
    entry: MemberRosterEntry,
    user: User,
) -> Membership:
    existing = await session.scalar(
        select(Membership.id).where(Membership.user_id == user.id)
    )
    if existing is not None:
        raise MemberClaimError("user_has_membership")

    now = datetime.now(timezone.utc)
    membership = Membership(
        user=user,
        member_number=entry.member_number,
        status=MembershipStatus.ACTIVE,
        activated_at=now,
        status_reason="既有社員名冊認領",
        share_certificate_number=entry.share_certificate_number,
        share_capital_amount=entry.share_capital_amount,
        share_count=entry.share_count,
        share_subscribed_on=entry.share_subscribed_on,
        share_paid_on=entry.share_paid_on,
    )
    user.membership = membership
    user.membership_type = MembershipType.MEMBER
    entry.claimed_user_id = user.id
    entry.claimed_at = now
    session.add(membership)
    await session.flush()
    return membership
