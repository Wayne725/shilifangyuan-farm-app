import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.integrations.common import IntegrationResponseError
from app.integrations.pii_crypto import pii_cipher_from_settings
from app.membership_admission import round_aad, submission_aad
from app.membership_admission_content import get_admission_config
from app.membership_education_settings import MEMBERSHIP_EDUCATION_KEY
from app.models import (
    AdminAudit, MemberProfile, Membership, MembershipApplication, MembershipApplicationStatus,
    MembershipCharge, MembershipAdmissionQuizAttempt, MembershipAdmissionReview,
    MembershipAdmissionRound, MembershipAdmissionSubmission, MembershipStatus, Notification,
    OutboxEvent, SystemSetting, User, UserRole,
)
from app.routers.membership_admission import membership_admission_router
from tests.support import api_test_context, auth_headers, make_test_settings


URL = "/v1/membership/admission"
ADMIN_URL = "/v1/admin/membership/admissions"
PROFILE = {
    "legal_name": "隔離申請人", "phone": "0900000000", "birth_date": "1995-01-01",
    "address": "隔離測試地址", "emergency_contact": "隔離緊急聯絡人", "contact_email": "admission@example.com",
}
CORRECT = "CBBCCDBC CB".replace(" ", "")


def admission_settings():
    return make_test_settings(pii_encryption_keys_json=json.dumps({
        "v1": base64.b64encode(b"p" * 32).decode(),
    }))


def profile_body(row, **overrides):
    declaration = get_admission_config()["declaration"]
    return {
        "expected_version": row["version"], "profile": PROFILE,
        "requested_share_count": 10, "student_claimed": False,
        "discovery_channel": "朋友介紹", "declaration_version": declaration["version"],
        "privacy_version": declaration["privacy_version"], "declaration_accepted": True,
        "privacy_accepted": True, "share_rules_understood": True, "payment_notice_understood": True,
        **overrides,
    }


