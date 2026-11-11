from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .auth import membership_type_for_user
from .models import (
    Activity, ActivityCharge, ActivityRefundRequest, ActivityRegistration,
    ActivityRegistrationStatus as RegistrationStatus, ActivityStatus, AdminAudit,
    MembershipType, Notification, OutboxEvent, PaymentAttempt, PaymentStatus,
    Refund, RefundStatus, User, UserRole,
)
from .schemas import ActivityChargeRead, ActivityRefundRequestRead, ActivityRegistrationRead
from .integrations.notifications import NotificationCommand, NotificationService, SQLAlchemyNotificationRepository

OCCUPIED = {RegistrationStatus.PENDING_PAYMENT, RegistrationStatus.REGISTERED, RegistrationStatus.ATTENDED, RegistrationStatus.NO_SHOW}
RECEIVED = {PaymentStatus.PAID, PaymentStatus.LATE_PAID_REFUND_REQUIRED, PaymentStatus.REFUND_PENDING}
UNSETTLED = {PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED}


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def valid_email(*values):
    for value in values:
        if not value:
            continue
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized
        except EmailNotValidError:
            continue
    return None


def activity_options():
    return (
        selectinload(Activity.registrations).selectinload(ActivityRegistration.user).selectinload(User.membership),
        selectinload(Activity.registrations).selectinload(ActivityRegistration.charges).selectinload(ActivityCharge.payment_attempts),
        selectinload(Activity.registrations).selectinload(ActivityRegistration.charges).selectinload(ActivityCharge.refund_requests),
    )


async def load_activity(session, activity_id, *, lock=False):
    query = select(Activity).where(Activity.id == activity_id).options(*activity_options()).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def lock_activity_payment_subject(session, charge_id):
    identity = (await session.execute(select(ActivityCharge.activity_id, ActivityCharge.registration_id).where(ActivityCharge.id == charge_id))).first()
    if identity is None:
        return None
    await session.execute(select(Activity.id).where(Activity.id == identity.activity_id).with_for_update())
    await session.execute(select(ActivityRegistration.id).where(ActivityRegistration.id == identity.registration_id).with_for_update())
    return await session.scalar(select(ActivityCharge).where(ActivityCharge.id == charge_id).options(
        selectinload(ActivityCharge.activity), selectinload(ActivityCharge.registration),
        selectinload(ActivityCharge.payment_attempts), selectinload(ActivityCharge.refund_requests),
        selectinload(ActivityCharge.refunds),
    ).execution_options(populate_existing=True).with_for_update())


def current_charge(registration):
    return next((item for item in registration.__dict__.get("charges", []) if item.registration_revision == registration.revision), None)


def seat_matches(charge, registration):
    return registration.revision == charge.registration_revision and registration.status in OCCUPIED


def payable(charge, registration, activity, now=None):
    current = now or datetime.now(timezone.utc)
    return (seat_matches(charge, registration) and registration.status == RegistrationStatus.PENDING_PAYMENT
        and charge.payment_status == PaymentStatus.PENDING and activity.status == ActivityStatus.PUBLISHED
        and aware(charge.expires_at) > current and aware(activity.registration_deadline) > current)


def charge_read(charge, registration=None, activity=None):
    data = ActivityChargeRead.model_validate(charge)
    attempts = charge.__dict__.get("payment_attempts", [])
    waiting_verification = any(item.status in UNSETTLED and item.unpaid_verified_at is None
        and aware(item.expires_at) <= datetime.now(timezone.utc) for item in attempts)
    if registration is not None and activity is not None and payable(charge, registration, activity) and not waiting_verification:
        data.available_actions.append("pay")
    request_statuses = {item.payment_attempt_id: item.status for item in charge.refund_requests}
    if (registration is not None and seat_matches(charge, registration) and registration.status == RegistrationStatus.REGISTERED
        and charge.payment_status == PaymentStatus.PAID and any(item.status == PaymentStatus.PAID
            and request_statuses.get(item.id) not in {"pending_review", "approved"}
            for item in charge.__dict__.get("payment_attempts", []))):
        data.available_actions.append("request_refund")
    return data


