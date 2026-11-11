from datetime import datetime, timedelta

import time_machine
from sqlalchemy import func, select

from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.meal_booking import TAIPEI
from app.models import AdminAudit, InventoryReservation, MealEventOffering, Order, OutboxEvent
from tests.support import auth_headers
from tests.test_v2_payment_jobs import fake_payment, payment_settings, successful_callback
from tests.test_workday_meal_booking import FRIDAY, create_event, event_body, workday_context


def capacity_path(event, offering_id=None):
    offering_id = offering_id or event["offerings"][0]["id"]
    return f"/v1/admin/meal-events/{event['id']}/offerings/{offering_id}/capacity"


async def test_capacity_increase_preserves_paid_and_held_quantities_for_paused_event(workday_context, fake_payment):
    client, session, admin, buyer, _meal = workday_context
    event, body = await create_event(workday_context)
    other, _ = await create_event(workday_context, "2026-11-10", booking_policy="special")
    order_path = f"/v1/meal-events/{event['id']}/orders"
    admin_headers, buyer_headers = auth_headers(admin), auth_headers(buyer)
    paid = await client.post(order_path, json=body, headers=buyer_headers)
    assert paid.status_code == 201, paid.text
    paid_attempt = await create_payment_attempt(session, paid.json()["id"], buyer, payment_settings())
    applied = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback(
        "meal-capacity-paid", successful_callback(paid_attempt, FRIDAY, "MEAL-CAPACITY-PAID"),
    )
    assert applied == "paid"
    held = await client.post(order_path, json={
        **body, "items": [{**body["items"][0], "quantity": 2}],
    }, headers=buyer_headers)
    assert held.status_code == 201, held.text
    held_attempt = await create_payment_attempt(session, held.json()["id"], buyer, payment_settings())

    paused = await client.patch(f"/v1/admin/meal-events/{event['id']}/ordering-pause",
                                json={"paused": True, "expected_version": 0}, headers=admin_headers)
    assert paused.status_code == 200, paused.text
    orders = select(Order.id, Order.payment_status, Order.amount_total).order_by(Order.id)
    reservations = select(
        InventoryReservation.id, InventoryReservation.quantity, InventoryReservation.status,
        InventoryReservation.expires_at,
    ).order_by(InventoryReservation.id)
    original_orders = (await session.execute(orders)).all()
    original_reservations = (await session.execute(reservations)).all()
    original_jobs = await session.scalar(select(func.count(OutboxEvent.id)))
    data = {"expected_capacity": 50, "capacity": 65, "reason": "備料已確認"}
    path = capacity_path(event)

    for headers, code in [({}, 401), (buyer_headers, 403)]:
        denied = await client.patch(path, json=data, headers=headers)
        assert denied.status_code == code, denied.text
    foreign = await client.patch(capacity_path(event, other["offerings"][0]["id"]), json=data, headers=admin_headers)
    assert foreign.status_code == 404, foreign.text
    for invalid in (49, 50, 100001, "65"):
        denied = await client.patch(path, json={**data, "capacity": invalid}, headers=admin_headers)
        assert denied.status_code == 422, denied.text

    increased = await client.patch(path, json=data, headers=admin_headers)
    assert increased.status_code == 200, increased.text
    result = increased.json()
    assert result["status"] == "published"
    assert result["ordering_paused"] is True and result["ordering_pause_version"] == 1
    offering = result["offerings"][0]
    assert [offering[key] for key in ("price", "capacity", "paid_quantity", "reserved_quantity", "available_quantity")] == [100, 65, 1, 2, 62]
    assert (await session.execute(orders)).all() == original_orders
    assert (await session.execute(reservations)).all() == original_reservations
    assert await session.scalar(select(func.count(OutboxEvent.id))) == original_jobs
    assert (await client.get(f"/payments/{held_attempt.id}/checkout")).status_code == 200
    assert (await client.post(order_path, json=body, headers=buyer_headers)).status_code == 409

    replay = await client.patch(path, json=data, headers=admin_headers)
    assert replay.status_code == 409 and "供應份數已更新" in replay.json()["detail"]
    audits = list(await session.scalars(
        select(AdminAudit).where(AdminAudit.action == "meal_event.offering_capacity_increased"),
    ))
    assert len(audits) == 1
    audit = audits[0]
    assert audit.actor_id == admin.id and audit.aggregate_id == event["id"] and audit.reason == data["reason"]
    assert audit.data == {"offering_id": offering["id"], "before": {"capacity": 50}, "after": {"capacity": 65}}


async def test_capacity_increase_leaves_editable_events_to_existing_edit_flow(workday_context):
    client, session, admin, _buyer, meal = workday_context
    admin_headers = auth_headers(admin)
    payload = {"expected_capacity": 50, "capacity": 65}
    created = await client.post("/v1/admin/meal-events", json=event_body(meal), headers=admin_headers)
    assert created.status_code == 201, created.text
    draft = created.json()
    assert (await client.patch(capacity_path(draft), json=payload, headers=admin_headers)).status_code == 409
    published = await client.post(f"/v1/admin/meal-events/{draft['id']}/publish", headers=admin_headers)
    assert published.status_code == 200, published.text
    rejected = await client.patch(capacity_path(draft), json=payload, headers=admin_headers)
    assert rejected.status_code == 409 and "請編輯場次" in rejected.json()["detail"]

    offering = await session.get(MealEventOffering, draft["offerings"][0]["id"])
    offering.is_active = False
    await session.commit()
    inactive = await client.patch(capacity_path(draft), json=payload, headers=admin_headers)
    assert inactive.status_code == 404, inactive.text


async def test_capacity_increase_after_pickup_open_rechecks_last_slot_after_offering_lock(workday_context, monkeypatch):
    client, session, admin, _buyer, _meal = workday_context
    event, _ = await create_event(
        workday_context, same_day_preparation_minutes=60, ordering_ends_at="2026-11-09T12:00:00+08:00",
    )
    cutoff = datetime(2026, 11, 9, 12, tzinfo=TAIPEI)
    with time_machine.travel(cutoff - timedelta(minutes=30), tick=False):
        headers = auth_headers(admin)
        opened = await client.post(f"/v1/admin/meal-events/{event['id']}/open-pickup", headers=headers)
        assert opened.status_code == 200, opened.text
        increased = await client.patch(capacity_path(event), json={"expected_capacity": 50, "capacity": 60}, headers=headers)
        assert increased.status_code == 200, increased.text
        assert increased.json()["status"] == "pickup_open"
        assert increased.json()["offerings"][0]["capacity"] == 60

    with time_machine.travel(cutoff - timedelta(seconds=1), tick=False) as clock:
        original = session.refresh

        async def cross_cutoff(instance, *args, **options):
            await original(instance, *args, **options)
            if isinstance(instance, MealEventOffering) and options.get("with_for_update"):
                clock.move_to(cutoff)

        monkeypatch.setattr(session, "refresh", cross_cutoff)
        expired = await client.patch(capacity_path(event), json={"expected_capacity": 60, "capacity": 61},
                                     headers=auth_headers(admin))
        assert expired.status_code == 409 and "無可預訂" in expired.json()["detail"]
        offering = await session.get(MealEventOffering, event["offerings"][0]["id"])
        assert offering.capacity == 60
