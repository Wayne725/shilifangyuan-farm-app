from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_active_member, require_admin
from ..database import get_session
from ..models import (
    AdminAudit,
    BadgeDefinition,
    FiscalYear,
    GroupCampaign,
    GroupDecisionStatus,
    Meeting,
    MeetingAttendance,
    MeetingResolution,
    MeetingType,
    MemberBadge,
    MemberProposalVote,
    Membership,
    MembershipStatus,
    MembershipType,
    Notification,
    Order,
    OrderItem,
    PaymentStatus,
    PointAccount,
    PointSourceType,
    PointTransaction,
    Product,
    SalesChannel,
    SurplusDistribution,
    SurplusLedger,
    TaxType,
    User,
    Wish,
    WishStatus,
    WishSupport,
    VoteProposal,
    utcnow,
)


cooperative_router = APIRouter(tags=["cooperative"])


def _period(start: date, end: date) -> tuple[datetime, datetime]:
    if end < start:
        raise HTTPException(status_code=422, detail="結束日期不可早於開始日期")
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end, time.max, tzinfo=timezone.utc),
    )


def _csv_response(filename: str, rows: list[list[Any]]) -> StreamingResponse:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerows(rows)
    return StreamingResponse(
        iter(["\ufeff" + stream.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _paid_contributions(
    session: AsyncSession, start: datetime, end: datetime
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Order.user_id, func.sum(Order.amount_total))
            .where(
                Order.payment_status == PaymentStatus.PAID,
                Order.membership_type_snapshot == MembershipType.MEMBER,
                Order.paid_at >= start,
                Order.paid_at <= end,
            )
            .group_by(Order.user_id)
        )
    ).all()
    return {user_id: int(amount or 0) for user_id, amount in rows}


def _allocate(distributable: int, contributions: dict[str, int]) -> list[dict[str, Any]]:
    total = sum(contributions.values())
    if total <= 0 or distributable <= 0:
        return []
    ordered = sorted(contributions.items(), key=lambda item: (-item[1], item[0]))
    allocations = []
    allocated = 0
    for user_id, contribution in ordered:
        amount = distributable * contribution // total
        allocated += amount
        allocations.append(
            {
                "member_id": user_id,
                "contribution_amount": contribution,
                "contribution_basis_points": contribution * 10_000 // total,
                "distribution_amount": amount,
            }
        )
    for item in allocations[: distributable - allocated]:
        item["distribution_amount"] += 1
    return allocations


class SurplusRunRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    starts_on: date
    ends_on: date
    total_cost: int = Field(ge=0)
    reserve_percentage: int = Field(default=50, ge=0, le=100)


async def _surplus_result(session: AsyncSession, body: SurplusRunRequest) -> dict[str, Any]:
    start, end = _period(body.starts_on, body.ends_on)
    revenue = int(
        await session.scalar(
            select(func.coalesce(func.sum(Order.amount_total), 0)).where(
                Order.payment_status == PaymentStatus.PAID,
                Order.paid_at >= start,
                Order.paid_at <= end,
            )
        )
        or 0
    )
    surplus = max(revenue - body.total_cost, 0)
    reserve = surplus * body.reserve_percentage // 100
    distributable = surplus - reserve
    distributions = _allocate(
        distributable, await _paid_contributions(session, start, end)
    )
    names = dict(
        (
            await session.execute(
                select(User.id, User.display_name).where(
                    User.id.in_([item["member_id"] for item in distributions])
                )
            )
        ).all()
    ) if distributions else {}
    for item in distributions:
        item["member_name"] = names.get(item["member_id"], "")
    return {
        "label": body.label,
        "starts_on": body.starts_on,
        "ends_on": body.ends_on,
        "total_revenue": revenue,
        "total_cost": body.total_cost,
        "total_surplus": surplus,
        "reserve_percentage": body.reserve_percentage,
        "reserve_amount": reserve,
        "distributable_surplus": distributable,
        "contribution_basis": "paid_orders",
        "distributions": distributions,
    }


@cooperative_router.post("/v1/admin/surplus/dry-run")
async def dry_run_surplus(
    body: SurplusRunRequest,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _surplus_result(session, body)


@cooperative_router.post("/v1/admin/surplus/confirm", status_code=status.HTTP_201_CREATED)
async def confirm_surplus(
    body: SurplusRunRequest,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await session.scalar(select(FiscalYear).where(FiscalYear.label == body.label)):
        raise HTTPException(status_code=409, detail="此會計年度已確認，不可重複或修改")
    result = await _surplus_result(session, body)
    fiscal_year = FiscalYear(
        label=body.label,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        reserve_percentage=body.reserve_percentage,
        confirmed_at=utcnow(),
        confirmed_by_id=admin.id,
    )
    session.add(fiscal_year)
    await session.flush()
    session.add(
        SurplusLedger(
            fiscal_year_id=fiscal_year.id,
            total_revenue=result["total_revenue"],
            total_cost=result["total_cost"],
            total_surplus=result["total_surplus"],
            reserve_amount=result["reserve_amount"],
            distributable_surplus=result["distributable_surplus"],
        )
    )
    for item in result["distributions"]:
        session.add(
            SurplusDistribution(
                fiscal_year_id=fiscal_year.id,
                member_id=item["member_id"],
                contribution_amount=item["contribution_amount"],
                contribution_basis_points=item["contribution_basis_points"],
                distribution_amount=item["distribution_amount"],
            )
        )
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="surplus.confirmed",
            aggregate_type="fiscal_year",
            aggregate_id=fiscal_year.id,
            data=jsonable_encoder(result),
        )
    )
    await session.commit()
    return {**result, "fiscal_year_id": fiscal_year.id, "confirmed_at": fiscal_year.confirmed_at}


@cooperative_router.get("/v1/me/surplus-distributions")
async def my_surplus_distributions(
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(SurplusDistribution, FiscalYear, SurplusLedger)
            .join(FiscalYear, FiscalYear.id == SurplusDistribution.fiscal_year_id)
            .join(SurplusLedger, SurplusLedger.fiscal_year_id == FiscalYear.id)
            .where(SurplusDistribution.member_id == user.id)
            .order_by(FiscalYear.starts_on.desc())
        )
    ).all()
    return [
        {
            "fiscal_year_id": year.id,
            "label": year.label,
            "contribution_amount": distribution.contribution_amount,
            "contribution_basis_points": distribution.contribution_basis_points,
            "distribution_amount": distribution.distribution_amount,
            "distributable_surplus": ledger.distributable_surplus,
            "confirmed_at": year.confirmed_at,
        }
        for distribution, year, ledger in rows
    ]


