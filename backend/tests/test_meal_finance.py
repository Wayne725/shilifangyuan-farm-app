import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import (
    MealCancellationRequest, Order, OrderItem, OrderItemOption, OrderKind, PaymentAttempt,
    PaymentStatus, Refund, RefundStatus, SalesChannel, TaxType,
)
from app.routers.meal_finance import meal_finance_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import seed_cancellation_order


FIRST = datetime(2030, 1, 1, 16, 0, tzinfo=timezone.utc)


@pytest.fixture
async def finance_context(database_session):
    c = await seed_cancellation_order(database_session)
    c.order.created_at = FIRST - timedelta(days=1)
    c.order.paid_at = FIRST
    c.order.payment_status = PaymentStatus.PAID
    c.order.amount_total = 320
    c.order.items[0].subtotal = 220
    c.order.items[0].unit_price = 110
    await c.session.refresh(c.order.items[0], ["selected_options"])
    c.order.items[0].selected_options = [OrderItemOption(group_name="主食", option_name="紫米", price_delta=10)]
    c.order.items.append(OrderItem(product_name="蔬食便當", unit_label="份", quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE))
    c.order.contact_email = "finance-buyer@example.test"
    attempts = [PaymentAttempt(order=c.order, merchant_trade_no=f"REPORT-{index}", amount=320,
        status=PaymentStatus.PAID, expires_at=FIRST + timedelta(hours=1), paid_at=at,
        provider_response={"secret": "private-provider-token"}) for index, at in enumerate([FIRST, FIRST + timedelta(minutes=30)])]
    c.session.add_all(attempts)
    c.session.add_all([
        Refund(order=c.order, payment_attempt=attempts[1], amount=320, status=RefundStatus.COMPLETED,
               reason="重複付款", requested_by_id=c.admin.id, completed_at=FIRST + timedelta(hours=1), created_at=FIRST),
        Refund(order=c.order, amount=50, status=RefundStatus.PENDING, reason="待供應商退款", requested_by_id=c.admin.id, created_at=FIRST),
        MealCancellationRequest(order=c.order, requested_by_id=c.buyer.id, reason="退單待審", requested_at=FIRST),
    ])
    unpaid = Order(order_number="UNPAID-REPORT", order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.MEAL_PREORDER,
                   user=c.buyer, meal_event=c.event, amount_total=100, contact_email=c.buyer.email,
                   membership_type_snapshot=c.order.membership_type_snapshot, created_at=FIRST + timedelta(hours=2))
    ordinary = Order(order_number="ORDINARY-REPORT", order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.REGULAR,
                    user=c.buyer, amount_total=5000, contact_email=c.buyer.email, paid_at=FIRST,
                    membership_type_snapshot=c.order.membership_type_snapshot, payment_status=PaymentStatus.PAID)
    c.session.add_all([unpaid, ordinary])
    await c.session.commit()
    async with api_test_context(c.session, [meal_finance_router], settings=make_test_settings()) as client:
        c.client = client
        c.attempts = attempts
        yield c