def registration_read(registration, activity=None):
    data = ActivityRegistrationRead.model_validate(registration)
    if activity is not None:
        data.activity_title = activity.title
        data.starts_at, data.ends_at, data.location = aware(activity.starts_at), aware(activity.ends_at), activity.location
    charge = current_charge(registration)
    if charge is not None:
        data.charge = charge_read(charge, registration, activity)
        requests = sorted(charge.refund_requests, key=lambda item: aware(item.created_at), reverse=True)
        if requests:
            data.refund_request = ActivityRefundRequestRead.model_validate(requests[0])
    if (registration.status == RegistrationStatus.WAITLISTED and activity is not None
        and activity.status == ActivityStatus.PUBLISHED and aware(activity.registration_deadline) > datetime.now(timezone.utc)):
        data.available_actions.append("update_contact")
    if registration.status == RegistrationStatus.WAITLISTED or registration.status == RegistrationStatus.REGISTERED and charge is None:
        data.available_actions.append("cancel")
    elif charge is not None:
        if registration.status == RegistrationStatus.PENDING_PAYMENT and not any(item.status in UNSETTLED and item.unpaid_verified_at is None for item in charge.__dict__.get("payment_attempts", [])):
            data.available_actions.append("cancel")
        if data.charge and "request_refund" in data.charge.available_actions:
            data.available_actions.append("request_refund")
    return data


async def notify_once(session, *, user_id, key, title, body, email=None, activity_id=None, charge_id=None):
    # Every caller holds the activity row, so the notification and email share one deduplicated transaction.
    exists = await session.scalar(select(Notification.id).where(Notification.user_id == user_id, Notification.data["dedupe_key"].as_string() == key))
    if exists is not None:
        return
    await NotificationService(SQLAlchemyNotificationRepository(session), email_event_types={"activity.payment"}).publish(NotificationCommand(
        user_id=user_id, event_type="activity.payment", title=title, body=body,
        data={"activity_id": activity_id, "activity_charge_id": charge_id}, email=valid_email(email), dedupe_key=key,
    ))


async def admit_registration(session, activity, registration, now):
    user = await session.scalar(select(User).where(User.id == registration.user_id).options(selectinload(User.membership)))
    membership_type = membership_type_for_user(user)
    amount = activity.member_price if membership_type in {MembershipType.MEMBER, MembershipType.TRAINEE} else activity.non_member_price
    if amount:
        email = valid_email(registration.contact_email, user.notification_email, user.email)
        if email is None:
            return False
        registration.contact_email = email
        registration.contact_name = registration.contact_name or user.display_name
        charge = ActivityCharge(
            id=str(uuid4()), number=f"A{now:%y%m%d}{uuid4().hex[:18].upper()}",
            activity_id=activity.id, registration_id=registration.id, user_id=user.id,
            registration_revision=registration.revision, activity_title=activity.title,
            contact_name=registration.contact_name, contact_email=email, membership_type=membership_type,
            amount=amount, payment_status=PaymentStatus.PENDING,
            expires_at=min(now + timedelta(hours=activity.payment_window_hours), aware(activity.registration_deadline)),
            payment_attempts=[], refund_requests=[], refunds=[],
        )
        session.add(charge)
        registration.charges.append(charge)
        registration.status = RegistrationStatus.PENDING_PAYMENT
        await session.flush()
        await notify_once(session, user_id=user.id, key=f"activity-charge:{charge.id}", title="活動名額已保留，請完成付款",
            body=f"「{activity.title}」應繳 NT${amount}，請於 {aware(charge.expires_at).astimezone(timezone(timedelta(hours=8))):%m/%d %H:%M} 前付款。憑證由活動主辦單位提供。",
            email=email, activity_id=activity.id, charge_id=charge.id)
    else:
        registration.status = RegistrationStatus.REGISTERED
    return True


