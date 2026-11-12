from datetime import date, datetime, time, timedelta, timezone

import pytest
import time_machine
from sqlalchemy import select

from app.meal_booking import TAIPEI
from app.models import (
    FulfillmentMethod, FulfillmentState, MealEvent, MealEventOffering, MealEventStatus,
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
    day = datetime.now(TAIPEI).date() + timedelta(days=1)
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
    bad = await client.post(cancel_path, headers=admin_headers, json={**payload, "preview_token": "0" * 64})
    assert bad.status_code == 409 and all(event.status == MealEventStatus.PUBLISHED for event in events)
    body = {**payload, "preview_token": preview["preview_token"], "announcement": "颱風期間暫停供餐"}
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
    }]


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
