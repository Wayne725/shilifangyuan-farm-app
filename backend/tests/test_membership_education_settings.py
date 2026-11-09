from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.membership_education_settings import MEMBERSHIP_EDUCATION_KEY, read_membership_education_settings
from app.membership_policy import membership_quiz_eligibility
from app.models import AdminAudit, SystemSetting, User, UserRole
from app.routers.membership_education_admin import membership_education_admin_router
from tests.support import api_test_context, auth_headers, make_test_settings


URL = "/v1/admin/membership-education/settings"
BODY = {"expected_version": 0, "retry_limit": 2, "retry_interval_minutes": 60, "reason": "設定本期重考規則"}
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def education_settings_context(database_session):
    session = database_session
    admin = User(email="education-admin@example.test", display_name="教育管理員", password_hash="test", user_role=UserRole.ADMIN)
    member = User(email="education-member@example.test", display_name="申請人", password_hash="test")
    session.add_all([admin, member])
    await session.commit()
    async with api_test_context(session, [membership_education_admin_router], settings=make_test_settings()) as client:
        yield client, session, admin, member


@pytest.mark.asyncio
async def test_unconfigured_settings_are_read_only_and_do_not_allow_retries(education_settings_context):
    client, session, admin, _ = education_settings_context
    response = await client.get(URL, headers=auth_headers(admin))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "configured": False, "version": 0, "retry_limit": 0, "retry_interval_minutes": 0,
        "updated_at": None, "updated_by_id": None,
    }
    assert await session.get(SystemSetting, MEMBERSHIP_EDUCATION_KEY) is None
    current = await read_membership_education_settings(session)
    assert current.retry_policy() is None
    first = membership_quiz_eligibility(
        policy=current.retry_policy(), attempt_count=0, last_attempt_at=None, passed=False, now=NOW,
    )
    retry = membership_quiz_eligibility(
        policy=current.retry_policy(), attempt_count=1, last_attempt_at=NOW - timedelta(days=1), passed=False, now=NOW,
    )
    assert first.allowed and first.remaining_attempts == 1
    assert not retry.allowed and retry.remaining_attempts == 0


@pytest.mark.asyncio
async def test_only_admin_can_read_and_change_education_settings(education_settings_context):
    client, session, _, member = education_settings_context
    for headers, status in (({}, 401), (auth_headers(member), 403)):
        assert (await client.get(URL, headers=headers)).status_code == status
        assert (await client.put(URL, headers=headers, json=BODY)).status_code == status
    assert await session.get(SystemSetting, MEMBERSHIP_EDUCATION_KEY) is None
    assert await session.scalar(select(func.count(AdminAudit.id))) == 0


@pytest.mark.asyncio
async def test_changes_persist_version_actor_audit_and_retry_behavior(education_settings_context):
    client, session, admin, _ = education_settings_context
    session.add(SystemSetting(key="storefront_operations", value={"orders_open": False}))
    await session.commit()
    created = await client.put(URL, headers=auth_headers(admin), json=BODY)
    assert created.status_code == 200, created.text
    assert created.headers["cache-control"] == "no-store"
    data = created.json()
    assert data["configured"] and data["version"] == 1
    assert data["updated_by_id"] == admin.id and data["updated_at"] is not None
    assert (await client.get(URL, headers=auth_headers(admin))).json() == data
    current = await read_membership_education_settings(session)
    blocked = membership_quiz_eligibility(
        policy=current.retry_policy(), attempt_count=1, last_attempt_at=NOW, passed=False, now=NOW + timedelta(minutes=59),
    )
    allowed = membership_quiz_eligibility(
        policy=current.retry_policy(), attempt_count=1, last_attempt_at=NOW, passed=False, now=NOW + timedelta(minutes=60),
    )
    assert not blocked.allowed and blocked.next_available_at == NOW + timedelta(minutes=60)
    assert allowed.allowed and allowed.remaining_attempts == 2

    updated = await client.put(URL, headers=auth_headers(admin), json={
        **BODY, "expected_version": 1, "retry_limit": 0, "retry_interval_minutes": 0,
    })
    assert updated.status_code == 200 and updated.json()["version"] == 2
    audits = (await session.scalars(select(AdminAudit).order_by(AdminAudit.created_at))).all()
    assert len(audits) == 2
    assert audits[0].action == "membership.education_settings_updated"
    assert audits[0].data["previous"]["configured"] is False
    assert audits[0].data["current"]["retry_limit"] == 2
    assert audits[1].data["previous"]["version"] == 1
    assert audits[1].data["current"]["retry_limit"] == 0
    assert audits[1].actor_id == admin.id and audits[1].reason == BODY["reason"]
    assert (await session.get(SystemSetting, "storefront_operations")).value == {"orders_open": False}


@pytest.mark.asyncio
async def test_stale_admin_save_does_not_overwrite_or_audit(education_settings_context):
    client, session, admin, _ = education_settings_context
    headers = auth_headers(admin)
    assert (await client.put(URL, headers=headers, json={**BODY, "expected_version": 1})).status_code == 409
    assert await session.get(SystemSetting, MEMBERSHIP_EDUCATION_KEY) is None
    assert (await client.put(URL, headers=headers, json=BODY)).status_code == 200
    stale = await client.put(URL, headers=headers, json={**BODY, "retry_limit": 9})
    assert stale.status_code == 409
    stored = await client.get(URL, headers=headers)
    assert stored.json()["version"] == 1 and stored.json()["retry_limit"] == 2
    assert await session.scalar(select(func.count(AdminAudit.id))) == 1


@pytest.mark.parametrize("changed", [
    {"retry_limit": -1}, {"retry_limit": 101}, {"retry_limit": True}, {"retry_limit": 1.0},
    {"retry_interval_minutes": -1}, {"retry_interval_minutes": 525_601},
    {"retry_interval_minutes": False}, {"retry_interval_minutes": "60"},
    {"expected_version": True}, {"expected_version": -1}, {"reason": "x" * 1001},
    {"configured": True},
])
@pytest.mark.asyncio
async def test_invalid_configuration_cannot_be_saved(education_settings_context, changed):
    client, session, admin, _ = education_settings_context
    response = await client.put(URL, headers=auth_headers(admin), json={**BODY, **changed})
    assert response.status_code == 422, response.text
    assert await session.get(SystemSetting, MEMBERSHIP_EDUCATION_KEY) is None
    assert await session.scalar(select(func.count(AdminAudit.id))) == 0


@pytest.mark.parametrize("note", [{}, {"reason": ""}, {"reason": " \t "}])
@pytest.mark.asyncio
async def test_education_settings_optional_note_preserves_version_and_audit(education_settings_context, note):
    client, session, admin, _ = education_settings_context
    body = {key: value for key, value in BODY.items() if key != "reason"} | note
    result = await client.put(URL, headers=auth_headers(admin), json=body)
    assert result.status_code == 200, result.text
    assert result.json()["version"] == 1 and result.json()["retry_limit"] == 2
    audit = await session.scalar(select(AdminAudit))
    assert audit.actor_id == admin.id and audit.reason == ""
    assert (await client.put(URL, headers=auth_headers(admin), json=body)).status_code == 409
    assert await session.scalar(select(func.count(AdminAudit.id))) == 1
