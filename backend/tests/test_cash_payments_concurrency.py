"""Requires the same disposable local PostgreSQL database as the CI pgtests."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.models import User, UserRole
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, tomorrow_meal_timeline
from tests.test_integrations import payment_settings


pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="Set TEST_POSTGRES_URL to the disposable local shilifangyuan_test database",
)


@pytest.fixture
async def cash_api(postgres_sessions):
    async with postgres_sessions() as session:
        buyer = User(email="cash-pg-buyer@example.com", display_name="現場買家", password_hash="test")
        other = User(email="cash-pg-other@example.com", display_name="另一買家", password_hash="test")
        admin = User(email="cash-pg-admin@example.com", display_name="收款員甲", password_hash="test", user_role=UserRole.ADMIN)
        second_admin = User(email="cash-pg-second-admin@example.com", display_name="收款員乙", password_hash="test", user_role=UserRole.ADMIN)
        session.add_all([buyer, other, admin, second_admin])
        await session.commit()

    async def request(method, path, actor, body=None):
        async with postgres_sessions() as session:
            async with api_test_context(
                session,
                [meals_router, orders_router, payments_router],
                settings=payment_settings(),
            ) as client:
                return await client.request(method, path, headers=auth_headers(actor), json=body)

    meal_response = await request(
        "POST", "/v1/admin/meals", admin,
        {"name": "隔離現場付款便當", "price": 100, "tax_type": "taxable"},
    )
    assert meal_response.status_code == 201, meal_response.text
    now = datetime.now(timezone.utc)
    event_response = await request(
        "POST", "/v1/admin/meal-events", admin,
        {
            "title": "隔離現場付款最後一份",
            "location": "離線測試取餐點",
            "booking_policy": "special",
            "allow_cash_payment": True,
            **{key: value.isoformat() for key, value in tomorrow_meal_timeline(now).items()},
            "offerings": [{"meal_id": meal_response.json()["id"], "price": 100, "capacity": 1}],
        },
    )
    assert event_response.status_code == 201, event_response.text
    event = event_response.json()
    published = await request("POST", f"/v1/admin/meal-events/{event['id']}/publish", admin)
    assert published.status_code == 200, published.text
    return request, buyer, other, admin, second_admin, event


def order_body(event, buyer, payment_method="cash_on_pickup"):
    return {
        "items": [{"offering_id": event["offerings"][0]["id"], "quantity": 1}],
        "contact_phone": "0912345678", "contact_email": buyer.email,
        "payment_method": payment_method,
        "invoice_carrier_type": "cloud",
    }


async def concurrently(*operations):
    start = asyncio.Event()

    async def run(operation):
        await start.wait()
        return await operation

    tasks = [asyncio.create_task(run(operation)) for operation in operations]
    start.set()
    return await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)


@pytest.mark.asyncio
async def test_concurrent_cash_orders_cannot_reserve_the_same_last_meal(cash_api):
    request, buyer, other, admin, _second_admin, event = cash_api
    path = f"/v1/meal-events/{event['id']}/orders"
    responses = await concurrently(
        request("POST", path, buyer, order_body(event, buyer)),
        request("POST", path, other, order_body(event, other)),
    )
    assert sorted(response.status_code for response in responses) == [201, 409], [
        response.text for response in responses
    ]
    updated = await request("GET", f"/v1/meal-events/{event['id']}", admin)
    assert updated.status_code == 200, updated.text
    offering = updated.json()["offerings"][0]
    assert (offering["reserved_quantity"], offering["paid_quantity"], offering["available_quantity"]) == (1, 0, 0)
    accepted = next(response.json() for response in responses if response.status_code == 201)
    assert accepted["payment_status"] == "pending"
    assert accepted["invoice_status"] == "external"


@pytest.mark.asyncio
async def test_cash_order_and_online_checkout_compete_for_one_shared_meal(cash_api):
    request, buyer, other, admin, _second_admin, event = cash_api
    path = f"/v1/meal-events/{event['id']}/orders"
    online_order = await request("POST", path, other, order_body(event, other, "online"))
    assert online_order.status_code == 201, online_order.text

    responses = await concurrently(
        request("POST", path, buyer, order_body(event, buyer)),
        request("POST", f"/v1/orders/{online_order.json()['id']}/payment-attempts", other),
    )
    assert sorted(response.status_code for response in responses) == [201, 409], [
        response.text for response in responses
    ]
    updated = await request("GET", f"/v1/meal-events/{event['id']}", admin)
    assert updated.status_code == 200, updated.text
    offering = updated.json()["offerings"][0]
    assert (offering["reserved_quantity"], offering["paid_quantity"], offering["available_quantity"]) == (1, 0, 0)


@pytest.mark.asyncio
async def test_two_admins_confirming_cash_cannot_double_collect_or_release_capacity(cash_api):
    request, buyer, _other, admin, second_admin, event = cash_api
    created = await request(
        "POST", f"/v1/meal-events/{event['id']}/orders", buyer, order_body(event, buyer),
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    payment_path = f"/v1/orders/{order_id}/admin/cash-payment"
    confirmation = {"amount": 100, "reason": "已核對並實際收訖現金"}
    payments = await concurrently(
        request("POST", payment_path, admin, confirmation),
        request("POST", payment_path, second_admin, confirmation),
    )
    assert [response.status_code for response in payments] == [200, 200], [
        response.text for response in payments
    ]
    successful_payments = [response.json() for response in payments]
    assert {order["payment_status"] for order in successful_payments} == {"paid"}
    assert {order["invoice_status"] for order in successful_payments} == {"external"}
    assert len({order["paid_at"] for order in successful_payments}) == 1
    assert successful_payments[0]["paid_at"] is not None
    updated = await request("GET", f"/v1/meal-events/{event['id']}", admin)
    offering = updated.json()["offerings"][0]
    assert (offering["reserved_quantity"], offering["paid_quantity"], offering["available_quantity"]) == (0, 1, 0)

    reviewed = await request("POST", f"/v1/admin/meal-orders/{order_id}/cancel", admin, {"reason": "電話確認退單"})
    assert reviewed.status_code == 200, reviewed.text
    refund_path = f"/v1/orders/{order_id}/admin/cash-refund"
    refund_confirmation = {"amount": 100, "reason": "已核對並實際退還現金"}
    refunds = await concurrently(
        request("POST", refund_path, admin, refund_confirmation),
        request("POST", refund_path, second_admin, refund_confirmation),
    )
    assert [response.status_code for response in refunds] == [200, 200], [
        response.text for response in refunds
    ]
    assert {response.json()["payment_status"] for response in refunds} == {"refunded"}
    final_order = await request("GET", f"/v1/meal-orders/{order_id}", buyer)
    assert final_order.status_code == 200, final_order.text
    assert final_order.json()["payment_status"] == "refunded"
    assert final_order.json()["pickup_code"] is None
    final_event = await request("GET", f"/v1/meal-events/{event['id']}", admin)
    offering = final_event.json()["offerings"][0]
    assert (offering["reserved_quantity"], offering["paid_quantity"], offering["available_quantity"]) == (0, 0, 1)


@pytest.mark.asyncio
async def test_two_admin_corrections_cannot_duplicate_revenue_or_capacity(cash_api):
    request, buyer, _other, admin, second_admin, event = cash_api
    created = await request("POST", f"/v1/meal-events/{event['id']}/orders", buyer, order_body(event, buyer))
    assert created.status_code == 201, created.text
    order_id = created.json()["id"]
    assert (await request("POST", f"/v1/orders/{order_id}/admin/cash-payment", admin, {"amount": 100, "reason": "已收款"})).status_code == 200
    path = f"/v1/orders/{order_id}/admin/cash-unpaid"
    responses = await concurrently(
        request("POST", path, admin, {"amount": 100, "reason": "誤標"}),
        request("POST", path, second_admin, {"amount": 100, "reason": "誤標"}),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    detail = await request("GET", f"/v1/meal-events/{event['id']}", admin)
    offering = detail.json()["offerings"][0]
    assert (offering["reserved_quantity"], offering["paid_quantity"]) == (1, 0)