@cooperative_router.get("/v1/admin/finance/nonmember-sales")
async def nonmember_sales(
    starts_on: date,
    ends_on: date,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    start, end = _period(starts_on, ends_on)
    rows = (
        await session.execute(
            select(
                Order.membership_type_snapshot,
                func.coalesce(func.sum(OrderItem.subtotal), 0),
            )
            .join(OrderItem, OrderItem.order_id == Order.id)
            .where(
                Order.payment_status == PaymentStatus.PAID,
                Order.paid_at >= start,
                Order.paid_at <= end,
            )
            .group_by(Order.membership_type_snapshot)
        )
    ).all()
    revenue_by_type = {
        membership_type: int(revenue)
        for membership_type, revenue in rows
    }
    revenues = {
        membership_type: revenue_by_type.get(membership_type, 0)
        for membership_type in (
            MembershipType.NONMEMBER,
            MembershipType.TRAINEE,
            MembershipType.MEMBER,
        )
    }
    total = sum(revenues.values())
    ratios = {
        membership_type: revenue / total if total else 0.0
        for membership_type, revenue in revenues.items()
    }
    nonmember = revenues[MembershipType.NONMEMBER]
    ratio = ratios[MembershipType.NONMEMBER]
    level = "limit" if ratio >= 0.30 else "warning" if ratio >= 0.25 else "normal"
    if level != "normal":
        existing = await session.scalar(
            select(Notification).where(
                Notification.user_id == admin.id,
                Notification.event_type == f"nonmember_sales_{level}",
                Notification.created_at >= start,
            )
        )
        if existing is None:
            session.add(
                Notification(
                    user_id=admin.id,
                    event_type=f"nonmember_sales_{level}",
                    title="非社員銷售比例警示",
                    body=f"期間非社員銷售占比為 {ratio:.1%}，系統僅警示、不阻擋交易。",
                )
            )
            await session.commit()
    return {
        "starts_on": starts_on,
        "ends_on": ends_on,
        "total_revenue": total,
        "nonmember_revenue": nonmember,
        "ratio": ratio,
        "nonmember_ratio": ratio,
        "trainee_revenue": revenues[MembershipType.TRAINEE],
        "trainee_ratio": ratios[MembershipType.TRAINEE],
        "member_revenue": revenues[MembershipType.MEMBER],
        "member_ratio": ratios[MembershipType.MEMBER],
        "sales_breakdown": [
            {
                "membership_type": membership_type.value,
                "revenue": revenues[membership_type],
                "ratio": ratios[membership_type],
            }
            for membership_type in (
                MembershipType.NONMEMBER,
                MembershipType.TRAINEE,
                MembershipType.MEMBER,
            )
        ],
        "warning_threshold": 0.25,
        "legal_limit": 0.30,
        "headroom_amount": max(int(total * 0.30) - nonmember, 0),
        "level": level,
        "transactions_blocked": False,
    }


async def _tax_rows(session: AsyncSession, start: datetime, end: datetime) -> list[list[Any]]:
    rows = (
        await session.execute(
            select(
                OrderItem.tax_type,
                Order.membership_type_snapshot,
                Order.sales_channel,
                func.sum(OrderItem.subtotal),
                func.count(func.distinct(Order.id)),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.payment_status == PaymentStatus.PAID,
                Order.paid_at >= start,
                Order.paid_at <= end,
            )
            .group_by(OrderItem.tax_type, Order.membership_type_snapshot, Order.sales_channel)
            .order_by(OrderItem.tax_type, Order.membership_type_snapshot, Order.sales_channel)
        )
    ).all()
    return [
        [tax_type.value, membership.value, channel.value, int(sales), int(sales) * 5 // 105 if tax_type == TaxType.TAXABLE else 0, int(count)]
        for tax_type, membership, channel, sales, count in rows
    ]


@cooperative_router.get("/v1/admin/finance/tax-ledger")
async def tax_ledger(
    starts_on: date,
    ends_on: date,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await _tax_rows(session, *_period(starts_on, ends_on))
    return {
        "starts_on": starts_on,
        "ends_on": ends_on,
        "rows": [
            dict(zip(["tax_type", "membership_type", "sales_channel", "sales_amount", "tax_amount", "order_count"], row))
            for row in rows
        ],
    }


@cooperative_router.get("/v1/admin/finance/tax-ledger.csv")
async def export_tax_ledger(
    starts_on: date,
    ends_on: date,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    rows = await _tax_rows(session, *_period(starts_on, ends_on))
    return _csv_response(
        f"tax-ledger-{starts_on}-{ends_on}.csv",
        [["稅別", "身分", "銷售通路", "銷售額", "稅額", "訂單筆數"], *rows],
    )


async def _point_account(session: AsyncSession, user_id: str) -> PointAccount:
    account = await session.scalar(select(PointAccount).where(PointAccount.user_id == user_id))
    if account is None:
        account = PointAccount(user_id=user_id)
        session.add(account)
        await session.flush()
    return account


@cooperative_router.get("/v1/me/points")
async def my_points(
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    account = await _point_account(session, user.id)
    transactions = (
        await session.scalars(
            select(PointTransaction).where(PointTransaction.account_id == account.id).order_by(PointTransaction.created_at.desc())
        )
    ).all()
    await session.commit()
    return {
        "balance": sum(item.amount for item in transactions),
        "transactions": [{"id": item.id, "amount": item.amount, "source_type": item.source_type, "reference_id": item.reference_id, "note": item.note, "created_at": item.created_at} for item in transactions],
    }


@cooperative_router.get("/v1/me/badges")
async def my_badges(
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    account = await _point_account(session, user.id)
    balance = int(await session.scalar(select(func.coalesce(func.sum(PointTransaction.amount), 0)).where(PointTransaction.account_id == account.id)) or 0)
    vote_count = int(await session.scalar(select(func.count(MemberProposalVote.id)).where(MemberProposalVote.user_id == user.id)) or 0)
    successful_groups = int(await session.scalar(select(func.count(GroupCampaign.id)).join(VoteProposal, VoteProposal.id == GroupCampaign.source_proposal_id).where(VoteProposal.proposer_id == user.id, GroupCampaign.decision_status == GroupDecisionStatus.CONFIRMED)) or 0)
    paid_dates = (await session.scalars(select(Order.paid_at).where(Order.user_id == user.id, Order.payment_status == PaymentStatus.PAID, Order.paid_at.is_not(None)).order_by(Order.paid_at.desc()))).all()
    months = sorted({(item.year, item.month) for item in paid_dates if item}, reverse=True)
    consecutive_months = 0
    if months:
        year, month = months[0]
        month_set = set(months)
        while (year, month) in month_set:
            consecutive_months += 1
            month -= 1
            if month == 0:
                year -= 1
                month = 12
    rules = [
        ("points_100", "合作新芽", "累積 100 點", balance >= 100, {"points": 100}),
        ("votes_5", "民主實踐者", "參與 5 次社員表決", vote_count >= 5, {"votes": 5}),
        ("groups_3", "揪團推手", "成功發起 3 次團購", successful_groups >= 3, {"successful_groups": 3}),
        ("months_3", "持續支持者", "連續 3 個消費月份", consecutive_months >= 3, {"consecutive_purchase_months": 3}),
    ]
    for key, name, description, earned, rule in rules:
        definition = await session.scalar(select(BadgeDefinition).where(BadgeDefinition.key == key))
        if definition is None:
            definition = BadgeDefinition(key=key, name=name, description=description, rule=rule)
            session.add(definition)
            await session.flush()
        if earned and await session.scalar(select(MemberBadge.id).where(MemberBadge.user_id == user.id, MemberBadge.badge_id == definition.id)) is None:
            session.add(MemberBadge(user_id=user.id, badge_id=definition.id))
    await session.commit()
    rows = (
        await session.execute(
            select(MemberBadge, BadgeDefinition).join(BadgeDefinition, BadgeDefinition.id == MemberBadge.badge_id).where(MemberBadge.user_id == user.id).order_by(MemberBadge.granted_at)
        )
    ).all()
    return [{"key": badge.key, "name": badge.name, "description": badge.description, "granted_at": grant.granted_at} for grant, badge in rows]


class PointAdjustment(BaseModel):
    user_id: str
    amount: int
    reason: str = Field(min_length=1, max_length=240)


@cooperative_router.post("/v1/admin/points/adjustments", status_code=status.HTTP_201_CREATED)
async def adjust_points(
    body: PointAdjustment,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if body.amount == 0:
        raise HTTPException(status_code=422, detail="調整點數不可為 0")
    account = await _point_account(session, body.user_id)
    transaction = PointTransaction(account_id=account.id, amount=body.amount, source_type=PointSourceType.ADMIN_ADJUSTMENT, note=body.reason, created_by_id=admin.id)
    session.add(transaction)
    await session.flush()
    session.add(AdminAudit(actor_id=admin.id, action="points.adjusted", aggregate_type="point_transaction", aggregate_id=transaction.id, reason=body.reason, data={"user_id": body.user_id, "amount": body.amount}))
    await session.commit()
    return {"id": transaction.id, "amount": transaction.amount}


class WishCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1)
    expected_price: Optional[int] = Field(default=None, ge=0)
    reference_url: Optional[str] = Field(default=None, max_length=500)


def _wish_read(wish: Wish, support_count: int, supported: bool = False) -> dict[str, Any]:
    return {"id": wish.id, "proposer_id": wish.proposer_id, "name": wish.name, "description": wish.description, "expected_price": wish.expected_price, "reference_url": wish.reference_url, "status": wish.status, "support_count": support_count, "supported_by_me": supported, "launched_product_id": wish.launched_product_id, "launched_campaign_id": wish.launched_campaign_id, "admin_note": wish.admin_note, "created_at": wish.created_at}


@cooperative_router.get("/v1/wishes")
async def list_wishes(
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Wish, func.count(WishSupport.id)).outerjoin(WishSupport).group_by(Wish.id).order_by(Wish.created_at.desc())
        )
    ).all()
    mine = set((await session.scalars(select(WishSupport.wish_id).where(WishSupport.user_id == user.id))).all())
    return [_wish_read(wish, count, wish.id in mine) for wish, count in rows]


@cooperative_router.post("/v1/wishes", status_code=status.HTTP_201_CREATED)
async def create_wish(
    body: WishCreate,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    wish = Wish(proposer_id=user.id, **body.model_dump())
    session.add(wish)
    await session.commit()
    return _wish_read(wish, 0)


@cooperative_router.post("/v1/wishes/{wish_id}/support")
async def support_wish(
    wish_id: str,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    if await session.get(Wish, wish_id) is None:
        raise HTTPException(status_code=404, detail="找不到願望")
    existing = await session.scalar(select(WishSupport).where(WishSupport.wish_id == wish_id, WishSupport.user_id == user.id))
    if existing is None:
        session.add(WishSupport(wish_id=wish_id, user_id=user.id))
        await session.commit()
    count = int(await session.scalar(select(func.count(WishSupport.id)).where(WishSupport.wish_id == wish_id)) or 0)
    return {"support_count": count}


class WishStatusUpdate(BaseModel):
    status: WishStatus
    admin_note: str = ""
    launched_product_id: Optional[str] = None
    launched_campaign_id: Optional[str] = None
    proposer_points: int = Field(default=100, ge=0)


@cooperative_router.put("/v1/admin/wishes/{wish_id}/status")
async def update_wish_status(
    wish_id: str,
    body: WishStatusUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    wish = await session.scalar(select(Wish).where(Wish.id == wish_id).with_for_update())
    if wish is None:
        raise HTTPException(status_code=404, detail="找不到願望")
    if body.status == WishStatus.LAUNCHED and not (body.launched_product_id or body.launched_campaign_id):
        raise HTTPException(status_code=422, detail="成案需關聯正式商品或團購")
    first_launch = wish.status != WishStatus.LAUNCHED and body.status == WishStatus.LAUNCHED
    wish.status = body.status
    wish.admin_note = body.admin_note
    wish.launched_product_id = body.launched_product_id
    wish.launched_campaign_id = body.launched_campaign_id
    if first_launch and body.proposer_points:
        account = await _point_account(session, wish.proposer_id)
        session.add(PointTransaction(account_id=account.id, amount=body.proposer_points, source_type=PointSourceType.WISH_LAUNCHED, reference_id=wish.id, note="願望成案", created_by_id=admin.id))
    session.add(AdminAudit(actor_id=admin.id, action="wish.status_updated", aggregate_type="wish", aggregate_id=wish.id, data=body.model_dump(mode="json")))
    await session.commit()
    count = int(await session.scalar(select(func.count(WishSupport.id)).where(WishSupport.wish_id == wish.id)) or 0)
    return _wish_read(wish, count)


class MeetingCreate(BaseModel):
    meeting_type: MeetingType
    title: str = Field(min_length=1, max_length=160)
    agenda: list[dict[str, Any]] = Field(default_factory=list)
    starts_at: datetime
    location: str = Field(default="", max_length=240)


@cooperative_router.get("/v1/meetings")
async def list_meetings(
    _user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    meetings = (await session.scalars(select(Meeting).order_by(Meeting.starts_at.desc()))).all()
    active_members = int(await session.scalar(select(func.count(Membership.id)).where(Membership.status == MembershipStatus.ACTIVE)) or 0)
    result = []
    for meeting in meetings:
        attended = int(await session.scalar(select(func.count(MeetingAttendance.id)).where(MeetingAttendance.meeting_id == meeting.id, MeetingAttendance.attended.is_(True))) or 0)
        resolutions = (await session.scalars(select(MeetingResolution).where(MeetingResolution.meeting_id == meeting.id).order_by(MeetingResolution.created_at))).all()
        result.append({"id": meeting.id, "meeting_type": meeting.meeting_type, "title": meeting.title, "agenda": meeting.agenda, "starts_at": meeting.starts_at, "location": meeting.location, "attended_count": attended, "eligible_member_count": active_members, "attendance_rate": attended / active_members if active_members else 0, "resolutions": [{"id": resolution.id, "title": resolution.title, "resolution_text": resolution.resolution_text, "member_proposal_id": resolution.member_proposal_id, "created_at": resolution.created_at} for resolution in resolutions]})
    return result


@cooperative_router.post("/v1/admin/meetings", status_code=status.HTTP_201_CREATED)
async def create_meeting(
    body: MeetingCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    meeting = Meeting(created_by_id=admin.id, **body.model_dump())
    session.add(meeting)
    await session.commit()
    return {"id": meeting.id, **body.model_dump()}


class AttendanceUpsert(BaseModel):
    member_id: str
    attended: bool = True


@cooperative_router.put("/v1/admin/meetings/{meeting_id}/attendance")
async def upsert_attendance(
    meeting_id: str,
    body: AttendanceUpsert,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    attendance = await session.scalar(select(MeetingAttendance).where(MeetingAttendance.meeting_id == meeting_id, MeetingAttendance.member_id == body.member_id))
    if attendance is None:
        attendance = MeetingAttendance(meeting_id=meeting_id, member_id=body.member_id)
        session.add(attendance)
    attendance.attended = body.attended
    attendance.checked_in_at = utcnow() if body.attended else None
    await session.flush()
    session.add(AdminAudit(actor_id=admin.id, action="meeting.attendance_updated", aggregate_type="meeting", aggregate_id=meeting_id, data=body.model_dump()))
    await session.commit()
    return {"member_id": body.member_id, "attended": attendance.attended, "checked_in_at": attendance.checked_in_at}


class ResolutionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    resolution_text: str = Field(min_length=1)
    member_proposal_id: Optional[str] = None


@cooperative_router.post("/v1/admin/meetings/{meeting_id}/resolutions", status_code=status.HTTP_201_CREATED)
async def create_meeting_resolution(
    meeting_id: str,
    body: ResolutionCreate,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if await session.get(Meeting, meeting_id) is None:
        raise HTTPException(status_code=404, detail="找不到會議")
    resolution = MeetingResolution(meeting_id=meeting_id, **body.model_dump())
    session.add(resolution)
    await session.commit()
    return {"id": resolution.id, "meeting_id": meeting_id, **body.model_dump()}


@cooperative_router.get("/v1/admin/supplier-orders.csv")
async def supplier_orders_csv(
    starts_on: date,
    ends_on: date,
    expected_arrival_on: date,
    note: str = "",
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    start, end = _period(starts_on, ends_on)
    rows = (
        await session.execute(
            select(
                OrderItem.product_name,
                func.sum(OrderItem.quantity),
                OrderItem.unit_label,
                Product.shipping_temperature,
            )
            .join(Order, Order.id == OrderItem.order_id)
            .outerjoin(Product, Product.id == OrderItem.source_product_id)
            .where(
                Order.payment_status == PaymentStatus.PAID,
                Order.paid_at >= start,
                Order.paid_at <= end,
            )
            .group_by(OrderItem.product_name, OrderItem.unit_label, Product.shipping_temperature)
            .order_by(OrderItem.product_name)
        )
    ).all()
    return _csv_response(
        f"supplier-order-{starts_on}-{ends_on}.csv",
        [["商品", "數量", "單位", "溫層", "預計到貨日", "備註"], *[[name, int(quantity), unit, temperature.value if temperature else "未設定", expected_arrival_on, note] for name, quantity, unit, temperature in rows]],
    )
