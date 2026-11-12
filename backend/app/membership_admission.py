from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .integrations.common import IntegrationError
from .integrations.pii_crypto import pii_cipher_from_settings
from .membership_admission_content import get_admission_config
from .membership_admission_fee import read_admission_fee_settings
from .membership_admission_schemas import (
    AdmissionEducationConfirm, AdmissionEligibilityRead, AdmissionListRead,
    AdmissionMeRead, AdmissionProfile, AdmissionQuizSubmit, AdmissionReviewInput,
    AdmissionRoundRead, AdmissionRoundStart, AdmissionRoundUpdate,
    AdmissionVersionInput, AdmissionWithdraw,
)
from .membership_education_settings import MembershipEducationSettingsRead, read_membership_education_settings
from .membership_policy import (
    MembershipPolicyError, get_membership_quiz_feedback, grade_membership_quiz,
    membership_quiz_eligibility, quote_membership_shares,
)
from .models import (
    AdminAudit, MemberProfile, Membership, MembershipApplication, MembershipCharge, MembershipStatus,
    MembershipAdmissionQuizAttempt, MembershipAdmissionReview,
    MembershipAdmissionRound, MembershipAdmissionSubmission, User, UserRole, new_id,
)


EDITABLE = {"draft", "needs_supplement"}
WITHDRAWABLE = EDITABLE | {"submitted"}


def _fail(message: str, status: int = 409):
    raise HTTPException(status_code=status, detail=message, headers={"Cache-Control": "no-store"})


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _cipher(settings: Settings):
    try:
        return pii_cipher_from_settings(settings)
    except IntegrationError as exc:
        raise HTTPException(status_code=503, detail="私密資料加密設定尚未完成") from exc


def round_aad(row: MembershipAdmissionRound) -> str:
    return f"membership-admission:{row.user_id}:{row.id}:profile"


def submission_aad(row: MembershipAdmissionSubmission) -> str:
    return f"membership-admission-submission:{row.round_id}:{row.id}:profile"


async def _lock_user(session: AsyncSession, user_id: str) -> User:
    user = await session.scalar(select(User).where(User.id == user_id)
        .with_for_update().execution_options(populate_existing=True))
    if user is None or not user.is_active:
        _fail("找不到此使用者", 404)
    return user


