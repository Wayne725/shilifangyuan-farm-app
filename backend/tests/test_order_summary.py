from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from app.models import (
    FulfillmentMethod, FulfillmentState, FulfillmentStatus, GroupBundle, Meal,
    MealCancellationRequest, MealEvent, MealEventOffering, MealEventStatus, MembershipType,
    Order, OrderFulfillment, OrderItem, OrderItemOption, OrderKind, PaymentAttempt,
    PaymentStatus, Product, Refund, RefundStatus, SalesChannel, TaxType, User, UserRole,
)
from app.order_summary import OrderSummaryFilters, _summary_lines
from app.routers import ALL_ROUTERS
from app.routers.order_summary import order_summary_router
from tests.support import api_test_context, auth_headers, make_test_settings


NOW = datetime(2030, 1, 2, 4, tzinfo=timezone.utc)
PARAMS = {"starts_on": "2030-01-02", "ends_on": "2030-01-02"}


@pytest.fixture
async def summary_context(database_session):
    session = database_session
    admin = User(email="summary-admin@example.test", display_name="匯總管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="private-buyer@example.test", display_name="私密買家姓名", password_hash="test")
    products = [
        Product(slug=slug, name=name, category=category, unit="包", member_price=100, nonmember_price=100)
        for slug, name, category in [
            ("same-a", "同名商品", "當季蔬果"), ("same-b", "同名商品", "當季蔬果"),
            ("rice", "白米", "米・雜糧"), ("literal", "100%_特選", "加工品"),
        ]
    ]
    bundle = GroupBundle(name="組合", member_price=100, nonmember_price=100)
    meal = Meal(slug="same-meal", name="主餐", price=100)
    another_meal = Meal(slug="other-meal", name="同名主餐來源", price=100)
    session.add_all([admin, buyer, bundle, meal, another_meal, *products])
    await session.flush()
    events = [
        MealEvent(title=f"測試餐期{index}", location="測試點", status=MealEventStatus.PUBLISHED, created_by_id=admin.id,
                  ordering_starts_at=NOW, ordering_ends_at=NOW + timedelta(hours=1),
                  pickup_starts_at=NOW + timedelta(hours=2), pickup_ends_at=NOW + timedelta(hours=3))
        for index in range(2)
    ]
    offerings = [MealEventOffering(event=event_row, meal=meal, price=100, capacity=100) for event_row in events]
    offerings.append(MealEventOffering(event=events[0], meal=another_meal, price=100, capacity=100))
    session.add_all([*events, *offerings])
    await session.commit()
    async with api_test_context(session, [order_summary_router], settings=make_test_settings()) as client:
        yield SimpleNamespace(session=session, client=client, admin=admin, buyer=buyer, products=products, bundle=bundle, offerings=offerings)


def line(quantity=1, *, product=None, offering=None, bundle=None, name=None, unit="包"):
    return OrderItem(
        source_product_id=product.id if product else None,
        source_meal_offering_id=offering.id if offering else None,
        source_bundle_id=bundle.id if bundle else None,
        product_name=name or (product.name if product else "主餐" if offering else "組合" if bundle else "歷史商品"),
        unit_label=unit, quantity=quantity, unit_price=100, subtotal=quantity * 100, tax_type=TaxType.TAXABLE,
    )


def add_order(c, label, items, *, channel=SalesChannel.REGULAR, payment=PaymentStatus.PAID,
              fulfillment=FulfillmentState.PREPARING, legacy=FulfillmentStatus.PREPARING,
              created_at=NOW, cancelled_at=None):
    order = Order(
        order_number=label, user=c.buyer, order_kind=OrderKind.GROUP if channel == SalesChannel.GROUP else OrderKind.REGULAR,
        sales_channel=channel, membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=sum(item.subtotal for item in items), contact_email=c.buyer.email,
        payment_status=payment, fulfillment_status=legacy, cancelled_at=cancelled_at, created_at=created_at,
        items=items,
        fulfillment=OrderFulfillment(method=FulfillmentMethod.COOPERATIVE_PICKUP, status=fulfillment) if fulfillment else None,
    )
    c.session.add(order)
    return order


async def read(c, **params):
    response = await c.client.get("/v1/admin/order-summary", params=PARAMS | params, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    result = response.json()
    data = str({key: result[key] for key in ["summary", "categories", "channels", "products"]})
    assert c.buyer.email not in data and c.buyer.display_name not in data
    assert "amount" not in data
    return result


async def test_summary_requires_admin(summary_context):
    c = summary_context
    assert (await c.client.get("/v1/admin/order-summary", params=PARAMS)).status_code == 401
    assert (await c.client.get("/v1/admin/order-summary", params=PARAMS, headers=auth_headers(c.buyer))).status_code == 403


async def test_taiwan_order_date_includes_both_dates_and_excludes_next_midnight(summary_context):
    c = summary_context
    start = datetime(2030, 1, 1, 16, tzinfo=timezone.utc)
    end = datetime(2030, 1, 3, 16, tzinfo=timezone.utc)
    for index, (created_at, quantity) in enumerate([
        (start - timedelta(microseconds=1), 1), (start, 2), (end - timedelta(microseconds=1), 4), (end, 8),
    ]):
        add_order(c, f"BOUNDARY{index}", [line(quantity)], created_at=created_at)
    await c.session.commit()
    result = await read(c, ends_on="2030-01-03")
    assert result["summary"]["order_count"] == 2
    assert result["summary"]["quantity"] == 6
    assert result["products"]["items"][0]["quantity"] == 6


@pytest.mark.parametrize("payment,fulfillment,legacy,cancelled_at,expected", [
    (PaymentStatus.PAID, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, "paid"),
    (PaymentStatus.PENDING, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, "pending"),
    *[(payment, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, None, "inactive")
      for payment in PaymentStatus if payment not in {PaymentStatus.PAID, PaymentStatus.PENDING}],
    (PaymentStatus.PAID, FulfillmentState.CANCELLED, FulfillmentStatus.PREPARING, None, "inactive"),
    (PaymentStatus.PAID, FulfillmentState.PREPARING, FulfillmentStatus.CANCELLED, None, "paid"),
    (PaymentStatus.PAID, None, FulfillmentStatus.CANCELLED, None, "inactive"),
    (PaymentStatus.PAID, None, FulfillmentStatus.PREPARING, None, "paid"),
    (PaymentStatus.PAID, FulfillmentState.PREPARING, FulfillmentStatus.PREPARING, NOW, "inactive"),
    (PaymentStatus.PAID, FulfillmentState.PICKED_UP, FulfillmentStatus.PICKED_UP, None, "paid"),
])
async def test_status_filters_follow_payment_cancellation_and_authoritative_fulfillment(
    summary_context, payment, fulfillment, legacy, cancelled_at, expected,
):
    c = summary_context
    add_order(c, "STATE", [line(3)], payment=payment, fulfillment=fulfillment, legacy=legacy, cancelled_at=cancelled_at)
    await c.session.commit()
    for status in ["active", "paid", "pending", "inactive", "all"]:
        included = status == "all" or status == expected or (status == "active" and expected != "inactive")
        result = await read(c, status=status)
        assert result["summary"]["order_count"] == int(included)
        assert result["summary"]["quantity"] == (3 if included else 0)
        assert result["summary"][f"{expected}_order_count"] == int(included)


async def test_item_grain_avoids_attempt_refund_options_multiplication_and_deduplicates_order_counts(summary_context):
    c = summary_context
    first = add_order(c, "MULTI", [line(2, product=c.products[0]), line(3, product=c.products[0]), line(6, product=c.products[2])])
    add_order(c, "OTHER-SOURCE", [line(4, product=c.products[1])])
    first.items[0].selected_options = [OrderItemOption(group_name="配菜", option_name=name, price_delta=0) for name in ["青菜", "豆腐"]]
    await c.session.flush()
    c.session.add_all([
        *[PaymentAttempt(order=first, merchant_trade_no=f"SUMMARYATTEMPT{index}", amount=1100, status=PaymentStatus.PAID, expires_at=NOW + timedelta(hours=1)) for index in range(2)],
        Refund(order=first, amount=100, status=RefundStatus.FAILED, requested_by_id=c.admin.id, reason="測試退款失敗"),
        Refund(order=first, amount=100, status=RefundStatus.COMPLETED, completed_at=NOW, requested_by_id=c.admin.id, reason="重複付款已退款，原單有效"),
        MealCancellationRequest(order=first, requested_by_id=c.buyer.id, reason="尚待審核"),
    ])
    await c.session.commit()
    result = await read(c)
    assert result["summary"] == {"order_count": 2, "quantity": 15, "product_count": 3, "paid_order_count": 2, "pending_order_count": 0, "inactive_order_count": 0}
    same_name = [item for item in result["products"]["items"] if item["name"] == "同名商品"]
    assert [item["quantity"] for item in same_name] == [5, 4]
    assert len({item["key"] for item in same_name}) == 2
    assert all(item["order_count"] == 1 for item in result["products"]["items"])
    assert result["channels"] == [{"sales_channel": "regular", "order_count": 2, "quantity": 15}]


async def test_meal_sources_merge_across_periods_but_historical_names_units_and_channels_do_not(summary_context):
    c = summary_context
    add_order(c, "LUNCH", [line(2, offering=c.offerings[0], unit="份")], channel=SalesChannel.MEAL_PREORDER)
    add_order(c, "DINNER", [line(4, offering=c.offerings[1], unit="份")], channel=SalesChannel.MEAL_PREORDER)
    add_order(c, "OTHER-MEAL", [line(1, offering=c.offerings[2], unit="份")], channel=SalesChannel.MEAL_PREORDER)
    add_order(c, "RENAMED", [line(3, offering=c.offerings[0], name="舊主餐名稱", unit="份")], channel=SalesChannel.MEAL_PREORDER)
    add_order(c, "UNIT", [line(5, offering=c.offerings[0], unit="盒")], channel=SalesChannel.MEAL_PREORDER)
    add_order(c, "CHANNEL", [line(7, offering=c.offerings[0], unit="份")], channel=SalesChannel.GROUP)
    add_order(c, "BUNDLE", [line(8, bundle=c.bundle)], channel=SalesChannel.GROUP)
    add_order(c, "LEGACY-A", [line(1, name="舊品", unit="包")])
    add_order(c, "LEGACY-B", [line(2, name="舊品", unit="包")])
    await c.session.commit()
    result = await read(c)
    assert result["summary"]["product_count"] == 7
    merged = [row for row in result["products"]["items"] if row["sales_channel"] == "meal_preorder" and row["name"] == "主餐" and row["unit"] == "份"]
    assert [(row["quantity"], row["order_count"]) for row in merged] == [(6, 2), (1, 1)]
    assert set(result["category_options"]) == {"便當", "共同購買組合", "未分類"}
    legacy = next(row for row in result["products"]["items"] if row["name"] == "舊品")
    assert legacy["quantity"] == 3 and legacy["order_count"] == 2


async def test_filters_and_pagination_keep_full_totals_and_category_options(summary_context):
    c = summary_context
    add_order(c, "MIXED", [line(2, product=c.products[0]), line(5, product=c.products[2])])
    add_order(c, "PENDING", [line(3, product=c.products[1])], payment=PaymentStatus.PENDING)
    add_order(c, "GROUP", [line(4, bundle=c.bundle)], channel=SalesChannel.GROUP)
    add_order(c, "REFUND", [line(9, product=c.products[0])], payment=PaymentStatus.REFUND_PENDING)
    await c.session.commit()
    result = await read(c)
    first = await read(c, limit=1)
    second = await read(c, limit=1, offset=1)
    assert first["summary"] == second["summary"] == result["summary"]
    assert first["categories"] == second["categories"] == result["categories"]
    assert first["channels"] == second["channels"] == result["channels"]
    assert first["products"]["total"] == second["products"]["total"] == 4
    assert first["products"]["items"][0]["key"] != second["products"]["items"][0]["key"]
    assert result["summary"]["order_count"] == 3 and result["summary"]["quantity"] == 14
    filtered = await read(c, category="當季蔬果")
    assert filtered["summary"]["order_count"] == 2 and filtered["summary"]["quantity"] == 5
    assert filtered["category_options"] == result["category_options"]
    assert filtered["categories"] == [{"category": "當季蔬果", "order_count": 2, "quantity": 5}]
    searched = await read(c, search=" 同名 ", category="米・雜糧")
    assert searched["search"] == "同名"
    assert searched["summary"]["quantity"] == 0
    assert searched["category_options"] == ["當季蔬果"]
    regular = await read(c, sales_channel="regular")
    assert regular["summary"]["order_count"] == 2 and regular["summary"]["quantity"] == 10
    assert (await read(c, offset=100))["products"]["items"] == []
    c.products[2].category = "新分類"
    await c.session.commit()
    assert (await read(c, category="新分類"))["summary"]["quantity"] == 5


async def test_search_matches_only_historical_product_names_and_treats_wildcards_literally(summary_context):
    c = summary_context
    add_order(c, "LITERAL", [line(2, product=c.products[3]), line(5, name="100Xanything特選")])
    c.products[3].name = "新名稱"
    await c.session.commit()
    assert (await read(c, search="100%_"))["summary"]["quantity"] == 2
    assert (await read(c, search="新名稱"))["summary"]["quantity"] == 0
    assert (await read(c, search=c.buyer.display_name))["summary"]["quantity"] == 0


async def test_empty_summary_has_zero_counts_and_arrays(summary_context):
    result = await read(summary_context)
    assert result["summary"] == {"order_count": 0, "quantity": 0, "product_count": 0, "paid_order_count": 0, "pending_order_count": 0, "inactive_order_count": 0}
    assert result["categories"] == result["channels"] == result["category_options"] == []
    assert result["products"] == {"items": [], "total": 0, "limit": 25, "offset": 0}


@pytest.mark.parametrize("params", [
    {"starts_on": "invalid"}, {"starts_on": "2030-01-03"}, {"ends_on": "9999-12-31"},
    {"starts_on": "0001-01-01"}, {"sales_channel": "invalid"}, {"status": "invalid"},
    {"search": "a" * 101}, {"limit": 101}, {"limit": 0}, {"offset": -1},
])
async def test_invalid_filters_are_rejected(summary_context, params):
    c = summary_context
    response = await c.client.get("/v1/admin/order-summary", params=PARAMS | params, headers=auth_headers(c.admin))
    assert response.status_code == 422, response.text


async def test_dates_are_required(summary_context):
    c = summary_context
    assert (await c.client.get("/v1/admin/order-summary", headers=auth_headers(c.admin))).status_code == 422


async def test_report_uses_a_bounded_number_of_aggregate_queries(summary_context):
    c = summary_context
    for index in range(50):
        add_order(c, f"BOUNDED{index}", [line(index + 1, product=c.products[0])])
    await c.session.commit()
    queries = []
    engine = c.session.bind.sync_engine
    def collect(_connection, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)
    event.listen(engine, "before_cursor_execute", collect)
    try:
        result = await read(c, limit=1)
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    report_queries = [query for query in queries if "FROM order_items" in query]
    assert 0 < len(report_queries) <= 6
    assert result["summary"]["quantity"] == sum(range(1, 51))


def test_summary_lines_compile_for_postgres_without_sqlite_date_functions():
    query = select(_summary_lines(OrderSummaryFilters(**PARAMS, status="inactive", search="100%_")))
    statement = str(query.compile(dialect=postgresql.dialect()))
    assert "strftime" not in statement and "date(" not in statement
    assert "payment_attempts" not in statement and "order_item_options" not in statement
    assert "ILIKE" in statement


def test_order_summary_is_registered_in_application_openapi():
    application = FastAPI()
    for router in ALL_ROUTERS:
        application.include_router(router)
    operation = application.openapi()["paths"]["/v1/admin/order-summary"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {"starts_on", "ends_on", "date_basis", "sales_channel", "order_category", "status", "search", "category", "limit", "offset"}
    assert parameters["starts_on"]["required"] and parameters["ends_on"]["required"]
    assert parameters["status"]["schema"]["default"] == "active"
    assert parameters["date_basis"]["schema"]["default"] == "order"


async def test_order_categories_separate_general_and_meal_before_pagination_and_totals(summary_context):
    c = summary_context
    add_order(c, "GENERAL", [line(2, product=c.products[0])])
    add_order(c, "GROUP", [line(3, bundle=c.bundle)], channel=SalesChannel.GROUP)
    add_order(c, "MEAL", [line(7, offering=c.offerings[0])], channel=SalesChannel.MEAL_PREORDER)
    await c.session.commit()
    general = await read(c, order_category="general", limit=1)
    second = await read(c, order_category="general", limit=1, offset=1)
    assert general["summary"] == second["summary"]
    assert general["summary"]["quantity"] == 5
    assert general["summary"]["order_count"] == 2
    assert {row["sales_channel"] for row in general["channels"]} == {"regular", "group"}
    assert general["products"]["items"][0]["key"] != second["products"]["items"][0]["key"]
    assert "便當" not in general["category_options"]
    meal = await read(c, order_category="meal")
    assert meal["summary"]["quantity"] == 7
    assert meal["summary"]["order_count"] == 1
    assert meal["channels"][0]["sales_channel"] == "meal_preorder"
    assert (await read(c, order_category="general", search="主餐"))["summary"]["order_count"] == 0
    invalid = await c.client.get("/v1/admin/order-summary", params=PARAMS | {"order_category": "unknown"}, headers=auth_headers(c.admin))
    assert invalid.status_code == 422
