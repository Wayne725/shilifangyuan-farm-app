from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, require_admin
from ..config import Settings, get_settings
from ..database import get_session
from .. import activity_payments as service
from ..integrations.common import IntegrationError
from ..integrations.payment_service import PaymentApplicationError, payment_attempt_read
from ..models import Activity, ActivityCharge, ActivityRefundRequest, ActivityRegistration, ActivityStatus, AdminAudit, User, UserRole
from ..schemas import ActivityChargeRead, ActivityPaymentSettings, ActivityRefundRequestRead, ActivityRefundReview, ActivityRegistrationRead
from .payments import PaymentAttemptResponse

router = APIRouter(tags=["activity-payments"])


@router.get("/v1/activity-registrations", response_model=list[ActivityRegistrationRead])
async def my_registrations(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    activities = list(await session.scalars(select(Activity).where(Activity.id.in_(select(ActivityRegistration.activity_id).where(ActivityRegistration.user_id == user.id)))
        .options(*service.activity_options()).order_by(Activity.starts_at.desc())))
    return [service.registration_read(item, activity) for activity in activities for item in activity.registrations if item.user_id == user.id]


@router.get("/v1/activity-charges", response_model=list[ActivityChargeRead])
async def my_charges(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    charges = list(await session.scalars(select(ActivityCharge).where(ActivityCharge.user_id == user.id).options(
        selectinload(ActivityCharge.registration), selectinload(ActivityCharge.activity), selectinload(ActivityCharge.refund_requests), selectinload(ActivityCharge.payment_attempts)).order_by(ActivityCharge.created_at.desc())))
    return [service.charge_read(item, item.registration, item.activity) for item in charges]


@router.get("/v1/activity-charges/{charge_id}", response_model=ActivityChargeRead)
async def get_charge(charge_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    charge = await session.scalar(select(ActivityCharge).where(ActivityCharge.id == charge_id).options(
        selectinload(ActivityCharge.registration), selectinload(ActivityCharge.activity), selectinload(ActivityCharge.refund_requests), selectinload(ActivityCharge.payment_attempts)))
    if charge is None or charge.user_id != user.id and user.user_role != UserRole.ADMIN:
        raise HTTPException(404, "找不到活動繳款")
    return service.charge_read(charge, charge.registration, charge.activity)


@router.post("/v1/activity-charges/{charge_id}/payment-attempts", response_model=PaymentAttemptResponse, status_code=201)
async def start_payment(charge_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)):
    try:
        attempt = await service.create_payment_attempt(session, charge_id, user, settings)
        return payment_attempt_read(attempt, settings)
    except PaymentApplicationError as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    except (IntegrationError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(503, "線上付款目前無法使用，活動繳款紀錄已保留") from exc


@router.patch("/v1/admin/activities/{activity_id}/payment-settings")
async def update_payment_settings(activity_id: str, body: ActivityPaymentSettings, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    from .community import _activity_read
    activity = await service.load_activity(session, activity_id, lock=True)
    if activity is None:
        raise HTTPException(404, "找不到活動")
    if activity.status in {ActivityStatus.CANCELLED, ActivityStatus.COMPLETED, ActivityStatus.REJECTED}:
        raise HTTPException(409, "此活動不可修改收費設定")
    if service.aware(body.expected_updated_at) != service.aware(activity.updated_at):
        raise HTTPException(409, "活動設定已更新，請重新核對")
    before = {key: getattr(activity, key) for key in ("member_price", "non_member_price", "payment_window_hours")}
    for key in before:
        setattr(activity, key, getattr(body, key))
    session.add(AdminAudit(actor_id=admin.id, action="activity.payment_settings", aggregate_type="activity", aggregate_id=activity.id,
        data={"before": before, "after": body.model_dump(mode="json", exclude={"expected_updated_at"})}))
    await session.commit()
    return _activity_read(activity, admin)


@router.get("/v1/admin/activity-refund-requests", response_model=list[ActivityRefundRequestRead])
async def refund_requests(activity_id: str | None = None, status: str | None = None, _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    query = select(ActivityRefundRequest).join(ActivityCharge).order_by(ActivityRefundRequest.created_at.desc())
    if activity_id:
        query = query.where(ActivityCharge.activity_id == activity_id)
    if status:
        if status not in {"pending_review", "approved", "rejected", "externally_refunded"}:
            raise HTTPException(422, "無效的審核狀態")
        query = query.where(ActivityRefundRequest.status == status)
    return list(await session.scalars(query))


@router.post("/v1/admin/activity-refund-requests/{request_id}/review", response_model=ActivityRefundRequestRead)
async def review_refund(request_id: str, body: ActivityRefundReview, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if not body.reason.strip():
        raise HTTPException(422, "請填寫審核原因")
    charge_id = await session.scalar(select(ActivityRefundRequest.activity_charge_id).where(ActivityRefundRequest.id == request_id))
    if charge_id is None:
        raise HTTPException(404, "找不到退款申請")
    charge = await service.lock_activity_payment_subject(session, charge_id)
    request = await session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.id == request_id).execution_options(populate_existing=True).with_for_update())
    if request.status != "pending_review" or request.version != body.expected_version or request.amount != body.expected_amount:
        raise HTTPException(409, "退款申請已更新，請重新核對金額與版本")
    if body.decision == "approve":
        try:
            await service.approve_refund(session, request, charge, admin, reason=body.reason.strip())
        except PaymentApplicationError as exc:
            raise HTTPException(409, str(exc)) from exc
    else:
        request.status, request.version = "rejected", request.version + 1
        request.reviewed_by_id, request.reviewed_at, request.review_reason = admin.id, datetime.now(timezone.utc), body.reason.strip()
    session.add(AdminAudit(actor_id=admin.id, action=f"activity.refund_{body.decision}", aggregate_type="activity_refund_request", aggregate_id=request.id,
        reason=body.reason, data={"amount": request.amount, "payment_attempt_id": request.payment_attempt_id, "version": request.version}))
    await session.commit()
    return request


@router.post("/v1/admin/activities/{activity_id}/cancellation-preview")
async def preview_cancel(activity_id: str, _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    activity = await service.load_activity(session, activity_id, lock=True)
    if activity is None:
        raise HTTPException(404, "找不到活動")
    if activity.status in {ActivityStatus.CANCELLED, ActivityStatus.COMPLETED}:
        raise HTTPException(409, "此活動不可取消")
    return await service.cancellation_preview(session, activity)
