import csv
import io
from datetime import timedelta
from uuid import uuid4

import pytest

from app.models import Invoice, InvoiceStatus, MembershipType, Order, OrderKind, PaymentMethod, SalesChannel, WalkInSale
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
async def test_invoice_register_projects_walk_in_categories_in_screen_and_csv(review_context):
    c = review_context
    c.client._transport.app.include_router(invoice_reports_router)
    counter_orders = {
        category: Order(
            order_number=number, customer_kind='walk_in', user_id=None,
            order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.REGULAR,
            payment_method=PaymentMethod.CASH_ON_PICKUP, membership_type_snapshot=MembershipType.NONMEMBER,
            amount_total=120, contact_email='',
        )
        for category, number in (('meal', 'COUNTER-MEAL'), ('product', 'COUNTER-PRODUCT'), (None, 'COUNTER-UNKNOWN'))
    }
    c.session.add_all(counter_orders.values())
    await c.session.flush()
    c.session.add_all([
        WalkInSale(order_id=order.id, client_request_id=str(uuid4()), request_fingerprint='a' * 64,
                   operator_id=c.admin.id, sale_category=category)
        for category, order in counter_orders.items()
    ])
    await c.session.commit()

    headers = auth_headers(c.admin)
    path = '/v1/admin/invoice-register'
    expected = {
        'meal': {c.order.order_number: '便當訂單', 'COUNTER-MEAL': '現場便當'},
        'general': {'COUNTER-PRODUCT': '現場一般商品'},
        'walk_in_unclassified': {'COUNTER-UNKNOWN': '現場未分類'},
    }
    expected['all'] = {number: label for entries in expected.values() for number, label in entries.items()}
    for category, labels in expected.items():
        params = {'order_category': category}
        page = await c.client.get(path, params=params, headers=headers)
        exported = await c.client.get(path + '.csv', params=params, headers=headers)
        assert page.status_code == exported.status_code == 200
        assert page.json()['total'] == len(labels)
        items = page.json()['items']
        assert {row['order_number']: row['financial_order_category_label'] for row in items} == labels
        assert all(row['sales_channel'] == 'regular' for row in items if row['order_number'].startswith('COUNTER-'))
        table = csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff')))
        assert {row['訂單編號']: row['訂單分類'] for row in table} == labels

    content = statement_file('newebpay', [
        {**payment_row(reference=order.order_number, external=f'COUNTER{index}'), '訂單交易日期': '2030-01-02'}
        for index, order in enumerate(counter_orders.values(), 1)
    ])
    uploaded = await upload(c, 'newebpay', content)
    assert uploaded.status_code == 201
    batch_id = uploaded.json()['id']
    statements = '/v1/admin/financial-statements'
    statement = f'{statements}/{batch_id}'
    for filter_key, counter_key in (('meal', 'meal'), ('general', 'product'), ('walk_in_unclassified', None)):
        order = counter_orders[counter_key]
        label = expected[filter_key][order.order_number]
        params = {'order_category': filter_key}
        detail = await c.client.get(statement, params=params, headers=headers)
        assert detail.status_code == 200
        assert detail.json()['total'] == 1
        assert detail.json()['items'][0]['financial_order_category_label'] == label
        report = await c.client.get(statement + '/reconciliation.csv', params=params, headers=headers)
        assert report.status_code == 200
        reconciled = list(csv.DictReader(io.StringIO(report.text.lstrip('\ufeff'))))
        assert [(row['網站訂單編號'], row['訂單分類']) for row in reconciled] == [(order.order_number, label)]
        options = await c.client.get(statements + '/order-options', params={**params, 'q': 'COUNTER-'}, headers=headers)
        assert options.status_code == 200
        assert [(row['id'], row['financial_order_category_label']) for row in options.json()] == [(order.id, label)]

        accounting = await c.client.get(statements + '/accounting-export', headers=headers, params={
            **params, 'source': 'newebpay', 'batch_id': batch_id,
            'starts_on': '2030-01-02', 'ends_on': '2030-01-02',
        })
        assert accounting.status_code == 200
        template = spreadsheet_values(accounting.content, 'newebpay')
        assert '訂單分類' not in template[0] and len(template) == 2
        assert template[1][template[0].index('商店訂單編號')] == order.order_number


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