async def _round(session: AsyncSession, round_id: str, actor: User, *, lock=False, admin=False):
    owner_id = await session.scalar(select(MembershipAdmissionRound.user_id).where(
        MembershipAdmissionRound.id == round_id))
    if owner_id is None or (owner_id != actor.id and not (admin and actor.user_role == UserRole.ADMIN)):
        _fail("找不到此申請", 404)
    if lock:
        await _lock_user(session, owner_id)
    query = select(MembershipAdmissionRound).where(MembershipAdmissionRound.id == round_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


def _version(row, expected_version):
    if row.version != expected_version:
        _fail("申請已被更新，請重新讀取後再操作")


def _editable(row):
    if row.status not in EDITABLE:
        _fail("此申請目前不可修改")


def _bump(row):
    row.version += 1
    row.updated_at = datetime.now(timezone.utc)


def _audit(session, row, actor_id, action, *, reason=None, data=None):
    session.add(AdminAudit(actor_id=actor_id, action=f"membership.admission_{action}",
        aggregate_type="membership_admission_round", aggregate_id=row.id,
        reason=reason, data={"round_number": row.round_number, "version": row.version, **(data or {})}))


def _membership_blocked_reason(membership: Membership | None) -> str | None:
    if membership is not None:
        if membership.status in {MembershipStatus.ACTIVE, MembershipStatus.TRAINEE, MembershipStatus.PENDING_PAYMENT}:
            return "此帳號已有會籍或進行中的入社款項，請先處理原案件"
        if membership.status in {MembershipStatus.RESIGNED, MembershipStatus.TERMINATED}:
            return "此帳號有退社歷史，請先由管理員核對"
        if membership.status == MembershipStatus.SUSPENDED and not membership.member_number:
            return "原會籍資格待核對，請先由管理員處理"
    return None


async def start_blocked_reason(session: AsyncSession, user_id: str) -> str | None:
    membership = await session.scalar(select(Membership).where(Membership.user_id == user_id)
        .execution_options(populate_existing=True))
    blocked = _membership_blocked_reason(membership)
    if blocked:
        return blocked
    if await session.scalar(select(MembershipCharge.id).where(MembershipCharge.user_id == user_id).limit(1)):
        return "此帳號有歷史入社款項，請先由管理員核對，避免重複申請"
    return None


async def _require_eligible(session, row):
    blocked = await start_blocked_reason(session, row.user_id)
    if blocked:
        _fail(blocked)


async def _attempts(session, row):
    return list((await session.scalars(select(MembershipAdmissionQuizAttempt)
        .where(MembershipAdmissionQuizAttempt.round_id == row.id)
        .order_by(MembershipAdmissionQuizAttempt.attempt_number))).all())


def _profile(row, settings):
    if row.profile_encrypted is None:
        return None
    return AdmissionProfile.model_validate(_cipher(settings).decrypt_json(
        row.profile_encrypted, associated_data=round_aad(row)))


def _consented(row):
    config = get_admission_config()
    declaration = config["declaration"]
    return (row.profile_encrypted is not None and row.consented_at is not None
        and row.declaration_version == declaration["version"]
        and row.privacy_version == declaration["privacy_version"])


def _educated(row):
    return row.material_completed_at is not None and row.material_version == get_admission_config()["material"]["version"]


def _passing(row, attempts):
    return next((attempt for attempt in reversed(attempts)
        if attempt.quiz_version == row.quiz_version and attempt.passed and attempt.score >= 80), None)


async def read_round(session, row, settings) -> AdmissionRoundRead:
    attempts = await _attempts(session, row)
    policy = await read_membership_education_settings(session)
    blocked = await start_blocked_reason(session, row.user_id)
    submissions = list((await session.scalars(select(MembershipAdmissionSubmission).where(
        MembershipAdmissionSubmission.round_id == row.id).order_by(MembershipAdmissionSubmission.revision_number))).all())
    reviews = list((await session.scalars(select(MembershipAdmissionReview).where(
        MembershipAdmissionReview.round_id == row.id).order_by(MembershipAdmissionReview.reviewed_at, MembershipAdmissionReview.id))).all())
    return _render_round(row, settings, _RoundReadData(attempts, policy, blocked, submissions, reviews, _profile(row, settings)))


@dataclass
class _RoundReadData:
    attempts: list[MembershipAdmissionQuizAttempt]
    policy: MembershipEducationSettingsRead
    blocked: str | None
    submissions: list[MembershipAdmissionSubmission]
    reviews: list[MembershipAdmissionReview]
    profile: AdmissionProfile | None


def _render_round(row, settings, data: _RoundReadData) -> AdmissionRoundRead:
    attempts, policy, blocked = data.attempts, data.policy, data.blocked
    eligibility = membership_quiz_eligibility(policy=policy.retry_policy(), attempt_count=len(attempts),
        last_attempt_at=_aware(attempts[-1].submitted_at) if attempts else None,
        passed=_passing(row, attempts) is not None, now=datetime.now(timezone.utc))
    editable = row.status in EDITABLE and blocked is None
    reason = blocked
    if not editable:
        reason = blocked or "此申請目前不可作答"
    elif not _consented(row):
        reason = "請先儲存基本資料與志願書"
    elif not _educated(row):
        reason = "請先完成教材閱讀確認"
    elif _passing(row, attempts):
        reason = "本輪已合格，可送出審核"
    elif eligibility.next_available_at:
        reason = "尚未到重考時間"
    elif not eligibility.allowed:
        reason = "本輪可作答次數已用完" if policy.configured else "重考規則尚未設定，首次交卷後暫不可重考"
    return AdmissionRoundRead(
        **{name: getattr(row, name) for name in (
            "id", "user_id", "round_number", "version", "flow_version", "status", "requested_share_count",
            "admission_fee_amount", "admission_fee_version", "share_unit_amount",
            "student_claimed", "discovery_channel", "declaration_version", "privacy_version", "material_version",
            "quiz_version", "review_reason", "student_verified", "decision_reference")},
        **{name: _aware(getattr(row, name)) for name in (
            "consented_at", "material_completed_at", "submitted_at", "reviewed_at", "created_at", "updated_at")},
        profile=data.profile,
        discovery_other=_cipher(settings).decrypt_text(row.discovery_other_encrypted,
            associated_data=f"{round_aad(row)}:discovery") if row.discovery_other_encrypted else None,
        quiz_eligibility=AdmissionEligibilityRead(allowed=reason is None and eligibility.allowed,
            remaining_attempts=eligibility.remaining_attempts, next_available_at=eligibility.next_available_at,
            reason=reason, settings_configured=policy.configured),
        attempts=[{**{name: getattr(attempt, name) for name in (
            "id", "attempt_number", "quiz_version", "score", "passed", "settings_version", "results")},
            "submitted_at": _aware(attempt.submitted_at)} for attempt in attempts],
        submissions=[{"id": item.id, "revision_number": item.revision_number,
            "submitted_at": _aware(item.submitted_at)} for item in data.submissions],
        reviews=[{**{name: getattr(item, name) for name in (
            "id", "decision", "reason", "decision_reference", "student_verified")},
            "reviewed_at": _aware(item.reviewed_at)} for item in data.reviews],
        can_edit=editable, can_submit=bool(editable and _consented(row) and _educated(row) and _passing(row, attempts)),
        can_withdraw=row.status in WITHDRAWABLE,
    )


async def read_one(session, round_id, actor, settings):
    return await read_round(session, await _round(session, round_id, actor, admin=True), settings)


async def _profile_defaults(session, user, settings):
    profile = await session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
    if profile is None or not (user.notification_email or user.email):
        return None
    cipher = _cipher(settings)
    data = {"contact_email": user.notification_email or user.email}
    for name in AdmissionProfile.model_fields:
        if name == "contact_email":
            continue
        envelope = getattr(profile, f"{name}_encrypted")
        data[name] = cipher.decrypt_text(envelope, associated_data=f"member-profile:{user.id}") if envelope else None
    try:
        return AdmissionProfile.model_validate(data)
    except ValidationError:
        return None


async def read_me(session, user, settings):
    rounds = list((await session.scalars(select(MembershipAdmissionRound).where(
        MembershipAdmissionRound.user_id == user.id).order_by(MembershipAdmissionRound.round_number.desc()))).all())
    latest = rounds[0] if rounds else None
    blocked = await start_blocked_reason(session, user.id)
    if blocked is None and latest is not None and latest.status not in {"rejected", "withdrawn"}:
        blocked = "請先完成目前申請" if latest.status != "approved" else "本輪已審核同意，後續繳費與入社階段尚未開放"
    config = get_admission_config()
    if blocked is None:
        fee = await read_admission_fee_settings(session)
        config["declaration"].update(admission_fee_amount=fee.admission_fee_amount,
            admission_fee_version=fee.version, share_unit_amount=fee.share_unit_amount)
    elif latest is not None:
        config["declaration"].update(admission_fee_amount=latest.admission_fee_amount,
            admission_fee_version=latest.admission_fee_version, share_unit_amount=latest.share_unit_amount)
    return AdmissionMeRead(config=config,
        current_round=await read_round(session, latest, settings) if latest else None,
        history=[{"id": row.id, "round_number": row.round_number, "status": row.status,
            "created_at": _aware(row.created_at), "reviewed_at": _aware(row.reviewed_at)} for row in rounds],
        profile_defaults=await _profile_defaults(session, user, settings),
        has_legacy_application=await session.scalar(select(MembershipApplication.id).where(
            MembershipApplication.user_id == user.id).limit(1)) is not None,
        can_start=blocked is None, start_blocked_reason=blocked)


async def start_round(session, user, body: AdmissionRoundStart, settings):
    await _lock_user(session, user.id)
    existing = await session.scalar(select(MembershipAdmissionRound).where(
        MembershipAdmissionRound.user_id == user.id,
        MembershipAdmissionRound.idempotency_key == str(body.idempotency_key)))
    if existing:
        return await read_round(session, existing, settings)
    blocked = await start_blocked_reason(session, user.id)
    if blocked:
        _fail(blocked)
    latest = await session.scalar(select(MembershipAdmissionRound).where(
        MembershipAdmissionRound.user_id == user.id).order_by(MembershipAdmissionRound.round_number.desc()).limit(1))
    if latest is not None and latest.status not in {"rejected", "withdrawn"}:
        return await read_round(session, latest, settings)
    fee = await read_admission_fee_settings(session)
    if body.expected_fee_version != fee.version:
        _fail("入社費已更新，請重新讀取並確認金額")
    row = MembershipAdmissionRound(user_id=user.id, round_number=latest.round_number + 1 if latest else 1,
        idempotency_key=str(body.idempotency_key), quiz_version=get_admission_config()["quiz"]["version"],
        admission_fee_amount=fee.admission_fee_amount, admission_fee_version=fee.version,
        share_unit_amount=fee.share_unit_amount)
    session.add(row)
    await session.flush()
    _audit(session, row, user.id, "started", data={"admission_fee_amount": row.admission_fee_amount,
        "admission_fee_version": row.admission_fee_version, "share_unit_amount": row.share_unit_amount})
    await session.flush()
    return await read_round(session, row, settings)


async def update_round(session, round_id, user, body: AdmissionRoundUpdate, settings):
    row = await _round(session, round_id, user, lock=True)
    _version(row, body.expected_version)
    _editable(row)
    await _require_eligible(session, row)
    config = get_admission_config()
    if body.declaration_version != config["declaration"]["version"] or body.privacy_version != config["declaration"]["privacy_version"]:
        _fail("志願書版本已變更，請重新讀取並確認")
    if body.discovery_channel not in config["discovery_channels"]:
        _fail("請選擇有效的得知管道", 422)
    if body.discovery_channel != "其他" and body.discovery_other:
        _fail("只有其他管道可填寫補充內容", 422)
    try:
        quote_membership_shares(body.requested_share_count, approved_full_time_student=body.student_claimed)
    except MembershipPolicyError as exc:
        _fail(str(exc), 422)
    cipher = _cipher(settings)
    row.profile_encrypted = cipher.encrypt_json(body.profile.model_dump(mode="json"), associated_data=round_aad(row))
    row.requested_share_count = body.requested_share_count
    row.student_claimed = body.student_claimed
    row.discovery_channel = body.discovery_channel
    row.discovery_other_encrypted = cipher.encrypt_text(body.discovery_other,
        associated_data=f"{round_aad(row)}:discovery") if body.discovery_other else None
    row.declaration_version = body.declaration_version
    row.privacy_version = body.privacy_version
    row.consented_at = datetime.now(timezone.utc)
    _bump(row)
    _audit(session, row, user.id, "profile_saved")
    await session.flush()
    return await read_round(session, row, settings)


async def confirm_education(session, round_id, user, body: AdmissionEducationConfirm, settings):
    row = await _round(session, round_id, user, lock=True)
    _version(row, body.expected_version)
    _editable(row)
    await _require_eligible(session, row)
    if not _consented(row):
        _fail("請先儲存基本資料與志願書")
    if body.material_version != get_admission_config()["material"]["version"]:
        _fail("教材版本已變更，請重新閱讀")
    row.material_version = body.material_version
    row.material_completed_at = datetime.now(timezone.utc)
    _bump(row)
    _audit(session, row, user.id, "education_confirmed")
    await session.flush()
    return await read_round(session, row, settings)


async def submit_quiz(session, round_id, user, body: AdmissionQuizSubmit, settings):
    row = await _round(session, round_id, user, lock=True)
    answers = sorted((answer.model_dump() for answer in body.answers), key=lambda answer: answer["question_id"])
    previous = await session.scalar(select(MembershipAdmissionQuizAttempt).where(
        MembershipAdmissionQuizAttempt.round_id == row.id,
        MembershipAdmissionQuizAttempt.idempotency_key == str(body.idempotency_key)))
    if previous:
        if previous.quiz_version != body.quiz_version or previous.answers != answers:
            _fail("此交卷識別碼已用於不同答案，請重新讀取紀錄")
        return await read_round(session, row, settings)
    _version(row, body.expected_version)
    _editable(row)
    await _require_eligible(session, row)
    if not _consented(row) or not _educated(row):
        _fail("請先完成資料、志願書與教材閱讀確認")
    if body.quiz_version != row.quiz_version:
        _fail("測驗版本不屬於本輪申請")
    attempts = await _attempts(session, row)
    policy = await read_membership_education_settings(session)
    now = datetime.now(timezone.utc)
    eligibility = membership_quiz_eligibility(policy=policy.retry_policy(), attempt_count=len(attempts),
        last_attempt_at=_aware(attempts[-1].submitted_at) if attempts else None,
        passed=_passing(row, attempts) is not None, now=now)
    if not eligibility.allowed:
        _fail("本輪目前不可再次交卷，請查看合格紀錄或重考規則")
    try:
        result = grade_membership_quiz(body.quiz_version, answers)
        feedback = get_membership_quiz_feedback(body.quiz_version, answers)
    except MembershipPolicyError as exc:
        _fail(str(exc), 422)
    attempt = MembershipAdmissionQuizAttempt(round_id=row.id, attempt_number=len(attempts) + 1,
        idempotency_key=str(body.idempotency_key), quiz_version=body.quiz_version,
        answers=answers, results=feedback, score=result.score, passed=result.passed,
        settings_version=policy.version, retry_limit=policy.retry_limit,
        retry_interval_minutes=policy.retry_interval_minutes, submitted_at=now)
    session.add(attempt)
    _bump(row)
    await session.flush()
    _audit(session, row, user.id, "quiz_submitted", data={"attempt_id": attempt.id,
        "score": result.score, "settings_version": policy.version})
    await session.flush()
    return await read_round(session, row, settings)


async def submit_round(session, round_id, user, body: AdmissionVersionInput, settings):
    row = await _round(session, round_id, user, lock=True)
    _version(row, body.expected_version)
    _editable(row)
    await _require_eligible(session, row)
    passing = _passing(row, await _attempts(session, row))
    if not _consented(row) or not _educated(row) or passing is None:
        _fail("請先完成資料同意、教材閱讀與本輪 80 分合格測驗")
    revision = await session.scalar(select(func.max(MembershipAdmissionSubmission.revision_number)).where(
        MembershipAdmissionSubmission.round_id == row.id))
    now = datetime.now(timezone.utc)
    snapshot = MembershipAdmissionSubmission(id=new_id(), round_id=row.id,
        revision_number=(revision or 0) + 1, quiz_attempt_id=passing.id, submitted_at=now,
        facts={"requested_share_count": row.requested_share_count, "student_claimed": row.student_claimed,
            "admission_fee_amount": row.admission_fee_amount, "admission_fee_version": row.admission_fee_version,
            "share_unit_amount": row.share_unit_amount, "share_capital_amount": row.requested_share_count * row.share_unit_amount,
            "discovery_channel": row.discovery_channel, "declaration_version": row.declaration_version,
            "privacy_version": row.privacy_version, "consented_at": _aware(row.consented_at).isoformat(),
            "material_version": row.material_version, "material_completed_at": _aware(row.material_completed_at).isoformat(),
            "quiz_version": row.quiz_version, "score": passing.score})
    cipher = _cipher(settings)
    private = {"profile": _profile(row, settings).model_dump(mode="json"),
        "discovery_other": cipher.decrypt_text(row.discovery_other_encrypted,
            associated_data=f"{round_aad(row)}:discovery") if row.discovery_other_encrypted else None}
    snapshot.profile_encrypted = cipher.encrypt_json(private, associated_data=submission_aad(snapshot))
    session.add(snapshot)
    row.status = "submitted"
    row.submitted_at = now
    row.reviewed_at = None
    row.reviewed_by_id = None
    row.review_reason = None
    row.decision_reference = None
    row.student_verified = None
    _bump(row)
    _audit(session, row, user.id, "submitted", data={"submission_id": snapshot.id, "revision_number": snapshot.revision_number})
    await session.flush()
    return await read_round(session, row, settings)


async def withdraw_round(session, round_id, user, body: AdmissionWithdraw, settings):
    row = await _round(session, round_id, user, lock=True)
    _version(row, body.expected_version)
    if row.status not in WITHDRAWABLE:
        _fail("此申請目前不可撤回")
    row.status = "withdrawn"
    _bump(row)
    _audit(session, row, user.id, "withdrawn", reason=body.reason)
    await session.flush()
    return await read_round(session, row, settings)


async def review_round(session, round_id, admin, body: AdmissionReviewInput, settings):
    if admin.user_role != UserRole.ADMIN:
        _fail("需要管理員權限", 403)
    row = await _round(session, round_id, admin, lock=True, admin=True)
    _version(row, body.expected_version)
    if row.status != "submitted":
        _fail("僅可審核已送出的申請")
    snapshot = await session.scalar(select(MembershipAdmissionSubmission).where(
        MembershipAdmissionSubmission.round_id == row.id).order_by(MembershipAdmissionSubmission.revision_number.desc()).limit(1))
    if snapshot is None:
        _fail("缺少本輪送審快照，請先核對申請")
    if body.decision == "approve":
        await _require_eligible(session, row)
        passing = await session.scalar(select(MembershipAdmissionQuizAttempt).where(
            MembershipAdmissionQuizAttempt.id == snapshot.quiz_attempt_id,
            MembershipAdmissionQuizAttempt.round_id == row.id,
            MembershipAdmissionQuizAttempt.quiz_version == row.quiz_version,
            MembershipAdmissionQuizAttempt.passed.is_(True), MembershipAdmissionQuizAttempt.score >= 80))
        if passing is None or not _consented(row) or not _educated(row):
            _fail("本輪尚未完成教材、同意與 80 分合格測驗")
        if not body.decision_reference:
            _fail("核准需填寫正式決議依據", 422)
        if row.student_claimed and body.student_verified is not True:
            _fail("請先核驗全職在校學生資格", 422)
        try:
            quote_membership_shares(row.requested_share_count,
                approved_full_time_student=row.student_claimed and body.student_verified is True)
        except MembershipPolicyError as exc:
            _fail(str(exc), 422)
    now = datetime.now(timezone.utc)
    review = MembershipAdmissionReview(round_id=row.id, submission_id=snapshot.id,
        reviewer_id=admin.id, decision=body.decision, reason=body.reason,
        decision_reference=body.decision_reference or None, student_verified=body.student_verified, reviewed_at=now)
    session.add(review)
    row.status = {"approve": "approved", "request_supplement": "needs_supplement", "reject": "rejected"}[body.decision]
    row.reviewed_at = now
    row.reviewed_by_id = admin.id
    row.review_reason = body.reason
    row.decision_reference = body.decision_reference or None
    row.student_verified = body.student_verified
    _bump(row)
    await session.flush()
    _audit(session, row, admin.id, "reviewed", reason=body.reason,
        data={"review_id": review.id, "submission_id": snapshot.id, "decision": body.decision,
            "decision_reference": body.decision_reference, "student_verified": body.student_verified})
    await session.flush()
    return await read_round(session, row, settings)


async def _related_rounds(session, model, round_ids, *order):
    grouped = defaultdict(list)
    records = await session.scalars(select(model).where(model.round_id.in_(round_ids)).order_by(*order))
    for record in records:
        grouped[record.round_id].append(record)
    return grouped


async def _read_round_page(session, rows, settings, profiles):
    if not rows:
        return []
    round_ids = [row.id for row in rows]
    user_ids = {row.user_id for row in rows}
    attempts = await _related_rounds(session, MembershipAdmissionQuizAttempt, round_ids, MembershipAdmissionQuizAttempt.attempt_number)
    submissions = await _related_rounds(session, MembershipAdmissionSubmission, round_ids, MembershipAdmissionSubmission.revision_number)
    reviews = await _related_rounds(session, MembershipAdmissionReview, round_ids, MembershipAdmissionReview.reviewed_at, MembershipAdmissionReview.id)
    memberships = {membership.user_id: membership for membership in await session.scalars(
        select(Membership).where(Membership.user_id.in_(user_ids)).execution_options(populate_existing=True))}
    charge_users = set(await session.scalars(select(MembershipCharge.user_id).where(MembershipCharge.user_id.in_(user_ids)).distinct()))
    policy = await read_membership_education_settings(session)
    return [_render_round(row, settings, _RoundReadData(
        attempts[row.id], policy,
        _membership_blocked_reason(memberships.get(row.user_id)) or (
            "此帳號有歷史入社款項，請先由管理員核對，避免重複申請" if row.user_id in charge_users else None),
        submissions[row.id], reviews[row.id],
        profiles[row.id] if row.id in profiles else _profile(row, settings),
    )) for row in rows]


async def list_rounds(session, settings, *, status, q, limit, offset):
    query = select(MembershipAdmissionRound).order_by(MembershipAdmissionRound.created_at.desc(), MembershipAdmissionRound.id)
    if status == "pending":
        query = query.where(MembershipAdmissionRound.status.in_({"submitted", "needs_supplement"}))
    elif status != "all":
        query = query.where(MembershipAdmissionRound.status == status)
    profiles = {}
    if not q.strip():
        total = await session.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        rows = list((await session.scalars(query.limit(limit).offset(offset))).all())
    else:
        term = q.strip().casefold()
        candidates = await session.stream(query.add_columns(User.display_name, User.email).join(User,
            User.id == MembershipAdmissionRound.user_id).execution_options(yield_per=100))
        total, rows = 0, []
        try:
            async for row, display_name, email in candidates:
                public_match = any(term in value.casefold() for value in (display_name, email) if value)
                profile = None if public_match else _profile(row, settings)
                if public_match or (profile and any(term in value.casefold() for value in (profile.legal_name, profile.contact_email))):
                    if offset <= total < offset + limit:
                        rows.append(row)
                        if not public_match:
                            profiles[row.id] = profile
                    total += 1
        finally:
            await candidates.close()
    return AdmissionListRead(items=await _read_round_page(session, rows, settings, profiles),
        total=total, limit=limit, offset=offset)
