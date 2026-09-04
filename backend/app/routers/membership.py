from __future__ import annotations

import hmac
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import (
    get_current_user,
    membership_type_for_user,
    require_active_member,
    require_admin,
)
from ..config import Settings, get_settings
from ..database import get_session
from ..identity_numbers import next_identity_number
from ..integrations.common import IntegrationError
from ..integrations.pii_crypto import pii_cipher_from_settings
from ..integrations.r2_storage import r2_document_storage_from_settings
from ..integrations.notifications import (
    NotificationCommand,
    NotificationService,
    SQLAlchemyNotificationRepository,
)
from ..integrations.payment_service import (
    PaymentApplicationError,
    allow_local_refund_without_payment_attempt,
    create_provider_aware_refund,
)
from ..member_claims import (
    MemberClaimError,
    attach_roster_membership,
    normalize_email,
    normalize_member_number,
    normalize_phone,
    roster_aad,
    verified_roster_entry,
)
from ..models import (
    AdminAudit,
    MemberDirectoryEntry,
    MemberProfile,
    MemberRosterEntry,
    Membership,
    MembershipApplication,
    MembershipApplicationStatus,
    MembershipCharge,
    MembershipChargeKind,
    MembershipChargeStatus,
    MembershipDocument,
    MembershipDocumentStatus,
    MembershipFeeSchedule,
    MembershipStatus,
    MembershipType,
    OutboxEvent,
    User,
    new_id,
)
from ..rate_limit import DOCUMENT_UPLOAD_RULE, client_key, enforce
from ..schemas import (
    ExistingMemberClaimRequest,
    MemberDirectoryRead,
    MemberDirectoryUpdate,
    MembershipApplicationRead,
    MembershipApplicationReview,
    MembershipApplicationSubmit,
    MembershipChargeRead,
    MembershipDocumentConfirm,
    MembershipDocumentRead,
    MembershipDocumentUploadRead,
    MembershipDocumentUploadRequest,
    MembershipFeeScheduleInput,
    MembershipMeRead,
    MembershipProfileRead,
    MembershipRead,
    MemberRosterEntryCreate,
    MemberRosterEntryRead,
)


membership_router = APIRouter(tags=["membership"])


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _aware_optional(value: Optional[datetime]) -> Optional[datetime]:
    return _aware(value) if value is not None else None


def _ensure_user_can_apply(user: User) -> None:
    membership = user.__dict__.get("membership")
    if membership is not None and membership.status in {
        MembershipStatus.TRAINEE,
        MembershipStatus.ACTIVE,
        MembershipStatus.SUSPENDED,
    }:
        raise HTTPException(
            status_code=409,
            detail="此帳號已有會籍，無法重複提出入社申請",
        )


def _document_response(
    document: MembershipDocument,
) -> MembershipDocumentRead:
    return MembershipDocumentRead(
        id=document.id,
        document_type=document.document_type,
        status=document.status,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        checksum_sha256=document.checksum_sha256,
        confirmed_at=_aware_optional(document.confirmed_at),
    )


def _profile_aad(user_id: str) -> str:
    return f"member-profile:{user_id}"


def _decrypt_optional(cipher, value: Optional[str], aad: str) -> Optional[str]:
    if value is None:
        return None
    return cipher.decrypt_text(value, associated_data=aad)


def _cipher(settings: Settings):
    try:
        return pii_cipher_from_settings(settings)
    except IntegrationError as exc:
        raise HTTPException(
            status_code=503,
            detail="私密資料加密設定尚未完成",
        ) from exc


def _mask_email(value: str) -> str:
    local, separator, domain = value.partition("@")
    if not separator:
        return "***"
    visible = local[:1]
    return f"{visible}{'*' * max(3, len(local) - 1)}@{domain}"


def _mask_phone(value: str) -> str:
    digits = normalize_phone(value)
    return f"{'*' * max(0, len(digits) - 4)}{digits[-4:]}"


def _roster_response(
    entry: MemberRosterEntry,
    cipher,
) -> MemberRosterEntryRead:
    aad = roster_aad(entry.id)
    return MemberRosterEntryRead(
        id=entry.id,
        member_number=entry.member_number,
        legal_name=cipher.decrypt_text(
            entry.legal_name_encrypted,
            associated_data=aad,
        ),
        email_masked=_mask_email(
            cipher.decrypt_text(
                entry.email_encrypted,
                associated_data=aad,
            )
        ),
        phone_masked=_mask_phone(
            cipher.decrypt_text(
                entry.phone_encrypted,
                associated_data=aad,
            )
        ),
        share_certificate_number=entry.share_certificate_number,
        share_capital_amount=entry.share_capital_amount,
        share_count=entry.share_count,
        is_active=entry.is_active,
        claimed=entry.claimed_user_id is not None,
        claimed_at=_aware_optional(entry.claimed_at),
    )


def _storage(settings: Settings):
    try:
        return r2_document_storage_from_settings(settings)
    except IntegrationError as exc:
        raise HTTPException(
            status_code=503,
            detail="私密證件儲存設定尚未完成",
        ) from exc


def get_document_storage(settings: Settings = Depends(get_settings)):
    """Injectable so the private-document path can be covered by tests."""
    return _storage(settings)


async def _application_for_user(
    session: AsyncSession,
    user_id: str,
    *,
    with_documents: bool = False,
    for_update: bool = False,
) -> Optional[MembershipApplication]:
    if for_update:
        await session.scalar(
            select(User.id)
            .where(User.id == user_id)
            .with_for_update()
        )
    query = select(MembershipApplication).where(
        MembershipApplication.user_id == user_id
    )
    if with_documents:
        query = query.options(selectinload(MembershipApplication.documents))
    if for_update:
        query = query.with_for_update()
    return await session.scalar(query)


