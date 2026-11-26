from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.admin_deletions import router as admin_deletions_router
from app.models import (
    AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealOption, MealOptionGroup, MealPhotoAsset,
    MealScheduleTemplate, PickupLocation, Product, ProfilePhotoAsset, User, UserRole,
)
from tests.support import api_test_context, auth_headers, make_test_settings, tomorrow_meal_timeline
from tests.test_group_checkout_contract import group_checkout, join, join_body  # noqa: F401
from tests.test_meal_booking_policy import booking_context  # noqa: F401


def schedule_template(admin_id: str, meal_id: str, **changes) -> MealScheduleTemplate:
    return MealScheduleTemplate(**{
        "title": "打錯的排程", "location": "清華大學", "meal_period": "lunch", "pickup_start_time": "11:00",
        "pickup_end_time": "13:00", "cutoff_time": "20:00", "cutoff_days_before": 1, "advance_days": 7,
        "weekdays": [0, 1, 2, 3, 4], "enabled": False, "auto_publish": False, "created_by_id": admin_id,
        "offerings": [{"meal_id": meal_id, "price": 100, "capacity": 100000, "position": 0}], **changes,
    })


@pytest.fixture
async def deletion_context(database_session):
    session = database_session
    admin = User(email="delete-admin@example.com", display_name="刪除管理員", password_hash="test", user_role=UserRole.ADMIN)
    member = User(email="delete-member@example.com", display_name="一般會員", password_hash="test")
    session.add_all([admin, member])
    await session.commit()
    async with api_test_context(session, [admin_deletions_router], settings=make_test_settings()) as client:
        yield SimpleNamespace(client=client, session=session, admin=admin,
                              admin_headers=auth_headers(admin), member_headers=auth_headers(member))


async def count(session, model, *conditions) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*conditions))


async def test_unused_master_data_is_deleted_with_audit(deletion_context):
    c = deletion_context
    location = PickupLocation(code="typo-point", name="打錯的點")
    meal = Meal(slug="typo-meal", name="打錯的餐", price=90)
    group = MealOptionGroup(meal=meal, name="主食", options=[MealOption(name="白飯")])
    product = Product(slug="typo-product", name="打錯的商品", category="測試", unit="份",
                      member_price=50, nonmember_price=60, stock_quantity=3)
    menu_meal = Meal(slug="schedule-meal", name="排程用餐", price=100)
    c.session.add_all([location, meal, group, product, menu_meal])
    await c.session.flush()
    template = schedule_template(c.admin.id, menu_meal.id)
    c.session.add(template)
    await c.session.flush()
    event = MealEvent(title="排程產生的場次", location="清華大學", **tomorrow_meal_timeline(datetime.now(timezone.utc)),
                      status=MealEventStatus.DRAFT, created_by_id=c.admin.id, schedule_template_id=template.id,
                      offerings=[MealEventOffering(meal_id=menu_meal.id, price=100, capacity=100000)])
    c.session.add_all([event, MealPhotoAsset(meal_id=meal.id, option_id=group.options[0].id, content=b"x"),
                       ProfilePhotoAsset(product_id=product.id, content=b"y")])
    await c.session.commit()
    paths = {
        "pickup_location": f"/v1/admin/pickup-locations/{location.id}",
        "meal": f"/v1/admin/meals/{meal.id}",
        "product": f"/v1/admin/products/{product.id}",
        "meal_schedule": f"/v1/admin/meal-schedules/{template.id}",
    }
    for path in paths.values():
        assert (await c.client.delete(path, headers=c.member_headers)).status_code == 403
    for kind, path in paths.items():
        response = await c.client.delete(path, headers=c.admin_headers)
        assert response.status_code == 204, (kind, response.text)
        assert (await c.client.delete(path, headers=c.admin_headers)).status_code == 404

    for model in (PickupLocation, MealOptionGroup, MealOption, MealPhotoAsset, Product, ProfilePhotoAsset,
                  MealScheduleTemplate, MealEvent, MealEventOffering):
        assert await count(c.session, model) == 0, model
    assert await count(c.session, Meal) == 1
    audits = {audit.action: audit for audit in await c.session.scalars(select(AdminAudit))}
    assert set(audits) == {f"{kind}.delete" for kind in paths}
    assert audits["product.delete"].data["product"]["name"] == "打錯的商品"
    assert audits["meal_schedule.delete"].data["deleted_event_ids"] == [event.id]
    assert audits["pickup_location.delete"].actor_id == c.admin.id


