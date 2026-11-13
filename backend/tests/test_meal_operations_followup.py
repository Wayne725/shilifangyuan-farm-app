from datetime import date, datetime, time, timedelta, timezone

import pytest
import time_machine
from sqlalchemy import select

from app.meal_booking import TAIPEI
from app.models import (
    AdminAudit, FulfillmentMethod, FulfillmentState, MealEvent, MealEventOffering, MealEventStatus,
    FulfillmentStatus, InvoiceBuyerType, MembershipType, Notification, Order, OrderFulfillment, OrderKind, OutboxEvent, PaymentStatus, Refund,
    SalesChannel, User,
)
from app.printing import format_receipt
from app.workday_calendar import calendar_status, is_saturday_catering_day, is_workday
from tests.support import auth_headers
from tests.test_cash_payments import cash_context
from tests.test_meal_cancellation_review import review_context
from tests.test_meal_schedules import schedule_body, schedule_context


@pytest.mark.parametrize("service_date, enabled", [("2026-10-03", True), ("2026-10-10", False), ("2025-02-08", True)])
async def test_automatic_saturday_catering_uses_official_holiday_note(schedule_context, service_date, enabled):
    client, session, admin, buyer, meal = schedule_context
    day = date.fromisoformat(service_date)
    friday = datetime.combine(day - timedelta(days=1), time(10), TAIPEI)
    assert not is_workday(day)
    assert is_saturday_catering_day(day) is enabled
    with time_machine.travel(friday, tick=False):
        headers = auth_headers(admin)
        response = await client.post("/v1/admin/meal-schedules", headers=headers, json=schedule_body(
            meal.id, weekdays=[], saturday_business_only=True, enabled=True, auto_publish=True,
        ))
        assert response.status_code == 201, response.text
        event = await session.scalar(select(MealEvent).where(MealEvent.service_date == day))
        if not enabled:
            assert event is None
            return
        assert event.business_only and event.booking_policy == "special"
        assert event.ordering_starts_at.replace(tzinfo=timezone.utc).astimezone(TAIPEI) == friday.replace(hour=0)
        assert event.ordering_ends_at.replace(tzinfo=timezone.utc).astimezone(TAIPEI) == friday.replace(hour=20)
        detail = await client.get(f"/v1/meal-events/{event.id}")
        assert detail.json()["business_only"]
        response = await client.post(f"/v1/meal-events/{event.id}/quote", headers=auth_headers(buyer), json={
            "items": [{"offering_id": detail.json()["offerings"][0]["id"], "quantity": 1}],
            "contact_email": buyer.email,
        })
        assert response.status_code == 422 and response.json()["detail"] == "此場次僅開放會餐"


def test_remind_in_july_of_last_loaded_year():
    assert calendar_status(date(2027, 6, 30))["update_year"] is None
    assert calendar_status(date(2027, 7, 1))["update_year"] == 2028


async def test_contact_required_encrypted_and_reused_in_both_admin_views(cash_context):
    c = cash_context
    root = f"/v1/meal-events/{c.event.id}/orders"
    body = {"items": [{"offering_id": c.event.offerings[0].id, "quantity": 1}], "contact_email": c.buyer.email}
    headers = auth_headers(c.buyer)
    assert (await c.client.get("/v1/meal-checkout/contact", headers=headers)).json() == {"phone": None}
    for email in (None, "", "invalid-email"):
        invalid_email = await c.client.post(root, headers=headers, json={
            **body, "contact_email": email, "invoice_buyer_email": c.buyer.email, "contact_phone": "0912345678",
        })
        assert invalid_email.status_code == 422, invalid_email.text
    missing = await c.client.post(root, headers=headers, json=body)
    assert missing.status_code == 422 and missing.json()["detail"] == "請填寫聯絡電話"
    created = await c.client.post(root, headers=headers, json={
        **body, "contact_email": "order-contact@example.com", "invoice_buyer_email": c.buyer.email,
        "contact_phone": "0912 345 678",
    })
    assert created.status_code == 201, created.text
    assert created.json()["contact_phone"] is None
    stored = await c.session.get(Order, created.json()["id"])
    assert stored.contact_email == "order-contact@example.com" and stored.invoice_buyer_email == c.buyer.email
    assert c.buyer.meal_contact_phone_encrypted and "0912345678" not in c.buyer.meal_contact_phone_encrypted
    saved = await c.client.get("/v1/meal-checkout/contact", headers=headers)
    assert saved.json() == {"phone": "0912345678"} and "no-store" in saved.headers["cache-control"]
    replay = await c.client.post(root, headers=headers, json=body)
    assert replay.status_code == 201, replay.text
    admin = auth_headers(c.admin)
    roster = await c.client.get(f"/v1/admin/meal-events/{c.event.id}/orders", headers=admin)
    search = await c.client.get("/v1/admin/meal-orders", headers=admin)
    for row in roster.json() + [item["order"] for item in search.json()["items"]]:
        assert row["contact_phone"] == "0912345678"
        expected_email = "order-contact@example.com" if row["id"] == stored.id else c.buyer.email
        assert row["customer_name"] == c.buyer.display_name and row["customer_email"] == expected_email


