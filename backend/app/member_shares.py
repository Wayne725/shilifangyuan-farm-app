from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminAudit, MemberRosterEntry, Membership
from .schemas import MemberSharesUpdate


class MemberSharesError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _snapshot(record: MemberRosterEntry | Membership | None) -> dict | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "share_capital_amount": record.share_capital_amount,
        "share_count": record.share_count,
        "updated_at": _aware(record.updated_at).isoformat(),
    }


async def update_member_shares(
    session: AsyncSession,
    *,
    target_kind: Literal["roster", "membership"],
    target_id: str,
    actor_id: str,
    changes: MemberSharesUpdate,
) -> tuple[MemberRosterEntry | None, Membership | None]:
    roster: MemberRosterEntry | None = None
    membership: Membership | None = None
    if target_kind == "roster":
        roster = await session.scalar(
            select(MemberRosterEntry).where(MemberRosterEntry.id == target_id)
            .execution_options(populate_existing=True).with_for_update()
        )
        if roster is None:
            raise MemberSharesError("找不到社員名冊紀錄", 404)
        if roster.claimed_user_id is not None:
            membership = await session.scalar(
                select(Membership).where(Membership.user_id == roster.claimed_user_id)
                .execution_options(populate_existing=True).with_for_update()
            )
            if membership is None or membership.member_number != roster.member_number:
                raise MemberSharesError("名冊與已認領會籍連結不一致，請先核對資料")
        elif await session.scalar(select(Membership.id).where(Membership.member_number == roster.member_number)):
            raise MemberSharesError("同一社員編號已有會籍，但名冊尚未正確連結")
        target = roster
    else:
        identity = (await session.execute(
            select(Membership.user_id, Membership.member_number).where(Membership.id == target_id)
        )).one_or_none()
        if identity is None:
            raise MemberSharesError("找不到社員會籍", 404)
        conditions = [MemberRosterEntry.claimed_user_id == identity.user_id]
        if identity.member_number is not None:
            conditions.append(MemberRosterEntry.member_number == identity.member_number)
        candidates = list(await session.scalars(
            select(MemberRosterEntry).where(or_(*conditions)).order_by(MemberRosterEntry.id)
            .execution_options(populate_existing=True).with_for_update()
        ))
        if len(candidates) > 1:
            raise MemberSharesError("會籍對應多筆不一致名冊，請先核對資料")
        roster = candidates[0] if candidates else None
        membership = await session.scalar(
            select(Membership).where(Membership.id == target_id)
            .execution_options(populate_existing=True).with_for_update()
        )
        if membership is None:
            raise MemberSharesError("找不到社員會籍", 404)
        if (membership.user_id, membership.member_number) != tuple(identity):
            raise MemberSharesError("社員資料已變更，請重新讀取後再修改")
        if roster is not None and (
            roster.claimed_user_id != membership.user_id or roster.member_number != membership.member_number
        ):
            raise MemberSharesError("名冊與已認領會籍連結不一致，請先核對資料")
        target = membership

    if _aware(target.updated_at) != changes.expected_updated_at:
        raise MemberSharesError("社員資料已變更，請重新讀取後再修改")
    before = {"roster": _snapshot(roster), "membership": _snapshot(membership)}
    records = [record for record in (roster, membership) if record is not None]
    updated_at = max(datetime.now(timezone.utc), *(_aware(record.updated_at) + timedelta(microseconds=1) for record in records))
    for record in records:
        record.share_capital_amount = changes.share_capital_amount
        record.share_count = changes.share_count
        record.updated_at = updated_at
    session.add(AdminAudit(
        actor_id=actor_id,
        action="member_shares.updated",
        aggregate_type="member_roster_entry" if target_kind == "roster" else "membership",
        aggregate_id=target_id,
        reason=changes.reason,
        data={"before": before, "after": {"roster": _snapshot(roster), "membership": _snapshot(membership)}},
    ))
    await session.flush()
    return roster, membership