async def report(c, **params):
    response = await c.client.get("/v1/admin/finance/meal-sales", params={"starts_on": "2030-01-02", "ends_on": "2030-01-02", **params}, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    assert "private-provider-token" not in response.text
    return response.json()


@pytest.mark.asyncio
async def test_actual_receipts_and_completed_refunds_do_not_multiply_by_items(finance_context):
    c = finance_context
    result = await report(c)
    assert result["summary"] == {"received": 640, "refunded": 320, "net": 320, "receipt_count": 2,
        "refund_count": 1, "pending_refund_amount": 50, "paid_meal_quantity": 3}
    assert sum(product["sales_amount"] for product in result["products"]) == 320
    assert sum(product["quantity"] for product in result["products"]) == 3
    assert result["daily"] == [{"period": "2030-01-02", "received": 640, "refunded": 320, "net": 320, "receipt_count": 2, "refund_count": 1}]
    assert result["weekly"][0]["period"] == "2029-12-31"
    assert result["monthly"][0]["period"] == "2030-01"
    order = next(order for order in result["orders"]["items"] if order["id"] == c.order.id)
    assert order["period_received"] == 640 and order["period_refunded"] == 320 and order["period_net"] == 320
    assert order["paid_at"].endswith(("Z", "+00:00"))
    assert order["cancellation_status"] == "pending"
    assert order["items"][0]["options"][0]["name"] == "紫米"
    assert result["orders"]["total"] == 2


@pytest.mark.asyncio
async def test_refund_in_later_period_is_counted_on_completion_not_original_sale(finance_context):
    c = finance_context
    c.session.add(Refund(order=c.order, amount=320, status=RefundStatus.COMPLETED, reason="跨期退單",
                         requested_by_id=c.admin.id, completed_at=FIRST + timedelta(days=31), created_at=FIRST))
    c.order.payment_status = PaymentStatus.REFUNDED
    await c.session.commit()
    january = await report(c)
    february = await report(c, starts_on="2030-02-02", ends_on="2030-02-02")
    assert january["summary"]["received"] == 640 and january["summary"]["refunded"] == 320
    assert february["summary"]["received"] == 0 and february["summary"]["refunded"] == 320
    assert february["summary"]["net"] == -320
    assert february["products"] == []
    assert february["orders"]["total"] == 1


@pytest.mark.asyncio
async def test_period_tables_and_transaction_histories_show_newest_without_changing_totals(finance_context):
    c = finance_context
    for index, attempt in enumerate(c.attempts):
        attempt.created_at = FIRST + timedelta(minutes=index)
    pending = await c.session.scalar(select(Refund).where(Refund.order_id == c.order.id, Refund.status == RefundStatus.PENDING))
    pending.created_at = FIRST + timedelta(days=1)
    c.session.add(PaymentAttempt(order=c.order, merchant_trade_no="REPORT-LATER", amount=320,
        status=PaymentStatus.PAID, created_at=FIRST + timedelta(days=31),
        paid_at=FIRST + timedelta(days=31), expires_at=FIRST + timedelta(days=32)))
    await c.session.commit()
    result = await report(c, ends_on="2030-02-02")
    assert [row["period"] for row in result["daily"]] == ["2030-02-02", "2030-01-02"]
    assert [row["period"] for row in result["weekly"]] == ["2030-01-28", "2029-12-31"]
    assert [row["period"] for row in result["monthly"]] == ["2030-02", "2030-01"]
    for periods in [result["daily"], result["weekly"], result["monthly"]]:
        assert sum(row["received"] for row in periods) == result["summary"]["received"] == 960
        assert sum(row["refunded"] for row in periods) == result["summary"]["refunded"] == 320
        assert sum(row["net"] for row in periods) == result["summary"]["net"] == 640
    order = next(row for row in result["orders"]["items"] if row["id"] == c.order.id)
    assert [row["merchant_trade_no"] for row in order["receipts"]] == ["REPORT-LATER", "REPORT-1", "REPORT-0"]
    assert [row["reason"] for row in order["refunds"]] == ["待供應商退款", "重複付款"]
    assert order["period_received"] == 960 and order["period_refunded"] == 320


@pytest.mark.asyncio
async def test_midnight_boundaries_and_legacy_cash_receipts_are_not_double_counted(finance_context):
    c = finance_context
    c.attempts[1].paid_at = FIRST - timedelta(microseconds=1)
    c.attempts[0].paid_at = FIRST + timedelta(days=1)
    c.session.add(Order(order_number="CASH-REPORT", order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.MEAL_PREORDER,
        user=c.buyer, meal_event=c.event, amount_total=100, contact_email=c.buyer.email, paid_at=FIRST,
        membership_type_snapshot=c.order.membership_type_snapshot, payment_status=PaymentStatus.PAID, created_at=FIRST))
    await c.session.commit()
    result = await report(c)
    assert result["summary"]["received"] == 100
    assert result["summary"]["receipt_count"] == 1


@pytest.mark.asyncio
async def test_terminal_attempt_without_paid_time_uses_legacy_order_time(finance_context):
    c = finance_context
    c.attempts[0].paid_at = None
    c.attempts[1].paid_at = None
    c.attempts[0].status = PaymentStatus.REFUND_PENDING
    c.attempts[1].status = PaymentStatus.REFUNDED
    await c.session.commit()
    result = await report(c)
    assert result["summary"]["received"] == 640


@pytest.mark.asyncio
async def test_pagination_search_and_customer_fields_use_same_financial_scope(finance_context):
    c = finance_context
    c.buyer.display_name = "林財務"
    c.order.order_number = "MEAL_100%"
    await c.session.commit()
    first = await report(c, limit=1)
    second = await report(c, limit=1, offset=1)
    assert first["summary"] == second["summary"]
    assert first["orders"]["items"][0]["id"] != second["orders"]["items"][0]["id"]
    matching = await report(c, search="MEAL_100%")
    assert matching["orders"]["total"] == 1
    assert matching["summary"]["received"] == 640
    assert (await report(c, search="林財務"))["orders"]["total"] == 2
    empty = await report(c, search="not-a-customer")
    assert empty["summary"]["net"] == 0 and empty["orders"]["total"] == 0


@pytest.mark.asyncio
async def test_csv_exports_all_matching_orders_and_neutralizes_user_formula_text(finance_context):
    c = finance_context
    c.buyer.display_name = "=HYPERLINK(\"https://example.test\")"
    await c.session.commit()
    response = await c.client.get("/v1/admin/finance/meal-sales.csv", params={"starts_on": "2030-01-02", "ends_on": "2030-01-02", "limit": 1, "offset": 1}, headers=auth_headers(c.admin))
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(rows) == 3
    assert rows[0][0] == "訂單編號"
    assert rows[1][3].startswith("'=HYPERLINK")
    assert "private-provider-token" not in response.text


@pytest.mark.parametrize("params", [{"starts_on": "2030-01-03", "ends_on": "2030-01-02"}, {"starts_on": "invalid"}, {"limit": 101}, {"offset": -1}, {"search": "x" * 101}])
@pytest.mark.asyncio
async def test_invalid_period_and_paging_are_rejected(finance_context, params):
    c = finance_context
    response = await c.client.get("/v1/admin/finance/meal-sales", params={"starts_on": "2030-01-02", "ends_on": "2030-01-02", **params}, headers=auth_headers(c.admin))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reports_are_admin_only_and_do_not_mutate_financial_records(finance_context):
    c = finance_context
    before = await c.session.scalar(select(func.count(Refund.id)))
    for path in ["/v1/admin/finance/meal-sales", "/v1/admin/finance/meal-sales.csv"]:
        response = await c.client.get(path, params={"starts_on": "2030-01-02", "ends_on": "2030-01-02"}, headers=auth_headers(c.buyer))
        assert response.status_code == 403
    await report(c)
    assert await c.session.scalar(select(func.count(Refund.id))) == before
    assert c.order.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_all_channels_include_regular_and_group_sales_refunds_and_csv(finance_context):
    c = finance_context
    ordinary = await c.session.scalar(select(Order).where(Order.order_number == 'ORDINARY-REPORT'))
    await c.session.refresh(ordinary, ['items'])
    ordinary.created_at = FIRST
    ordinary.items = [OrderItem(product_name='一般選品', unit_label='袋', quantity=2, unit_price=2500, subtotal=5000, tax_type=TaxType.TAX_EXEMPT)]
    group = Order(order_number='GROUP-REPORT', order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.GROUP,
                  user=c.buyer, amount_total=600, contact_email=c.buyer.email, paid_at=FIRST, created_at=FIRST,
                  membership_type_snapshot=c.order.membership_type_snapshot, payment_status=PaymentStatus.PAID,
                  items=[OrderItem(product_name='團購選品', unit_label='箱', quantity=1, unit_price=600, subtotal=600, tax_type=TaxType.TAXABLE)])
    c.session.add_all([group, Refund(order=ordinary, amount=1000, status=RefundStatus.COMPLETED,
                                  reason='一般選品退貨', requested_by_id=c.admin.id, created_at=FIRST, completed_at=FIRST)])
    await c.session.commit()
    all_sales = await report(c, sales_channel='all')
    assert all_sales['summary']['received'] == 6240
    assert all_sales['summary']['refunded'] == 1320
    assert all_sales['summary']['net'] == 4920
    assert all_sales['summary']['paid_meal_quantity'] == 6
    assert all_sales['orders']['total'] == 4
    assert {row['sales_channel'] for row in all_sales['orders']['items']} == {'meal_preorder', 'regular', 'group'}
    regular = await report(c, sales_channel='regular')
    assert regular['summary']['net'] == 4000
    assert regular['orders']['total'] == 1
    assert regular['products'][0]['quantity'] == 2
    grouped = await report(c, sales_channel='group')
    assert grouped['summary']['received'] == 600 and grouped['orders']['total'] == 1
    assert (await report(c))['summary']['received'] == 640
    general = await report(c, sales_channel="all", order_category="general", limit=1)
    general_next = await report(c, sales_channel="all", order_category="general", limit=1, offset=1)
    assert general["orders"]["total"] == general_next["orders"]["total"] == 2
    assert general["summary"] == general_next["summary"]
    assert general["summary"]["received"] == 5600 and general["summary"]["refunded"] == 1000
    assert general["orders"]["items"][0]["id"] != general_next["orders"]["items"][0]["id"]
    assert (await report(c, sales_channel="all", order_category="meal"))["summary"]["received"] == 640
    csv_result = await c.client.get("/v1/admin/finance/meal-sales.csv", params={"starts_on": "2030-01-02", "ends_on": "2030-01-02", "sales_channel": "all", "order_category": "general", "offset": 1, "limit": 1}, headers=auth_headers(c.admin))
    csv_rows = list(csv.DictReader(io.StringIO(csv_result.text.lstrip("\ufeff"))))
    assert {row["訂單編號"] for row in csv_rows} == {ordinary.order_number, group.order_number}
    group_csv = next(row for row in csv_rows if row["訂單編號"] == group.order_number)
    assert group_csv["銷售類型"] == "共同購買"
    assert "團購選品" in group_csv["品項與加選"]
    assert sum(int(row["期間收款"]) for row in csv_rows) == general["summary"]["received"]
    response = await c.client.get('/v1/admin/finance/meal-sales.csv', params={'starts_on': '2030-01-02', 'ends_on': '2030-01-02', 'sales_channel': 'regular'}, headers=auth_headers(c.admin))
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip('\ufeff'))))
    assert len(rows) == 1 and rows[0]['銷售類型'] == '日常選品'
    assert rows[0]['期間收款'] == '5000' and rows[0]['期間完成退款'] == '1000'


@pytest.mark.asyncio
async def test_invalid_channel_is_rejected(finance_context):
    c = finance_context
    response = await c.client.get('/v1/admin/finance/meal-sales', params={'starts_on': '2030-01-02', 'ends_on': '2030-01-02', 'sales_channel': 'unknown'}, headers=auth_headers(c.admin))
    assert response.status_code == 422