async def _application_response(
    application: MembershipApplication,
    session: AsyncSession,
    settings: Settings,
) -> MembershipApplicationRead:
    profile = await session.scalar(
        select(MemberProfile).where(MemberProfile.user_id == application.user_id)
    )
    documents = (
        await session.scalars(
            select(MembershipDocument)
            .where(
                MembershipDocument.application_id == application.id,
                MembershipDocument.status != MembershipDocumentStatus.DELETED,
            )
            .order_by(MembershipDocument.created_at)
        )
    ).all()
    profile_read = None
    if profile is not None:
        cipher = _cipher(settings)
        aad = _profile_aad(application.user_id)
        profile_read = MembershipProfileRead(
            legal_name=cipher.decrypt_text(
                profile.legal_name_encrypted,
                associated_data=aad,
            ),
            phone=cipher.decrypt_text(
                profile.phone_encrypted,
                associated_data=aad,
            ),
            birth_date=date.fromisoformat(
                cipher.decrypt_text(
                    profile.birth_date_encrypted,
                    associated_data=aad,
                )
            ),
            address=cipher.decrypt_text(
                profile.address_encrypted,
                associated_data=aad,
            ),
            emergency_contact=cipher.decrypt_text(
                profile.emergency_contact_encrypted,
                associated_data=aad,
            ),
            consent_version=profile.consent_version,
            consented_at=_aware(profile.consented_at),
            identity_number=_decrypt_optional(
                cipher, profile.identity_number_encrypted, aad
            ),
            gender=_decrypt_optional(cipher, profile.gender_encrypted, aad),
            place_of_origin=_decrypt_optional(
                cipher, profile.place_of_origin_encrypted, aad
            ),
            occupation=_decrypt_optional(
                cipher, profile.occupation_encrypted, aad
            ),
            registered_address=_decrypt_optional(
                cipher, profile.registered_address_encrypted, aad
            ),
            correspondence_address=(
                _decrypt_optional(
                    cipher,
                    profile.correspondence_address_encrypted,
                    aad,
                )
                or cipher.decrypt_text(profile.address_encrypted, associated_data=aad)
            ),
            landline_phone=_decrypt_optional(
                cipher, profile.landline_phone_encrypted, aad
            ),
            line_id=_decrypt_optional(cipher, profile.line_id_encrypted, aad),
        )
    return MembershipApplicationRead(
        id=application.id,
        user_id=application.user_id,
        status=application.status,
        submitted_at=_aware_optional(application.submitted_at),
        reviewed_at=_aware_optional(application.reviewed_at),
        review_reason=application.review_reason,
        created_at=_aware(application.created_at),
        updated_at=_aware(application.updated_at),
        profile=profile_read,
        documents=[_document_response(document) for document in documents],
    )


async def _save_profile_and_application(
    session: AsyncSession,
    user: User,
    body: MembershipApplicationSubmit,
    settings: Settings,
) -> MembershipApplication:
    _ensure_user_can_apply(user)
    application = await _application_for_user(
        session,
        user.id,
        for_update=True,
    )
    if application is None:
        application = MembershipApplication(user_id=user.id)
        session.add(application)
        await session.flush()
    if application.status not in {
        MembershipApplicationStatus.DRAFT,
        MembershipApplicationStatus.NEEDS_SUPPLEMENT,
    }:
        raise HTTPException(status_code=409, detail="此申請目前不可修改")

    cipher = _cipher(settings)
    encrypted = {
        "legal_name_encrypted": cipher.encrypt_text(
            body.legal_name,
            associated_data=_profile_aad(user.id),
        ),
        "phone_encrypted": cipher.encrypt_text(
            body.phone,
            associated_data=_profile_aad(user.id),
        ),
        "birth_date_encrypted": cipher.encrypt_text(
            body.birth_date.isoformat(),
            associated_data=_profile_aad(user.id),
        ),
        "address_encrypted": cipher.encrypt_text(
            body.address,
            associated_data=_profile_aad(user.id),
        ),
        "emergency_contact_encrypted": cipher.encrypt_text(
            body.emergency_contact,
            associated_data=_profile_aad(user.id),
        ),
    }
    optional_private_fields = {
        "identity_number_encrypted": body.identity_number,
        "gender_encrypted": body.gender,
        "place_of_origin_encrypted": body.place_of_origin,
        "occupation_encrypted": body.occupation,
        "registered_address_encrypted": body.registered_address,
        "correspondence_address_encrypted": (
            body.correspondence_address or body.address
        ),
        "landline_phone_encrypted": body.landline_phone,
        "line_id_encrypted": body.line_id,
    }
    for field, value in optional_private_fields.items():
        if value is not None:
            encrypted[field] = (
                cipher.encrypt_text(
                    value,
                    associated_data=_profile_aad(user.id),
                )
                if value
                else None
            )
    profile = await session.scalar(
        select(MemberProfile).where(MemberProfile.user_id == user.id)
    )
    if profile is None:
        profile = MemberProfile(
            user_id=user.id,
            consent_version=body.consent_version,
            consented_at=datetime.now(timezone.utc),
            encryption_key_version=cipher.current_version,
            **encrypted,
        )
        session.add(profile)
    else:
        for field, value in encrypted.items():
            setattr(profile, field, value)
        profile.consent_version = body.consent_version
        profile.consented_at = datetime.now(timezone.utc)
        profile.encryption_key_version = cipher.current_version
    await session.flush()
    return application


async def _ensure_membership_charges(
    session: AsyncSession,
    application: MembershipApplication,
) -> Membership:
    membership = await session.scalar(
        select(Membership)
        .where(Membership.user_id == application.user_id)
        .with_for_update()
    )
    if membership is None:
        membership = Membership(
            user_id=application.user_id,
            application_id=application.id,
            status=MembershipStatus.PENDING_PAYMENT,
        )
        session.add(membership)
        await session.flush()
    elif membership.status in {
        MembershipStatus.TRAINEE,
        MembershipStatus.ACTIVE,
        MembershipStatus.SUSPENDED,
    }:
        raise HTTPException(
            status_code=409,
            detail="此帳號已有會籍，無法重複建立入社費用",
        )
    elif membership.application_id is None:
        membership.application_id = application.id
    today = date.today()
    for kind in (
        MembershipChargeKind.ADMISSION_FEE,
        MembershipChargeKind.SHARE_CAPITAL,
    ):
        existing = await session.scalar(
            select(MembershipCharge.id).where(
                MembershipCharge.application_id == application.id,
                MembershipCharge.charge_kind == kind,
            )
        )
        if existing is not None:
            continue
        schedule = await _active_fee_schedule(session, kind, today)
        session.add(
            MembershipCharge(
                user_id=application.user_id,
                application_id=application.id,
                membership_id=membership.id,
                fee_schedule_id=schedule.id,
                charge_kind=kind,
                amount=schedule.amount,
            )
        )
    await session.flush()
    return membership


