import csv
import io
from datetime import timedelta

import pytest

from app.models import Invoice, InvoiceStatus, Order, SalesChannel
from app.routers.invoice_reports import invoice_reports_router
from tests.support import auth_headers
from tests.test_accounting_exports import spreadsheet_values
from tests.test_financial_statements import payment_row, statement_file, upload
from tests.test_meal_cancellation_review import review_context, mark_paid


async def add_general_orders(c):
    orders = [Order(order_number=f'CATEGORY-{channel.value}', user_id=c.buyer.id,
                    order_kind=c.order.order_kind, sales_channel=channel,
                    membership_type_snapshot=c.order.membership_type_snapshot,
                    amount_total=200, contact_email=c.buyer.email)
              for channel in [SalesChannel.REGULAR, SalesChannel.GROUP]]
    c.session.add_all(orders)
    await c.session.commit()
    return orders


@pytest.mark.asyncio
async def test_invoice_categories_include_group_and_csv_matches_search(review_context):
    c = review_context
    c.client._transport.app.include_router(invoice_reports_router)
    general = await add_general_orders(c)
    headers = auth_headers(c.admin)
    path = '/v1/admin/invoice-register'
    page = (await c.client.get(path, params={'order_category': 'general', 'limit': 1}, headers=headers)).json()
    assert page['total'] == 2 and len(page['items']) == 1
    assert page['items'][0]['sales_channel'] in {'regular', 'group'}
    meals = (await c.client.get(path, params={'order_category': 'meal'}, headers=headers)).json()
    assert [row['order_number'] for row in meals['items']] == [c.order.order_number]
    exported = await c.client.get(path + '.csv', params={'order_category': 'general', 'q': general[1].order_number, 'limit': 1, 'offset': 1}, headers=headers)
    table = list(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert len(table) == 1 and table[0]['訂單編號'] == general[1].order_number
    assert table[0]['訂單分類'] == '一般訂單'
    assert (await c.client.get(path + '?order_category=wrong', headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_reconciliation_filters_summary_page_csv_and_order_options_consistently(review_context):
    c = review_context
    await mark_paid(c)
    general = await add_general_orders(c)
    content = statement_file('newebpay', [payment_row(), payment_row(reference=general[0].order_number, external='regular'),
        payment_row(reference=general[1].order_number, external='group'), payment_row(reference='OUTSIDE', external='unknown')])
    batch = (await upload(c, 'newebpay', content)).json()
    path = f"/v1/admin/financial-statements/{batch['id']}"
    headers = auth_headers(c.admin)
    page = (await c.client.get(path, params={'order_category': 'general', 'limit': 1}, headers=headers)).json()
    assert page['total'] == 2 and len(page['items']) == 1
    assert sum(page['summary'].values()) == 2
    meals = (await c.client.get(path, params={'order_category': 'meal'}, headers=headers)).json()
    assert meals['total'] == 1 and meals['items'][0]['order_number'] == c.order.order_number
    unlinked = (await c.client.get(path, params={'result': 'unmatched'}, headers=headers)).json()
    assert unlinked['total'] == 1 and unlinked['items'][0]['sales_channel'] is None
    assert (await c.client.get(path, params={'order_category': 'general', 'result': 'unmatched'}, headers=headers)).json()['total'] == 0
    params = {'order_category': 'general', 'q': 'group', 'result': 'difference', 'limit': 1, 'offset': 0}
    selected = (await c.client.get(path, params=params, headers=headers)).json()
    exported = await c.client.get(path + '/reconciliation.csv', params={**params, 'offset': 99}, headers=headers)
    table = list(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert selected['total'] == len(table) == 1
    assert table[0]['網站訂單編號'] == selected['items'][0]['order_number'] == general[1].order_number
    assert table[0]['訂單分類'] == '一般訂單'
    options = (await c.client.get('/v1/admin/financial-statements/order-options?order_category=general', headers=headers)).json()
    assert {row['id'] for row in options} == {row.id for row in general}
    original = await c.client.get(path + '/original', headers=headers)
    assert original.content == content
    assert (await c.client.get(path + '?q=%25', headers=headers)).json()['total'] == 0


@pytest.mark.asyncio
async def test_accounting_category_keeps_fixed_template_and_filters_linked_batch_rows(review_context):
    c = review_context
    await mark_paid(c)
    general = await add_general_orders(c)
    batch = (await upload(c, 'newebpay', statement_file('newebpay', [
        {**payment_row(), '訂單交易日期': '2030-01-02'},
        {**payment_row(reference=general[1].order_number, external='group'), '訂單交易日期': '2030-01-02'},
        {**payment_row(reference='OUTSIDE', external='unknown'), '訂單交易日期': '2030-01-02'},
    ]))).json()
    path = '/v1/admin/financial-statements/accounting-export'
    params = {'source': 'newebpay', 'starts_on': '2030-01-02', 'ends_on': '2030-01-02', 'batch_id': batch['id'], 'order_category': 'general'}
    exported = await c.client.get(path, params=params, headers=auth_headers(c.admin))
    assert exported.status_code == 200
    table = spreadsheet_values(exported.content, 'newebpay')
    assert len(table) == 2
    assert table[1][table[0].index('商店訂單編號')] == general[1].order_number
    assert '訂單分類' not in table[0]
    invoice = c.order.invoice
    invoice.sales_amount = 200
    invoice.provider_request = {'salesAmount': '200', 'freetaxSalesamount': '0', 'zerotaxSalesamount': '0', 'taxType': '1'}
    c.session.add(Invoice(order_id=general[1].id, provider='fanyu', relate_number='CATEGORYGROUPINV', status=InvoiceStatus.ISSUED,
        invoice_number='CD12345678', invoice_date=c.now, total_amount=200, sales_amount=200, provider_request=invoice.provider_request))
    await c.session.commit()
    params = {'source': 'invoice', 'starts_on': (c.now-timedelta(days=1)).date().isoformat(), 'ends_on': (c.now+timedelta(days=1)).date().isoformat(), 'order_category': 'general'}
    exported = await c.client.get(path, params=params, headers=auth_headers(c.admin))
    assert exported.status_code == 200, exported.text if exported.status_code != 200 else ''
    table = spreadsheet_values(exported.content, 'invoice')
    assert len(table) == 2 and len(table[0]) == 27 and table[1][0] == 'CD12345678'
