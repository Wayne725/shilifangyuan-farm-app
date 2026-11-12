import csv
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
from sqlalchemy import select

from app.integrations.pii_crypto import pii_cipher_from_settings
from app.member_claims import attach_roster_membership, roster_aad
from app.models import (
    Activity, ActivityRegistration, ActivityRegistrationStatus, ActivityStatus, AdminAudit,
    Invoice, InvoiceStatus, MealEventStatus, MealScheduleTemplate, MeetingCategory, MeetingResolution,
    MemberProfile, MemberRosterEntry, Membership, MembershipStatus, Notification, OutboxEvent, Product, User, UserRole,
)
from app.routers.admin_users import admin_users_router
from app.routers.auth import auth_router
from app.routers.catalog import catalog_router
from app.routers.community import community_router
from app.routers.cooperative import cooperative_router
from app.routers.meals import meals_router
from app.routers.member_reports import member_reports_router
from app.routers.membership import membership_router
from tests.support import api_test_context, auth_headers, fast_password_hash, make_test_settings
from tests.test_meal_booking_policy import booking_context


@pytest.fixture
async def editing_context(database_session):
    session = database_session
    settings = make_test_settings()
    admin = User(email="edit-admin@example.com", password_hash="test", display_name="隔離管理員", user_role=UserRole.ADMIN)
    buyer = User(email="edit-buyer@example.com", password_hash=fast_password_hash("SyntheticPassword123!"),
                 display_name="原姓名", email_verified_at=datetime.now(timezone.utc))
    session.add_all([admin, buyer])
    await session.commit()
    routers = [auth_router, admin_users_router, catalog_router, meals_router,
               membership_router, member_reports_router, cooperative_router, community_router]
    async with api_test_context(session, routers, settings=settings) as client:
        yield session, client, admin, buyer, settings


async def _audit(session, action):
    return await session.scalar(select(AdminAudit).where(AdminAudit.action == action)
        .order_by(AdminAudit.created_at.desc()).limit(1))


async def test_meal_edit_affects_new_order_with_confirmation_and_preserves_original(booking_context):
    client, session, buyer, admin, event, order_body = booking_context
    order_path = f"/v1/meal-events/{event.id}/orders"
    first = await client.post(order_path, json=order_body, headers=auth_headers(buyer))
    assert first.status_code == 201, first.text
    meals = await client.get("/v1/admin/meals", headers=auth_headers(admin))
    meal = meals.json()[0]
    template = MealScheduleTemplate(created_by_id=admin.id, title="原價排程", location="合作社", meal_period="lunch",
        pickup_start_time="11:00", pickup_end_time="13:00", cutoff_time="20:00", weekdays=[0],
        offerings=[{"meal_id": meal["id"], "price": 100, "capacity": 20, "position": 0}])
    session.add(template)
    await session.commit()
    path = f"/v1/admin/meals/{meal['id']}"
    preview = await client.get(f"{path}/edit-impact", headers=auth_headers(admin))
    assert preview.json() == [{"id": event.id, "title": event.title}]
    body = {"name": "新便當", "description": "新說明", "price": 145, "tax_type": "tax_exempt",
            "is_active": True, "expected_updated_at": meal["updated_at"]}
    assert (await client.patch(path, json=body, headers=auth_headers(buyer))).status_code == 403
    assert (await client.patch(path, json=body, headers=auth_headers(admin))).status_code == 409
    response = await client.patch(path, json={**body, "confirmed_event_ids": [event.id]}, headers=auth_headers(admin))
    assert response.status_code == 200, response.text
    stale = await client.post(order_path, json={**order_body, "expected_total": 100}, headers=auth_headers(buyer))
    assert stale.status_code == 409 and "價格" in stale.json()["detail"]
    second = await client.post(order_path, json=order_body, headers=auth_headers(buyer))
    assert second.status_code == 201, second.text
    original = (await client.get(f"/v1/meal-orders/{first.json()['id']}", headers=auth_headers(buyer))).json()
    assert (original["items"][0]["meal_name"], original["amount_total"]) == ("半小時便當", 100)
    assert (second.json()["items"][0]["meal_name"], second.json()["amount_total"]) == ("新便當", 145)
    assert second.json()["items"][0]["tax_type"] == "tax_exempt"
    assert (await _audit(session, "meal.details_update")).data["before"]["price"] == 100
    assert (await _audit(session, "meal_offering.price_update")).data["after"]["price"] == 145
    assert template.offerings[0]["price"] == 145
    assert (await _audit(session, "meal_schedule.price_update")).data["before"]["offerings"][0]["price"] == 100
    pause_body = {**body, "is_active": False, "expected_updated_at": response.json()["updated_at"]}
    assert (await client.patch(path, json=pause_body, headers=auth_headers(admin))).status_code == 409
    paused = await client.patch(path, json={**pause_body, "confirmed_event_ids": [event.id]}, headers=auth_headers(admin))
    assert paused.status_code == 200, paused.text
    assert (await client.post(order_path, json=order_body, headers=auth_headers(buyer))).status_code == 409
    detail = (await client.get(f"/v1/meal-events/{event.id}")).json()
    assert detail["offerings"][0]["is_active"] is False
    resumed = await client.patch(path, json={**body,
        "expected_updated_at": paused.json()["updated_at"]}, headers=auth_headers(admin))
    assert resumed.status_code == 200, resumed.text