async def promote_waitlist(session, activity, now):
    if activity.status != ActivityStatus.PUBLISHED or aware(activity.registration_deadline) <= now:
        return
    available = activity.capacity - sum(item.status in OCCUPIED for item in activity.registrations)
    waiting = sorted((item for item in activity.registrations if item.status == RegistrationStatus.WAITLISTED), key=lambda item: (item.queue_position, aware(item.registered_at), item.id))
    for registration in waiting[:available]:
        if not await admit_registration(session, activity, registration, now):
            await notify_once(session, user_id=registration.user_id, key=f"activity-contact-needed:{registration.id}:{registration.revision}",
                title="活動候補請補聯絡資料", body=f"「{activity.title}」已可遞補，請至我的活動補上有效 Email 後完成報名付款。", activity_id=activity.id)
            break
        if registration.status == RegistrationStatus.REGISTERED:
            await notify_once(session, user_id=registration.user_id, key=f"activity-promoted:{registration.id}:{registration.revision}", title="活動候補遞補成功", body=f"你已遞補為「{activity.title}」正式名額。", email=registration.contact_email, activity_id=activity.id)


async def register(session, activity_id, user, contact=None):
    activity = await load_activity(session, activity_id, lock=True)
    if activity is None or activity.status != ActivityStatus.PUBLISHED:
        raise HTTPException(404, "找不到活動")
    now = datetime.now(timezone.utc)
    existing = next((item for item in activity.registrations if item.user_id == user.id), None)
    if existing and existing.status != RegistrationStatus.CANCELLED:
        if existing.status == RegistrationStatus.WAITLISTED and contact is not None:
            existing.contact_name, existing.contact_email = contact.contact_name.strip(), str(contact.contact_email)
            await promote_waitlist(session, activity, now)
        await session.commit()
        return registration_read(existing, activity)
    if not activity.is_public:
        raise HTTPException(404, '找不到活動')
    if aware(activity.registration_deadline) <= now:
        raise HTTPException(409, "活動報名已截止")
    name = contact.contact_name.strip() if contact else user.display_name
    email = valid_email(str(contact.contact_email) if contact else None, user.notification_email, user.email)
    if (activity.member_price or activity.non_member_price) and not email:
        raise HTTPException(422, "收費活動請提供有效的聯絡 Email")
    if not name:
        raise HTTPException(422, "請填寫聯絡姓名")
    await promote_waitlist(session, activity, now)
    waiting = any(item.status == RegistrationStatus.WAITLISTED for item in activity.registrations)
    seat = not waiting and sum(item.status in OCCUPIED for item in activity.registrations) < activity.capacity
    if not seat and not activity.waitlist_enabled:
        raise HTTPException(409, "活動名額已額滿")
    position = max((item.queue_position for item in activity.registrations), default=0) + 1
    if existing is None:
        existing = ActivityRegistration(id=str(uuid4()), activity_id=activity.id, user_id=user.id, revision=1, queue_position=position, charges=[])
        session.add(existing)
        activity.registrations.append(existing)
    else:
        existing.revision += 1
        existing.queue_position = position
    existing.contact_name, existing.contact_email = name, email
    existing.registered_at, existing.cancelled_at, existing.checked_in_at = now, None, None
    existing.status = RegistrationStatus.WAITLISTED
    await session.flush()
    if seat:
        await admit_registration(session, activity, existing, now)
    await session.commit()
    return registration_read(existing, activity)