async def test_buyer_cancellation_is_forbidden_before_and_after_pickup(review_context):
    c = review_context
    c.order.fulfillment.pickup_at = c.now + timedelta(minutes=20)
    await c.session.commit()
    buyer = auth_headers(c.buyer)
    path = f"/v1/meal-orders/{c.order.id}/cancellation-requests"
    for now in (c.now, c.now + timedelta(minutes=20)):
        with time_machine.travel(now, tick=False):
            denied = await c.client.post(path, headers=buyer, json={"reason": "改期"})
            assert denied.status_code == 403, denied.text
            assert denied.json()["detail"] == "退單請致電客服，由管理員協助辦理"
    with time_machine.travel(c.now + timedelta(minutes=20), tick=False):
        admin = await c.client.post(f"/v1/admin/meal-orders/{c.order.id}/cancel", headers=auth_headers(c.admin), json={"reason": "同意退單"})
        assert admin.status_code == 200, admin.text
        assert admin.json()["cancelled_at"]
        assert admin.json()["cancellation_request"]["source"] == "admin"


async def test_disaster_batch_cancels_lunch_and_dinner_and_notifies_every_order(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    day = datetime.now(TAIPEI).date() + timedelta(days=2)
    events = []
    users = [buyer] + [User(email=f"meal-buyer-{n}@example.test", display_name=f"隔離買家 {n}", password_hash="test") for n in range(1, 6)]
    session.add_all(users)
    for event_index, (period, hour) in enumerate([("lunch", 11), ("dinner", 17)]):
        start = datetime.combine(day, time(hour), TAIPEI).astimezone(timezone.utc)
        event = MealEvent(title=period, location="測試", meal_period=period, status=MealEventStatus.PUBLISHED,
            ordering_starts_at=start - timedelta(days=2), ordering_ends_at=start - timedelta(hours=15),
            pickup_starts_at=start, pickup_ends_at=start + timedelta(hours=2), created_by_id=admin.id,
            offerings=[MealEventOffering(meal=meal, price=100, capacity=10)])
        session.add(event)
        events.append(event)
        for n in range(3):
            owner = users[event_index * 3 + n]
            order = Order(order_number=f"M-DISASTER-{event_index}-{n}", user=owner, meal_event=event,
                order_kind=OrderKind.REGULAR, membership_type_snapshot=MembershipType.NONMEMBER,
                fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION, sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
                meal_booking_type="business" if n == 2 else "standard", contact_email=owner.email,
                invoice_buyer_type=InvoiceBuyerType.COMPANY if n == 2 else InvoiceBuyerType.PERSONAL,
                invoice_buyer_tax_id="15989995" if n == 2 else None,
                invoice_buyer_name="隔離公司" if n == 2 else None,
                invoice_handling="pickup_day" if n == 2 else "on_payment",
                invoice_issue_after=start if n == 2 else None,
                amount_total=100, payment_status=PaymentStatus.PAID if n == 0 else PaymentStatus.PENDING,
                paid_at=datetime.now(timezone.utc) if n == 0 else None,
                fulfillment=OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP, status=FulfillmentState.PENDING_CONFIRMATION))
            session.add(order)
    await session.commit()
    admin_headers = auth_headers(admin)
    payload = {"service_date": day.isoformat(), "periods": ["lunch", "dinner"]}
    preview_path = "/v1/admin/meal-disaster/preview"
    cancel_path = "/v1/admin/meal-disaster/cancel"
    assert (await client.post(preview_path, headers=auth_headers(buyer), json=payload)).status_code == 403
    preview = (await client.post(preview_path, headers=admin_headers, json=payload)).json()
    assert len(preview["events"]) == 2 and sum(row["paid_total"] for row in preview["events"]) == 200
    lunch = next(row for row in preview["events"] if row["period"] == "lunch")
    assert lunch["ordering_pause_blocked_reason"] is None and lunch["ordering_paused"] is False
    pause_path = f"/v1/admin/meal-events/{lunch['id']}/ordering-pause"
    pause = await client.patch(pause_path, headers=admin_headers, json={
        "paused": True, "expected_version": lunch["ordering_pause_version"],
    })
    assert pause.status_code == 200, pause.text
    refreshed = (await client.post(preview_path, headers=admin_headers, json=payload)).json()
    paused_lunch = next(row for row in refreshed["events"] if row["period"] == "lunch")
    assert paused_lunch["ordering_paused"] is True and paused_lunch["ordering_pause_version"] == 1
    assert paused_lunch["order_count"] == 3 and paused_lunch["paid_total"] == 100
    assert not next(row for row in refreshed["events"] if row["period"] == "dinner")["ordering_paused"]
    assert not list(await session.scalars(select(Refund)))
    assert not list(await session.scalars(select(Notification)))
    bad = await client.post(cancel_path, headers=admin_headers, json={**payload, "preview_token": "0" * 64})
    assert bad.status_code == 409 and all(event.status == MealEventStatus.PUBLISHED for event in events)
    body = {**payload, "preview_token": refreshed["preview_token"], "announcement": "颱風期間暫停供餐"}
    result = await client.post(cancel_path, headers=admin_headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json() == {"cancelled_event_count": 2, "order_count": 6, "paid_count": 2, "paid_total": 200}
    assert (await client.post(cancel_path, headers=admin_headers, json=body)).status_code == 409
    assert all(event.status == MealEventStatus.CANCELLED for event in events)
    assert len(list(await session.scalars(select(Refund)))) == 2
    notices = list(await session.scalars(select(Notification)))
    assert len(notices) == 6 and sum("已全額退款" in item.body for item in notices) == 2
    assert sum("已取消" in item.body for item in notices) == 6
    emails = list(await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "send_email")))
    assert {email.payload["to_email"] for email in emails} == {owner.email for owner in users}
    assert (await client.get("/v1/meal-announcements")).json() == [{
        "service_date": day.isoformat(), "periods": ["dinner", "lunch"], "message": "颱風期間暫停供餐",
        "cancelled_event_ids": sorted(event.id for event in events),
    }]
    replacement = await create_disaster_meal(client, admin_headers, recovery_draft(
        meal.id, day, location="測試", replaces_event_id=lunch["id"],
    ))
    orders = list(await session.scalars(select(Order)))
    assert len(orders) == 6 and all(order.cancelled_at is not None for order in orders)
    assert all(order.meal_event_id != replacement["id"] for order in orders)
    assert len(list(await session.scalars(select(Refund)))) == 2


