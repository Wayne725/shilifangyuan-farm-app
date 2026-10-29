from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import membership_type_for_user, require_admin
from ..database import get_session
from ..identity_numbers import next_identity_number
from ..meal_schedules import TAIPEI
from ..member_claims import normalize_member_number
from ..membership_admission_guards import require_legacy_admission_allowed
from ..models import (
    AdminAudit, MemberRosterEntry, Membership, MembershipApplication,
    MembershipApplicationStatus, MembershipCharge, MembershipChargeStatus,
    MembershipStatus, MembershipType, PaymentAttempt, PaymentStatus, User,
)


admin_users_router = APIRouter(prefix="/v1/admin/users", tags=["admin-users"])


class OfflineMembershipConfirmation(BaseModel):
    confirmed_offline: bool
    confirmed_on: date
    reason: str = Field(min_length=2, max_length=1000)
    member_number: str | None = Field(default=None, max_length=32)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("請填寫線下核對說明")
        return value

    @field_validator("member_number", mode="before")
    @classmethod
    def normalized_number(cls, value):
        return normalize_member_number(value) if isinstance(value, str) else value


def _user_item(user: User) -> dict:
    membership = user.__dict__.get("membership")
    return {
        "id": user.id, "customer_number": user.customer_number,
        "display_name": user.display_name, "email": user.email, "notification_email": user.notification_email,
        "user_role": user.user_role.value, "is_active": user.is_active,
        "created_at": user.created_at.replace(tzinfo=user.created_at.tzinfo or timezone.utc), "email_verified": user.email_verified_at is not None,
        "membership_type": membership_type_for_user(user).value,
        "membership_id": membership.id if membership else None,
        "membership_status": membership.status.value if membership else None,
        "member_number": membership.member_number if membership else None,
    }