async def create_payment_attempt(session, charge_id, user, settings, *, return_storefront=None):
    from .integrations.payment_service import (
        PaymentApplicationError, _build_checkout_payload, browser_return_base, ensure_payment_runtime_enabled,
    )
    from .integrations.ecpay import create_merchant_trade_no
    charge = await lock_activity_payment_subject(session, charge_id)
    if charge is None or charge.user_id != user.id:
        raise HTTPException(404, "找不到活動繳款")
    now = datetime.now(timezone.utc)
    if not payable(charge, charge.registration, charge.activity, now):
        raise PaymentApplicationError("此活動款項目前無法付款，請重新查詢報名狀態")
    ensure_payment_runtime_enabled(settings, amount=charge.amount)
    for attempt in sorted(charge.payment_attempts, key=lambda item: aware(item.created_at), reverse=True):
        if attempt.status == PaymentStatus.PENDING and aware(attempt.expires_at) > now and attempt.checkout_payload:
            return attempt
        if attempt.status in UNSETTLED and attempt.unpaid_verified_at is None:
            raise PaymentApplicationError("前次付款結果仍在確認中，請稍後重新整理")
    attempt = PaymentAttempt(activity_charge=charge, provider=settings.payment_provider, browser_return_storefront=return_storefront,
        merchant_trade_no=create_merchant_trade_no(f"activity-{charge.id}", len(charge.payment_attempts)+1, now),
        amount=charge.amount, status=PaymentStatus.PENDING, expires_at=min(aware(charge.expires_at), now+timedelta(minutes=settings.payment_reservation_minutes)))
    session.add(attempt)
    await session.flush()
    client_back_url = f"{browser_return_base(settings, return_storefront)}/activities/registrations"
    if return_storefront == "legacy":
        client_back_url += "?" + urlencode({"activity_charge_id": charge.id, "attempt_id": attempt.id})
    attempt.checkout_payload = _build_checkout_payload(settings=settings, attempt=attempt, amount=charge.amount,
        item_name=charge.activity_title, client_back_url=client_back_url,
        custom_fields={"CustomField1": charge.id, "CustomField2": "activity_charge"}, current=now)
    await session.commit()
    return attempt


async def request_refund(session, charge, attempt, *, kind, reason, actor_id=None):
    request = await session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id == attempt.id).with_for_update())
    if request is not None:
        if request.status == "rejected" and actor_id is not None:
            request.status, request.version = "pending_review", request.version + 1
            request.reason, request.requested_by_id = reason, actor_id
            request.reviewed_at = request.reviewed_by_id = request.review_reason = None
        return request
    request = ActivityRefundRequest(activity_charge_id=charge.id, payment_attempt_id=attempt.id, kind=kind,
        amount=attempt.amount, reason=reason, requested_by_id=actor_id, status="pending_review", version=1)
    session.add(request)
    await session.flush()
    await notify_once(session, user_id=charge.user_id, key=f"activity-refund-review:{request.id}", title="活動退款待審核",
        body=f"「{charge.activity_title}」NT${attempt.amount} 已列入退款審核；須管理員核准後才會辦理退款。", email=charge.contact_email, activity_id=charge.activity_id, charge_id=charge.id)
    return request


async def cancel_registration(session, activity_id, user, body):
    activity = await load_activity(session, activity_id, lock=True)
    if activity is None:
        raise HTTPException(404, "找不到活動")
    registration = next((item for item in activity.registrations if item.user_id == user.id), None)
    if registration is None or registration.status not in {RegistrationStatus.REGISTERED, RegistrationStatus.WAITLISTED, RegistrationStatus.PENDING_PAYMENT}:
        raise HTTPException(409, "目前沒有可取消的報名")
    if body.expected_revision is not None and body.expected_revision != registration.revision:
        raise HTTPException(409, "報名資料已更新，請重新核對")
    charge = current_charge(registration)
    if charge and charge.payment_status == PaymentStatus.PAID:
        attempts = [item for item in charge.payment_attempts if item.status == PaymentStatus.PAID]
        for attempt in sorted(attempts, key=lambda item: item.id):
            await request_refund(session, charge, attempt, kind="registration_cancellation", reason=body.reason, actor_id=user.id)
    else:
        if charge and any(item.status in UNSETTLED and item.unpaid_verified_at is None for item in charge.payment_attempts):
            raise HTTPException(409, "付款結果仍在確認中，請先核對付款，再申請取消")
        registration.status, registration.cancelled_at = RegistrationStatus.CANCELLED, datetime.now(timezone.utc)
        if charge and charge.payment_status == PaymentStatus.PENDING:
            charge.payment_status = PaymentStatus.EXPIRED
        await promote_waitlist(session, activity, datetime.now(timezone.utc))
    await session.commit()
    refreshed = await load_activity(session, activity_id)
    return registration_read(next(item for item in refreshed.registrations if item.id == registration.id), refreshed)


