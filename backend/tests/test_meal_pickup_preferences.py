import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import Order, PickupLocation, PrintJob
from app.printing import format_receipt
from app.routers.meal_kitchen import meal_kitchen_router
from app.routers.operations import operations_router
from app.schemas import MealOrderCreate
from tests.support import api_test_context, auth_headers
from tests.test_cash_payments import action, cash_context, create_cash


@pytest.fixture
async def meal_pickup_context(cash_context):
    c = cash_context
    c.location = PickupLocation(code="meal-pickup", name="測試取餐櫃台", address="測試地址 1 號", instructions="請至測試櫃台領餐")
    c.inactive = PickupLocation(code="inactive-pickup", name="停用取餐點", is_active=False)
    c.session.add_all([c.location, c.inactive])
    await c.session.commit()
    return c


def quote_body(c, **extra):
    return {
        "items": [{"offering_id": c.event.offerings[0].id, "quantity": 1}],
        "contact_email": c.buyer.email,
        **extra,
    }


async def test_selected_pickup_and_utensils_survive_location_updates_and_reach_kitchen_receipt(meal_pickup_context):
    c = meal_pickup_context
    c.settings.printer_enabled = True
    body = quote_body(c, pickup_location_id=c.location.id, needs_utensils=True)
    quote = await c.client.post(f"/v1/meal-events/{c.event.id}/quote", json=body, headers=auth_headers(c.buyer))
    assert quote.status_code == 200, quote.text
    quoted = quote.json()
    assert quoted["pickup"]["location_id"] == c.location.id
    assert quoted["pickup"]["location"] == c.location.name
    assert quoted["pickup"]["address"] == c.location.address
    assert quoted["pickup"]["instructions"] == c.location.instructions
    assert quoted["needs_utensils"] is True
    assert await c.session.scalar(select(func.count(Order.id))) == 0

    created = await create_cash(c, pickup_location_id=c.location.id, needs_utensils=True)
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    assert (await action(c, order_id)).status_code == 200
    snapshot = {
        "pickup_location_id": c.location.id, "venue_name": c.location.name,
        "pickup_address": c.location.address, "pickup_instructions": c.location.instructions,
        "needs_utensils": True,
    }
    c.location.name = "變更後取餐點"
    c.location.address = "變更後地址"
    c.location.instructions = "變更後說明"
    c.location.is_active = False
    c.event.location = "變更後場次地點"
    await c.session.commit()

    detail = await c.client.get(f"/v1/meal-orders/{order_id}", headers=auth_headers(c.buyer))
    assert detail.status_code == 200, detail.text
    assert {key: detail.json()[key] for key in snapshot} == snapshot
    assert detail.json()["pickup_at"] == quoted["pickup_at"]
    assert detail.json()["pickup_code"]
    admin_orders = await c.client.get("/v1/admin/meal-orders", headers=auth_headers(c.admin))
    assert {key: admin_orders.json()["items"][0]["order"][key] for key in snapshot} == snapshot

    async with api_test_context(c.session, [meal_kitchen_router], settings=c.settings) as client:
        ticket = await client.get(f"/v1/admin/meal-kitchen/orders/{order_id}/ticket", headers=auth_headers(c.admin))
    assert ticket.status_code == 200, ticket.text
    assert ticket.json()["location"] == snapshot["venue_name"]
    for key in ["pickup_location_id", "pickup_address", "pickup_instructions", "needs_utensils"]:
        assert ticket.json()[key] == snapshot[key]
    job = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == order_id))
    assert job.snapshot["location"] == snapshot["venue_name"]
    assert job.snapshot["pickup_address"] == snapshot["pickup_address"]
    assert job.snapshot["needs_utensils"] is True
    receipt = format_receipt(job.snapshot)
    assert "餐具：需要提供" in receipt
    assert snapshot["venue_name"] in receipt and snapshot["pickup_address"] in receipt


async def test_available_meal_pickup_locations_share_daily_product_list(meal_pickup_context):
    c = meal_pickup_context
    async with api_test_context(c.session, [operations_router], settings=c.settings) as client:
        response = await client.get("/v1/pickup-locations")
    assert response.status_code == 200
    assert [location["id"] for location in response.json()] == [c.location.id]


@pytest.mark.parametrize("operation", ["quote", "orders"])
@pytest.mark.parametrize("location", ["inactive", "missing"])
async def test_unavailable_pickup_is_rejected_without_order_or_stock_change(meal_pickup_context, operation, location):
    c = meal_pickup_context
    pickup_location_id = c.inactive.id if location == "inactive" else str(uuid4())
    response = await c.client.post(
        f"/v1/meal-events/{c.event.id}/{operation}", headers=auth_headers(c.buyer),
        json=quote_body(c, pickup_location_id=pickup_location_id, needs_utensils=True),
    )
    assert response.status_code == 422, response.text
    assert "取餐點" in response.json()["detail"]
    assert await c.session.scalar(select(func.count(Order.id))) == 0
    assert c.event.offerings[0].reserved_quantity == 0


async def test_no_pickup_selection_preserves_legacy_location_and_defaults_no_utensils(cash_context):
    c = cash_context
    c.settings.printer_enabled = True
    response = await create_cash(c)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["venue_name"] == c.event.location
    assert order["pickup_location_id"] is None
    assert order["pickup_address"] == order["pickup_instructions"] == ""
    assert order["needs_utensils"] is False
    job = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == order["id"]))
    assert "餐具：不需要" in format_receipt(job.snapshot)


@pytest.mark.parametrize("changed", [{"needs_utensils": False}, {"pickup_location_id": None}])
async def test_order_replay_cannot_change_pickup_or_utensils(meal_pickup_context, changed):
    c = meal_pickup_context
    body = {"client_request_id": str(uuid4()), "pickup_location_id": c.location.id, "needs_utensils": True}
    first = await create_cash(c, **body)
    repeat = await create_cash(c, **body)
    mismatch = await create_cash(c, **(body | changed))
    assert first.status_code == repeat.status_code == 201
    assert first.json()["id"] == repeat.json()["id"]
    assert mismatch.status_code == 409
    assert await c.session.scalar(select(func.count(Order.id))) == 1


async def test_location_disabled_after_quote_requires_reselection(meal_pickup_context):
    c = meal_pickup_context
    response = await c.client.post(f"/v1/meal-events/{c.event.id}/quote", json=quote_body(c, pickup_location_id=c.location.id))
    assert response.status_code == 200
    c.location.is_active = False
    await c.session.commit()
    created = await create_cash(c, pickup_location_id=c.location.id)
    assert created.status_code == 422
    assert await c.session.scalar(select(func.count(Order.id))) == 0


async def test_legacy_submission_can_be_replayed_after_preferences_are_added(cash_context):
    c = cash_context
    request_id = str(uuid4())
    response = await create_cash(c, client_request_id=request_id)
    assert response.status_code == 201, response.text
    order = await c.session.get(Order, response.json()["id"])
    old_body = MealOrderCreate(**quote_body(c, payment_method="cash_on_pickup")).model_dump(
        mode="json", exclude={"client_request_id", "pickup_location_id", "needs_utensils"},
    )
    order.request_fingerprint = hashlib.sha256(json.dumps(
        {"event_id": c.event.id, "body": old_body}, sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    await c.session.commit()
    repeat = await create_cash(c, client_request_id=request_id, pickup_location_id=None, needs_utensils=False)
    assert repeat.status_code == 201, repeat.text
    assert repeat.json()["id"] == response.json()["id"]
    assert await c.session.scalar(select(func.count(Order.id))) == 1
