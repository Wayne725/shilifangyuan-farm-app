from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, Meeting, MeetingCategory, MeetingType, Membership, MembershipStatus, User, UserRole
from app.routers.cooperative import cooperative_router
from tests.support import api_test_context, auth_headers


@pytest.fixture
async def meeting_context(database_session):
    session = database_session
    users = {}
    for name, membership_status, role in [
        ("admin", None, UserRole.ADMIN), ("member", MembershipStatus.ACTIVE, UserRole.CUSTOMER),
        ("customer", None, UserRole.CUSTOMER), ("trainee", MembershipStatus.TRAINEE, UserRole.CUSTOMER),
        ("resigned", MembershipStatus.RESIGNED, UserRole.CUSTOMER),
    ]:
        user = User(email=f"{name}@meetings.example.test", display_name=name, password_hash="test", user_role=role)
        if membership_status is not None:
            user.membership = Membership(status=membership_status)
        users[name] = user
        session.add(user)
    original = [MeetingCategory(id="00000000-0000-4000-8000-000000000001", name="社員大會", legacy_type="general_assembly", sort_order=0),
                MeetingCategory(id="00000000-0000-4000-8000-000000000002", name="社務會議", legacy_type="affairs", sort_order=1)]
    session.add_all(original)
    await session.commit()
    original_values = [SimpleNamespace(id=row.id, name=row.name, legacy_type=row.legacy_type, updated_at=row.updated_at) for row in original]
    async with api_test_context(session, [cooperative_router]) as client:
        yield session, client, users, original_values