def invalidate_unpaid_proof(attempt, payload):
    if attempt.activity_charge_id is None or attempt.status not in UNSETTLED:
        return
    if payload.get("PaymentDisposition") == "pending" or payload.get("RtnCode") != "1" or payload.get("SimulatePaid") == "1":
        attempt.unpaid_verified_at = None
        attempt.settlement_unknown_since = attempt.settlement_unknown_since or datetime.now(timezone.utc)


async def apply_callback(session, repository, attempt, payload):
    from .integrations.payment_service import _provider_payment_time
    charge = await lock_activity_payment_subject(session, attempt.activity_charge_id)
    disposition = payload.get("PaymentDisposition")
    if disposition == "refunded":
        await repository._assign_provider_trade_no(attempt, payload)
        refund = await session.scalar(select(Refund).where(Refund.payment_attempt_id == attempt.id).with_for_update())
        if refund is None:
            refund = Refund(activity_charge_id=charge.id, payment_attempt_id=attempt.id, provider=attempt.provider,
                amount=attempt.amount, status=RefundStatus.COMPLETED, purpose="activity_external_refund",
                reason="金流已完成退款，系統同步實際退款結果", requested_by_id=charge.user_id)
            session.add(refund)
        refund.provider_response = dict(payload)
        refund.provider_refund_id = payload.get("RayGateAssociatedOrderID") or refund.provider_refund_id
        refund.status, refund.completed_at = RefundStatus.COMPLETED, datetime.now(timezone.utc)
        attempt.paid_at = attempt.paid_at or _provider_payment_time(payload) or refund.completed_at
        charge.paid_at = charge.paid_at or attempt.paid_at
        request = await session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id == attempt.id).with_for_update())
        if request is not None and request.status in {"pending_review", "rejected"}:
            request.status, request.version = "externally_refunded", request.version + 1
            request.reviewed_by_id, request.reviewed_at = None, refund.completed_at
            request.review_reason = "金流已完成退款；此紀錄不代表管理員核准"
        await apply_refund_completion(session, charge, attempt, refund)
        return "refunded"
    if disposition == "refund_failed":
        refund = await session.scalar(select(Refund).where(Refund.payment_attempt_id == attempt.id).with_for_update())
        if refund and refund.status != RefundStatus.COMPLETED:
            refund.status, refund.provider_response = RefundStatus.FAILED, dict(payload)
        return "refund_failed"
    if attempt.status in RECEIVED | {PaymentStatus.REFUNDED}:
        return "duplicate"
    attempt.provider_response = dict(payload)
    if disposition == "pending" or payload.get("RtnCode") != "1" or payload.get("SimulatePaid") == "1":
        invalidate_unpaid_proof(attempt, payload)
        return "pending"
    await repository._assign_provider_trade_no(attempt, payload)
    now = datetime.now(timezone.utc)
    paid_at = _provider_payment_time(payload) or now
    attempt.paid_at = paid_at
    attempt.unpaid_verified_at = attempt.settlement_unknown_since = None
    registration, activity = charge.registration, charge.activity
    accepted = (seat_matches(charge, registration) and registration.status == RegistrationStatus.PENDING_PAYMENT
        and activity.status == ActivityStatus.PUBLISHED and charge.payment_status == PaymentStatus.PENDING
        and paid_at <= aware(charge.expires_at))
    if accepted:
        attempt.status = charge.payment_status = PaymentStatus.PAID
        charge.paid_at = paid_at
        registration.status = RegistrationStatus.REGISTERED
        await notify_once(session, user_id=charge.user_id, key=f"activity-paid:{attempt.id}", title="活動付款完成",
            body=f"「{charge.activity_title}」NT${attempt.amount} 付款完成，報名已確認。憑證由活動主辦單位提供。", email=charge.contact_email, activity_id=charge.activity_id, charge_id=charge.id)
    else:
        attempt.status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
        if charge.payment_status not in {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING}:
            charge.payment_status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
            charge.paid_at = charge.paid_at or paid_at
        await request_refund(session, charge, attempt, kind="late_or_duplicate_payment", reason="付款晚到、名額已失效或重複收款，待管理員核准整筆退款")
        if seat_matches(charge, registration) and registration.status == RegistrationStatus.PENDING_PAYMENT:
            registration.status, registration.cancelled_at = RegistrationStatus.CANCELLED, now
            await session.flush()
            activity = await load_activity(session, activity.id, lock=True)
            await promote_waitlist(session, activity, now)
    return attempt.status.value