async def test_meal_text_update_saves_without_impact_confirmation(booking_context):
    client, _, _, admin, event, _ = booking_context
    headers = auth_headers(admin)
    meal = (await client.get("/v1/admin/meals", headers=headers)).json()[0]
    path = f"/v1/admin/meals/{meal['id']}"
    assert (await client.get(f"{path}/edit-impact", headers=headers)).json() == [{"id": event.id, "title": event.title}]
    body = {key: meal[key] for key in ("name", "description", "price", "tax_type", "is_active")}
    body.update(description="更新餐點說明", expected_updated_at=meal["updated_at"])
    result = await client.patch(path, json=body, headers=headers)
    assert result.status_code == 200 and result.json()["description"] == "更新餐點說明"
    body.update(name="更新餐點名稱", expected_updated_at=result.json()["updated_at"])
    result = await client.patch(path, json=body, headers=headers)
    assert result.status_code == 200 and result.json()["name"] == "更新餐點名稱"
    body.update(tax_type="tax_exempt", expected_updated_at=result.json()["updated_at"])
    assert (await client.patch(path, json=body, headers=headers)).status_code == 409


async def test_account_edit_reuses_checkout_phone_and_revokes_tokens(editing_context):
    session, client, admin, buyer, settings = editing_context
    prior = auth_headers(buyer)
    edited = await client.patch("/v1/auth/profile", headers=prior,
        json={"display_name": "自己改名", "phone": "0912 345 678"})
    assert edited.status_code == 200, edited.text
    assert "no-store" in edited.headers["cache-control"]
    assert (await client.get("/v1/meal-checkout/contact", headers=prior)).json() == {"phone": "0912345678"}
    path = f"/v1/admin/users/{buyer.id}/profile"
    assert (await client.get(path, headers=prior)).status_code == 403
    result = await client.patch(path, headers=auth_headers(admin),
        json={"display_name": "管理員改名", "phone": "0923456789", "is_active": False})
    assert result.status_code == 200, result.text
    assert (await client.get("/v1/auth/me", headers=prior)).status_code == 401
    assert (await client.post("/v1/meal-events/isolated/orders", headers=prior, json={
        "items": [{"offering_id": "isolated", "quantity": 1}], "contact_email": buyer.email,
    })).status_code == 401
    login_body = {"email": buyer.email, "password": "SyntheticPassword123!"}
    assert (await client.post("/v1/auth/login", json=login_body)).status_code == 401
    audit = await _audit(session, "user.profile_update")
    assert audit.actor_id == admin.id and audit.data["before"]["is_active"] is True
    cipher = pii_cipher_from_settings(settings)
    for key, expected in [("before", "0912345678"), ("after", "0923456789")]:
        encrypted = audit.data[key]["phone_encrypted"]
        assert cipher.decrypt_text(encrypted, associated_data=f"meal-contact:{buyer.id}") == expected
    assert (await client.patch(path, headers=auth_headers(admin), json={"is_active": True})).status_code == 200
    assert (await client.get("/v1/auth/me", headers=prior)).status_code == 401
    assert (await client.get("/v1/auth/profile", headers=auth_headers(buyer))).json()["display_name"] == "管理員改名"