def recovery_draft(meal_id, day, *, period="lunch", **overrides):
    pickup_start, pickup_end = (time(11), time(13, 15)) if period == "lunch" else (time(16, 30), time(18, 30))
    previous = day - timedelta(days=1)
    return {
        "title": period, "location": "原取餐區", "booking_policy": "special",
        "ordering_starts_at": datetime.combine(previous, time.min, TAIPEI).isoformat(),
        "ordering_ends_at": datetime.combine(previous, time(20), TAIPEI).isoformat(),
        "pickup_starts_at": datetime.combine(day, pickup_start, TAIPEI).isoformat(),
        "pickup_ends_at": datetime.combine(day, pickup_end, TAIPEI).isoformat(),
        "offerings": [{"meal_id": meal_id, "price": 100, "capacity": 20}],
        **overrides,
    }


async def create_disaster_meal(client, headers, body, *, publish=True):
    response = await client.post("/v1/admin/meal-events", headers=headers, json=body)
    assert response.status_code == 201, response.text
    event = response.json()
    assert event["status"] == "draft"
    if publish:
        response = await client.post(f"/v1/admin/meal-events/{event['id']}/publish", headers=headers)
        assert response.status_code == 200, response.text
    return event


async def stop_disaster_meals(client, headers, day, *, periods=("lunch", "dinner"), announcement=""):
    payload = {"service_date": day.isoformat(), "periods": list(periods)}
    preview = await client.post("/v1/admin/meal-disaster/preview", headers=headers, json=payload)
    assert preview.status_code == 200, preview.text
    result = await client.post("/v1/admin/meal-disaster/cancel", headers=headers, json={
        **payload, "preview_token": preview.json()["preview_token"], "announcement": announcement,
    })
    assert result.status_code == 200, result.text
    return result.json()