async def record_payment_verification(session, attempt_id, now, *, verified_unpaid=False, response=None):
    charge_id = await session.scalar(select(PaymentAttempt.activity_charge_id).where(PaymentAttempt.id == attempt_id))
    if charge_id is None:
        return False
    charge = await lock_activity_payment_subject(session, charge_id)
    attempt = await session.scalar(select(PaymentAttempt).where(PaymentAttempt.id == attempt_id).execution_options(populate_existing=True).with_for_update())
    if attempt.status not in UNSETTLED:
        await session.commit()
        return True
    if response is not None:
        attempt.provider_response = dict(response)
    if verified_unpaid and aware(attempt.expires_at) <= now:
        attempt.unpaid_verified_at = now
        attempt.settlement_unknown_since = None
        attempt.status = PaymentStatus.EXPIRED
    elif not verified_unpaid:
        attempt.unpaid_verified_at = None
        attempt.settlement_unknown_since = attempt.settlement_unknown_since or now
    await session.flush()
    if aware(charge.expires_at) <= now:
        await expire_charge(session, charge, now)
    await session.commit()
    return True


async def expire_charge(session, charge, now):
    if charge.payment_status != PaymentStatus.PENDING or aware(charge.expires_at) > now:
        return
    unresolved = await session.scalar(select(PaymentAttempt.id).where(PaymentAttempt.activity_charge_id == charge.id,
        PaymentAttempt.status.in_(UNSETTLED), PaymentAttempt.unpaid_verified_at.is_(None)).limit(1))
    if unresolved:
        return
    charge.payment_status = PaymentStatus.EXPIRED
    registration = charge.registration
    if seat_matches(charge, registration) and registration.status == RegistrationStatus.PENDING_PAYMENT:
        registration.status, registration.cancelled_at = RegistrationStatus.CANCELLED, now
        await session.flush()
        activity = await load_activity(session, charge.activity_id, lock=True)
        await promote_waitlist(session, activity, now)


async def reconcile_charges(session, now, limit):
    from sqlalchemy import exists
    ids = list(await session.scalars(select(ActivityCharge.id).where(ActivityCharge.payment_status == PaymentStatus.PENDING,
        ActivityCharge.expires_at <= now, ~exists().where(PaymentAttempt.activity_charge_id == ActivityCharge.id, PaymentAttempt.status.in_(UNSETTLED), PaymentAttempt.unpaid_verified_at.is_(None))).order_by(ActivityCharge.expires_at, ActivityCharge.id).limit(limit)))
    for charge_id in ids:
        charge = await lock_activity_payment_subject(session, charge_id)
        await expire_charge(session, charge, now)
        await session.commit()
    return len(ids)


