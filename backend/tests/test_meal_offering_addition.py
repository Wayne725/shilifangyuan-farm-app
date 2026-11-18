from datetime import datetime

import time_machine
from sqlalchemy import select

from app.integrations.payment_service import create_payment_attempt
from app.meal_booking import TAIPEI
from app.models import AdminAudit, InventoryReservation, Meal, MealEvent, MealEventStatus, Order
from tests.support import auth_headers
from tests.test_v2_payment_jobs import fake_payment, payment_settings
from tests.test_workday_meal_booking import FRIDAY, create_event, workday_context


async def test_append_offerings_preserves_orders_reservations_and_the_old_checkout(workday_context, fake_payment):
    client, session, admin, buyer, _meal = workday_context
    addition = Meal(slug="new-lunch", name="追加餐點", price=145)
    session.add(addition)
    await session.commit()
    event, old_cart = await create_event(workday_context)
    admin_headers, buyer_headers = auth_headers(admin), auth_headers(buyer)
    public = f"/v1/meal-events/{event['id']}"
    order = await client.post(f"{public}/orders", headers=buyer_headers, json=old_cart)
    assert order.status_code == 201, order.text
    payment = await create_payment_attempt(session, order.json()["id"], buyer, payment_settings())
    old_event = (await client.get(public)).json()
    old_quote = (await client.post(f"{public}/quote", json=old_cart, headers=buyer_headers)).json()
    orders = select(Order.id, Order.amount_total, Order.payment_status)
    reservations = select(InventoryReservation.id, InventoryReservation.quantity, InventoryReservation.status)
    original_orders = (await session.execute(orders)).all()
    original_reservations = (await session.execute(reservations)).all()
    path = f"/v1/admin/meal-events/{event['id']}/offerings"
    data = {"offerings": [{"meal_id": addition.id, "capacity": 20}], "reason": "當日追加"}

    assert (await client.post(path, json=data)).status_code == 401
    assert (await client.post(path, json=data, headers=buyer_headers)).status_code == 403
    added = await client.post(path, json=data, headers=admin_headers)
    assert added.status_code == 200, added.text
    result = added.json()
    assert result["can_add_offerings"] and not result["can_edit"]
    original, new = result["offerings"]
    assert original == old_event["offerings"][0]
    assert (new["meal_id"], new["price"], new["capacity"], new["position"]) == (addition.id, 145, 20, 1)
    assert (await session.execute(orders)).all() == original_orders
    assert (await session.execute(reservations)).all() == original_reservations
    assert (await client.get(f"/payments/{payment.id}/checkout")).status_code == 200
    assert (await client.post(f"{public}/quote", json=old_cart, headers=buyer_headers)).json() == old_quote
    second = await client.post(f"{public}/orders", headers=buyer_headers, json=old_cart)
    assert second.status_code == 201 and second.json()["amount_total"] == old_quote["amount_total"], second.text
    new_quote = await client.post(f"{public}/quote", headers=buyer_headers, json={
        **old_cart, "items": [{"offering_id": new["id"], "quantity": 1}],
    })
    assert new_quote.status_code == 200 and new_quote.json()["amount_total"] == 145, new_quote.text

    replay = await client.post(path, json=data, headers=admin_headers)
    assert replay.status_code == 200 and replay.json()["offerings"][1] == new, replay.text
    changed = await client.post(path, json={"offerings": [{"meal_id": addition.id, "capacity": 25}]}, headers=admin_headers)
    assert changed.status_code == 409, changed.text
    audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.action == "meal_event.offerings_added")))
    assert len(audits) == 1
    assert audits[0].actor_id == admin.id and audits[0].aggregate_id == event["id"] and audits[0].reason == "當日追加"
    assert audits[0].data == {"added": [{"offering_id": new["id"], "meal_id": addition.id, "price": 145,
                                         "capacity": 20, "position": 1}]}


async def test_append_is_atomic_and_checks_booking_state(workday_context):
    client, session, admin, _buyer, _meal = workday_context
    active = Meal(slug="available-meal", name="新餐點", price=145)
    inactive = Meal(slug="retired-meal", name="停售餐點", price=150, is_active=False)
    session.add_all([active, inactive])
    await session.commit()
    event, _ = await create_event(workday_context)
    headers = auth_headers(admin)
    event_path = f"/v1/admin/meal-events/{event['id']}"
    path = f"{event_path}/offerings"
    request = {"meal_id": active.id, "capacity": 20}
    for rows in ([], [request, request], [{**request, "capacity": True}],
                 [request, {"meal_id": inactive.id, "capacity": 30}],
                 [request, {"meal_id": "missing", "capacity": 30}]):
        response = await client.post(path, json={"offerings": rows}, headers=headers)
        assert response.status_code == 422, response.text
    admin_list = await client.get("/v1/admin/meal-events", headers=headers)
    assert len(admin_list.json()[0]["offerings"]) == 1
    assert admin_list.json()[0]["can_add_offerings"]

    cutoff = datetime(2026, 11, 8, 20, tzinfo=TAIPEI)
    for moment in (datetime(2026, 11, 5, 23, 59, tzinfo=TAIPEI), cutoff):
        with time_machine.travel(moment, tick=False):
            rejected = await client.post(path, json={"offerings": [request]}, headers=auth_headers(admin))
            assert rejected.status_code == 409, rejected.text

    model = await session.get(MealEvent, event["id"])
    for status, archived_at in ((MealEventStatus.DRAFT, None), (MealEventStatus.CANCELLED, None),
                                (MealEventStatus.PUBLISHED, FRIDAY)):
        model.status, model.archived_at = status, archived_at
        await session.commit()
        rejected = await client.post(path, json={"offerings": [request]}, headers=headers)
        assert rejected.status_code == 409, rejected.text
    model.status, model.archived_at = MealEventStatus.PUBLISHED, None
    await session.commit()
    paused = await client.patch(f"{event_path}/ordering-pause", json={"paused": True, "expected_version": 0}, headers=headers)
    assert paused.status_code == 200, paused.text
    result = await client.post(path, json={"offerings": [request]}, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["ordering_paused"] and result.json()["can_add_offerings"] and result.json()["can_edit"]


async def test_append_after_pickup_opens_requires_an_orderable_slot(workday_context):
    client, session, admin, _buyer, _meal = workday_context
    first = Meal(slug="pickup-addition", name="現場追加", price=155)
    second = Meal(slug="pickup-too-late", name="截單後追加", price=160)
    session.add_all([first, second])
    await session.commit()
    event, _ = await create_event(
        workday_context, same_day_preparation_minutes=60, ordering_ends_at="2026-11-09T13:00:00+08:00",
    )
    event_path = f"/v1/admin/meal-events/{event['id']}"
    with time_machine.travel(datetime(2026, 11, 9, 11, 30, tzinfo=TAIPEI), tick=False):
        headers = auth_headers(admin)
        opened = await client.post(f"{event_path}/open-pickup", headers=headers)
        assert opened.status_code == 200, opened.text
        added = await client.post(f"{event_path}/offerings", headers=headers, json={
            "offerings": [{"meal_id": first.id, "capacity": 8}],
        })
        assert added.status_code == 200 and added.json()["status"] == "pickup_open", added.text
    with time_machine.travel(datetime(2026, 11, 9, 12, tzinfo=TAIPEI), tick=False):
        rejected = await client.post(f"{event_path}/offerings", headers=auth_headers(admin), json={
            "offerings": [{"meal_id": second.id, "capacity": 8}],
        })
        assert rejected.status_code == 409 and "無可預訂的取餐時段" in rejected.json()["detail"]