async def test_disaster_notice_clears_only_the_restored_cancelled_service(schedule_context):
    client, session, admin, buyer, meal = schedule_context
    day = date(2026, 11, 13)
    with time_machine.travel(datetime(2026, 11, 11, 10, tzinfo=TAIPEI), tick=False):
        headers = auth_headers(admin)
        lunch_body = recovery_draft(meal.id, day)
        dinner_body = recovery_draft(meal.id, day, period="dinner")
        lunch = await create_disaster_meal(client, headers, lunch_body)
        dinner = await create_disaster_meal(client, headers, dinner_body)
        source = await session.get(MealEvent, lunch["id"])
        source.pickup_slot_ends_at = datetime.combine(day, time(12, 45), TAIPEI).astimezone(timezone.utc)
        source.meal_period = "lunch"
        await session.commit()

        rebuild = {**lunch_body, "replaces_event_id": lunch["id"]}
        assert (await client.post("/v1/admin/meal-events", headers=auth_headers(buyer), json=rebuild)).status_code == 403
        assert (await client.post("/v1/admin/meal-events", headers=headers, json=rebuild)).status_code == 409
        assert (await client.post("/v1/admin/meal-events", headers=headers, json={
            **rebuild, "replaces_event_id": "missing-event",
        })).status_code == 404
        assert (await stop_disaster_meals(client, headers, day, announcement="當天停餐"))["cancelled_event_count"] == 2

        both_stopped = [{
            "service_date": day.isoformat(), "periods": ["dinner", "lunch"], "message": "當天停餐",
            "cancelled_event_ids": sorted([lunch["id"], dinner["id"]]),
        }]
        announcements = await client.get("/v1/meal-announcements")
        assert announcements.headers["cache-control"] == "no-store"
        assert announcements.json() == both_stopped

        await create_disaster_meal(client, headers, {**rebuild, "location": "另一取餐區"})
        await create_disaster_meal(client, headers, recovery_draft(meal.id, day + timedelta(days=1), replaces_event_id=lunch["id"]))
        assert (await client.get("/v1/meal-announcements")).json() == both_stopped

        replacement = await create_disaster_meal(client, headers, rebuild, publish=False)
        replacement_id = replacement["id"]
        assert replacement_id != lunch["id"] and replacement["schedule_template_id"] is None
        assert replacement["pickup_slots"] == [
            datetime.combine(day, time(hour, minute), TAIPEI).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            for hour, minute in [(11, 0), (11, 30), (12, 0), (12, 30)]
        ]
        assert (await client.get("/v1/meal-announcements")).json() == both_stopped
        assert (await client.post("/v1/admin/meal-events", headers=headers, json=rebuild)).status_code == 409
        audit = await session.scalar(select(AdminAudit).where(
            AdminAudit.aggregate_id == replacement_id, AdminAudit.action == "meal_event.create",
        ))
        assert audit.data["replaces_event_id"] == lunch["id"]

        result = await client.post(f"/v1/admin/meal-events/{replacement_id}/publish", headers=headers)
        assert result.status_code == 200, result.text
        assert source.status == MealEventStatus.CANCELLED
        assert (await client.get("/v1/meal-announcements")).json() == [{
            "service_date": day.isoformat(), "periods": ["dinner"], "message": "當天停餐",
            "cancelled_event_ids": [dinner["id"]],
        }]
        calendar = await client.get("/v1/meal-events/calendar")
        assert replacement_id in {event["id"] for event in calendar.json()}
        assert lunch["id"] not in {event["id"] for event in calendar.json()}

        await create_disaster_meal(client, headers, {**dinner_body, "replaces_event_id": dinner["id"]})
        assert (await client.get("/v1/meal-announcements")).json() == []


