from datetime import datetime, timedelta, timezone

import pytest

import app.meal_operations as operations
from app.models import (
    FulfillmentMethod, FulfillmentState, FulfillmentStatus, Invoice, InvoiceStatus,
    MealCancellationRequest, MealEvent, MealEventStatus, MembershipType, Order,
    OrderFulfillment, OrderItem, OrderKind, OutboxEvent, OutboxStatus, PaymentStatus,
    Refund, RefundStatus, SalesChannel, TaxType,
)
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import seed_cancellation_order


NOW = datetime(2030, 1, 1, 16, 10, tzinfo=timezone.utc)


async def seed_search_orders(session):
    c = await seed_cancellation_order(session)
    c.buyer.display_name = "王小明"
    c.event.ordering_starts_at = NOW - timedelta(hours=1)
    c.event.ordering_ends_at = NOW + timedelta(minutes=30)
    c.event.pickup_starts_at = NOW - timedelta(minutes=20)
    c.event.pickup_ends_at = NOW + timedelta(hours=2)
    c.event.meal_period = "dinner"
    c.order.fulfillment.pickup_at = NOW - timedelta(minutes=5)
    c.order.fulfillment.status = FulfillmentState.PREPARING
    c.order.payment_status = PaymentStatus.PAID
    c.order.invoice_status = InvoiceStatus.FAILED
    c.order.invoice = Invoice(order=c.order, relate_number="SEARCHFIRST", status=InvoiceStatus.FAILED, error_message="查票失敗", provider_response={"private_token": "must-not-be-returned"})
    session.add(c.order.invoice)
    c.orders = {"first": c.order}
    future_event = MealEvent(title="明日午餐", location="測試區", created_by_id=c.admin.id, status=MealEventStatus.PUBLISHED,
        ordering_starts_at=NOW, ordering_ends_at=NOW + timedelta(hours=30),
        pickup_starts_at=NOW + timedelta(hours=36), pickup_ends_at=NOW + timedelta(hours=38), meal_period="lunch")
    session.add(future_event)

    def add(label, pickup_at, *, event=None, payment=PaymentStatus.PENDING, fulfillment=FulfillmentState.PENDING_CONFIRMATION, invoice=InvoiceStatus.NOT_ELIGIBLE, owner=None, number=None):
        order = Order(order_number=number or label.upper(), order_kind=OrderKind.REGULAR,
            sales_channel=SalesChannel.MEAL_PREORDER, fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
            user=owner or c.buyer, meal_event=event or c.event, membership_type_snapshot=MembershipType.NONMEMBER,
            amount_total=200, contact_email=(owner or c.buyer).email, payment_status=payment, invoice_status=invoice,
            fulfillment_status=FulfillmentStatus.PENDING_CONFIRMATION,
            items=[OrderItem(product_name="搜尋便當", unit_label="份", quantity=2, unit_price=100, subtotal=200, tax_type=TaxType.TAXABLE)],
            fulfillment=OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP, status=fulfillment, pickup_at=pickup_at))
        session.add(order)
        c.orders[label] = order
        return order

    ready = add("ready", NOW + timedelta(minutes=20), payment=PaymentStatus.PAID, fulfillment=FulfillmentState.READY_FOR_PICKUP, invoice=InvoiceStatus.ISSUED)
    add("future", NOW + timedelta(hours=36), event=future_event, payment=PaymentStatus.REFUND_PENDING, fulfillment=FulfillmentState.CANCELLED)
    legacy = add("legacy", None, payment=PaymentStatus.PAID, invoice=InvoiceStatus.VOID_PENDING)
    add("literal", NOW + timedelta(minutes=40), number="LITERAL_100%", owner=c.other)
    add("similar", NOW + timedelta(minutes=50), number="LITERALX100anything")
    regular = add("ordinary", NOW + timedelta(minutes=10), payment=PaymentStatus.PAID, invoice=InvoiceStatus.FAILED)
    regular.sales_channel = SalesChannel.REGULAR
    await session.flush()
    session.add_all([
        MealCancellationRequest(order=ready, requested_by_id=c.buyer.id, reason="舊申請", status="rejected", requested_at=NOW - timedelta(minutes=5), reviewed_by_id=c.admin.id, reviewed_at=NOW, review_reason="請補資料"),
        MealCancellationRequest(order=ready, requested_by_id=c.buyer.id, reason="新申請", requested_at=NOW),
        MealCancellationRequest(order=regular, requested_by_id=c.buyer.id, reason="普通商品不應計入", requested_at=NOW),
        Refund(order=c.order, amount=200, status=RefundStatus.FAILED, reason="供應商退款失敗", requested_by_id=c.admin.id),
        OutboxEvent(aggregate_type="order", aggregate_id="different-aggregate", event_type="invoice.adjustment_required", status=OutboxStatus.FAILED, payload={"order_id": legacy.id}),
        OutboxEvent(aggregate_type="order", aggregate_id=c.order.id, event_type="invoice.issue_requested", status=OutboxStatus.FAILED, payload={"order_id": c.order.id}),
    ])
    await session.commit()
    return c