def meeting_payload(**overrides):
    return {"title": "測試會議", "starts_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "location": "合作社", "agenda": [{"title": "議程一"}], **overrides}


async def test_category_management_and_disabled_history_preserve_records(meeting_context):
    session, client, users, original = meeting_context
    admin = auth_headers(users["admin"])
    created = await client.post("/v1/admin/meeting-categories", headers=admin, json={"name": "  理事會  ", "sort_order": 2})
    assert created.status_code == 201, created.text
    category = created.json()
    assert category["name"] == "理事會" and category["is_active"] and category["updated_at"].endswith("Z")
    meeting = await client.post("/v1/admin/meetings", headers=admin, json=meeting_payload(category_id=category["id"]))
    assert meeting.status_code == 201, meeting.text
    meeting_id = meeting.json()["id"]
    assert meeting.json()["meeting_type"] == "affairs" and meeting.json()["category_name"] == "理事會"
    assert (await client.put(f"/v1/admin/meetings/{meeting_id}/attendance", headers=admin,
        json={"member_id": users["member"].id, "attended": True})).status_code == 200
    assert (await client.post(f"/v1/admin/meetings/{meeting_id}/resolutions", headers=admin,
        json={"title": "決議一", "resolution_text": "通過"})).status_code == 201
    updated = await client.patch(f"/v1/admin/meeting-categories/{category['id']}", headers=admin,
        json={"name": "理監事會", "sort_order": 0, "is_active": False, "expected_updated_at": category["updated_at"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated_at"] != category["updated_at"]
    visible_categories = await client.get("/v1/meeting-categories", headers=auth_headers(users["member"]))
    assert [row["name"] for row in visible_categories.json()] == ["社員大會", "社務會議"]
    all_categories = await client.get("/v1/admin/meeting-categories", headers=admin)
    assert len(all_categories.json()) == 3 and all_categories.json()[0]["name"] == "理監事會"
    historical = (await client.get("/v1/meetings", headers=auth_headers(users["member"]))).json()[0]
    assert historical["category_id"] == category["id"] and historical["category_name"] == "理監事會"
    assert historical["attended_count"] == 1 and historical["resolutions"][0]["resolution_text"] == "通過"
    assert historical["starts_at"].endswith("Z") and historical["resolutions"][0]["created_at"].endswith("Z")
    blocked = await client.post("/v1/admin/meetings", headers=admin, json=meeting_payload(category_id=category["id"]))
    assert blocked.status_code == 409 and "停用" in blocked.json()["detail"]
    restored = await client.patch(f"/v1/admin/meeting-categories/{category['id']}", headers=admin,
        json={"is_active": True, "expected_updated_at": updated.json()["updated_at"]})
    assert restored.status_code == 200
    assert await session.scalar(select(func.count(Meeting.id))) == 1
    assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action.like("meeting_category.%"))) == 3


@pytest.mark.parametrize("role", ["customer", "trainee", "resigned", "admin"])
async def test_member_reads_keep_existing_active_membership_gate(meeting_context, role):
    _, client, users, _ = meeting_context
    for path in ["/v1/meetings", "/v1/meeting-categories"]:
        assert (await client.get(path, headers=auth_headers(users[role]))).status_code == 403
        assert (await client.get(path)).status_code == 401


@pytest.mark.parametrize("role", ["member", "customer", "trainee", "resigned"])
async def test_only_admins_can_manage_categories_or_create_meetings(meeting_context, role):
    _, client, users, original = meeting_context
    headers = auth_headers(users[role])
    assert (await client.get("/v1/admin/meeting-categories", headers=headers)).status_code == 403
    assert (await client.get("/v1/admin/meetings", headers=headers)).status_code == 403
    assert (await client.post("/v1/admin/meeting-categories", headers=headers, json={"name": "新類別"})).status_code == 403
    assert (await client.patch(f"/v1/admin/meeting-categories/{original[0].id}", headers=headers,
        json={"name": "變更", "expected_updated_at": original[0].updated_at.isoformat()})).status_code == 403
    assert (await client.post("/v1/admin/meetings", headers=headers, json=meeting_payload(category_id=original[0].id))).status_code == 403


async def test_legacy_meeting_type_mapping_cannot_bypass_disabled_category(meeting_context):
    _, client, users, original = meeting_context
    admin = auth_headers(users["admin"])
    for category in original:
        response = await client.post("/v1/admin/meetings", headers=admin, json=meeting_payload(meeting_type=category.legacy_type))
        assert response.status_code == 201 and response.json()["category_id"] == category.id
    mismatch = await client.post("/v1/admin/meetings", headers=admin,
        json=meeting_payload(category_id=original[0].id, meeting_type="affairs"))
    assert mismatch.status_code == 422
    assert (await client.post("/v1/admin/meetings", headers=admin, json=meeting_payload())).status_code == 422
    assert (await client.post("/v1/admin/meetings", headers=admin, json=meeting_payload(category_id="missing"))).status_code == 404
    disabled = await client.patch(f"/v1/admin/meeting-categories/{original[0].id}", headers=admin,
        json={"is_active": False, "expected_updated_at": original[0].updated_at.isoformat()})
    assert disabled.status_code == 200
    assert (await client.post("/v1/admin/meetings", headers=admin, json=meeting_payload(meeting_type="general_assembly"))).status_code == 409
    historical = await client.get("/v1/admin/meetings", headers=admin)
    assert historical.status_code == 200 and len(historical.json()) == 2


async def test_category_unique_trim_validation_and_stale_edit_are_atomic(meeting_context):
    session, client, users, original = meeting_context
    admin = auth_headers(users["admin"])
    assert (await client.post("/v1/admin/meeting-categories", headers=admin, json={"name": " 社員大會 "})).status_code == 409
    for body in [{"name": "  "}, {"name": "長" * 81}, {"name": "新類別", "sort_order": -1},
                 {"name": "新類別", "sort_order": True}, {"name": "新類別", "is_active": "false"},
                 {"name": "新類別", "legacy_type": "affairs"}]:
        assert (await client.post("/v1/admin/meeting-categories", headers=admin, json=body)).status_code == 422
    version = original[0].updated_at.isoformat()
    updated = await client.patch(f"/v1/admin/meeting-categories/{original[0].id}", headers=admin,
        json={"sort_order": 5, "expected_updated_at": version})
    assert updated.status_code == 200
    stale = await client.patch(f"/v1/admin/meeting-categories/{original[0].id}", headers=admin,
        json={"name": "過時編輯", "expected_updated_at": version})
    assert stale.status_code == 409 and "已更新" in stale.json()["detail"]
    for updates in [{"name": None}, {"name": "  "}, {"sort_order": None}, {"is_active": None}, {"legacy_type": "affairs"}]:
        response = await client.patch(f"/v1/admin/meeting-categories/{original[0].id}", headers=admin,
            json={**updates, "expected_updated_at": updated.json()["updated_at"]})
        assert response.status_code == 422
    duplicate = await client.patch(f"/v1/admin/meeting-categories/{original[0].id}", headers=admin,
        json={"name": "社務會議", "expected_updated_at": updated.json()["updated_at"]})
    assert duplicate.status_code == 409
    rows = (await client.get("/v1/admin/meeting-categories", headers=admin)).json()
    assert [row["name"] for row in rows] == ["社務會議", "社員大會"]
    assert rows[1]["updated_at"] == updated.json()["updated_at"]
    assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "meeting_category.update")) == 1


async def test_demo_seed_reuses_migrated_categories_without_overwriting_names(database_session):
    from app.seed import seed_demo_data

    session = database_session
    session.add_all([
        MeetingCategory(id="00000000-0000-4000-8000-000000000001", name="社員大會", legacy_type="general_assembly", sort_order=0),
        MeetingCategory(id="00000000-0000-4000-8000-000000000002", name="社務工作會議", legacy_type="affairs", sort_order=7),
    ])
    await session.commit()
    await seed_demo_data(session)
    categories = (await session.scalars(select(MeetingCategory).order_by(MeetingCategory.sort_order))).all()
    assert len(categories) == 2
    assert categories[1].name == "社務工作會議" and categories[1].sort_order == 7
    meeting = await session.get(Meeting, "meeting-affairs-demo")
    assert meeting.category_id == categories[1].id