@admin_users_router.get("")
async def list_registered_users(
    search: str = Query(default="", max_length=100),
    membership_type: Literal["all", "member", "trainee", "nonmember"] = "all",
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(User).outerjoin(Membership, Membership.user_id == User.id)
    if search.strip():
        query = query.where(or_(*(column.icontains(search.strip(), autoescape=True) for column in [User.email, User.notification_email, User.display_name, User.customer_number, Membership.member_number])))
    if membership_type == "member":
        query = query.where(Membership.status == MembershipStatus.ACTIVE)
    elif membership_type == "trainee":
        query = query.where(Membership.status == MembershipStatus.TRAINEE)
    elif membership_type == "nonmember":
        query = query.where(or_(Membership.id.is_(None), Membership.status.not_in({MembershipStatus.ACTIVE, MembershipStatus.TRAINEE})))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    users = await session.scalars(query.options(selectinload(User.membership)).order_by(User.created_at.desc(), User.id).offset(offset).limit(limit))
    return {"items": [_user_item(user) for user in users], "total": total or 0, "limit": limit, "offset": offset}


@admin_users_router.post("/{user_id}/offline-membership")
async def confirm_offline_membership(
    user_id: str,
    body: OfflineMembershipConfirmation,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    now = datetime.now(timezone.utc)
    if not body.confirmed_offline or body.confirmed_on > now.astimezone(TAIPEI).date():
        raise HTTPException(status_code=422, detail="請確認已核對本人與線下入社／繳費，日期不能在未來")
    user = await session.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True).with_for_update())
    if user is None:
        raise HTTPException(status_code=404, detail="找不到使用者")
    if not user.is_active:
        raise HTTPException(status_code=409, detail="停用帳號不能授予會籍")
    await require_legacy_admission_allowed(session, user_id)
    requested_number = body.member_number or None
    charge_ids = list(await session.scalars(select(MembershipCharge.id).where(MembershipCharge.user_id == user_id)))
    attempts = list(await session.scalars(select(PaymentAttempt).where(PaymentAttempt.membership_charge_id.in_(charge_ids)).order_by(PaymentAttempt.id).execution_options(populate_existing=True).with_for_update())) if charge_ids else []
    current_number = await session.scalar(select(Membership.member_number).where(Membership.user_id == user_id))
    number = current_number or requested_number
    roster_conditions = [MemberRosterEntry.claimed_user_id == user_id, MemberRosterEntry.pending_claim_user_id == user_id]
    if number:
        roster_conditions.append(MemberRosterEntry.member_number == number)
    entries = list(await session.scalars(select(MemberRosterEntry).where(or_(*roster_conditions)).order_by(MemberRosterEntry.id).execution_options(populate_existing=True).with_for_update()))
    if len(entries) > 1 or (entries and number and entries[0].member_number != number):
        raise HTTPException(status_code=409, detail="此帳號已有其他社員名冊連結，請先核對並沿用原社員編號")
    entry = entries[0] if entries else None
    application = await session.scalar(select(MembershipApplication).where(MembershipApplication.user_id == user_id).execution_options(populate_existing=True).with_for_update())
    membership = await session.scalar(select(Membership).where(Membership.user_id == user_id).execution_options(populate_existing=True).with_for_update())
    charges = list(await session.scalars(select(MembershipCharge).where(MembershipCharge.user_id == user_id).order_by(MembershipCharge.id).execution_options(populate_existing=True).with_for_update()))
    if membership and membership.member_number and requested_number and requested_number != membership.member_number:
        raise HTTPException(status_code=409, detail="已有社員編號，請沿用原編號")
    if membership and membership.status == MembershipStatus.ACTIVE:
        user.membership = membership
        return _user_item(user)
    if membership and membership.status in {MembershipStatus.RESIGNED, MembershipStatus.TERMINATED}:
        raise HTTPException(status_code=409, detail="已退出／終止會籍需先核對歷史股金與退還紀錄，不能直接重新入社")

    if any(charge.status in {MembershipChargeStatus.PAID, MembershipChargeStatus.REFUND_PENDING} for charge in charges):
        raise HTTPException(status_code=409, detail="已有線上繳款或退款中的入社案件，請先核對款項後沿用既有會籍流程")
    if any(attempt.status in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.LATE_PAID_REFUND_REQUIRED} for attempt in attempts):
        raise HTTPException(status_code=409, detail="入社款項仍需核對，請先完成既有收退款處理")

    number = (membership.member_number if membership else None) or requested_number
    if entry:
        if not entry.is_active or entry.claimed_user_id not in {None, user_id}:
            raise HTTPException(status_code=409, detail="此社員名冊已停用或屬於其他帳號")
        pending_until = entry.pending_claim_expires_at
        if entry.pending_claim_user_id not in {None, user_id} and (pending_until is None or pending_until.replace(tzinfo=pending_until.tzinfo or timezone.utc) > now):
            raise HTTPException(status_code=409, detail="此社員名冊正由其他帳號認領")
        number = entry.member_number
    if number:
        conflict = await session.scalar(select(Membership.id).where(Membership.member_number == number, Membership.user_id != user_id))
        if conflict:
            raise HTTPException(status_code=409, detail="此社員編號已屬於其他帳號")
    else:
        number = await next_identity_number(session, Membership.member_number, "SLF", now, reserved_columns=(MemberRosterEntry.member_number,))

    previous_status = membership.status.value if membership else None
    if membership is None:
        membership = Membership(user=user, application_id=application.id if application else None)
        session.add(membership)
    if entry and (membership.share_capital_amount or membership.share_count) and (membership.share_capital_amount != entry.share_capital_amount or membership.share_count != entry.share_count):
        raise HTTPException(status_code=409, detail="會籍與名冊股籍資料不一致，請先核對股籍")
    membership.member_number = number
    membership.status = MembershipStatus.ACTIVE
    membership.activated_at = membership.activated_at or now
    membership.suspended_at = None
    membership.ended_at = None
    membership.status_reason = body.reason
    if entry:
        membership.share_certificate_number = entry.share_certificate_number
        membership.share_capital_amount = entry.share_capital_amount
        membership.share_count = entry.share_count
        membership.share_subscribed_on = entry.share_subscribed_on
        membership.share_paid_on = entry.share_paid_on
        entry.claimed_user_id = user_id
        entry.claimed_at = entry.claimed_at or now
        entry.pending_claim_user_id = None
        entry.pending_claim_expires_at = None
    for charge in charges:
        if charge.status == MembershipChargeStatus.PENDING:
            charge.status = MembershipChargeStatus.WAIVED
    for attempt in attempts:
        if attempt.status == PaymentStatus.PENDING:
            attempt.status = PaymentStatus.EXPIRED
            attempt.expires_at = now
    if application:
        application.status = MembershipApplicationStatus.APPROVED
        application.reviewed_by_id = admin.id
        application.reviewed_at = now
        application.review_reason = body.reason
    user.membership = membership
    user.membership_type = MembershipType.MEMBER
    user.pending_member_claim = False
    try:
        await session.flush()
        session.add(AdminAudit(actor_id=admin.id, action="membership.offline_confirmed", aggregate_type="membership", aggregate_id=membership.id, reason=body.reason, data={"user_id": user_id, "member_number": number, "previous_status": previous_status, "confirmed_on": body.confirmed_on.isoformat(), "source": "offline", "closed_pending_charge_ids": [charge.id for charge in charges if charge.status == MembershipChargeStatus.WAIVED]}))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="社員編號或名冊已被其他操作使用，請重新查詢") from exc
    return _user_item(user)