async def test_meals_locations_and_schedules_in_use_are_kept(booking_context):
    client, session, buyer, admin, event, body = booking_context
    used = PickupLocation(code="used-point", name="已用過的點")
    listed = PickupLocation(code="listed-point", name="排程指定的點")
    menu_only = Meal(slug="menu-only", name="只在排程菜單", price=80)
    session.add_all([used, listed, menu_only])
    await session.flush()
    template = schedule_template(admin.id, menu_only.id, pickup_location_ids=[listed.id])
    session.add(template)
    await session.commit()
    order = await client.post(f"/v1/meal-events/{event.id}/orders", headers=auth_headers(buyer),
                              json={**body, "pickup_location_id": used.id, "client_request_id": str(uuid4())})
    assert order.status_code == 201, order.text
    event.schedule_template_id = template.id
    await session.commit()

    headers = auth_headers(admin)
    async with api_test_context(session, [admin_deletions_router], settings=make_test_settings()) as admin_client:
        cases = [
            (f"/v1/admin/pickup-locations/{used.id}", "已有訂單"),
            (f"/v1/admin/pickup-locations/{listed.id}", "仍指定這個取貨點"),
            (f"/v1/admin/meals/{event.offerings[0].meal_id}", "已排進便當場次"),
            (f"/v1/admin/meals/{menu_only.id}", "排程的菜單"),
            (f"/v1/admin/meal-schedules/{template.id}", "已有訂單"),
        ]
        for path, reason in cases:
            response = await admin_client.delete(path, headers=headers)
            assert response.status_code == 409 and reason in response.json()["detail"], (path, response.text)
    assert await count(session, PickupLocation) == 2
    assert await count(session, Meal) == 2
    assert await count(session, MealScheduleTemplate) == 1
    assert await count(session, MealEvent) == 1
    assert await count(session, AdminAudit, AdminAudit.action.like("%.delete")) == 0


async def test_schedule_with_reserved_meals_is_kept(deletion_context):
    c = deletion_context
    meal = Meal(slug="reserved-meal", name="保留中的餐", price=100)
    c.session.add(meal)
    await c.session.flush()
    template = schedule_template(c.admin.id, meal.id)
    c.session.add(template)
    await c.session.flush()
    c.session.add(MealEvent(title="有保留的場次", location="清華大學", **tomorrow_meal_timeline(datetime.now(timezone.utc)),
                            status=MealEventStatus.PUBLISHED, created_by_id=c.admin.id, schedule_template_id=template.id,
                            offerings=[MealEventOffering(meal_id=meal.id, price=100, capacity=100000, reserved_quantity=1)]))
    await c.session.commit()
    response = await c.client.delete(f"/v1/admin/meal-schedules/{template.id}", headers=c.admin_headers)
    assert response.status_code == 409, response.text
    assert await count(c.session, MealEvent) == 1


async def test_products_in_orders_or_campaigns_are_kept(group_checkout):
    c = group_checkout
    store = PickupLocation(code="store", name="合作社門市")
    c.session.add(store)
    await c.session.commit()
    daily = await c.client.post("/v1/orders", headers=c.buyer_headers[0], json={
        "items": [{"product_id": c.product_ids[1], "quantity": 1}], "contact_email": "daily-buyer@example.com",
        "fulfillment_method": "cooperative_pickup", "pickup_location_id": store.id,
    })
    assert daily.status_code == 201, daily.text
    async with api_test_context(c.session, [admin_deletions_router], settings=make_test_settings()) as admin_client:
        campaign = await admin_client.delete(f"/v1/admin/products/{c.product_ids[0]}", headers=c.admin_headers)
        ordered = await admin_client.delete(f"/v1/admin/products/{c.product_ids[1]}", headers=c.admin_headers)
        location = await admin_client.delete(f"/v1/admin/pickup-locations/{store.id}", headers=c.admin_headers)
    assert [campaign.status_code, ordered.status_code, location.status_code] == [409, 409, 409]
    assert "改用封存" in ordered.json()["detail"]
    assert await count(c.session, Product) == 2