@membership_router.get(
    "/v1/membership/application",
    response_model=MembershipApplicationRead,
)
async def get_my_application(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    application = await _application_for_user(session, user.id)
    if application is None:
        raise HTTPException(status_code=404, detail="尚未建立入社申請")
    return await _application_response(application, session, settings)


@membership_router.put(
    "/v1/membership/application",
    response_model=MembershipApplicationRead,
)
async def save_my_application(
    body: MembershipApplicationSubmit,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    application = await _save_profile_and_application(
        session,
        user,
        body,
        settings,
    )
    await session.commit()
    return await _application_response(application, session, settings)


@membership_router.post(
    "/v1/membership/application/submit",
    response_model=MembershipApplicationRead,
)
async def submit_my_application(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    application = await _application_for_user(
        session,
        user.id,
        with_documents=True,
        for_update=True,
    )
    if application is None:
        raise HTTPException(status_code=409, detail="請先填寫入社資料")
    if application.status != MembershipApplicationStatus.DRAFT:
        raise HTTPException(status_code=409, detail="此申請目前不可送件")
    profile = await session.scalar(
        select(MemberProfile.id).where(MemberProfile.user_id == user.id)
    )
    if profile is None:
        raise HTTPException(status_code=409, detail="請先填寫入社資料")
    confirmed_types = {
        document.document_type
        for document in application.documents
        if document.status == MembershipDocumentStatus.CONFIRMED
    }
    if len(confirmed_types) < 3:
        raise HTTPException(
            status_code=409,
            detail="請先上傳並確認身分證正反面及第二證件",
        )
    await _ensure_membership_charges(session, application)
    application.status = MembershipApplicationStatus.SUBMITTED
    application.submitted_at = datetime.now(timezone.utc)
    application.review_reason = None
    session.add(
        OutboxEvent(
            event_type="membership.application_submitted",
            aggregate_type="membership_application",
            aggregate_id=application.id,
            payload={"user_id": user.id},
        )
    )
    await session.commit()
    return await _application_response(application, session, settings)


@membership_router.post(
    "/v1/membership/application/supplement",
    response_model=MembershipApplicationRead,
)
async def resubmit_supplement(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    application = await _application_for_user(
        session,
        user.id,
        with_documents=True,
        for_update=True,
    )
    if (
        application is None
        or application.status
        != MembershipApplicationStatus.NEEDS_SUPPLEMENT
    ):
        raise HTTPException(status_code=409, detail="此申請目前不需補件")
    profile = await session.scalar(
        select(MemberProfile.id).where(MemberProfile.user_id == user.id)
    )
    if profile is None:
        raise HTTPException(status_code=409, detail="請先填寫入社資料")
    confirmed_types = {
        document.document_type
        for document in application.documents
        if document.status == MembershipDocumentStatus.CONFIRMED
    }
    if len(confirmed_types) < 3:
        raise HTTPException(
            status_code=409,
            detail="請先上傳並確認身分證正反面及第二證件",
        )
    await _ensure_membership_charges(session, application)
    application.status = MembershipApplicationStatus.SUBMITTED
    application.submitted_at = datetime.now(timezone.utc)
    application.review_reason = None
    session.add(
        OutboxEvent(
            event_type="membership.application_submitted",
            aggregate_type="membership_application",
            aggregate_id=application.id,
            payload={"user_id": user.id},
        )
    )
    await session.commit()
    return await _application_response(application, session, settings)


@membership_router.post(
    "/v1/membership/application/withdraw",
    response_model=MembershipApplicationRead,
)
async def withdraw_application(
    body: MembershipApplicationReview,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    application = await _application_for_user(
        session,
        user.id,
        for_update=True,
    )
    if application is None or application.status not in {
        MembershipApplicationStatus.DRAFT,
        MembershipApplicationStatus.SUBMITTED,
        MembershipApplicationStatus.NEEDS_SUPPLEMENT,
        MembershipApplicationStatus.APPROVED,
    }:
        raise HTTPException(status_code=409, detail="此申請目前不可撤回")
    membership = await session.scalar(
        select(Membership)
        .where(Membership.user_id == user.id)
        .options(selectinload(Membership.charges))
        .with_for_update()
    )
    if membership is not None and (
        membership.activated_at is not None
        or membership.member_number is not None
        or membership.trainee_number is not None
        or membership.status
        in {
            MembershipStatus.TRAINEE,
            MembershipStatus.ACTIVE,
            MembershipStatus.SUSPENDED,
            MembershipStatus.RESIGNED,
        }
    ):
        raise HTTPException(status_code=409, detail="會籍啟用後不可撤回申請")
    now = datetime.now(timezone.utc)
    refunded_amount = 0
    pending_refund_amount = 0
    if membership is not None:
        for charge in membership.charges:
            if charge.status == MembershipChargeStatus.PAID:
                refunded_amount += charge.amount
                try:
                    _, refund_queued = await create_provider_aware_refund(
                        session,
                        membership_charge=charge,
                        amount=charge.amount,
                        reason=body.reason or "入社申請啟用前撤回",
                        requested_by_id=user.id,
                        allow_unbound_local_completion=(
                            allow_local_refund_without_payment_attempt(settings)
                        ),
                        now=now,
                    )
                except PaymentApplicationError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                if refund_queued:
                    pending_refund_amount += charge.amount
                    charge.status = MembershipChargeStatus.REFUND_PENDING
                    charge.refunded_at = None
                else:
                    charge.status = MembershipChargeStatus.REFUNDED
                    charge.refunded_at = now
            elif charge.status == MembershipChargeStatus.PENDING:
                charge.status = MembershipChargeStatus.WAIVED
        if membership.status == MembershipStatus.PENDING_PAYMENT:
            membership.status = MembershipStatus.TERMINATED
            membership.ended_at = now
            membership.status_reason = body.reason or "入社申請已撤回"
    application.status = MembershipApplicationStatus.WITHDRAWN
    application.review_reason = body.reason
    if refunded_amount:
        refund_pending = pending_refund_amount > 0
        service = NotificationService(
            SQLAlchemyNotificationRepository(session)
        )
        await service.publish(
            NotificationCommand(
                user_id=user.id,
                event_type=(
                    "membership_refund_requested"
                    if refund_pending
                    else "membership_refund_completed"
                ),
                title=(
                    "入社申請退款已送出"
                    if refund_pending
                    else "入社申請退款紀錄已建立"
                ),
                body=(
                    f"已送出 NT${pending_refund_amount} 的退款申請，"
                    "待金流確認完成後會再通知。"
                    if refund_pending
                    else f"已建立 NT${refunded_amount} 的 Sandbox 退款紀錄。"
                ),
                data={"membership_application_id": application.id},
                email=user.email,
                dedupe_key=f"membership-withdraw:{application.id}",
            )
        )
    await session.commit()
    return await _application_response(application, session, settings)


@membership_router.get(
    "/v1/membership/documents",
    response_model=list[MembershipDocumentRead],
)
async def list_my_documents(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MembershipDocumentRead]:
    application = await _application_for_user(
        session,
        user.id,
        with_documents=True,
    )
    if application is None:
        return []
    return [
        _document_response(document)
        for document in application.documents
        if document.status != MembershipDocumentStatus.DELETED
    ]


@membership_router.post(
    "/v1/membership/documents/upload-url",
    response_model=MembershipDocumentUploadRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_upload_url(
    body: MembershipDocumentUploadRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    storage=Depends(get_document_storage),
) -> MembershipDocumentUploadRead:
    _ensure_user_can_apply(user)
    enforce(
        client_key(request, "membership-document-upload", user.id),
        DOCUMENT_UPLOAD_RULE,
    )
    application = await _application_for_user(
        session,
        user.id,
        for_update=True,
    )
    if application is None:
        application = MembershipApplication(user_id=user.id)
        session.add(application)
        await session.flush()
    if application.status not in {
        MembershipApplicationStatus.DRAFT,
        MembershipApplicationStatus.NEEDS_SUPPLEMENT,
    }:
        raise HTTPException(status_code=409, detail="此申請目前不可上傳證件")
    expected_checksum = body.checksum_sha256.lower()
    try:
        ticket = storage.create_upload_ticket(
            content_type=body.content_type,
            content_length=body.size_bytes,
            sha256=expected_checksum,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    document = await session.scalar(
        select(MembershipDocument).where(
            MembershipDocument.application_id == application.id,
            MembershipDocument.document_type == body.document_type,
        )
    )
    if document is None:
        document = MembershipDocument(
            application_id=application.id,
            document_type=body.document_type,
            object_key=ticket.object_key,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            checksum_sha256=expected_checksum,
        )
        session.add(document)
    else:
        old_object_key = document.object_key
        previous_status = document.status
        if old_object_key and old_object_key != ticket.object_key:
            try:
                await storage.delete_document(old_object_key)
            except IntegrationError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="舊證件刪除失敗，請稍後再試",
                ) from exc
        document.object_key = ticket.object_key
        document.content_type = body.content_type
        document.size_bytes = body.size_bytes
        document.status = MembershipDocumentStatus.PENDING_UPLOAD
        document.checksum_sha256 = expected_checksum
        document.confirmed_at = None
        document.deleted_at = None
        session.add(
            AdminAudit(
                actor_id=user.id,
                action="membership.document_replaced",
                aggregate_type="membership_document",
                aggregate_id=document.id,
                data={
                    "actor_role": "applicant",
                    "application_id": application.id,
                    "document_type": body.document_type.value,
                    "previous_status": previous_status.value,
                },
            )
        )
    await session.flush()
    await session.commit()
    return MembershipDocumentUploadRead(
        document_id=document.id,
        object_key=ticket.object_key,
        upload_url=ticket.upload_url,
        expires_in_seconds=ticket.expires_in_seconds,
        required_headers=dict(ticket.required_headers),
    )


@membership_router.delete(
    "/v1/membership/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_my_document(
    document_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    storage=Depends(get_document_storage),
) -> None:
    document = await session.scalar(
        select(MembershipDocument)
        .join(MembershipApplication)
        .options(selectinload(MembershipDocument.application))
        .where(
            MembershipDocument.id == document_id,
            MembershipApplication.user_id == user.id,
            MembershipDocument.status != MembershipDocumentStatus.DELETED,
        )
        .with_for_update()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="找不到證件")
    application = document.application
    if application.status not in {
        MembershipApplicationStatus.DRAFT,
        MembershipApplicationStatus.NEEDS_SUPPLEMENT,
        MembershipApplicationStatus.REJECTED,
        MembershipApplicationStatus.WITHDRAWN,
    }:
        raise HTTPException(status_code=409, detail="此申請目前不可刪除證件")
    try:
        await storage.delete_document(document.object_key)
    except IntegrationError as exc:
        raise HTTPException(
            status_code=503,
            detail="證件刪除失敗，請稍後再試",
        ) from exc
    now = datetime.now(timezone.utc)
    document.status = MembershipDocumentStatus.DELETED
    document.deleted_at = now
    document.confirmed_at = None
    session.add(
        AdminAudit(
            actor_id=user.id,
            action="membership.document_deleted",
            aggregate_type="membership_document",
            aggregate_id=document.id,
            data={
                "actor_role": "applicant",
                "application_id": application.id,
                "document_type": document.document_type.value,
            },
        )
    )
    await session.commit()


@membership_router.post(
    "/v1/membership/documents/{document_id}/confirm",
    response_model=MembershipDocumentRead,
)
async def confirm_document_upload(
    document_id: str,
    body: MembershipDocumentConfirm,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    storage=Depends(get_document_storage),
) -> MembershipDocumentRead:
    document = await session.scalar(
        select(MembershipDocument)
        .join(MembershipApplication)
        .options(selectinload(MembershipDocument.application))
        .where(
            MembershipDocument.id == document_id,
            MembershipApplication.user_id == user.id,
        )
        .with_for_update()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="找不到證件")
    if document.application.status not in {
        MembershipApplicationStatus.DRAFT,
        MembershipApplicationStatus.NEEDS_SUPPLEMENT,
    }:
        raise HTTPException(status_code=409, detail="此申請目前不可確認證件")
    if document.status != MembershipDocumentStatus.PENDING_UPLOAD:
        raise HTTPException(status_code=409, detail="此證件不在待確認狀態")
    if document.checksum_sha256 and not hmac.compare_digest(
        document.checksum_sha256.lower(),
        body.checksum_sha256.lower(),
    ):
        raise HTTPException(
            status_code=409,
            detail="證件雜湊值與取得上傳網址時不符",
        )
    try:
        head = await storage.confirm_upload(
            object_key=document.object_key,
            expected_content_type=document.content_type,
            expected_content_length=document.size_bytes,
            expected_sha256=document.checksum_sha256 or body.checksum_sha256,
        )
    except IntegrationError as exc:
        raise HTTPException(status_code=409, detail="證件上傳驗證失敗") from exc
    document.status = MembershipDocumentStatus.CONFIRMED
    document.object_key = head.object_key
    document.checksum_sha256 = head.sha256
    document.confirmed_at = datetime.now(timezone.utc)
    await session.commit()
    return _document_response(document)


@membership_router.get(
    "/v1/membership/charges",
    response_model=list[MembershipChargeRead],
)
async def list_my_membership_charges(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[MembershipChargeRead]:
    charges = (
        await session.scalars(
            select(MembershipCharge)
            .where(MembershipCharge.user_id == user.id)
            .order_by(MembershipCharge.created_at)
        )
    ).all()
    return [MembershipChargeRead.model_validate(charge) for charge in charges]


@membership_router.get("/v1/membership/charges/{charge_id}/receipt")
async def get_membership_receipt(
    charge_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    charge = await session.scalar(
        select(MembershipCharge).where(
            MembershipCharge.id == charge_id,
            MembershipCharge.user_id == user.id,
        )
    )
    if charge is None:
        raise HTTPException(status_code=404, detail="找不到應繳款")
    if charge.status != MembershipChargeStatus.PAID:
        raise HTTPException(status_code=409, detail="付款完成後才會產生收據")
    return {
        "receipt_number": charge.receipt_number,
        "charge_kind": charge.charge_kind.value,
        "amount": charge.amount,
        "paid_at": charge.paid_at,
        "invoice": None,
    }


@membership_router.get("/v1/members/me", response_model=MembershipMeRead)
async def get_my_membership(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MembershipMeRead:
    membership = user.__dict__.get("membership")
    directory = await session.scalar(
        select(MemberDirectoryEntry).where(
            MemberDirectoryEntry.user_id == user.id
        )
    )
    return MembershipMeRead(
        membership_type=membership_type_for_user(user),
        membership=(
            MembershipRead.model_validate(membership).model_dump(mode="json")
            if membership is not None
            else None
        ),
        directory=(
            MemberDirectoryRead.model_validate(directory)
            if directory is not None
            else None
        ),
    )


@membership_router.post(
    "/v1/membership/claim-existing",
    response_model=MembershipRead,
)
async def claim_existing_membership(
    body: ExistingMemberClaimRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipRead:
    try:
        entry = await verified_roster_entry(
            session,
            _cipher(settings),
            member_number=body.member_number,
            legal_name=body.legal_name,
            email=user.email,
            phone=body.phone,
        )
        membership = await attach_roster_membership(session, entry, user)
    except MemberClaimError as exc:
        if exc.code == "claimed":
            raise HTTPException(
                status_code=409,
                detail="此社員名冊紀錄已由其他帳號認領",
            ) from exc
        if exc.code == "pending":
            raise HTTPException(
                status_code=409,
                detail="此社員名冊紀錄正等待另一個帳號完成 Email 驗證",
            ) from exc
        if exc.code == "user_has_membership":
            raise HTTPException(
                status_code=409,
                detail="此帳號已有會籍，不可重複認領",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail="社員資料無法核對，請確認名冊登記內容",
        ) from exc
    await session.commit()
    return MembershipRead.model_validate(membership)


@membership_router.get(
    "/v1/members/directory",
    response_model=list[MemberDirectoryRead],
)
async def list_member_directory(
    _user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> list[MemberDirectoryRead]:
    entries = (
        await session.scalars(
            select(MemberDirectoryEntry)
            .where(MemberDirectoryEntry.is_public.is_(True))
            .order_by(MemberDirectoryEntry.nickname)
        )
    ).all()
    return [MemberDirectoryRead.model_validate(entry) for entry in entries]


@membership_router.put(
    "/v1/members/me/directory",
    response_model=MemberDirectoryRead,
)
async def update_my_directory_entry(
    body: MemberDirectoryUpdate,
    user: User = Depends(require_active_member),
    session: AsyncSession = Depends(get_session),
) -> MemberDirectoryRead:
    entry = await session.scalar(
        select(MemberDirectoryEntry).where(
            MemberDirectoryEntry.user_id == user.id
        )
    )
    if entry is None:
        entry = MemberDirectoryEntry(user_id=user.id, nickname=body.nickname)
        session.add(entry)
    for field, value in body.model_dump().items():
        setattr(entry, field, value)
    await session.commit()
    return MemberDirectoryRead.model_validate(entry)


@membership_router.get(
    "/v1/admin/membership-applications",
    response_model=list[MembershipApplicationRead],
)
async def list_membership_applications(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[MembershipApplicationRead]:
    applications = (
        await session.scalars(
            select(MembershipApplication).order_by(
                MembershipApplication.created_at.desc()
            )
        )
    ).all()
    return [
        await _application_response(application, session, settings)
        for application in applications
    ]


@membership_router.post(
    "/v1/admin/membership-applications/{application_id}/request-supplement",
    response_model=MembershipApplicationRead,
)
async def request_application_supplement(
    application_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫補件原因")
    application = await session.scalar(
        select(MembershipApplication)
        .where(MembershipApplication.id == application_id)
        .with_for_update()
    )
    if (
        application is None
        or application.status != MembershipApplicationStatus.SUBMITTED
    ):
        raise HTTPException(status_code=409, detail="此申請目前不可要求補件")
    application.status = MembershipApplicationStatus.NEEDS_SUPPLEMENT
    application.review_reason = body.reason
    application.reviewed_by_id = admin.id
    application.reviewed_at = datetime.now(timezone.utc)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="membership.request_supplement",
            aggregate_type="membership_application",
            aggregate_id=application.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return await _application_response(application, session, settings)


async def _active_fee_schedule(
    session: AsyncSession,
    kind: MembershipChargeKind,
    today: date,
) -> MembershipFeeSchedule:
    schedule = await session.scalar(
        select(MembershipFeeSchedule)
        .where(
            MembershipFeeSchedule.charge_kind == kind,
            MembershipFeeSchedule.is_active.is_(True),
            MembershipFeeSchedule.effective_from <= today,
            (
                MembershipFeeSchedule.effective_to.is_(None)
                | (MembershipFeeSchedule.effective_to >= today)
            ),
        )
        .order_by(MembershipFeeSchedule.effective_from.desc())
    )
    if schedule is None:
        raise HTTPException(status_code=409, detail="尚未設定有效入社費率")
    return schedule


@membership_router.post(
    "/v1/admin/membership-applications/{application_id}/approve",
    response_model=MembershipRead,
)
async def approve_membership_application(
    application_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MembershipRead:
    application = await session.scalar(
        select(MembershipApplication)
        .where(MembershipApplication.id == application_id)
        .options(selectinload(MembershipApplication.documents))
        .with_for_update()
    )
    if (
        application is None
        or application.status != MembershipApplicationStatus.SUBMITTED
    ):
        raise HTTPException(status_code=409, detail="此申請目前不可核准")
    if (
        len(
            {
                document.document_type
                for document in application.documents
                if document.status == MembershipDocumentStatus.CONFIRMED
            }
        )
        < 3
    ):
        raise HTTPException(status_code=409, detail="申請人證件尚未齊全")
    membership = await _ensure_membership_charges(session, application)
    application.status = MembershipApplicationStatus.APPROVED
    application.reviewed_by_id = admin.id
    application.reviewed_at = datetime.now(timezone.utc)
    application.review_reason = body.reason
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="membership.approve",
            aggregate_type="membership_application",
            aggregate_id=application.id,
            reason=body.reason,
        )
    )
    session.add(
        OutboxEvent(
            event_type="membership.application_approved",
            aggregate_type="membership_application",
            aggregate_id=application.id,
            payload={"user_id": application.user_id},
        )
    )
    await session.commit()
    return MembershipRead.model_validate(membership)


@membership_router.post(
    "/v1/admin/membership-applications/{application_id}/reject",
    response_model=MembershipApplicationRead,
)
async def reject_membership_application(
    application_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫駁回原因")
    application = await session.scalar(
        select(MembershipApplication)
        .where(MembershipApplication.id == application_id)
        .with_for_update()
    )
    if (
        application is None
        or application.status
        not in {
            MembershipApplicationStatus.SUBMITTED,
            MembershipApplicationStatus.NEEDS_SUPPLEMENT,
        }
    ):
        raise HTTPException(status_code=409, detail="此申請目前不可駁回")
    membership = await session.scalar(
        select(Membership)
        .where(Membership.user_id == application.user_id)
        .options(selectinload(Membership.charges))
        .with_for_update()
    )
    if membership is not None and (
        membership.status
        in {MembershipStatus.TRAINEE, MembershipStatus.ACTIVE}
        or any(
            charge.status == MembershipChargeStatus.PAID
            for charge in membership.charges
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="此申請已有付款紀錄，請改走明確的終止與退款流程",
        )
    reviewed_at = datetime.now(timezone.utc)
    if (
        membership is not None
        and membership.status == MembershipStatus.PENDING_PAYMENT
    ):
        membership.status = MembershipStatus.TERMINATED
        membership.ended_at = reviewed_at
        membership.status_reason = body.reason or "入社申請未通過"
        for charge in membership.charges:
            if charge.status == MembershipChargeStatus.PENDING:
                charge.status = MembershipChargeStatus.WAIVED
    application.status = MembershipApplicationStatus.REJECTED
    application.reviewed_by_id = admin.id
    application.reviewed_at = reviewed_at
    application.review_reason = body.reason
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="membership.reject",
            aggregate_type="membership_application",
            aggregate_id=application.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return await _application_response(application, session, settings)


@membership_router.get(
    "/v1/admin/membership-applications/{application_id}",
    response_model=MembershipApplicationRead,
)
async def get_membership_application_private(
    application_id: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MembershipApplicationRead:
    application = await session.scalar(
        select(MembershipApplication)
        .where(MembershipApplication.id == application_id)
    )
    if application is None:
        raise HTTPException(status_code=404, detail="找不到入社申請")
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="membership.view_private_profile",
            aggregate_type="membership_application",
            aggregate_id=application.id,
        )
    )
    await session.commit()
    return await _application_response(application, session, settings)


@membership_router.get(
    "/v1/admin/membership-applications/{application_id}/documents/"
    "{document_id}/download-url"
)
async def create_document_download_url(
    application_id: str,
    document_id: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    storage=Depends(get_document_storage),
) -> dict[str, Any]:
    document = await session.scalar(
        select(MembershipDocument).where(
            MembershipDocument.id == document_id,
            MembershipDocument.application_id == application_id,
            MembershipDocument.status
            == MembershipDocumentStatus.CONFIRMED,
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="找不到證件")
    url = storage.create_download_url(document.object_key)
    expires_in_seconds = getattr(
        getattr(storage, "settings", None),
        "get_expiry_seconds",
        120,
    )
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="membership.view_document",
            aggregate_type="membership_document",
            aggregate_id=document.id,
            data={
                "actor_role": "admin",
                "application_id": application_id,
                "document_type": document.document_type.value,
                "expires_in_seconds": expires_in_seconds,
            },
        )
    )
    await session.commit()
    return {
        "download_url": url,
        "expires_in_seconds": expires_in_seconds,
    }


@membership_router.get(
    "/v1/admin/member-roster",
    response_model=list[MemberRosterEntryRead],
)
async def list_member_roster(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[MemberRosterEntryRead]:
    entries = (
        await session.scalars(
            select(MemberRosterEntry).order_by(
                MemberRosterEntry.member_number
            )
        )
    ).all()
    cipher = _cipher(settings)
    return [_roster_response(entry, cipher) for entry in entries]


@membership_router.post(
    "/v1/admin/member-roster",
    response_model=MemberRosterEntryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_member_roster_entry(
    body: MemberRosterEntryCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MemberRosterEntryRead:
    member_number = normalize_member_number(body.member_number)
    if not member_number:
        raise HTTPException(status_code=422, detail="請填寫社員編號")
    existing_roster = await session.scalar(
        select(MemberRosterEntry.id).where(
            MemberRosterEntry.member_number == member_number
        )
    )
    existing_membership = await session.scalar(
        select(Membership.id).where(
            Membership.member_number == member_number
        )
    )
    if existing_roster is not None or existing_membership is not None:
        raise HTTPException(status_code=409, detail="社員編號已存在")
    phone = normalize_phone(body.phone)
    if len(phone) < 8:
        raise HTTPException(status_code=422, detail="手機格式不正確")

    entry_id = new_id()
    cipher = _cipher(settings)
    aad = roster_aad(entry_id)
    entry = MemberRosterEntry(
        id=entry_id,
        member_number=member_number,
        legal_name_encrypted=cipher.encrypt_text(
            body.legal_name.strip(),
            associated_data=aad,
        ),
        email_encrypted=cipher.encrypt_text(
            normalize_email(str(body.email)),
            associated_data=aad,
        ),
        phone_encrypted=cipher.encrypt_text(
            phone,
            associated_data=aad,
        ),
        encryption_key_version=cipher.current_version,
        share_certificate_number=(
            body.share_certificate_number.strip()
            if body.share_certificate_number
            else None
        ),
        share_capital_amount=body.share_capital_amount,
        share_count=body.share_count,
        share_subscribed_on=body.share_subscribed_on,
        share_paid_on=body.share_paid_on,
    )
    session.add(entry)
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="member_roster.created",
            aggregate_type="member_roster_entry",
            aggregate_id=entry.id,
            data={"member_number": entry.member_number},
        )
    )
    await session.commit()
    return _roster_response(entry, cipher)


@membership_router.get(
    "/v1/admin/members",
    response_model=list[MembershipRead],
)
async def list_memberships(
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[MembershipRead]:
    memberships = (
        await session.scalars(
            select(Membership).order_by(Membership.created_at.desc())
        )
    ).all()
    return [MembershipRead.model_validate(item) for item in memberships]


async def _transition_membership(
    membership_id: str,
    next_status: MembershipStatus,
    body: MembershipApplicationReview,
    admin: User,
    session: AsyncSession,
) -> MembershipRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫處理原因")
    membership = await session.scalar(
        select(Membership)
        .where(Membership.id == membership_id)
        .with_for_update()
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="找不到會籍")
    allowed_statuses = {
        MembershipStatus.SUSPENDED: {MembershipStatus.ACTIVE},
        MembershipStatus.RESIGNED: {
            MembershipStatus.ACTIVE,
            MembershipStatus.SUSPENDED,
        },
        MembershipStatus.TERMINATED: {
            MembershipStatus.PENDING_PAYMENT,
            MembershipStatus.TRAINEE,
            MembershipStatus.ACTIVE,
            MembershipStatus.SUSPENDED,
        },
    }
    if membership.status not in allowed_statuses[next_status]:
        raise HTTPException(status_code=409, detail="此會籍目前不可執行該操作")
    now = datetime.now(timezone.utc)
    membership.status = next_status
    membership.status_reason = body.reason
    if next_status == MembershipStatus.SUSPENDED:
        membership.suspended_at = now
    if next_status in {
        MembershipStatus.RESIGNED,
        MembershipStatus.TERMINATED,
    }:
        membership.ended_at = now
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action=f"membership.{next_status.value}",
            aggregate_type="membership",
            aggregate_id=membership.id,
            reason=body.reason,
        )
    )
    await session.commit()
    return MembershipRead.model_validate(membership)


@membership_router.post(
    "/v1/admin/members/{membership_id}/activate",
    response_model=MembershipRead,
)
async def activate_trainee_membership(
    membership_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MembershipRead:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫轉正原因")
    membership = await session.scalar(
        select(Membership)
        .where(Membership.id == membership_id)
        .options(selectinload(Membership.application))
        .with_for_update()
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="找不到會籍")
    if membership.status != MembershipStatus.TRAINEE:
        raise HTTPException(status_code=409, detail="只有實習社員可以轉為正式社員")
    if membership.trainee_number is None:
        raise HTTPException(status_code=409, detail="實習社員編號資料不完整")
    if (
        membership.application is not None
        and membership.application.status
        in {
            MembershipApplicationStatus.REJECTED,
            MembershipApplicationStatus.WITHDRAWN,
        }
    ):
        raise HTTPException(status_code=409, detail="此入社申請已結案，不可轉正")
    current = datetime.now(timezone.utc)
    membership.member_number = await next_identity_number(
        session,
        Membership.member_number,
        "SLF",
        current,
    )
    membership.status = MembershipStatus.ACTIVE
    membership.activated_at = current
    membership.status_reason = body.reason
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="membership.activated",
            aggregate_type="membership",
            aggregate_id=membership.id,
            reason=body.reason,
            data={
                "trainee_number": membership.trainee_number,
                "member_number": membership.member_number,
            },
        )
    )
    member_user = await session.get(User, membership.user_id)
    await NotificationService(
        SQLAlchemyNotificationRepository(session)
    ).publish(
        NotificationCommand(
            user_id=membership.user_id,
            event_type="membership.activated",
            title="已成為十里方圓正式社員",
            body=f"社員編號 {membership.member_number} 已啟用。",
            data={
                "membership_id": membership.id,
                "trainee_number": membership.trainee_number,
                "member_number": membership.member_number,
            },
            email=member_user.email if member_user else None,
            dedupe_key=f"membership-activated:{membership.id}",
        )
    )
    await session.commit()
    return MembershipRead.model_validate(membership)


@membership_router.post(
    "/v1/admin/members/{membership_id}/suspend",
    response_model=MembershipRead,
)
async def suspend_membership(
    membership_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MembershipRead:
    return await _transition_membership(
        membership_id,
        MembershipStatus.SUSPENDED,
        body,
        admin,
        session,
    )


@membership_router.post(
    "/v1/admin/members/{membership_id}/resign",
    response_model=MembershipRead,
)
async def resign_membership(
    membership_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MembershipRead:
    return await _transition_membership(
        membership_id,
        MembershipStatus.RESIGNED,
        body,
        admin,
        session,
    )


@membership_router.post(
    "/v1/admin/members/{membership_id}/terminate",
    response_model=MembershipRead,
)
async def terminate_membership(
    membership_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> MembershipRead:
    return await _transition_membership(
        membership_id,
        MembershipStatus.TERMINATED,
        body,
        admin,
        session,
    )


@membership_router.post(
    "/v1/admin/members/{membership_id}/share-capital-return"
)
async def return_share_capital(
    membership_id: str,
    body: MembershipApplicationReview,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if not body.reason:
        raise HTTPException(status_code=422, detail="請填寫返還原因")
    membership = await session.get(Membership, membership_id)
    if (
        membership is None
        or membership.status != MembershipStatus.RESIGNED
    ):
        raise HTTPException(status_code=409, detail="只有已退社會籍可返還股金")
    charge = await session.scalar(
        select(MembershipCharge)
        .where(
            MembershipCharge.membership_id == membership.id,
            MembershipCharge.charge_kind
            == MembershipChargeKind.SHARE_CAPITAL,
            MembershipCharge.status == MembershipChargeStatus.PAID,
        )
        .with_for_update()
    )
    if charge is None:
        raise HTTPException(status_code=409, detail="沒有可返還的已繳股金")
    now = datetime.now(timezone.utc)
    try:
        refund, refund_queued = await create_provider_aware_refund(
            session,
            membership_charge=charge,
            amount=charge.amount,
            reason=body.reason,
            requested_by_id=admin.id,
            allow_unbound_local_completion=(
                allow_local_refund_without_payment_attempt(settings)
            ),
            now=now,
        )
    except PaymentApplicationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    charge.status = (
        MembershipChargeStatus.REFUND_PENDING
        if refund_queued
        else MembershipChargeStatus.REFUNDED
    )
    charge.refunded_at = None if refund_queued else now
    session.add_all(
        [
            AdminAudit(
                actor_id=admin.id,
                action="membership.share_capital_return",
                aggregate_type="membership",
                aggregate_id=membership.id,
                reason=body.reason,
                data={
                    "amount": charge.amount,
                    "provider": refund.provider,
                    "refund_status": refund.status.value,
                },
            ),
        ]
    )
    await session.commit()
    return {
        "refund_id": refund.id,
        "amount": charge.amount,
        "status": refund.status.value,
    }


@membership_router.post(
    "/v1/admin/membership-fee-schedules",
    status_code=status.HTTP_201_CREATED,
)
async def create_membership_fee_schedule(
    body: MembershipFeeScheduleInput,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    schedule = MembershipFeeSchedule(**body.model_dump())
    session.add(schedule)
    await session.flush()
    session.add(
        AdminAudit(
            actor_id=admin.id,
            action="membership.fee_schedule_create",
            aggregate_type="membership_fee_schedule",
            aggregate_id=schedule.id,
            data={"charge_kind": body.charge_kind.value, "amount": body.amount},
        )
    )
    await session.commit()
    return {
        "id": schedule.id,
        **body.model_dump(mode="json"),
        "is_active": schedule.is_active,
    }


async def start_traineeship_if_fully_paid(
    session: AsyncSession,
    membership_id: str,
    now: Optional[datetime] = None,
) -> Optional[Membership]:
    membership = await session.scalar(
        select(Membership)
        .where(Membership.id == membership_id)
        .options(
            selectinload(Membership.charges),
            selectinload(Membership.application),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        membership is None
        or membership.status != MembershipStatus.PENDING_PAYMENT
        or membership.application is None
        or membership.application.status
        not in {
            MembershipApplicationStatus.SUBMITTED,
            MembershipApplicationStatus.NEEDS_SUPPLEMENT,
            MembershipApplicationStatus.APPROVED,
        }
    ):
        return membership
    paid_kinds = {
        charge.charge_kind
        for charge in membership.charges
        if charge.status == MembershipChargeStatus.PAID
    }
    if paid_kinds != {
        MembershipChargeKind.ADMISSION_FEE,
        MembershipChargeKind.SHARE_CAPITAL,
    }:
        return membership
    current = now or datetime.now(timezone.utc)
    membership.trainee_number = await next_identity_number(
        session,
        Membership.trainee_number,
        "SLF-T",
        current,
    )
    membership.status = MembershipStatus.TRAINEE
    member_user = await session.get(User, membership.user_id)
    service = NotificationService(SQLAlchemyNotificationRepository(session))
    await service.publish(
        NotificationCommand(
            user_id=membership.user_id,
            event_type="membership.trainee_started",
            title="已成為十里方圓實習社員",
            body=f"實習社員編號 {membership.trainee_number} 已啟用。",
            data={
                "membership_id": membership.id,
                "trainee_number": membership.trainee_number,
            },
            email=member_user.email if member_user else None,
            dedupe_key=f"membership-trainee:{membership.id}",
        )
    )
    return membership


router = membership_router