def quiz_body(row, *, score=100, **overrides):
    answers = [{"question_id": f"q{index + 1:02}", "option": option if index < score // 10 else ("A" if option != "A" else "B")}
        for index, option in enumerate(CORRECT)]
    return {"expected_version": row["version"], "idempotency_key": str(uuid4()),
        "quiz_version": row["quiz_version"], "answers": answers, **overrides}


async def start(c):
    response = await c["client"].post(f"{URL}/rounds", headers=c["owner_headers"],
        json={"idempotency_key": str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


async def ready(c, **profile_overrides):
    row = await start(c)
    response = await c["client"].put(f"{URL}/rounds/{row['id']}", headers=c["owner_headers"],
        json=profile_body(row, **profile_overrides))
    assert response.status_code == 200, response.text
    row = response.json()
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/education", headers=c["owner_headers"],
        json={"expected_version": row["version"], "material_version": get_admission_config()["material"]["version"], "read_confirmed": True})
    assert response.status_code == 200, response.text
    return response.json()


async def passed(c, **profile_overrides):
    row = await ready(c, **profile_overrides)
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/quiz-attempts", headers=c["owner_headers"], json=quiz_body(row))
    assert response.status_code == 200, response.text
    assert response.json()["attempts"][0]["score"] == 100
    return response.json()


async def submitted(c, **profile_overrides):
    row = await passed(c, **profile_overrides)
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/submit", headers=c["owner_headers"], json={"expected_version": row["version"]})
    assert response.status_code == 200, response.text
    return response.json()


async def review(c, row, **overrides):
    return await c["client"].post(f"{ADMIN_URL}/{row['id']}/review", headers=c["admin_headers"], json={
        "expected_version": row["version"], "decision": "approve", "reason": "隔離審核紀錄",
        "decision_reference": "TEST-決議-001", **overrides,
    })


@pytest.fixture
async def admission_context(database_session):
    session = database_session
    session.autoflush = False
    owner = User(email="admission-owner@example.com", display_name="隔離帳號", password_hash="test")
    outsider = User(email="admission-outsider@example.com", display_name="其他帳號", password_hash="test")
    admin = User(email="admission-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    session.add_all([owner, outsider, admin])
    await session.commit()
    async with api_test_context(session, [membership_admission_router], settings=admission_settings()) as client:
        yield {"client": client, "session": session, "owner_id": owner.id, "admin_id": admin.id,
            "owner_headers": auth_headers(owner), "outsider_headers": auth_headers(outsider), "admin_headers": auth_headers(admin)}


@pytest.mark.asyncio
async def test_public_questions_no_answers_and_get_never_creates_admission(admission_context):
    c = admission_context
    assert (await c["client"].get(URL)).status_code == 401
    response = await c["client"].get(URL, headers=c["owner_headers"])
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["can_start"] and data["current_round"] is None
    assert data["config"]["quiz"]["pass_score"] == 80
    assert len(data["config"]["quiz"]["questions"]) == 10
    assert "correct_option" not in response.text and "selected_option" not in response.text
    assert await c["session"].scalar(select(func.count(MembershipAdmissionRound.id))) == 0


@pytest.mark.asyncio
async def test_admission_fee_and_legacy_flag_are_read_only_and_owner_scoped(admission_context):
    from app.membership_policy import quote_membership_shares

    c = admission_context
    response = await c["client"].get(URL, headers=c["owner_headers"])
    assert response.status_code == 200
    declaration = response.json()["config"]["declaration"]
    quote = quote_membership_shares(10, approved_full_time_student=False)
    assert declaration["admission_fee_amount"] == quote.admission_fee_amount
    assert declaration["share_unit_amount"] == quote.share_unit_amount
    assert response.json()["has_legacy_application"] is False
    c["session"].add(MembershipApplication(user_id=c["owner_id"], status=MembershipApplicationStatus.REJECTED))
    await c["session"].commit()
    response = await c["client"].get(URL, headers=c["owner_headers"])
    assert response.status_code == 200 and response.json()["has_legacy_application"] is True
    outsider = await c["client"].get(URL, headers=c["outsider_headers"])
    assert outsider.status_code == 200 and outsider.json()["has_legacy_application"] is False
    assert await c["session"].scalar(select(func.count(MembershipAdmissionRound.id))) == 0
    assert await c["session"].scalar(select(func.count(MembershipCharge.id))) == 0


@pytest.mark.asyncio
async def test_phase_one_approval_is_not_membership_money_or_notification(admission_context):
    c = admission_context
    row = await submitted(c)
    response = await review(c, row, reason="  ")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "approved" and not data["can_edit"] and not data["can_withdraw"]
    assert data["decision_reference"] == "TEST-決議-001"
    assert data["reviews"][0]["decision"] == "approve"
    assert data["reviews"][0]["reason"] == ""
    for model in (Membership, MembershipCharge, MemberProfile, MembershipApplication, Notification, OutboxEvent):
        assert await c["session"].scalar(select(func.count(model.id))) == 0
    owner = await c["session"].get(User, c["owner_id"])
    assert owner.membership_type.value == "nonmember"
    assert not (await c["client"].get(URL, headers=c["owner_headers"])).json()["can_start"]
    assert (await start(c))["id"] == row["id"]


@pytest.mark.asyncio
async def test_start_and_quiz_idempotency_conflict_and_version(admission_context):
    c = admission_context
    key = str(uuid4())
    first = await c["client"].post(f"{URL}/rounds", headers=c["owner_headers"], json={"idempotency_key": key})
    second = await c["client"].post(f"{URL}/rounds", headers=c["owner_headers"], json={"idempotency_key": key})
    assert first.json()["id"] == second.json()["id"]
    row = await ready(c)
    body = quiz_body(row, score=80)
    url = f"{URL}/rounds/{row['id']}/quiz-attempts"
    first = await c["client"].post(url, headers=c["owner_headers"], json=body)
    assert first.status_code == 200 and first.json()["attempts"][0]["score"] == 80
    assert first.json()["attempts"][0]["passed"]
    second = await c["client"].post(url, headers=c["owner_headers"], json={**body, "answers": list(reversed(body["answers"]))})
    assert second.status_code == 200 and second.json() == first.json()
    changed = [{**item, "option": "A"} for item in body["answers"]]
    assert (await c["client"].post(url, headers=c["owner_headers"], json={**body, "answers": changed})).status_code == 409
    assert (await c["client"].post(url, headers=c["owner_headers"], json=quiz_body(first.json()))).status_code == 409
    assert await c["session"].scalar(select(func.count(MembershipAdmissionQuizAttempt.id))) == 1


@pytest.mark.parametrize("kind", ["duplicate", "unknown_question", "unknown_option", "score", "passed", "short"])
@pytest.mark.asyncio
async def test_invalid_quiz_cannot_spend_attempt_or_forge_result(admission_context, kind):
    c = admission_context
    row = await ready(c)
    body = quiz_body(row)
    if kind == "duplicate":
        body["answers"][1] = body["answers"][0]
    elif kind == "unknown_question":
        body["answers"][0]["question_id"] = "q99"
    elif kind == "unknown_option":
        body["answers"][0]["option"] = "Z"
    elif kind in {"score", "passed"}:
        body[kind] = 100 if kind == "score" else True
    else:
        body["answers"].pop()
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/quiz-attempts", headers=c["owner_headers"], json=body)
    assert response.status_code == 422, response.text
    assert await c["session"].scalar(select(func.count(MembershipAdmissionQuizAttempt.id))) == 0
    current = await c["client"].get(f"{URL}/rounds/{row['id']}", headers=c["owner_headers"])
    assert current.json()["version"] == row["version"] and "correct_option" not in current.text


@pytest.mark.asyncio
async def test_reading_consent_and_passing_are_required_server_side(admission_context):
    c = admission_context
    row = await start(c)
    for suffix, body in (
        ("education", {"expected_version": row["version"], "material_version": get_admission_config()["material"]["version"], "read_confirmed": True}),
        ("quiz-attempts", quiz_body(row)), ("submit", {"expected_version": row["version"]}),
    ):
        assert (await c["client"].post(f"{URL}/rounds/{row['id']}/{suffix}", headers=c["owner_headers"], json=body)).status_code == 409
    row = await ready(c)
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/quiz-attempts", headers=c["owner_headers"], json=quiz_body(row, score=70))
    assert response.status_code == 200 and response.json()["attempts"][0]["score"] == 70
    row = response.json()
    assert not row["can_submit"] and not row["quiz_eligibility"]["allowed"]
    assert not row["quiz_eligibility"]["settings_configured"]
    assert (await c["client"].post(f"{URL}/rounds/{row['id']}/submit", headers=c["owner_headers"], json={"expected_version": row["version"]})).status_code == 409
    assert (await c["client"].post(f"{URL}/rounds/{row['id']}/quiz-attempts", headers=c["owner_headers"], json=quiz_body(row))).status_code == 409


@pytest.mark.parametrize("overrides", [
    {"declaration_accepted": False}, {"privacy_accepted": False}, {"share_rules_understood": False},
    {"payment_notice_understood": False}, {"declaration_accepted": 1}, {"requested_share_count": True},
    {"requested_share_count": 9}, {"requested_share_count": 11, "student_claimed": True},
    {"student_claimed": "true"}, {"identity_number": "A123456789"},
    {"profile": {**PROFILE, "identity_number": ""}}, {"profile": {**PROFILE, "document": "x"}},
    {"profile": {**PROFILE, "contact_email": "invalid"}}, {"profile": {**PROFILE, "legal_name": "   "}},
    {"discovery_channel": "unknown"}, {"discovery_other": "不該收集此處自由文字"},
])
@pytest.mark.asyncio
async def test_invalid_and_unconsented_profile_does_not_persist(admission_context, overrides):
    c = admission_context
    row = await start(c)
    response = await c["client"].put(f"{URL}/rounds/{row['id']}", headers=c["owner_headers"], json=profile_body(row, **overrides))
    assert response.status_code == 422, response.text
    saved = await c["session"].get(MembershipAdmissionRound, row["id"])
    assert saved.profile_encrypted is None and saved.version == 1


@pytest.mark.asyncio
async def test_profile_versions_and_student_review_require_verification(admission_context):
    c = admission_context
    row = await start(c)
    for changed in ({"declaration_version": "old"}, {"privacy_version": "old"}, {"expected_version": 999}):
        assert (await c["client"].put(f"{URL}/rounds/{row['id']}", headers=c["owner_headers"], json=profile_body(row, **changed))).status_code == 409
    row = await submitted(c, requested_share_count=5, student_claimed=True)
    for changed in ({"student_verified": False}, {"student_verified": None}, {"student_verified": True, "decision_reference": " "}):
        assert (await review(c, row, **changed)).status_code == 422
    response = await review(c, row, student_verified=True)
    assert response.status_code == 200 and response.json()["student_verified"] is True
    assert (await review(c, row, student_verified=True)).status_code == 409
    assert await c["session"].scalar(select(func.count(MembershipAdmissionReview.id))) == 1


@pytest.mark.asyncio
async def test_retry_policy_snapshots_and_interval_do_not_reset_used_count(admission_context):
    c = admission_context
    row = await ready(c)
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/quiz-attempts", headers=c["owner_headers"], json=quiz_body(row, score=0))
    row = response.json()
    c["session"].add(SystemSetting(key=MEMBERSHIP_EDUCATION_KEY,
        value={"version": 1, "retry_limit": 1, "retry_interval_minutes": 60}))
    await c["session"].commit()
    assert (await c["client"].post(f"{URL}/rounds/{row['id']}/quiz-attempts", headers=c["owner_headers"], json=quiz_body(row))).status_code == 409
    current = (await c["client"].get(f"{URL}/rounds/{row['id']}", headers=c["owner_headers"])).json()
    assert current["quiz_eligibility"]["remaining_attempts"] == 1 and current["quiz_eligibility"]["next_available_at"]
    attempt = await c["session"].scalar(select(MembershipAdmissionQuizAttempt))
    attempt.submitted_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await c["session"].commit()
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/quiz-attempts", headers=c["owner_headers"], json=quiz_body(row))
    assert response.status_code == 200, response.text
    assert [item["settings_version"] for item in response.json()["attempts"]] == [0, 1]
    assert [item["attempt_number"] for item in response.json()["attempts"]] == [1, 2]
    assert response.json()["quiz_eligibility"]["remaining_attempts"] == 0


@pytest.mark.asyncio
async def test_supplement_preserves_round_grade_and_immutable_encrypted_snapshots(admission_context):
    c = admission_context
    row = await submitted(c, discovery_channel="其他", discovery_other="私人說明")
    first_snapshot = await c["session"].scalar(select(MembershipAdmissionSubmission))
    original_envelope = first_snapshot.profile_encrypted
    first_id = first_snapshot.id
    response = await review(c, row, decision="request_supplement", decision_reference=None)
    assert response.status_code == 200
    row = response.json()
    assert row["status"] == "needs_supplement" and row["can_submit"]
    row = (await c["client"].put(f"{URL}/rounds/{row['id']}", headers=c["owner_headers"],
        json=profile_body(row, profile={**PROFILE, "legal_name": "補件姓名"}))).json()
    assert len(row["attempts"]) == 1
    response = await c["client"].post(f"{URL}/rounds/{row['id']}/submit", headers=c["owner_headers"], json={"expected_version": row["version"]})
    assert response.status_code == 200
    row = response.json()
    assert [item["revision_number"] for item in row["submissions"]] == [1, 2]
    assert row["review_reason"] is None
    assert (await review(c, row)).status_code == 200
    snapshots = list((await c["session"].scalars(select(MembershipAdmissionSubmission).order_by(MembershipAdmissionSubmission.revision_number))).all())
    cipher = pii_cipher_from_settings(admission_settings())
    first_snapshot = await c["session"].get(MembershipAdmissionSubmission, first_id)
    assert first_snapshot.profile_encrypted == original_envelope
    assert cipher.decrypt_json(original_envelope, associated_data=submission_aad(first_snapshot))["profile"]["legal_name"] == PROFILE["legal_name"]
    assert cipher.decrypt_json(snapshots[1].profile_encrypted, associated_data=submission_aad(snapshots[1]))["profile"]["legal_name"] == "補件姓名"
    for snapshot in snapshots:
        assert "私人說明" not in json.dumps(snapshot.facts, ensure_ascii=False)
    with pytest.raises(IntegrationResponseError):
        cipher.decrypt_json(original_envelope, associated_data=submission_aad(snapshots[1]))
    assert len(list((await c["session"].scalars(select(MembershipAdmissionReview))).all())) == 2


@pytest.mark.asyncio
async def test_withdraw_or_reject_then_new_round_never_reuses_old_pass(admission_context):
    c = admission_context
    old = await submitted(c)
    response = await review(c, old, decision="reject")
    assert response.status_code == 200
    new = await start(c)
    assert new["round_number"] == 2 and new["id"] != old["id"]
    assert new["attempts"] == [] and new["profile"] is None and new["consented_at"] is None
    assert new["material_completed_at"] is None and not new["can_submit"]
    assert (await c["client"].get(f"{URL}/rounds/{old['id']}", headers=c["owner_headers"])).json()["attempts"][0]["passed"]
    response = await c["client"].post(f"{URL}/rounds/{new['id']}/withdraw", headers=c["owner_headers"], json={"expected_version": new["version"]})
    assert response.status_code == 200
    assert (await start(c))["round_number"] == 3


@pytest.mark.asyncio
async def test_owner_and_admin_authorization_search_and_pagination(admission_context):
    c = admission_context
    row = await submitted(c)
    path = f"{URL}/rounds/{row['id']}"
    assert (await c["client"].get(path, headers=c["outsider_headers"])).status_code == 404
    assert (await c["client"].put(path, headers=c["outsider_headers"], json=profile_body(row))).status_code == 404
    assert (await c["client"].post(path + "/withdraw", headers=c["outsider_headers"], json={"expected_version": row["version"]})).status_code == 404
    assert (await c["client"].get(path, headers=c["admin_headers"])).status_code == 200
    assert (await c["client"].get(ADMIN_URL, headers=c["owner_headers"])).status_code == 403
    assert (await c["client"].post(f"{ADMIN_URL}/{row['id']}/review", headers=c["owner_headers"], json={"expected_version": row["version"], "decision": "reject", "reason": "越權"})).status_code == 403
    for q in ("隔離申請人", "admission@example.com", "隔離帳號", "admission-owner@example.com"):
        response = await c["client"].get(ADMIN_URL, headers=c["admin_headers"], params={"q": q, "limit": 1})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1 and response.json()["items"][0]["id"] == row["id"]
    page = (await c["client"].get(ADMIN_URL, headers=c["admin_headers"], params={"offset": 1})).json()
    assert page["total"] == 1 and page["items"] == []
    assert (await c["client"].get(ADMIN_URL, headers=c["admin_headers"], params={"q": "不存在"})).json()["total"] == 0


@pytest.mark.parametrize("status", [MembershipStatus.ACTIVE, MembershipStatus.TRAINEE,
    MembershipStatus.PENDING_PAYMENT, MembershipStatus.RESIGNED, MembershipStatus.TERMINATED])
@pytest.mark.asyncio
async def test_existing_memberships_do_not_start_new_flow(admission_context, status):
    c = admission_context
    c["session"].add(Membership(user_id=c["owner_id"], status=status))
    await c["session"].commit()
    assert not (await c["client"].get(URL, headers=c["owner_headers"])).json()["can_start"]
    response = await c["client"].post(f"{URL}/rounds", headers=c["owner_headers"], json={"idempotency_key": str(uuid4())})
    assert response.status_code == 409
    assert await c["session"].scalar(select(func.count(MembershipAdmissionRound.id))) == 0


@pytest.mark.asyncio
async def test_new_profile_does_not_modify_legacy_and_ciphertext_is_round_bound(admission_context):
    c = admission_context
    cipher = pii_cipher_from_settings(admission_settings())
    aad = f"member-profile:{c['owner_id']}"
    encrypted = {f"{key}_encrypted": cipher.encrypt_text(value, associated_data=aad)
        for key, value in PROFILE.items() if key not in {"contact_email"}}
    legacy = MemberProfile(user_id=c["owner_id"], **encrypted,
        consent_version="legacy", consented_at=datetime.now(timezone.utc))
    old_application = MembershipApplication(user_id=c["owner_id"], status=MembershipApplicationStatus.SUBMITTED)
    c["session"].add_all([legacy, old_application])
    await c["session"].commit()
    before = dict(encrypted)
    data = (await c["client"].get(URL, headers=c["owner_headers"])).json()
    assert data["profile_defaults"]["legal_name"] == PROFILE["legal_name"]
    row = await ready(c, profile={**PROFILE, "legal_name": "新版資料"})
    saved = await c["session"].get(MembershipAdmissionRound, row["id"])
    assert "新版資料" not in saved.profile_encrypted
    assert cipher.decrypt_json(saved.profile_encrypted, associated_data=round_aad(saved))["legal_name"] == "新版資料"
    assert all(getattr(legacy, key) == value for key, value in before.items())
    assert old_application.status == MembershipApplicationStatus.SUBMITTED
    audits = list((await c["session"].scalars(select(AdminAudit))).all())
    assert "新版資料" not in json.dumps([item.data for item in audits], ensure_ascii=False)


@pytest.mark.asyncio
async def test_existing_member_claim_can_close_duplicate_admission_without_changing_membership(admission_context):
    c = admission_context
    row = await submitted(c)
    membership = Membership(user_id=c["owner_id"], status=MembershipStatus.ACTIVE, member_number="TEST-EXISTING")
    c["session"].add(membership)
    await c["session"].commit()
    assert (await review(c, row)).status_code == 409
    response = await review(c, row, decision="request_supplement", reason="核對既有社員認領")
    assert response.status_code == 200
    data = response.json()
    assert not data["can_edit"] and not data["can_submit"] and not data["quiz_eligibility"]["allowed"]
    assert data["can_withdraw"]
    assert (await c["client"].put(f"{URL}/rounds/{row['id']}", headers=c["owner_headers"], json=profile_body(data))).status_code == 409
    membership = await c["session"].scalar(select(Membership).where(Membership.user_id == c["owner_id"]))
    assert membership.status == MembershipStatus.ACTIVE and membership.member_number == "TEST-EXISTING"


@pytest.mark.asyncio
async def test_admin_can_reject_duplicate_submitted_admission_after_existing_member_claim(admission_context):
    c = admission_context
    row = await submitted(c)
    c["session"].add(Membership(user_id=c["owner_id"], status=MembershipStatus.ACTIVE, member_number="TEST-EXISTING"))
    await c["session"].commit()
    response = await review(c, row, decision="reject", reason="已核對既有社員認領，結束重複申請")
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    membership = await c["session"].scalar(select(Membership).where(Membership.user_id == c["owner_id"]))
    assert membership.status == MembershipStatus.ACTIVE and membership.member_number == "TEST-EXISTING"
    assert await c["session"].scalar(select(func.count(MembershipCharge.id))) == 0