async def approve_refund(session, request, charge, admin, *, reason):
    from .integrations.payment_service import create_provider_aware_refund
    attempt = await session.scalar(select(PaymentAttempt).where(PaymentAttempt.id == request.payment_attempt_id).execution_options(populate_existing=True).with_for_update())
    if attempt.status not in RECEIVED or attempt.amount != request.amount:
        raise HTTPException(409, "原付款狀態已變更，請重新核對")
    request.status, request.version = "approved", request.version + 1
    request.reviewed_by_id, request.reviewed_at, request.review_reason = admin.id, datetime.now(timezone.utc), reason
    await session.flush()
    refund, _ = await create_provider_aware_refund(session, activity_charge=charge, payment_attempt=attempt,
        amount=attempt.amount, reason=reason, requested_by_id=admin.id, defer_completion=True, purpose="activity_cancellation")
    attempt.status = PaymentStatus.REFUND_PENDING
    registration = charge.registration
    # Refunding a duplicate receipt must not cancel the separate, accepted payment's seat.
    if request.kind != "late_or_duplicate_payment" and seat_matches(charge, registration):
        registration.status, registration.cancelled_at = RegistrationStatus.CANCELLED, datetime.now(timezone.utc)
        charge.payment_status = PaymentStatus.REFUND_PENDING
        await session.flush()
        activity = await load_activity(session, charge.activity_id, lock=True)
        await promote_waitlist(session, activity, datetime.now(timezone.utc))
    elif charge.payment_status != PaymentStatus.PAID:
        charge.payment_status = PaymentStatus.REFUND_PENDING
    return refund


async def apply_refund_completion(session, charge, attempt, refund):
    attempt.status = PaymentStatus.REFUNDED
    await session.flush()
    remaining = list(await session.scalars(select(PaymentAttempt.status).where(PaymentAttempt.activity_charge_id == charge.id,
        PaymentAttempt.id != attempt.id, PaymentAttempt.status.in_(RECEIVED))))
    if PaymentStatus.PAID in remaining:
        charge.payment_status = PaymentStatus.PAID
    elif PaymentStatus.REFUND_PENDING in remaining:
        charge.payment_status = PaymentStatus.REFUND_PENDING
    elif remaining:
        charge.payment_status = PaymentStatus.LATE_PAID_REFUND_REQUIRED
    else:
        charge.payment_status = PaymentStatus.REFUNDED
    if PaymentStatus.PAID not in remaining:
        registration = charge.registration
        if seat_matches(charge, registration):
            registration.status, registration.cancelled_at = RegistrationStatus.CANCELLED, datetime.now(timezone.utc)
            await session.flush()
            activity = await load_activity(session, charge.activity_id, lock=True)
            await promote_waitlist(session, activity, datetime.now(timezone.utc))
    await notify_once(session, user_id=charge.user_id, key=f"activity-refunded:{refund.id}", title="活動退款完成",
        body=f"「{charge.activity_title}」NT${refund.amount} 已完成退款。", email=charge.contact_email, activity_id=charge.activity_id, charge_id=charge.id)


async def cancellation_preview(session, activity):
    rows = (await session.execute(select(PaymentAttempt.id, PaymentAttempt.amount, PaymentAttempt.status, ActivityCharge.id)
        .join(ActivityCharge, PaymentAttempt.activity_charge_id == ActivityCharge.id)
        .where(ActivityCharge.activity_id == activity.id, PaymentAttempt.status.in_({PaymentStatus.PAID, PaymentStatus.LATE_PAID_REFUND_REQUIRED}))
        .order_by(PaymentAttempt.id))).all()
    registrations = sorted((item.id, item.revision, item.status.value) for item in activity.registrations)
    token = hashlib.sha256(json.dumps({"updated_at": aware(activity.updated_at).isoformat(), "rows": [tuple(row) for row in rows], "registrations": registrations}, sort_keys=True, default=str).encode()).hexdigest()
    return {"activity_id": activity.id, "expected_updated_at": aware(activity.updated_at), "preview_token": token,
        "registration_count": sum(item.status in OCCUPIED for item in activity.registrations),
        "waitlist_count": sum(item.status == RegistrationStatus.WAITLISTED for item in activity.registrations), "paid_count": len(rows), "refund_amount": sum(row.amount for row in rows)}