@pytest.fixture
async def operations_context(database_session, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(operations, "datetime", FixedDateTime)
    c = await seed_search_orders(database_session)
    async with api_test_context(database_session, [meals_router, orders_router], settings=make_test_settings()) as client:
        c.client = client
        yield c


async def search(c, **params):
    result = await c.client.get("/v1/admin/meal-orders", params=params, headers=auth_headers(c.admin))
    assert result.status_code == 200, result.text
    assert "private_token" not in result.text and "must-not-be-returned" not in result.text
    return result.json()


def ids(result):
    return {item["order"]["id"] for item in result["items"]}


@pytest.mark.asyncio
async def test_all_events_search_sorts_by_newest_creation_and_paginates_without_regular_orders(operations_context):
    c = operations_context
    for index, label in enumerate(["future", "similar", "literal", "ready", "legacy", "first"]):
        c.orders[label].created_at = NOW + timedelta(seconds=index)
    await c.session.commit()
    all_orders = await search(c)
    assert all_orders["total"] == 6
    assert c.orders["ordinary"].id not in ids(all_orders)
    assert [item["order"]["id"] for item in all_orders["items"]] == [c.orders[label].id for label in ["first", "legacy", "ready", "literal", "similar", "future"]]
    first = await search(c, limit=2)
    second = await search(c, limit=2, offset=2)
    assert first["total"] == second["total"] == 6
    assert ids(first).isdisjoint(ids(second))
    assert all_orders["items"][0]["customer_name"] == "王小明"


@pytest.mark.asyncio
async def test_same_created_time_search_pages_use_stable_order_id(operations_context):
    c = operations_context
    for order in c.orders.values():
        order.created_at = NOW
    await c.session.commit()
    expected = sorted((order.id for label, order in c.orders.items() if label != "ordinary"), reverse=True)
    pages = [await search(c, limit=2, offset=offset) for offset in [0, 2, 4]]
    assert [item["order"]["id"] for page in pages for item in page["items"]] == expected
    assert [item["order"]["id"] for item in (await search(c, limit=2))["items"]] == expected[:2]


@pytest.mark.parametrize("scope", ["own_orders", "own_meals", "event_meals"])
@pytest.mark.asyncio
async def test_order_lists_keep_newest_records_and_stable_ties(operations_context, scope):
    c = operations_context
    for order in c.orders.values():
        order.created_at = NOW
        order.contact_email = order.contact_email.replace("example.test", "example.com")
    c.orders["first"].created_at = NOW + timedelta(minutes=1)
    await c.session.commit()
    if scope == "event_meals":
        path, user = f"/v1/admin/meal-events/{c.event.id}/orders", c.admin
        candidates = [order for order in c.orders.values() if order.meal_event_id == c.event.id and order.sales_channel == SalesChannel.MEAL_PREORDER]
    else:
        path, user = ("/v1/orders" if scope == "own_orders" else "/v1/meal-orders"), c.buyer
        candidates = [order for order in c.orders.values() if order.user_id == c.buyer.id and (scope == "own_orders" or order.sales_channel == SalesChannel.MEAL_PREORDER)]
    response = await c.client.get(path, headers=auth_headers(user))
    assert response.status_code == 200, response.text
    rows = response.json()
    actual = [item["id"] for item in rows]
    expected = [c.order.id] + sorted((order.id for order in candidates if order != c.order), reverse=True)
    assert actual == expected


@pytest.mark.asyncio
async def test_admin_events_use_creation_time_and_stable_id_instead_of_pickup_date(operations_context):
    c = operations_context
    future = c.orders["future"].meal_event
    c.event.created_at = NOW + timedelta(seconds=1)
    future.created_at = NOW
    await c.session.commit()
    path = "/v1/admin/meal-events"
    response = await c.client.get(path, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    assert [event["id"] for event in response.json()] == [c.event.id, future.id]
    c.event.created_at = NOW
    await c.session.commit()
    response = await c.client.get(path, headers=auth_headers(c.admin))
    assert [event["id"] for event in response.json()] == sorted([c.event.id, future.id], reverse=True)


@pytest.mark.asyncio
async def test_taipei_date_and_clock_filters_use_selected_pickup_and_legacy_fallback(operations_context):
    c = operations_context
    assert ids(await search(c, date_from="2030-01-02", date_to="2030-01-02")) == {c.orders[label].id for label in ["first", "ready", "literal", "similar"]}
    assert ids(await search(c, date_from="2030-01-01", date_to="2030-01-01")) == {c.orders["legacy"].id}
    assert ids(await search(c, pickup_time="00:05")) == {c.order.id}
    assert ids(await search(c, pickup_time="23:50")) == {c.orders["legacy"].id}
    assert ids(await search(c, meal_period="lunch")) == {c.orders["future"].id}
    assert (await search(c, meal_period="unspecified"))["total"] == 0
    c.event.meal_period = None
    await c.session.commit()
    assert (await search(c, meal_period="unspecified"))["total"] == 5


@pytest.mark.parametrize("field,value,label", [("payment_status", "refund_pending", "future"), ("fulfillment_status", "ready_for_pickup", "ready"), ("invoice_status", "failed", "first"), ("review_status", "pending", "ready")])
@pytest.mark.asyncio
async def test_status_filters_follow_authoritative_state_and_latest_review(operations_context, field, value, label):
    c = operations_context
    assert ids(await search(c, **{field: value})) == {c.orders[label].id}
    assert (await search(c, review_status="rejected"))["total"] == 0
    assert (await search(c, review_status="none"))["total"] == 5


@pytest.mark.asyncio
async def test_search_uses_customer_and_order_fields_with_literal_wildcards(operations_context):
    c = operations_context
    assert ids(await search(c, search="王小明")) == {c.orders[label].id for label in ["first", "ready", "future", "legacy", "similar"]}
    assert ids(await search(c, search=c.other.email)) == {c.orders["literal"].id}
    assert ids(await search(c, search="LITERAL_100%")) == {c.orders["literal"].id}
    assert (await search(c, search="no-matching-order"))["total"] == 0


@pytest.mark.asyncio
async def test_global_summary_counts_each_order_once_and_links_to_matching_queues(operations_context):
    c = operations_context
    result = await c.client.get("/v1/admin/meal-operations-summary", headers=auth_headers(c.admin))
    assert result.status_code == 200, result.text
    assert result.json() == {"today": "2030-01-02", "pending_cancellation": 1, "refund_attention": 2, "invoice_attention": 2, "today_pickup": 2}
    for queue, expected in {"pending_cancellation": ["ready"], "refund_attention": ["first", "future"], "invoice_attention": ["first", "legacy"], "today_pickup": ["first", "ready"]}.items():
        assert ids(await search(c, work_queue=queue)) == {c.orders[label].id for label in expected}
    c.order.payment_status = PaymentStatus.REFUNDED
    c.order.invoice_status = InvoiceStatus.VOIDED
    c.orders["ready"].fulfillment.status = FulfillmentState.PICKED_UP
    await c.session.commit()
    changed = await c.client.get("/v1/admin/meal-operations-summary", headers=auth_headers(c.admin))
    assert changed.json()["refund_attention"] == 1
    assert changed.json()["invoice_attention"] == 1
    assert changed.json()["today_pickup"] == 0


@pytest.mark.parametrize("params", [{"date_from": "2030-01-03", "date_to": "2030-01-02"}, {"date_from": "invalid"}, {"pickup_time": "25:00"}, {"pickup_time": "12:60"}, {"payment_status": "invented"}, {"meal_period": "other"}, {"review_status": "invented"}, {"limit": 101}, {"offset": -1}, {"search": "x" * 101}])
@pytest.mark.asyncio
async def test_invalid_filters_are_rejected(operations_context, params):
    c = operations_context
    response = await c.client.get("/v1/admin/meal-orders", params=params, headers=auth_headers(c.admin))
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_pending_refund_is_tracked_even_when_original_order_remains_paid(operations_context):
    c = operations_context
    c.session.add(Refund(order=c.orders["ready"], amount=200, status=RefundStatus.PENDING, reason="重複付款待退款", requested_by_id=c.admin.id))
    await c.session.commit()
    assert ids(await search(c, work_queue="refund_attention")) == {c.orders[label].id for label in ["first", "ready", "future"]}
    assert c.orders["ready"].payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_failed_adjustment_remains_visible_for_issued_invoice_but_old_issue_failure_does_not(operations_context):
    c = operations_context
    order = c.orders["ready"]
    c.session.add(OutboxEvent(aggregate_type="order", aggregate_id=order.id, event_type="invoice.issue_requested", status=OutboxStatus.FAILED, payload={"order_id": order.id}))
    await c.session.commit()
    assert order.id not in ids(await search(c, work_queue="invoice_attention"))
    c.session.add(OutboxEvent(aggregate_type="order", aggregate_id="adjustment-task", event_type="invoice.adjustment_required", status=OutboxStatus.FAILED, payload={"order_id": order.id}))
    await c.session.commit()
    assert order.id in ids(await search(c, work_queue="invoice_attention"))
    summary = await c.client.get("/v1/admin/meal-operations-summary", headers=auth_headers(c.admin))
    assert summary.json()["invoice_attention"] == 3
    order.invoice_status = InvoiceStatus.VOIDED
    await c.session.commit()
    assert order.id not in ids(await search(c, work_queue="invoice_attention"))


@pytest.mark.asyncio
async def test_queries_require_admin_and_empty_dashboard_reports_zero(database_session):
    c = await seed_cancellation_order(database_session)
    async with api_test_context(database_session, [meals_router], settings=make_test_settings()) as client:
        for path in ["/v1/admin/meal-orders", "/v1/admin/meal-operations-summary"]:
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers=auth_headers(c.buyer))).status_code == 403
        result = await client.get("/v1/admin/meal-operations-summary", headers=auth_headers(c.admin))
        assert result.status_code == 200, result.text
        assert all(result.json()[key] == 0 for key in ["pending_cancellation", "refund_attention", "invoice_attention", "today_pickup"])