async def test_repeat_disaster_stop_preserves_newest_notice_until_replacement_is_published(schedule_context):
    client, _session, admin, _buyer, meal = schedule_context
    day = date(2026, 11, 13)
    start = datetime(2026, 11, 11, 10, tzinfo=TAIPEI)
    with time_machine.travel(start, tick=False):
        headers = auth_headers(admin)
        body = recovery_draft(meal.id, day)
        lunch = await create_disaster_meal(client, headers, body)
        assert (await stop_disaster_meals(client, headers, day))["cancelled_event_count"] == 1
        assert (await client.get("/v1/meal-announcements")).json() == [{
            "service_date": day.isoformat(), "periods": ["lunch"], "message": "颱風暫停供餐",
            "cancelled_event_ids": [lunch["id"]],
        }]
        replacement = await create_disaster_meal(client, headers, {**body, "replaces_event_id": lunch["id"]})
        assert (await client.get("/v1/meal-announcements")).json() == []

    with time_machine.travel(start + timedelta(minutes=1), tick=False):
        headers = auth_headers(admin)
        await stop_disaster_meals(client, headers, day, announcement="再次停餐")
        assert (await client.get("/v1/meal-announcements")).json() == [{
            "service_date": day.isoformat(), "periods": ["lunch"], "message": "再次停餐",
            "cancelled_event_ids": sorted([lunch["id"], replacement["id"]]),
        }]
        await create_disaster_meal(client, headers, {**body, "replaces_event_id": replacement["id"]})
        assert (await client.get("/v1/meal-announcements")).json() == []


async def test_disaster_notice_exposes_only_previously_public_event_ids(schedule_context):
    client, session, admin, _buyer, meal = schedule_context
    day = date(2026, 11, 13)
    with time_machine.travel(datetime(2026, 11, 11, 10, tzinfo=TAIPEI), tick=False):
        headers = auth_headers(admin)
        lunch = await create_disaster_meal(client, headers, recovery_draft(meal.id, day))
        draft = await create_disaster_meal(client, headers, recovery_draft(meal.id, day, period="dinner"), publish=False)
        assert (await client.get(f"/v1/meal-events/{draft['id']}")).status_code == 404
        assert (await stop_disaster_meals(client, headers, day))["cancelled_event_count"] == 2

        response = await client.get("/v1/meal-announcements")
        notice = {"service_date": day.isoformat(), "periods": ["dinner", "lunch"], "message": "颱風暫停供餐"}
        assert response.json() == [{**notice, "cancelled_event_ids": [lunch["id"]]}]
        assert draft["id"] not in response.text
        audit = await session.scalar(select(AdminAudit).where(AdminAudit.action == "meal_disaster.cancel"))
        assert set(audit.data["event_ids"]) == {lunch["id"], draft["id"]}
        audit.data = {key: value for key, value in audit.data.items() if key != "public_event_ids"}
        await session.commit()
        assert (await client.get("/v1/meal-announcements")).json() == [{**notice, "cancelled_event_ids": []}]


async def test_recovery_draft_requires_a_remaining_bookable_slot(schedule_context):
    client, _session, admin, _buyer, meal = schedule_context
    day = date(2026, 11, 13)
    with time_machine.travel(datetime(2026, 11, 11, 10, tzinfo=TAIPEI), tick=False):
        headers = auth_headers(admin)
        body = recovery_draft(meal.id, day)
        source = await create_disaster_meal(client, headers, body)
        await stop_disaster_meals(client, headers, day)

    with time_machine.travel(datetime.combine(day, time(10), TAIPEI), tick=False):
        headers = auth_headers(admin)
        later_raw_cutoff = {**body, "ordering_ends_at": datetime.combine(day, time(12), TAIPEI).isoformat()}
        await create_disaster_meal(client, headers, later_raw_cutoff, publish=False)
        for prep_minutes in (None, 240):
            response = await client.post("/v1/admin/meal-events", headers=headers, json={
                **later_raw_cutoff, "replaces_event_id": source["id"], "same_day_preparation_minutes": prep_minutes,
            })
            assert response.status_code == 422 and "已無可預訂的時段" in response.json()["detail"]
        await create_disaster_meal(client, headers, {
            **later_raw_cutoff, "replaces_event_id": source["id"], "same_day_preparation_minutes": 30,
        }, publish=False)


@pytest.mark.parametrize("booking_type", ["standard", "business"])
def test_kitchen_receipt_uses_large_customer_name_and_phone(booking_type):
    content = format_receipt({
        "kind": "order", "meal_booking_type": booking_type, "payment_status": "待付款",
        "pickup_code": "123456", "order_number": "M-1", "customer_name": "隔離買家",
        "customer_phone": "0912345678", "items": [], "amount_total": 120, "location": "測試",
        "pickup_at": "2026-11-14 11:00", "payment_method": "線上付款",
    })
    assert "<CB>訂購者：隔離買家</CB>" in content and "0912345678" in content
    assert "取餐碼" not in content and "123456" not in content.replace("0912345678", "")