async def test_activity_capacity_and_notifications_preserve_registration(editing_context):
    session, client, admin, buyer, _settings = editing_context
    other = User(email="edit-other@example.com", display_name="報名者", password_hash="test")
    starts = datetime.now(timezone.utc) + timedelta(days=7)
    activity = Activity(created_by_id=admin.id, title="舊活動", location="原地點", starts_at=starts,
        ends_at=starts + timedelta(hours=2), registration_deadline=starts - timedelta(days=1), capacity=2,
        status=ActivityStatus.PUBLISHED, registrations=[
            ActivityRegistration(user=buyer, status=ActivityRegistrationStatus.REGISTERED, queue_position=1),
            ActivityRegistration(user=other, status=ActivityRegistrationStatus.REGISTERED, queue_position=2),
        ])
    session.add(activity)
    await session.commit()
    ids = [row.id for row in activity.registrations]
    path = f"/v1/admin/activities/{activity.id}"
    payload = {"title": "新活動", "description": "更正", "location": "新地點", "capacity": 1,
        "starts_at": (starts + timedelta(hours=1)).isoformat(), "ends_at": (starts + timedelta(hours=3)).isoformat(),
        "registration_deadline": activity.registration_deadline.isoformat(), "expected_updated_at": activity.updated_at.isoformat()}
    assert (await client.patch(path, headers=auth_headers(admin), json=payload)).status_code == 409
    result = await client.patch(path, headers=auth_headers(admin), json={**payload, "capacity": 2})
    assert result.status_code == 200, result.text
    public = await client.get(f"/v1/activities/{activity.id}")
    assert public.json()["location"] == "新地點" and public.json()["registration_count"] == 2
    assert activity.starts_at.replace(tzinfo=timezone.utc) == starts + timedelta(hours=1)
    assert [row.id for row in activity.registrations] == ids
    assert len(list(await session.scalars(select(Notification)))) == 2
    assert {row.payload["to_email"] for row in await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "send_email"))} == {buyer.email, other.email}
    assert (await _audit(session, "activity.details_update")).data["before"]["location"] == "原地點"


async def test_meeting_edit_delete_and_resolution_history(editing_context):
    session, client, admin, buyer, _settings = editing_context
    category = MeetingCategory(name="隔離社務會議")
    session.add_all([category, Membership(user=buyer, status=MembershipStatus.ACTIVE)])
    await session.commit()
    headers = auth_headers(admin)
    root = "/v1/admin/meetings"
    body = {"title": "原會議", "category_id": category.id, "location": "原場地",
            "starts_at": "2030-01-15T12:00:00+08:00", "agenda": [{"title": "議案", "reference": "保留附件"}]}
    created = (await client.post(root, json=body, headers=headers)).json()
    path = f"{root}/{created['id']}"
    changed = await client.patch(path, json={**body, "title": "更新會議"}, headers=headers)
    assert changed.status_code == 200 and changed.json()["agenda"] == body["agenda"]
    empty = (await client.post(root, json=body, headers=headers)).json()
    assert (await client.delete(f"{root}/{empty['id']}", headers=headers)).status_code == 204
    assert (await _audit(session, "meeting.delete")).data["before"]["meeting"]["title"] == "原會議"
    record = await client.put(f"{path}/attendance", json={"member_id": buyer.id, "attended": False}, headers=headers)
    assert record.status_code == 200, record.text
    assert (await client.delete(path, headers=headers)).status_code == 409
    original = await client.post(f"{path}/resolutions", json={"title": "原決議", "resolution_text": "原決議文字"}, headers=headers)
    rid = original.json()["id"]
    resolution = f"{path}/resolutions/{rid}"
    assert (await client.patch(resolution, json={"title": "新決議", "resolution_text": "新決議文字"}, headers=headers)).status_code == 200
    assert (await client.post(f"{resolution}/revoke", headers=headers)).status_code == 200
    history = (await client.get(f"{resolution}/history", headers=headers)).json()
    assert any(row["action"] == "meeting_resolution.update" and row["before"]["resolution_text"] == "原決議文字" for row in history)
    assert (await session.get(MeetingResolution, rid)).revoked_at is not None
    meetings = (await client.get("/v1/meetings", headers=auth_headers(buyer))).json()
    assert meetings[0]["title"] == "更新會議" and meetings[0]["resolutions"][0]["revoked_at"]
    assert (await _audit(session, "meeting.update")).actor_id == admin.id


