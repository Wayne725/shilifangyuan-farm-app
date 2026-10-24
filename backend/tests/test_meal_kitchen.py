from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.meal_kitchen as kitchen
from app.models import AdminAudit, FulfillmentState, MealEventStatus, OrderFulfillment, PaymentStatus
from app.routers.meal_kitchen import meal_kitchen_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_operations import NOW, seed_search_orders


@pytest.fixture
async def kitchen_context(database_session, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(kitchen, "datetime", FixedDateTime)
    c = await seed_search_orders(database_session)
    async with api_test_context(database_session, [meal_kitchen_router], settings=make_test_settings()) as client:
        c.client = client
        yield c


async def read(c, **params):
    response = await c.client.get("/v1/admin/meal-kitchen", params={"service_date": "2030-01-02", **params}, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    assert "private_token" not in response.text and "pickup_code" not in response.text
    return response.json()


async def change(c, status, order_id=None):
    return await c.client.patch(f"/v1/admin/meal-kitchen/orders/{order_id or c.order.id}/status", json={"status": status}, headers=auth_headers(c.admin))


@pytest.mark.asyncio
async def test_list_uses_actual_pickup_time_and_warns_pending_cancellation(kitchen_context):
    c = kitchen_context
    result = await read(c)
    assert result["total"] == 2 and result["meal_quantity"] == 4
    assert [order["id"] for order in result["items"]] == [c.order.id, c.orders["ready"].id]
    assert result["items"][1]["pending_cancellation"]["reason"] == "新申請"
    assert result["items"][0]["pickup_at"].endswith(("Z", "+00:00"))
    assert (await read(c, meal_period="dinner"))["total"] == 2
    assert (await read(c, meal_period="lunch"))["total"] == 0
    first = await read(c, limit=1)
    second = await read(c, limit=1, offset=1)
    assert first["meal_quantity"] == second["meal_quantity"] == 4
    assert first["items"][0]["id"] != second["items"][0]["id"]


@pytest.mark.asyncio
async def test_kitchen_search_and_status_filter_counts_before_pagination(kitchen_context):
    c = kitchen_context
    result = await read(c, search="王小明", limit=1)
    assert result["total"] == 2 and result["meal_quantity"] == 4
    assert len(result["items"]) == 1
    ready = await read(c, search="  搜尋便當  ", fulfillment_status="ready_for_pickup")
    assert ready["total"] == 1 and ready["meal_quantity"] == 2
    assert ready["items"][0]["id"] == c.orders["ready"].id
    assert (await read(c, search="找不到的品項"))["products"] == []
    c.orders["ready"].order_number = "SEARCH_100%"
    await c.session.commit()
    assert (await read(c, search="_100%"))["total"] == 1
    assert (await read(c, search="%"))["total"] == 1
    assert (await read(c, search="SEARCHX100"))["total"] == 0


@pytest.mark.asyncio
async def test_closed_ordering_remains_available_for_preparation_and_printing_before_pickup(kitchen_context):
    c = kitchen_context
    c.event.status = MealEventStatus.ORDERING_CLOSED
    c.event.ordering_ends_at = NOW - timedelta(seconds=1)
    c.event.pickup_starts_at = NOW + timedelta(hours=1)
    c.event.pickup_ends_at = NOW + timedelta(hours=3)
    c.order.fulfillment.pickup_at = c.event.pickup_starts_at
    c.order.fulfillment.status = FulfillmentState.PENDING_CONFIRMATION
    c.orders["ready"].fulfillment.pickup_at = c.event.pickup_starts_at + timedelta(minutes=30)
    await c.session.commit()
    result = await read(c)
    assert result["total"] == 3
    assert {order["id"] for order in result["items"]} == {c.order.id, c.orders["ready"].id, c.orders["legacy"].id}
    assert (await change(c, "preparing")).status_code == 200
    ticket = await c.client.get(f"/v1/admin/meal-kitchen/orders/{c.order.id}/ticket", headers=auth_headers(c.admin))
    assert ticket.status_code == 200, ticket.text
    assert ticket.json()["fulfillment_status"] == "preparing"
    assert c.event.status == MealEventStatus.ORDERING_CLOSED


@pytest.mark.asyncio
async def test_legacy_pickup_without_selected_slot_is_explicit_and_not_guessed(kitchen_context):
    c = kitchen_context
    c.event.meal_period = None
    await c.session.commit()
    result = await read(c, service_date="2030-01-01", meal_period="unspecified")
    assert result["total"] == 1
    assert result["items"][0]["id"] == c.orders["legacy"].id
    assert result["items"][0]["pickup_time_confirmed"] is False
    assert (await read(c, service_date="2030-01-01", meal_period="dinner"))["total"] == 0


@pytest.mark.asyncio
async def test_completed_unpaid_and_cancelled_orders_never_enter_the_kitchen_queue(kitchen_context):
    c = kitchen_context
    c.order.fulfillment.status = FulfillmentState.PICKED_UP
    c.orders["ready"].cancelled_at = NOW
    await c.session.commit()
    assert (await read(c))["total"] == 0
    c.order.fulfillment.status = FulfillmentState.PREPARING
    c.order.payment_status = PaymentStatus.REFUND_PENDING
    c.orders["ready"].cancelled_at = None
    c.orders["ready"].fulfillment.status = FulfillmentState.NO_SHOW
    await c.session.commit()
    assert (await read(c))["total"] == 0


@pytest.mark.asyncio
async def test_forward_status_changes_are_idempotent_and_preserve_stock_and_redeem_fields(kitchen_context):
    c = kitchen_context
    c.order.fulfillment.status = FulfillmentState.PENDING_CONFIRMATION
    await c.session.commit()
    before = (c.offering.reserved_quantity, c.offering.paid_quantity, c.order.fulfillment.pickup_code, c.order.fulfillment.fulfilled_at)
    assert (await change(c, "ready_for_pickup")).status_code == 409
    assert (await change(c, "preparing")).status_code == 200
    assert (await change(c, "preparing")).status_code == 200
    assert await c.session.scalar(select(func.count(AdminAudit.id))) == 1
    assert (await change(c, "ready_for_pickup")).status_code == 200
    assert (await change(c, "preparing")).status_code == 409
    assert await c.session.scalar(select(func.count(AdminAudit.id))) == 2
    assert (c.offering.reserved_quantity, c.offering.paid_quantity, c.order.fulfillment.pickup_code, c.order.fulfillment.fulfilled_at) == before


@pytest.mark.parametrize("condition", ["picked_up", "cancelled", "unpaid", "no_show", "event_cancelled", "event_ended", "missing_fulfillment"])
@pytest.mark.asyncio
async def test_stale_status_updates_and_ticket_requests_cannot_revive_invalid_orders(kitchen_context, condition):
    c = kitchen_context
    if condition in {"picked_up", "no_show", "cancelled"}:
        c.order.fulfillment.status = FulfillmentState(condition)
    elif condition == "unpaid":
        c.order.payment_status = PaymentStatus.PENDING
    elif condition == "event_cancelled":
        c.event.status = MealEventStatus.CANCELLED
    elif condition == "event_ended":
        c.event.ordering_ends_at = NOW - timedelta(minutes=1)
        c.event.pickup_ends_at = NOW
    else:
        await c.session.delete(c.order.fulfillment)
        c.order.fulfillment = None
    await c.session.commit()
    result = await change(c, "ready_for_pickup")
    assert result.status_code == 409, result.text
    ticket = await c.client.get(f"/v1/admin/meal-kitchen/orders/{c.order.id}/ticket", headers=auth_headers(c.admin))
    assert ticket.status_code == 409
    assert await c.session.scalar(select(func.count(AdminAudit.id))) == 0


@pytest.mark.asyncio
async def test_ticket_preview_is_read_only_and_admin_only(kitchen_context):
    c = kitchen_context
    result = await c.client.get(f"/v1/admin/meal-kitchen/orders/{c.orders['ready'].id}/ticket", headers=auth_headers(c.admin))
    assert result.status_code == 200
    assert result.json()["pending_cancellation"]["reason"] == "新申請"
    assert await c.session.scalar(select(func.count(AdminAudit.id))) == 0
    unauthorized = await c.client.get("/v1/admin/meal-kitchen", params={"service_date": "2030-01-02"}, headers=auth_headers(c.buyer))
    assert unauthorized.status_code == 403
    unauthorized = await c.client.patch(f"/v1/admin/meal-kitchen/orders/{c.order.id}/status", json={"status": "ready_for_pickup"}, headers=auth_headers(c.buyer))
    assert unauthorized.status_code == 403


@pytest.mark.parametrize("params", [{"service_date": "invalid"}, {"meal_period": "other"}, {"limit": 101}, {"offset": -1}, {"search": "x" * 101}, {"fulfillment_status": "invalid"}])
@pytest.mark.asyncio
async def test_kitchen_rejects_invalid_filters(kitchen_context, params):
    c = kitchen_context
    response = await c.client.get("/v1/admin/meal-kitchen", params={"service_date": "2030-01-02", **params}, headers=auth_headers(c.admin))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ordinary_order_and_unknown_status_are_rejected(kitchen_context):
    c = kitchen_context
    assert (await change(c, "preparing", c.orders["ordinary"].id)).status_code == 404
    assert (await change(c, "picked_up")).status_code == 422
