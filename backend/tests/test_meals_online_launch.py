from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.auth import create_token
from app.models import Meal, MealEvent, MealEventOffering, MealEventStatus, PaymentAttempt, Refund, TaxType, User, UserRole
from app.routers.meals import meals_router
from app.routers.payments import payments_router
from app.sales_scope import DEMO_MEAL_EVENT_IDS
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meals_production_configuration import meal_production_settings
from tests.test_payment_return import payment_settings, successful_result


async def seed_meal_event(session, *, event_id=None):
    now = datetime.now(timezone.utc)
    admin = User(email="launch-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="launch-buyer@example.com", display_name="買家", password_hash="test")
    meal = Meal(slug="launch-lunch", name="便當", description="測試", price=120, tax_type=TaxType.TAXABLE)
    session.add_all([admin, buyer, meal])
    await session.flush()
    event = MealEvent(title="線上便當場次", location="合作社",
        ordering_starts_at=now-timedelta(hours=1), ordering_ends_at=now+timedelta(hours=1),
        pickup_starts_at=now+timedelta(hours=2), pickup_ends_at=now+timedelta(hours=3),
        status=MealEventStatus.PUBLISHED, created_by_id=admin.id,
        offerings=[MealEventOffering(meal=meal, price=120, capacity=2)])
    if event_id is not None:
        event.id = event_id
    session.add(event)
    await session.commit()
    return admin, buyer, event


@pytest.mark.asyncio
async def test_redeemed_online_meal_cannot_cancel_refund_or_restore_capacity(database_session):
    session = database_session
    admin, buyer, event = await seed_meal_event(session)
    await session.refresh(event, ["offerings"])
    event_id, offering_id = event.id, event.offerings[0].id
    buyer_headers, admin_headers = auth_headers(buyer), auth_headers(admin)
    async with api_test_context(session, [meals_router, payments_router], settings=payment_settings()) as client:
        created = await client.post(f"/v1/meal-events/{event_id}/orders", headers=buyer_headers, json={
            "items": [{"offering_id": offering_id, "quantity": 1}], "contact_email": buyer.email,
        })
        assert created.status_code == 201, created.text
        order_id = created.json()["id"]
        started = await client.post(f"/v1/orders/{order_id}/payment-attempts", headers=buyer_headers)
        assert started.status_code == 201, started.text
        attempt = await session.get(PaymentAttempt, started.json()["id"])
        paid = await client.post("/webhooks/ecpay/payment", data=successful_result(attempt))
        assert paid.status_code == 200 and paid.text == "1|OK", paid.text
        opened = await client.post(f"/v1/admin/meal-events/{event_id}/open-pickup", headers=admin_headers, json={"reason": "提早完成交付驗收"})
        assert opened.status_code == 200, opened.text
        credential = await client.get(f"/v1/meal-orders/{order_id}/pickup-credential", headers=buyer_headers)
        assert credential.status_code == 200
        redeemed = await client.post(f"/v1/admin/meal-events/{event_id}/redeem", headers=admin_headers, json={"qr_token": credential.json()["qr_token"]})
        assert redeemed.status_code == 200, redeemed.text
        session.expunge_all()
        cancelled = await client.post(f"/v1/meal-orders/{order_id}/cancel", headers=buyer_headers, json={"reason": "已領餐仍嘗試取消"})
        assert cancelled.status_code == 409, cancelled.text
        detail = await client.get(f"/v1/meal-orders/{order_id}", headers=buyer_headers)
        assert detail.json()["payment_status"] == "paid"
        assert detail.json()["fulfillment_status"] == "picked_up"
        assert detail.json()["cancelled_at"] is None
        assert "cancel" not in detail.json()["available_actions"]
        assert await session.scalar(select(func.count(Refund.id)).where(Refund.order_id == order_id)) == 0
        event_read = await client.get(f"/v1/meal-events/{event_id}")
        offering = event_read.json()["offerings"][0]
        assert (offering["reserved_quantity"], offering["paid_quantity"], offering["available_quantity"]) == (0, 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["production", "preview", "development"])
async def test_only_production_public_listing_hides_demo_events_without_deleting_them(database_session, environment):
    session = database_session
    admin, buyer, live = await seed_meal_event(session)
    live_id = live.id
    for demo_id in DEMO_MEAL_EVENT_IDS:
        session.add(MealEvent(id=demo_id, title="展示", location=live.location,
            ordering_starts_at=live.ordering_starts_at, ordering_ends_at=live.ordering_ends_at,
            pickup_starts_at=live.pickup_starts_at, pickup_ends_at=live.pickup_ends_at,
            status=MealEventStatus.PUBLISHED, created_by_id=admin.id))
    await session.commit()
    settings = meal_production_settings() if environment == "production" else make_test_settings(environment=environment)
    admin_token = create_token(admin, "access", timedelta(minutes=5), settings=settings)
    buyer_token = create_token(buyer, "access", timedelta(minutes=5), settings=settings)
    async with api_test_context(session, [meals_router], settings=settings) as client:
        listing = await client.get("/v1/meal-events")
        assert listing.status_code == 200, listing.text
        public_ids = {event["id"] for event in listing.json()}
        assert live_id in public_ids
        assert (public_ids & DEMO_MEAL_EVENT_IDS) == (set() if environment == "production" else DEMO_MEAL_EVENT_IDS)
        admin_list = await client.get("/v1/admin/meal-events", headers={"Authorization": f"Bearer {admin_token}"})
        assert admin_list.status_code == 200
        assert DEMO_MEAL_EVENT_IDS <= {event["id"] for event in admin_list.json()}
        for demo_id in DEMO_MEAL_EVENT_IDS:
            detail = await client.get(f"/v1/meal-events/{demo_id}")
            assert detail.status_code == 200
        if environment == "production":
            await session.refresh(live, ["offerings"])
            blocked = await client.post("/v1/meal-events/meal-event-dinner-demo/orders", headers={"Authorization": f"Bearer {buyer_token}"}, json={
                "items": [{"offering_id": live.offerings[0].id, "quantity": 1}], "contact_email": buyer.email,
            })
            assert blocked.status_code == 409
            assert "展示" in blocked.json()["detail"]
        assert await session.scalar(select(func.count(MealEvent.id))) == 4
