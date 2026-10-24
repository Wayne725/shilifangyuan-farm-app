from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import PointAccount, PointTransaction

from tests.support import auth_headers
from tests.test_v2_routers import v2_context


@pytest.mark.asyncio
async def test_public_activities_preserve_private_drafts_and_member_admin_permissions(v2_context):
    client = v2_context["client"]
    member = v2_context["member_a"]
    customer = v2_context["applicant"]
    admin = v2_context["admin"]
    now = datetime.now(timezone.utc)
    body = {
        "title": "公開永續走讀",
        "description": "歡迎鄰里參加",
        "location": "合作社門口",
        "starts_at": (now + timedelta(days=3)).isoformat(),
        "ends_at": (now + timedelta(days=3, hours=2)).isoformat(),
        "registration_deadline": (now + timedelta(days=2)).isoformat(),
        "capacity": 2,
        "waitlist_enabled": True,
    }
    forbidden_create = await client.post("/v1/activities", json=body, headers=auth_headers(customer))
    assert forbidden_create.status_code == 403
    created = await client.post("/v1/activities", json=body, headers=auth_headers(member))
    assert created.status_code == 201
    activity_id = created.json()["id"]
    for headers in ({}, auth_headers(customer), auth_headers(v2_context["member_b"])):
        assert (await client.get("/v1/activities", headers=headers)).json() == []
        assert (await client.get(f"/v1/activities/{activity_id}", headers=headers)).status_code == 404
    assert (await client.post(f"/v1/activities/{activity_id}/register", headers=auth_headers(customer))).status_code == 404
    assert (await client.get(f"/v1/activities/{activity_id}", headers=auth_headers(member))).status_code == 200
    for url in ("/v1/admin/activities", f"/v1/admin/activities/{activity_id}/registrations"):
        assert (await client.get(url, headers=auth_headers(customer))).status_code == 403
    assert (await client.post(f"/v1/admin/activities/{activity_id}/approve", json={}, headers=auth_headers(customer))).status_code == 403
    approved = await client.post(f"/v1/admin/activities/{activity_id}/approve", json={"reason": "內部審核備註"}, headers=auth_headers(admin))
    assert approved.status_code == 200
    assert approved.json()["review_reason"] == "內部審核備註"
    for headers in ({}, auth_headers(customer), auth_headers(v2_context["member_b"])):
        listing = await client.get("/v1/activities", headers=headers)
        assert listing.status_code == 200
        assert [item["id"] for item in listing.json()] == [activity_id]
        detail = await client.get(f"/v1/activities/{activity_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["my_registration"] is None
        assert detail.json()["review_reason"] is None
    for action in ("register", "cancel-registration"):
        assert (await client.post(f"/v1/activities/{activity_id}/{action}")).status_code == 401


@pytest.mark.asyncio
async def test_nonmembers_can_register_cancel_only_themselves_and_promote_waitlist(v2_context):
    client = v2_context["client"]
    first_user = v2_context["applicant"]
    second_user = v2_context["customer_b"]
    now = datetime.now(timezone.utc)
    created = await client.post("/v1/activities", json={
        "title": "公開候補走讀",
        "description": "候補名額測試",
        "location": "合作社門口",
        "starts_at": (now + timedelta(days=3)).isoformat(),
        "ends_at": (now + timedelta(days=3, hours=2)).isoformat(),
        "registration_deadline": (now + timedelta(days=2)).isoformat(),
        "capacity": 1,
        "waitlist_enabled": True,
    }, headers=auth_headers(v2_context["member_a"]))
    activity_id = created.json()["id"]
    await client.post(f"/v1/admin/activities/{activity_id}/approve", json={}, headers=auth_headers(v2_context["admin"]))
    first = await client.post(f"/v1/activities/{activity_id}/register", headers=auth_headers(first_user))
    assert first.status_code == 200
    assert first.json()["status"] == "registered"
    again = await client.post(f"/v1/activities/{activity_id}/register", headers=auth_headers(first_user))
    assert again.json()["id"] == first.json()["id"]
    assert (await client.post(f"/v1/activities/{activity_id}/cancel-registration", headers=auth_headers(second_user))).status_code == 409
    public = (await client.get(f"/v1/activities/{activity_id}")).json()
    assert public["registration_count"] == 1
    assert public["my_registration"] is None
    second_detail = (await client.get(f"/v1/activities/{activity_id}", headers=auth_headers(second_user))).json()
    assert second_detail["my_registration"] is None
    second = await client.post(f"/v1/activities/{activity_id}/register", headers=auth_headers(second_user))
    assert second.status_code == 200
    assert second.json()["status"] == "waitlisted"
    cancelled = await client.post(f"/v1/activities/{activity_id}/cancel-registration", headers=auth_headers(first_user))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    promoted = (await client.get(f"/v1/activities/{activity_id}", headers=auth_headers(second_user))).json()
    assert promoted["my_registration"]["user_id"] == second_user.id
    assert promoted["my_registration"]["status"] == "registered"
    assert promoted["waitlist_count"] == 0
    assert promoted["registration_count"] == 1
    repeated = await client.post(f"/v1/activities/{activity_id}/cancel-registration", headers=auth_headers(first_user))
    assert repeated.status_code == 409
    await client.post(f"/v1/activities/{activity_id}/cancel-registration", headers=auth_headers(second_user))
    assert (await client.get(f"/v1/activities/{activity_id}")).json()["registration_count"] == 0


@pytest.mark.asyncio
async def test_nonmember_attendance_does_not_grant_member_points(v2_context):
    client = v2_context["client"]
    admin = v2_context["admin"]
    now = datetime.now(timezone.utc)
    created = await client.post("/v1/activities", json={
        "title": "公開活動出席",
        "description": "社員權限隔離測試",
        "location": "合作社門口",
        "starts_at": (now + timedelta(days=3)).isoformat(),
        "ends_at": (now + timedelta(days=3, hours=2)).isoformat(),
        "registration_deadline": (now + timedelta(days=2)).isoformat(),
        "capacity": 2,
        "waitlist_enabled": True,
    }, headers=auth_headers(v2_context["member_a"]))
    activity_id = created.json()["id"]
    await client.post(f"/v1/admin/activities/{activity_id}/approve", json={}, headers=auth_headers(admin))
    for user_key in ("applicant", "member_a"):
        user = v2_context[user_key]
        registration = await client.post(f"/v1/activities/{activity_id}/register", headers=auth_headers(user))
        registration_id = registration.json()["id"]
        attended = await client.post(f"/v1/admin/activities/{activity_id}/registrations/{registration_id}/attended", headers=auth_headers(admin))
        assert attended.status_code == 200
        assert attended.json()["status"] == "attended"
        points = (await v2_context["session"].scalars(select(PointTransaction).join(PointAccount).where(PointAccount.user_id == user.id))).all()
        assert sum(item.amount for item in points) == (5 if user_key == "member_a" else 0)
    assert (await client.get("/v1/member-proposals", headers=auth_headers(v2_context["applicant"]))).status_code == 403
