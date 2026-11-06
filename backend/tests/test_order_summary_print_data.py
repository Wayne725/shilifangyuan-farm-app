import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models import FulfillmentState, FulfillmentStatus, OrderItemOption, PaymentStatus, SalesChannel
from app.order_summary import OrderSummaryFilters, build_print_summary
from tests.support import auth_headers
from tests.test_order_summary import NOW, PARAMS, add_order, line, read, summary_context


def confirmed_order(context, label, items, **kwargs):
    order = add_order(context, label, items, **kwargs)
    order.paid_at = NOW
    return order


async def test_pickup_date_uses_taiwan_boundaries_and_stored_start_fallback(summary_context):
    c = summary_context
    start = datetime(2030, 1, 1, 16, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    values = [
        (start - timedelta(microseconds=1), start, 1),
        (start, end, 2),
        (end - timedelta(microseconds=1), start, 4),
        (end, start, 8),
        (None, start, 16),
        (None, None, 32),
    ]
    for index, (pickup_at, pickup_starts_at, quantity) in enumerate(values):
        order = confirmed_order(c, f"PICKUP-{index}", [line(quantity, offering=c.offerings[0])], channel=SalesChannel.MEAL_PREORDER)
        order.fulfillment.pickup_at = pickup_at
        order.fulfillment.pickup_starts_at = pickup_starts_at
    confirmed_order(c, "NO-FULFILLMENT", [line(64, offering=c.offerings[0])], channel=SalesChannel.MEAL_PREORDER, fulfillment=None)
    await c.session.commit()
    report = await read(c, order_category="meal", date_basis="pickup")
    assert report["date_basis"] == "pickup"
    assert report["summary"]["quantity"] == 22
    assert report["summary"]["order_count"] == 3
    result = await build_print_summary(c.session, OrderSummaryFilters(**PARAMS, order_category="meal", date_basis="pickup"))
    assert result["summary"] == {"order_count": 3, "quantity": 22, "product_count": 1}
    legacy_report = await read(c, order_category="meal")
    assert legacy_report["date_basis"] == "order"
    assert legacy_report["summary"]["quantity"] == 127


@pytest.mark.parametrize("category", ["all", "general"])
async def test_pickup_date_is_rejected_for_non_meal_categories(summary_context, category):
    c = summary_context
    response = await c.client.get("/v1/admin/order-summary", params=PARAMS | {
        "date_basis": "pickup", "order_category": category,
    }, headers=auth_headers(c.admin))
    assert response.status_code == 422


async def test_print_summary_requires_confirmed_payment_and_authoritative_active_fulfillment(summary_context):
    c = summary_context
    cases = [
        ("PAID", PaymentStatus.PAID, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, NOW, True),
        ("NO-PAID-TIME", PaymentStatus.PAID, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, None, False),
        ("PENDING", PaymentStatus.PENDING, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, NOW, False),
        ("REFUNDED", PaymentStatus.REFUNDED, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, NOW, False),
        ("REFUND-PENDING", PaymentStatus.REFUND_PENDING, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, NOW, False),
        ("CANCELLED", PaymentStatus.PAID, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, NOW, NOW, False),
        ("FULFILLMENT-CANCELLED", PaymentStatus.PAID, FulfillmentState.CANCELLED, FulfillmentStatus.PREPARING, None, NOW, False),
        ("STALE-LEGACY", PaymentStatus.PAID, FulfillmentState.PREPARING, FulfillmentStatus.CANCELLED, None, NOW, True),
        ("LEGACY-ACTIVE", PaymentStatus.PAID, None, FulfillmentStatus.PREPARING, None, NOW, True),
        ("LEGACY-CANCELLED", PaymentStatus.PAID, None, FulfillmentStatus.CANCELLED, None, NOW, False),
        ("PICKED-UP", PaymentStatus.PAID, FulfillmentState.PICKED_UP, FulfillmentStatus.PICKED_UP, None, NOW, True),
    ]
    expected = []
    for label, payment, fulfillment, legacy, cancelled_at, paid_at, included in cases:
        order = add_order(c, label, [line(2, product=c.products[0])], payment=payment,
                          fulfillment=fulfillment, legacy=legacy, cancelled_at=cancelled_at)
        order.paid_at = paid_at
        if included:
            expected.append(order)
    await c.session.commit()
    for status in ["all", "active", "pending", "inactive", "paid"]:
        result = await build_print_summary(c.session, OrderSummaryFilters(**PARAMS, status=status))
        assert result["filters"]["status"] == "paid"
        assert result["summary"] == {"order_count": 4, "quantity": 8, "product_count": 1}
        assert result["source_versions"] == sorted(
            [{"id": order.id, "revision": 1} for order in expected], key=lambda row: row["id"],
        )


def options(*pairs):
    return [OrderItemOption(group_name=group, option_name=name, price_delta=0, position=index)
            for index, (group, name) in enumerate(pairs)]


async def test_print_summary_groups_complete_options_without_multiplication_and_keeps_order_notes(summary_context):
    c = summary_context
    first = line(3, offering=c.offerings[0], unit="份")
    first.selected_options = options(("主食", "紫米飯"), ("飲品", "汽水"))
    first_order = confirmed_order(c, "A-NOTE", [first, line(5, offering=c.offerings[2], name="副餐", unit="份")], channel=SalesChannel.MEAL_PREORDER)
    first_order.kitchen_note = "整單不加辣\n請分裝"
    first_order.revision = 3
    same = line(2, offering=c.offerings[1], unit="份")
    same.selected_options = options(("飲品", "汽水"), ("主食", "紫米飯"))
    confirmed_order(c, "B-SAME", [same], channel=SalesChannel.MEAL_PREORDER)
    different = line(4, offering=c.offerings[0], unit="份")
    different.selected_options = options(("主食", "麵包"))
    confirmed_order(c, "C-DIFFERENT", [different], channel=SalesChannel.MEAL_PREORDER)
    confirmed_order(c, "D-NONE", [line(2, offering=c.offerings[0], unit="份")], channel=SalesChannel.MEAL_PREORDER)
    confirmed_order(c, "E-OTHER-SOURCE", [line(7, offering=c.offerings[2], unit="份")], channel=SalesChannel.MEAL_PREORDER)
    await c.session.commit()
    filters = OrderSummaryFilters(**PARAMS, order_category="meal", search="主餐", limit=1, offset=999)
    result = await build_print_summary(c.session, filters)
    assert result["summary"] == {"order_count": 5, "quantity": 18, "product_count": 2}
    assert [row["quantity"] for row in result["products"]] == [11, 7]
    assert len({row["key"] for row in result["products"]}) == 2
    assert result["products"][0]["variants"] == [
        {"options": [], "quantity": 2},
        {"options": ["主食：紫米飯", "飲品：汽水"], "quantity": 5},
        {"options": ["主食：麵包"], "quantity": 4},
    ]
    assert result["notes"] == [{"order_number": "A-NOTE", "note": "整單不加辣\n請分裝", "items": [
        {"name": "主餐", "quantity": 3, "unit": "份"},
    ]}]
    assert {"id": first_order.id, "revision": 3} in result["source_versions"]
    assert "limit" not in result["filters"] and "offset" not in result["filters"]
    assert await build_print_summary(c.session, filters.model_copy(update={"limit": 25, "offset": 0})) == result
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    assert c.buyer.email not in str(result) and c.buyer.display_name not in str(result)


async def test_print_summary_applies_general_channel_category_and_literal_search_filters(summary_context):
    c = summary_context
    confirmed_order(c, "REGULAR", [line(2, product=c.products[3]), line(3, product=c.products[0])])
    confirmed_order(c, "GROUP", [line(4, product=c.products[3])], channel=SalesChannel.GROUP)
    confirmed_order(c, "MEAL", [line(8, offering=c.offerings[0], name="100%_特選")], channel=SalesChannel.MEAL_PREORDER)
    confirmed_order(c, "WILDCARD", [line(16, name="100Xanything特選")])
    await c.session.commit()
    result = await build_print_summary(c.session, OrderSummaryFilters(
        **PARAMS, order_category="general", category="加工品", search="100%_",
    ))
    assert result["summary"] == {"order_count": 2, "quantity": 6, "product_count": 2}
    assert {row["quantity"] for row in result["products"]} == {2, 4}
    result = await build_print_summary(c.session, OrderSummaryFilters(
        **PARAMS, order_category="general", sales_channel="group", category="加工品", search="100%_",
    ))
    assert result["summary"] == {"order_count": 1, "quantity": 4, "product_count": 1}


async def test_empty_print_summary_is_json_serializable_and_normalizes_filters(summary_context):
    filters = OrderSummaryFilters(**PARAMS, status="all", limit=1, offset=100)
    result = await build_print_summary(summary_context.session, filters)
    assert result == {
        "filters": {**PARAMS, "date_basis": "order", "sales_channel": "all", "order_category": "all", "status": "paid", "search": "", "category": ""},
        "summary": {"order_count": 0, "quantity": 0, "product_count": 0},
        "products": [], "notes": [], "source_versions": [],
    }


async def test_print_summary_rejects_oversized_results_instead_of_truncating(summary_context, monkeypatch):
    c = summary_context
    confirmed_order(c, "TOO-MANY", [line(1), line(2), line(3)])
    await c.session.commit()
    monkeypatch.setattr("app.order_summary.MAX_PRINT_SUMMARY_ITEMS", 2)
    with pytest.raises(HTTPException) as exc:
        await build_print_summary(c.session, OrderSummaryFilters(**PARAMS))
    assert exc.value.status_code == 422


async def test_print_summary_rejects_oversized_option_lists(summary_context, monkeypatch):
    c = summary_context
    item = line(3)
    item.selected_options = options(("主食", "紫米飯"), ("飲品", "汽水"))
    confirmed_order(c, "TOO-MANY-OPTIONS", [item])
    await c.session.commit()
    monkeypatch.setattr("app.order_summary.MAX_PRINT_SUMMARY_OPTIONS", 1)
    with pytest.raises(HTTPException) as exc:
        await build_print_summary(c.session, OrderSummaryFilters(**PARAMS))
    assert exc.value.status_code == 422
