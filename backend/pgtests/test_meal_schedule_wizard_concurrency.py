import os

import pytest
import time_machine
from sqlalchemy import select

from app.models import AdminAudit, Meal, MealScheduleTemplate, Order, User, UserRole
from app.routers.meals import meals_router
from pgtests.test_business_meal_concurrency import together
from pgtests.test_transaction_concurrency import postgres_sessions  # noqa: F401
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_schedule_wizard import BASE, FRIDAY, booking_body, preview_and_save, scheduled_events, wizard_entry


pytestmark = pytest.mark.skipif(not os.environ.get("TEST_POSTGRES_URL"), reason="Disposable local PostgreSQL required")


async def test_checkout_and_menu_replacement_share_event_lock(postgres_sessions):
    with time_machine.travel(FRIDAY, tick=False):
        async with postgres_sessions() as session:
            admin = User(email="wizard-pg-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
            buyer = User(email="wizard-pg-buyer@example.com", display_name="預訂買家", password_hash="test")
            meal = Meal(slug="wizard-pg-meal", name="隔離菜單", price=100)
            session.add_all([admin, buyer, meal])
            await session.commit()
            async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
                _, result = await preview_and_save(client, admin, {"schedules": [wizard_entry(meal)]})
                template = result["schedules"][0]
                event = (await scheduled_events(session))[0]
                event_id = event.id
                version = template["updated_at"]
                order = booking_body(event, buyer, business=True)
                draft = {"schedules": [wizard_entry(meal, id=template["id"], expected_updated_at=version,
                                          offerings=[{"meal_id": meal.id, "capacity": 40}])]}
                preview = await client.post(f"{BASE}/preview", json=draft, headers=auth_headers(admin))
                assert preview.status_code == 200, preview.text
                revision = {**draft, "preview_version": preview.json()["preview_version"]}

        async def update_menu():
            async with postgres_sessions() as session:
                async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
                    return await client.post(BASE, json=revision, headers=auth_headers(admin))

        async def checkout():
            async with postgres_sessions() as session:
                async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
                    return await client.post(f"/v1/meal-events/{event_id}/orders", json=order, headers=auth_headers(buyer))

        saved, bought = await together(update_menu(), checkout())
        assert bought.status_code == 201, bought.text
        assert saved.status_code in {200, 409}, saved.text
        async with postgres_sessions() as session:
            current = await session.get(MealScheduleTemplate, template["id"])
            events = await scheduled_events(session)
            ordered_event = next(event for event in events if event.id == event_id)
            records = list(await session.scalars(select(Order).where(Order.meal_event_id == event_id)))
            audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.action == "meal_event.schedule_menu_update")))
            assert len(records) == 1 and records[0].amount_total == 200
            assert ordered_event.offerings[0].reserved_quantity == 2
            expected = 40 if saved.status_code == 200 else 20
            assert current.offerings[0]["capacity"] == expected
            assert {event.offerings[0].capacity for event in events} == {expected}
            assert len(audits) == (len(events) if saved.status_code == 200 else 0)
