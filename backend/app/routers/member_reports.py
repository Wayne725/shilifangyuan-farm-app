from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..account_profiles import member_contact_from_profile
from ..admin_explanations import AdminExplanation
from ..auth import require_admin
from ..config import Settings, get_settings
from ..csv_export import csv_response
from ..database import get_session
from ..domain import aware
from ..integrations.common import IntegrationError
from ..integrations.notifications import NotificationCommand, NotificationService, SQLAlchemyNotificationRepository
from ..integrations.pii_crypto import pii_cipher_from_settings
from ..meal_booking import TAIPEI
from ..member_claims import roster_aad
from ..member_point_adjustments import (
    MemberPointAdjustment, MemberPointAdjustmentReceipt, adjust_member_points, read_member_point_adjustment,
)
from ..member_spending import CATEGORIES, SpendFilters, SpendPeriod, spend_details, spend_summary
from ..models import (AdminAudit, MemberProfile, MemberRosterEntry, Membership, MembershipCharge,
    MembershipChargeStatus, MembershipOfflineReceipt, MembershipStatus, PaymentStatus, PointAccount, PointTransaction,
    Refund, RefundStatus, User)

member_reports_router = APIRouter(prefix="/v1/admin", tags=["member-reports"])


def _cipher(settings):
    try:
        return pii_cipher_from_settings(settings)
    except IntegrationError as exc:
        raise HTTPException(status_code=503, detail="私密資料加密設定尚未完成") from exc


def _local(value):
    return aware(value).astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M") if value else ""


def _range(starts_on, ends_on):
    if starts_on and ends_on and starts_on > ends_on:
        raise HTTPException(status_code=422, detail="結束日期不可早於開始日期")
    start = datetime.combine(starts_on, time.min, TAIPEI) if starts_on else None
    end = datetime.combine(ends_on + timedelta(days=1), time.min, TAIPEI) if ends_on else None
    return start, end