async def test_roster_correction_is_private_and_audited(editing_context):
    session, client, admin, buyer, settings = editing_context
    headers = auth_headers(admin)
    created = await client.post("/v1/admin/member-roster", headers=headers,
        json={"member_number": "M-EDIT", "legal_name": "原名冊", "email": "roster@example.com", "phone": "0912345678"})
    assert created.status_code == 201, created.text
    row = created.json()
    path = f"/v1/admin/member-roster/{row['id']}/contact"
    assert (await client.get(path, headers=auth_headers(buyer))).status_code == 403
    updated = await client.patch(path, headers=headers, json={"legal_name": "名冊新名", "email": "updated@example.com",
        "phone": "0923456789", "address": "隔離地址 100 號", "expected_updated_at": row["updated_at"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["address"] == "隔離地址 100 號" and "no-store" in updated.headers["cache-control"]
    listed = (await client.get("/v1/admin/member-roster", headers=headers)).json()[0]
    assert listed["legal_name"] == "名冊新名" and listed["phone_masked"].endswith("6789")
    assert "address" not in listed and "email" not in listed
    report = (await client.get("/v1/admin/member-register", headers=headers)).json()["items"][0]
    assert (report["legal_name"], report["address"]) == ("名冊新名", "隔離地址 100 號")
    audit = await _audit(session, "member_roster.contact_corrected")
    assert audit.actor_id == admin.id and "隔離地址" not in str(audit.data)
    entry = await session.get(MemberRosterEntry, row["id"])
    assert pii_cipher_from_settings(settings).decrypt_text(entry.address_encrypted, associated_data=roster_aad(entry.id)) == "隔離地址 100 號"


async def test_linked_roster_and_csv_follow_member_contact(editing_context):
    session, client, admin, buyer, settings = editing_context
    headers = auth_headers(admin)
    original = {"member_number": "M-LINKED", "legal_name": "原名冊姓名", "email": "roster@example.com", "phone": "0911111111"}
    created = await client.post("/v1/admin/member-roster", headers=headers, json=original)
    assert created.status_code == 201, created.text
    entry = await session.get(MemberRosterEntry, created.json()["id"])
    cipher = pii_cipher_from_settings(settings)
    entry.address_encrypted = cipher.encrypt_text("匯入地址", associated_data=roster_aad(entry.id))
    profile = {"legal_name": "本人姓名", "phone": "0922222222", "birth_date": "", "address": "本人地址", "emergency_contact": ""}
    session.add(MemberProfile(user_id=buyer.id, consent_version="test", consented_at=datetime.now(timezone.utc), **{
        f"{key}_encrypted": cipher.encrypt_text(value, associated_data=f"member-profile:{buyer.id}") for key, value in profile.items()
    }))
    await attach_roster_membership(session, entry, buyer)
    await session.commit()
    path = f"/v1/admin/member-roster/{entry.id}/contact"
    assert (await client.get(path, headers=headers)).status_code == 409
    blocked = await client.patch(path, headers=headers, json={
        "legal_name": "修改名冊", "email": "edited@example.com", "phone": "0933333333",
        "expected_updated_at": entry.updated_at.replace(tzinfo=timezone.utc).isoformat(),
    })
    assert blocked.status_code == 409 and "社員本人資料" in blocked.json()["detail"]
    report = await client.get("/v1/admin/member-register", headers=headers)
    assert report.json()["items"][0]["phone"] == "0922222222"
    updated = await client.patch("/v1/auth/profile", headers=auth_headers(buyer), json={"phone": "0944444444"})
    assert updated.status_code == 200, updated.text
    report = (await client.get("/v1/admin/member-register", headers=headers)).json()["items"][0]
    assert (report["legal_name"], report["email"], report["phone"], report["address"]) == ("本人姓名", buyer.email, "0944444444", "本人地址")
    roster = (await client.get("/v1/admin/member-roster", headers=headers)).json()[0]
    assert roster["claimed"] and roster["legal_name"] == "本人姓名" and roster["phone_masked"].endswith("4444")
    exported = await client.get("/v1/admin/member-register.csv", headers=headers)
    assert exported.status_code == 200, exported.text
    assert next(csv.DictReader(StringIO(exported.content.decode("utf-8-sig"))))["手機"] == "0944444444"


async def test_product_archive_is_recoverable_without_republishing(editing_context):
    session, client, admin, buyer, _settings = editing_context
    product = Product(slug="editing-archive", name="隔離商品", category="當季蔬果", unit="份",
                      member_price=15, nonmember_price=20, stock_quantity=3)
    session.add(product)
    await session.commit()
    headers = auth_headers(admin)
    path = f"/v1/admin/products/{product.id}/archive"
    assert (await client.patch(path, json={"archived": True}, headers=auth_headers(buyer))).status_code == 403
    archived = await client.patch(path, json={"archived": True}, headers=headers)
    assert archived.status_code == 200, archived.text
    assert (await client.get("/v1/products", headers=headers)).json() == []
    assert (await client.get("/v1/products")).json() == []
    assert (await client.get(f"/v1/products/{product.id}")).status_code == 404
    assert (await client.get("/v1/products?archive=archived", headers=headers)).json()[0]["id"] == product.id
    assert (await client.patch(f"/v1/products/{product.id}", headers=headers, json={"is_active": True})).status_code == 409
    restored = await client.patch(path, json={"archived": False}, headers=headers)
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    assert restored.json()["is_active"] is False
    assert (await _audit(session, "product.archive_update")).data["after"]["archived_at"] is None


@pytest.mark.parametrize("event_status", [MealEventStatus.COMPLETED, MealEventStatus.CANCELLED])
async def test_terminal_meal_event_archive_preserves_order_and_invoice_history(booking_context, event_status):
    client, session, buyer, admin, event, order_body = booking_context
    headers = auth_headers(admin)
    event_url = f"/v1/meal-events/{event.id}"
    admin_url = f"/v1/admin/meal-events/{event.id}"
    archive_url = f"{admin_url}/archive"
    assert (await client.patch(archive_url, headers=headers, json={"archived": True})).status_code == 409

    order = await client.post(f"{event_url}/orders", headers=auth_headers(buyer), json=order_body)
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]
    invoice = Invoice(order_id=order_id, relate_number="ARCHIVEMEAL0001", status=InvoiceStatus.ISSUED,
                      invoice_number="AB12345678", total_amount=order.json()["amount_total"])
    session.add(invoice)
    event.status = event_status
    await session.commit()

    assert (await client.patch(archive_url, headers=auth_headers(buyer), json={"archived": True})).status_code == 403
    archived = await client.patch(archive_url, headers=headers, json={"archived": True})
    assert archived.status_code == 200, archived.text
    archived_at = archived.json()["archived_at"]
    assert archived_at and archived.json()["status"] == event_status.value
    repeated = await client.patch(archive_url, headers=headers, json={"archived": True})
    assert repeated.status_code == 200 and repeated.json()["archived_at"] == archived_at
    events = await client.get("/v1/admin/meal-events", headers=headers)
    assert events.json()[0]["archived_at"] == archived_at
    public_detail = await client.get(event_url)
    assert public_detail.status_code == 200 and "archived_at" not in public_detail.json()
    orders = await client.get(f"{admin_url}/orders", headers=headers)
    assert orders.status_code == 200 and [entry["id"] for entry in orders.json()] == [order_id]
    await session.refresh(invoice)
    assert invoice.order_id == order_id and invoice.invoice_number == "AB12345678"

    restored = await client.patch(archive_url, headers=headers, json={"archived": False})
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None and restored.json()["status"] == event_status.value
    audits = list(await session.scalars(select(AdminAudit).where(
        AdminAudit.aggregate_id == event.id, AdminAudit.action == "meal_event.archive_update",
    ).order_by(AdminAudit.created_at)))
    assert [row.actor_id for row in audits] == [admin.id, admin.id]
    assert [row.data["after"]["archived_at"] is None for row in audits] == [False, True]
