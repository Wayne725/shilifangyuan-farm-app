from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.admin_deletions import (
    delete_unused_meal, delete_unused_meal_schedule, delete_unused_pickup_location, delete_unused_product,
)
from app.models import (
    AdminAudit, Meal, MealEvent, MealEventOffering, MealEventStatus, MealOption, MealOptionGroup, MealPhotoAsset,
    MealScheduleTemplate, PickupLocation, Product, ProfilePhotoAsset, User, UserRole,
)
from pgtests.test_transaction_concurrency import postgres_sessions  # noqa: F401
from tests.support import make_test_settings, tomorrow_meal_timeline


async def test_unused_records_delete_cleanly_under_postgres_foreign_keys(postgres_sessions):
    async with postgres_sessions() as session:
        admin = User(email="pg-delete-admin@example.test", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        location = PickupLocation(code="pg-typo", name="打錯的點")
        meal = Meal(slug="pg-typo-meal", name="打錯的餐", price=90)
        group = MealOptionGroup(meal=meal, name="主食", options=[MealOption(name="白飯")])
        menu_meal = Meal(slug="pg-menu-meal", name="排程用餐", price=100)
        product = Product(slug="pg-typo-product", name="打錯的商品", category="測試", unit="份",
                          member_price=50, nonmember_price=60, stock_quantity=1)
        session.add_all([admin, location, meal, group, menu_meal, product])
        await session.flush()
        template = MealScheduleTemplate(
            title="打錯的排程", location="清華大學", meal_period="lunch", pickup_start_time="11:00", pickup_end_time="13:00",
            cutoff_time="20:00", cutoff_days_before=1, advance_days=7, weekdays=[0], enabled=False, auto_publish=False,
            offerings=[{"meal_id": menu_meal.id, "price": 100, "capacity": 100000, "position": 0}], created_by_id=admin.id,
        )
        session.add(template)
        await session.flush()
        session.add_all([
            MealEvent(title="排程場次", location="清華大學", **tomorrow_meal_timeline(datetime.now(timezone.utc)),
                      status=MealEventStatus.DRAFT, created_by_id=admin.id, schedule_template_id=template.id,
                      offerings=[MealEventOffering(meal_id=menu_meal.id, price=100, capacity=100000)]),
            MealPhotoAsset(meal_id=meal.id, option_id=group.options[0].id, content=b"x"),
            ProfilePhotoAsset(product_id=product.id, content=b"y"),
        ])
        await session.commit()
        ids = admin.id, location.id, meal.id, menu_meal.id, template.id, product.id
    admin_id, location_id, meal_id, menu_meal_id, template_id, product_id = ids

    async with postgres_sessions() as session:
        admin = await session.get(User, admin_id)
        with pytest.raises(HTTPException) as protected:
            await delete_unused_meal(menu_meal_id, admin, session)
        assert protected.value.status_code == 409
        await session.rollback()
        admin = await session.get(User, admin_id)
        await delete_unused_meal_schedule(template_id, admin, session)
        await delete_unused_meal(meal_id, admin, session)
        await delete_unused_meal(menu_meal_id, admin, session)
        await delete_unused_pickup_location(location_id, admin, session)
        await delete_unused_product(product_id, admin, session, make_test_settings())

    async with postgres_sessions() as session:
        for model in (Meal, MealOptionGroup, MealOption, MealPhotoAsset, MealScheduleTemplate, MealEvent,
                      MealEventOffering, PickupLocation, Product, ProfilePhotoAsset):
            assert await session.scalar(select(func.count()).select_from(model)) == 0, model
        actions = sorted(await session.scalars(select(AdminAudit.action)))
        assert actions == ["meal.delete", "meal.delete", "meal_schedule.delete", "pickup_location.delete", "product.delete"]