@member_reports_router.get("/member-spending")
async def read_member_spending(
    filters: Annotated[SpendFilters, Query()],
    response: Response,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    return await spend_summary(session, filters, _cipher(settings))


@member_reports_router.get("/member-spending.csv")
async def export_member_spending(
    filters: Annotated[SpendFilters, Query()],
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    result = await spend_summary(session, filters, _cipher(settings), export=True)
    rows = result["items"]
    await _audit_spending_export(session, admin.id, filters, len(rows), "summary")
    return csv_response(
        "member-spending.csv",
        ["社員編號", "姓名", "會籍狀態", "消費金額合計", "筆數", "最近消費日", *CATEGORIES.values()],
        ([row["member_number"], row["name"], row["status_label"], row["net_amount"], row["order_count"],
          row["latest_paid_date"], *(row["categories"][key] for key in CATEGORIES)] for row in rows),
    )


async def _member_spending_details(session, period, member_id, settings, *, export=False):
    result = await spend_details(session, period, str(member_id), _cipher(settings), export=export)
    if result is None:
        raise HTTPException(404, "找不到此社員")
    return result


@member_reports_router.get("/member-spending/{member_id}")
async def read_member_spending_details(
    member_id: UUID,
    period: Annotated[SpendPeriod, Query()],
    response: Response,
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    response.headers["Cache-Control"] = "no-store"
    return await _member_spending_details(session, period, member_id, settings)


@member_reports_router.get("/member-spending/{member_id}/details.csv")
async def export_member_spending_details(
    member_id: UUID,
    period: Annotated[SpendPeriod, Query()],
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    result = await _member_spending_details(session, period, member_id, settings, export=True)
    rows = result["items"]
    await _audit_spending_export(session, admin.id, period, len(rows), "details", str(member_id))
    return csv_response(
        "member-spending-details.csv",
        ["日期", "訂單編號", "類別", "金額", "付款方式", "發票號碼", "退款金額"],
        ([row[key] for key in ("date", "order_number", "category_label", "amount", "payment_method", "invoice_number", "refunded")]
         for row in rows),
    )


async def _audit_spending_export(session, admin_id, period, count, kind, member_id=None):
    start, end = period.dates()
    session.add(AdminAudit(
        actor_id=admin_id, action="member_spending.exported", aggregate_type="member_spending",
        aggregate_id=member_id or str(uuid4()),
        data={"kind": kind, "count": count, "starts_on": start.isoformat(), "ends_on": end.isoformat()},
    ))
    await session.commit()


async def _member_rows(session, settings, *, newest_first=False, member_number=""):
    cipher = _cipher(settings)
    roster_query = select(MemberRosterEntry).order_by(MemberRosterEntry.member_number)
    member_query = (select(Membership, User, MemberProfile).join(User, User.id == Membership.user_id)
        .outerjoin(MemberProfile, MemberProfile.user_id == User.id).order_by(Membership.member_number, Membership.id))
    if member_number:
        roster_query = roster_query.where(MemberRosterEntry.member_number == member_number)
        member_query = member_query.where(Membership.member_number == member_number)
    roster = list(await session.scalars(roster_query))
    joined = (await session.execute(member_query)).all()
    seen, rows = set(), []
    def decrypt(value, aad):
        return cipher.decrypt_text(value, associated_data=aad) if value else ""
    for membership, user, profile in joined:
        if membership.member_number:
            seen.add(membership.member_number)
        aad = f"member-profile:{user.id}"
        legal_name, email, phone = member_contact_from_profile(user, profile, cipher)
        row = {"id": membership.id, "source": "membership", "member_number": membership.member_number or membership.trainee_number or "",
               "status": membership.status.value, "legal_name": legal_name, "email": email, "phone": phone,
               "share_certificate_number": membership.share_certificate_number, "share_count": membership.share_count,
               "share_capital_amount": membership.share_capital_amount, "share_subscribed_on": membership.share_subscribed_on,
               "share_paid_on": membership.share_paid_on, "activated_at": membership.activated_at,
               "_created_at": membership.created_at}
        for name in ("birth_date", "gender", "place_of_origin", "occupation", "address", "registered_address", "correspondence_address", "landline_phone", "line_id", "emergency_contact"):
            row[name] = decrypt(getattr(profile, f"{name}_encrypted"), aad) if profile else ""
        rows.append(row)
    for entry in roster:
        if entry.member_number in seen:
            continue
        aad = roster_aad(entry.id)
        rows.append({"id": entry.id, "source": "roster", "member_number": entry.member_number,
            "status": "active" if entry.is_active else "inactive", "legal_name": decrypt(entry.legal_name_encrypted, aad),
            "email": decrypt(entry.email_encrypted, aad), "phone": decrypt(entry.phone_encrypted, aad),
            "address": decrypt(entry.address_encrypted, aad),
            "share_certificate_number": entry.share_certificate_number, "share_count": entry.share_count,
            "share_capital_amount": entry.share_capital_amount, "share_subscribed_on": entry.share_subscribed_on,
            "share_paid_on": entry.share_paid_on, "activated_at": entry.claimed_at,
            "_created_at": entry.created_at})
    if newest_first:
        rows.sort(key=lambda row: (aware(row["_created_at"]), row["id"], row["source"]), reverse=True)
    else:
        rows.sort(key=lambda row: (row["member_number"], row["id"]))
    for row in rows:
        row.pop("_created_at")
    return rows


def _filter_members(rows, status, q):
    term = q.strip().lower()
    return [row for row in rows if (status == "all" or row["status"] == status) and
            (not term or any(term in str(row.get(key) or "").lower() for key in ("member_number", "legal_name", "email", "share_certificate_number")))]


@member_reports_router.get("/member-register")
async def list_member_register(q: str = Query(default="", max_length=120), status: Literal["active", "all"] = "active",
    member_number: str = Query(default="", max_length=100),
    limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    rows = _filter_members(await _member_rows(session, settings, newest_first=True, member_number=member_number), status, q)
    return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}


@member_reports_router.get("/member-register.csv")
async def export_member_register(q: str = Query(default="", max_length=120), status: Literal["active", "all"] = "active",
    member_number: str = Query(default="", max_length=100),
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    rows = _filter_members(await _member_rows(session, settings, member_number=member_number), status, q)
    session.add(AdminAudit(actor_id=admin.id, action="membership.register_exported", aggregate_type="member_register", aggregate_id=str(uuid4()), data={"count": len(rows), "status": status}))
    await session.commit()
    columns = ["社員編號", "姓名", "Email", "手機", "股號", "股數", "股金", "認股日期", "繳股日期", "會籍狀態", "入社日期", "出生日期", "性別", "籍貫", "職業", "現居地址", "戶籍地址", "通訊地址", "市話", "LINE ID", "緊急聯絡人"]
    keys = ["member_number", "legal_name", "email", "phone", "share_certificate_number", "share_count", "share_capital_amount", "share_subscribed_on", "share_paid_on", "status", "activated_at", "birth_date", "gender", "place_of_origin", "occupation", "address", "registered_address", "correspondence_address", "landline_phone", "line_id", "emergency_contact"]
    return csv_response("member-register.csv", columns, [[_local(row[key]) if key == "activated_at" else row.get(key) for key in keys] for row in rows])


async def _payment_rows(session, settings, member_number):
    cipher = _cipher(settings)
    charge_query = select(MembershipCharge).options(
        selectinload(MembershipCharge.membership), selectinload(MembershipCharge.user),
        selectinload(MembershipCharge.payment_attempts), selectinload(MembershipCharge.refunds),
    )
    receipt_query = select(MembershipOfflineReceipt)
    if member_number:
        charge_query = charge_query.where(MembershipCharge.membership.has(Membership.member_number == member_number))
        receipt_query = receipt_query.where(MembershipOfflineReceipt.member_number == member_number)
    charges = list(await session.scalars(charge_query))
    rows = []
    for charge in charges:
        payments = sorted([attempt for attempt in charge.payment_attempts if attempt.paid_at], key=lambda attempt: aware(attempt.paid_at), reverse=True)
        attempt = payments[0] if payments else None
        refund = sum(item.amount for item in charge.refunds if item.status == RefundStatus.COMPLETED)
        received = sum(payment.amount for payment in payments) if payments else charge.amount if charge.paid_at else 0
        rows.append({"id": charge.id, "source": "online", "member_number": charge.membership.member_number or charge.membership.trainee_number or "",
            "name": charge.user.display_name, "email": charge.user.email or charge.user.notification_email or "", "kind": charge.charge_kind.value,
            "amount": charge.amount, "received": received, "refunded": refund, "net": received - refund,
            "status": charge.status.value, "paid_at": charge.paid_at, "receipt_number": charge.receipt_number,
            "payment_method": attempt.provider if attempt else "", "reference": attempt.provider_trade_no if attempt else "", "recorded_at": charge.created_at})
    receipts = list(await session.scalars(receipt_query))
    members = {row["member_number"]: row for row in await _member_rows(session, settings, member_number=member_number)} if receipts else {}
    for receipt in receipts:
        person = members.get(receipt.member_number, {})
        received = 0 if receipt.voided_at else receipt.amount
        rows.append({"id": receipt.id, "source": "offline", "member_number": receipt.member_number,
            "name": person.get("legal_name", ""), "email": person.get("email", ""), "kind": receipt.charge_kind,
            "amount": receipt.amount, "received": received, "refunded": 0, "net": received,
            "status": "voided" if receipt.voided_at else "paid", "paid_at": receipt.paid_at, "receipt_number": receipt.receipt_number,
            "payment_method": receipt.payment_method, "reference": cipher.decrypt_text(receipt.payer_reference_encrypted, associated_data=f"member-receipt:{receipt.id}") if receipt.payer_reference_encrypted else "", "recorded_at": receipt.recorded_at})
    return rows


async def _filtered_payments(session, settings, q, kind, starts_on, ends_on, member_number):
    start, end = _range(starts_on, ends_on)
    term = q.strip().lower()
    rows = await _payment_rows(session, settings, member_number)
    return sorted([row for row in rows if (kind == "all" or row["kind"] == kind) and
        (not term or any(term in str(row.get(key) or "").lower() for key in ("member_number", "name", "email", "receipt_number", "reference"))) and
        (not start or row["paid_at"] and aware(row["paid_at"]) >= start) and (not end or row["paid_at"] and aware(row["paid_at"]) < end)],
        key=lambda row: (aware(row["paid_at"] or row["recorded_at"]), row["id"], row["source"]), reverse=True)


@member_reports_router.get("/membership-payments")
async def list_membership_payments(q: str = Query(default="", max_length=120), kind: Literal["all", "admission_fee", "share_capital"] = "all",
    member_number: str = Query(default="", max_length=100),
    starts_on: date | None = None, ends_on: date | None = None, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    rows = await _filtered_payments(session, settings, q, kind, starts_on, ends_on, member_number)
    return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset,
            "summary": {key: sum(row[key] for row in rows) for key in ("received", "refunded", "net")}}


@member_reports_router.get("/membership-payments.csv")
async def export_membership_payments(q: str = Query(default="", max_length=120), kind: Literal["all", "admission_fee", "share_capital"] = "all",
    member_number: str = Query(default="", max_length=100),
    starts_on: date | None = None, ends_on: date | None = None,
    admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    rows = await _filtered_payments(session, settings, q, kind, starts_on, ends_on, member_number)
    session.add(AdminAudit(actor_id=admin.id, action="membership.payments_exported", aggregate_type="membership_payments", aggregate_id=str(uuid4()), data={"count": len(rows)}))
    await session.commit()
    return csv_response("membership-payments.csv", ["收據編號", "社員編號", "姓名", "Email", "款項", "應收金額", "實收", "已退款", "淨收款", "狀態", "收款日期", "收款方式", "收款資訊", "來源", "登錄時間"],
        [[row["receipt_number"], row["member_number"], row["name"], row["email"], "入社費" if row["kind"] == "admission_fee" else "股金", row["amount"], row["received"], row["refunded"], row["net"], row["status"], _local(row["paid_at"]), row["payment_method"], row["reference"], row["source"], _local(row["recorded_at"])] for row in rows])


class OfflineReceiptCreate(BaseModel):
    idempotency_key: UUID
    target_type: Literal["membership", "roster"]
    target_id: str = Field(min_length=1, max_length=36)
    kind: Literal["admission_fee", "share_capital"]
    amount: int = Field(gt=0, le=100000000)
    payment_method: Literal["cash", "bank_transfer", "other"]
    paid_at: datetime
    reference: str = Field(default="", max_length=500)
    confirmed_received: Literal[True]

    @field_validator("paid_at")
    @classmethod
    def validate_paid_at(cls, value):
        if value.tzinfo is None or value.utcoffset() is None or value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("收款日期須包含時區且不可在未來")
        return value


@member_reports_router.post("/membership-offline-receipts", status_code=201)
async def create_offline_receipt(body: OfflineReceiptCreate, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    # A stable member lock also serializes duplicate receipt submissions.
    if body.target_type == "membership":
        member = await session.scalar(select(Membership).where(Membership.id == body.target_id).with_for_update())
        if not member or not member.member_number:
            raise HTTPException(status_code=409, detail="請先核對正式社員編號；線上申請款項請依原案件收款")
        number, owner_id = member.member_number, member.user_id
        outstanding = await session.scalar(select(MembershipCharge.id).where(MembershipCharge.membership_id == member.id, MembershipCharge.status.in_({MembershipChargeStatus.PENDING, MembershipChargeStatus.REFUND_PENDING})).limit(1))
        if outstanding:
            raise HTTPException(status_code=409, detail="此社員有待繳或退款中的線上款項，請先核對原案件，避免重複登錄")
    else:
        member = await session.scalar(select(MemberRosterEntry).where(MemberRosterEntry.id == body.target_id).with_for_update())
        if not member:
            raise HTTPException(status_code=404, detail="找不到社員名冊")
        number, owner_id = member.member_number, member.claimed_user_id
    if body.target_type == "roster" and owner_id:
        outstanding = await session.scalar(select(MembershipCharge.id).where(MembershipCharge.user_id == owner_id, MembershipCharge.status.in_({MembershipChargeStatus.PENDING, MembershipChargeStatus.REFUND_PENDING})).limit(1))
        if outstanding:
            raise HTTPException(status_code=409, detail="此社員有待繳或退款中的線上款項，請先核對原案件")
    cipher = _cipher(settings)
    existing = await session.scalar(select(MembershipOfflineReceipt).where(MembershipOfflineReceipt.idempotency_key == str(body.idempotency_key)))
    if existing:
        same_target = existing.membership_id == body.target_id if body.target_type == "membership" else existing.roster_entry_id == body.target_id
        previous_reference = cipher.decrypt_text(existing.payer_reference_encrypted, associated_data=f"member-receipt:{existing.id}") if existing.payer_reference_encrypted else ""
        if previous_reference != body.reference or not same_target or existing.amount != body.amount or existing.charge_kind != body.kind or existing.payment_method != body.payment_method or aware(existing.paid_at) != body.paid_at:
            raise HTTPException(status_code=409, detail="此收款請求已使用，請重新核對")
        return {"id": existing.id, "receipt_number": existing.receipt_number, "status": "voided" if existing.voided_at else "paid"}
    cipher, receipt_id = _cipher(settings), str(uuid4())
    receipt = MembershipOfflineReceipt(id=receipt_id, idempotency_key=str(body.idempotency_key), member_number=number,
        membership_id=body.target_id if body.target_type == "membership" else None, roster_entry_id=body.target_id if body.target_type == "roster" else None,
        charge_kind=body.kind, amount=body.amount, payment_method=body.payment_method, paid_at=body.paid_at,
        receipt_number=f"MRC{datetime.now(TAIPEI):%Y%m%d}{uuid4().hex[:10].upper()}", recorded_by_id=admin.id,
        payer_reference_encrypted=cipher.encrypt_text(body.reference, associated_data=f"member-receipt:{receipt_id}") if body.reference else None)
    session.add(receipt)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="此收款請求已登錄，請重新查詢核對") from exc
    session.add(AdminAudit(actor_id=admin.id, action="membership.offline_receipt_created", aggregate_type="membership_receipt", aggregate_id=receipt.id, data={"amount": body.amount, "kind": body.kind, "member_number": number}))
    if owner_id:
        owner = await session.get(User, owner_id)
        await NotificationService(SQLAlchemyNotificationRepository(session)).publish(NotificationCommand(user_id=owner_id, event_type="membership_payment_recorded", title="入社款項收款紀錄", body=f"{'入社費' if body.kind == 'admission_fee' else '股金'} NT${body.amount}，收款日期 {_local(body.paid_at)}，收據編號 {receipt.receipt_number}。", data={"receipt_id": receipt.id}, email=owner.notification_email or owner.email, dedupe_key=f"membership-receipt:{receipt.id}"))
    await session.commit()
    return {"id": receipt.id, "receipt_number": receipt.receipt_number, "status": "paid"}


class ReceiptVoid(BaseModel):
    reason: AdminExplanation = Field(default="", max_length=1000)


@member_reports_router.post("/membership-offline-receipts/{receipt_id}/void")
async def void_offline_receipt(receipt_id: str, body: ReceiptVoid, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    receipt = await session.scalar(select(MembershipOfflineReceipt).where(MembershipOfflineReceipt.id == receipt_id).with_for_update())
    if not receipt:
        raise HTTPException(status_code=404, detail="找不到收款紀錄")
    if receipt.voided_at is None:
        receipt.voided_at = datetime.now(timezone.utc)
        receipt.void_reason = body.reason
        session.add(AdminAudit(actor_id=admin.id, action="membership.offline_receipt_voided", aggregate_type="membership_receipt", aggregate_id=receipt.id, reason=body.reason))
    await session.commit()
    return {"id": receipt.id, "status": "voided"}


async def _point_rows(session, member_number="", q=""):
    balance = select(PointTransaction.account_id, func.sum(PointTransaction.amount).label("balance")).group_by(PointTransaction.account_id).subquery()
    statement = select(User, Membership, func.coalesce(balance.c.balance, 0)).outerjoin(Membership, Membership.user_id == User.id)
    statement = (statement.outerjoin(PointAccount, PointAccount.user_id == User.id)
        .outerjoin(balance, balance.c.account_id == PointAccount.id)
        .where(Membership.status == MembershipStatus.ACTIVE).order_by(User.display_name, User.id))
    if member_number:
        statement = statement.where(Membership.member_number == member_number)
    if q:
        email = func.coalesce(func.nullif(User.email, ""), User.notification_email, "")
        searchable = func.coalesce(Membership.member_number, "") + " " + User.display_name + " " + email
        statement = statement.where(searchable.icontains(q, autoescape=True))
    rows = (await session.execute(statement)).all()
    return [{"user_id": user.id, "name": user.display_name, "email": user.email or user.notification_email or "",
             "member_number": membership.member_number, "balance": int(points),
             "can_adjust": bool(user.is_active and membership.member_number)} for user, membership, points in rows]


@member_reports_router.get("/points-ledger")
async def list_point_balances(
    member_number: str = Query(default="", max_length=100),
    q: str = Query(default="", max_length=120),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    search = q.strip()
    rows = await _point_rows(session, member_number, search)
    return {"items": rows, "total": len(rows), "applied_query": search, "purchase_rule": "正式社員每筆已確認付款消費滿 100 元累積 1 點；退款依原訂單扣回"}


@member_reports_router.get("/points-ledger.csv")
async def export_point_balances(member_number: str = Query(default="", max_length=100), admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return await _export_point_balances(session, admin, member_number)


@member_reports_router.post("/points-ledger/{user_id}/adjustments", status_code=201, response_model=MemberPointAdjustmentReceipt)
async def post_point_adjustment(user_id: str, body: MemberPointAdjustment, response: Response,
                                admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await adjust_member_points(session, user_id, admin.id, body)
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise


@member_reports_router.get("/points-ledger/{user_id}/adjustments/{request_id}", response_model=MemberPointAdjustmentReceipt)
async def point_adjustment_receipt(user_id: str, request_id: UUID, response: Response,
                                  admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return await read_member_point_adjustment(session, user_id, admin.id, request_id)


@member_reports_router.get("/points-ledger/filtered.csv")
async def export_filtered_point_balances(
    q: str = Query(min_length=1, max_length=120),
    member_number: str = Query(default="", max_length=100),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    search = q.strip()
    if not search:
        raise HTTPException(status_code=422, detail="請輸入點數搜尋條件")
    return await _export_point_balances(session, admin, member_number, search)


async def _export_point_balances(session, admin, member_number, search=""):
    rows = await _point_rows(session, member_number, search)
    session.add(AdminAudit(actor_id=admin.id, action="points.exported", aggregate_type="point_account", aggregate_id=str(uuid4()), data={"count": len(rows)}))
    await session.commit()
    return csv_response("member-points.csv", ["社員編號", "姓名", "Email", "累積點數"], [[row[key] for key in ("member_number", "name", "email", "balance")] for row in rows])
