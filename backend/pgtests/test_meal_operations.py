import pytest
from sqlalchemy import select

import app.meal_operations as operations
from app.models import Order, OutboxEvent, OutboxStatus
from app.routers.meals import meals_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_operations import NOW, ids, seed_search_orders


@pytest.mark.asyncio
async def test_postgres_clock_json_failure_filters_and_summary_agree(postgres_sessions, monkeypatch):
    from datetime import datetime

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(operations, "datetime", FixedDateTime)
    async with postgres_sessions() as session:
        c = await seed_search_orders(session)
        async with api_test_context(session, [meals_router], settings=make_test_settings()) as client:
            result = await client.get("/v1/admin/meal-orders", params={"pickup_time": "00:05", "date_from": "2030-01-02", "date_to": "2030-01-02"}, headers=auth_headers(c.admin))
            assert result.status_code == 200, result.text
            assert ids(result.json()) == {c.order.id}
            result = await client.get("/v1/admin/meal-orders", params={"work_queue": "invoice_attention"}, headers=auth_headers(c.admin))
            assert result.status_code == 200, result.text
            assert ids(result.json()) == {c.order.id, c.orders["legacy"].id}
            summary = await client.get("/v1/admin/meal-operations-summary", headers=auth_headers(c.admin))
            assert summary.status_code == 200, summary.text
            assert summary.json() == {"today": "2030-01-02", "pending_cancellation": 1, "refund_attention": 2, "invoice_attention": 2, "today_pickup": 2}
            # Querying the work queues does not mutate order or refund state.
            stored = await session.scalar(select(Order).where(Order.id == c.order.id))
            assert stored.cancelled_at is None
            session.add(OutboxEvent(aggregate_type="order", aggregate_id="adjustment-task", event_type="invoice.adjustment_required", status=OutboxStatus.FAILED, payload={"order_id": c.orders["ready"].id}))
            await session.commit()
            result = await client.get("/v1/admin/meal-orders", params={"work_queue": "invoice_attention"}, headers=auth_headers(c.admin))
            assert result.status_code == 200, result.text
            assert ids(result.json()) == {c.order.id, c.orders["legacy"].id, c.orders["ready"].id}
            summary = await client.get("/v1/admin/meal-operations-summary", headers=auth_headers(c.admin))
            assert summary.json()["invoice_attention"] == 3
