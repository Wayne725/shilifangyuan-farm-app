import csv
import io
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, FinancialStatementImport, InvoiceStatus, PaymentStatus, Refund, RefundStatus
from app.routers.financial_statements import router
from app.statement_formats import MAX_FILE_BYTES, SOURCES, money, parse_statement
from tests.support import auth_headers
from tests.test_meal_cancellation_review import review_context, mark_paid


def statement_file(source, rows):
    columns = SOURCES[source][3].split('|')
    values = [[row.get(key, '') for key in columns] for row in rows]
    if source == 'newebpay':
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerows([columns, *values])
        return b'\xef\xbb\xbf' + output.getvalue().encode()
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = SOURCES[source][2]
    sheet.append(columns)
    for row in values:
        sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def payment_row(reference='REVIEW-TRADE-0001', amount='200', external='0000123456789012345678'):
    return {'商店訂單編號': reference, '藍新金流交易序號': external, '訂單總金額': amount, '訂單支付狀態': '已付款',
            '主支付手續費': '2.50', '支付日期': '2030-01-02 12:00:00'}


async def upload(c, source, content):
    c.client._transport.app.include_router(router)
    return await c.client.post('/v1/admin/financial-statements', headers=auth_headers(c.admin), data={'source': source},
                               files={'file': ('明細' + SOURCES[source][1], content)})


def test_csv_preserves_identifier_precision_bom_and_duplicate_records():
    records, skipped = parse_statement(statement_file('newebpay', [payment_row(), payment_row()]), 'file.csv', 'newebpay')
    assert records[0]['external_id'] == '0000123456789012345678'
    assert records[0]['fee'] == '2.50'
    assert records[0]['duplicate'] is False and records[1]['duplicate'] is True
    assert skipped == 0


def test_jkopay_total_is_not_a_transaction_and_refunds_are_separate():
    content = statement_file('jkopay', [{'編號': '1', '交易行為': '支付', '訂單編號': '00001', '訂單金額': '1,200'},
                                       {'編號': '2', '交易行為': '退款', '訂單編號': '00001', '訂單金額': '200'}, {'編號': '總計', '訂單金額': '=SUM(G2:G3)'}])
    records, skipped = parse_statement(content, 'file.xlsx', 'jkopay')
    assert len(records) == 2 and skipped == 1
    assert records[0]['amount'] == '1200' and records[1]['kind'] == 'refund'
    assert not records[1]['duplicate']


def test_linepay_refund_keeps_actual_negative_fees_and_payout():
    content = statement_file('linepay', [{'交易號碼': '00991', '訂單號碼': 'ORDER1', '支付總額': '-100', '手續費合計': '-2.25', '預計撥款金額': '-97.75'}])
    record = parse_statement(content, 'file.xlsx', 'linepay')[0][0]
    assert record['kind'] == 'refund' and record['amount'] == '100'
    assert record['fee'] == '-2.25' and record['payout'] == '-97.75'


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '=1+1', '1000000000000', ''])
def test_invalid_money_is_not_silently_replaced_by_zero(value):
    with pytest.raises(ValueError):
        money(value)


def test_wrong_source_extension_headers_formulas_and_large_files_are_rejected():
    with pytest.raises(ValueError, match='原始檔'):
        parse_statement(b'fake', 'file.xlsx', 'esun')
    with pytest.raises(ValueError, match='樣板'):
        parse_statement(b'wrong,columns\n1,2', 'file.csv', 'newebpay')
    with pytest.raises(ValueError, match='公式'):
        parse_statement(statement_file('newebpay', [payment_row(reference='=HYPERLINK("https://example.test")')]), 'file.csv', 'newebpay')
    with pytest.raises(ValueError, match='5 MB'):
        parse_statement(b'x' * (MAX_FILE_BYTES + 1), 'file.csv', 'newebpay')


@pytest.mark.asyncio
async def test_import_reconcile_exact_original_download_dedup_and_full_csv(review_context):
    c = review_context
    await mark_paid(c)
    original = statement_file('newebpay', [payment_row(), payment_row(amount='199', external='2'), payment_row(reference='OTHER', external='3')])
    response = await upload(c, 'newebpay', original)
    assert response.status_code == 201, response.text
    batch = response.json()
    path = f"/v1/admin/financial-statements/{batch['id']}"
    result = (await c.client.get(path + '?limit=1', headers=auth_headers(c.admin))).json()
    assert result['summary'] == {'matched': 1, 'difference': 1, 'unmatched': 1}
    assert len(result['items']) == 1 and result['total'] == 3
    duplicate = await upload(c, 'newebpay', original)
    assert duplicate.json()['already_imported'] and duplicate.json()['id'] == batch['id']
    assert await c.session.scalar(select(func.count(FinancialStatementImport.id))) == 1
    downloaded = await c.client.get(path + '/original', headers=auth_headers(c.admin))
    assert downloaded.content == original
    assert downloaded.headers['cache-control'] == 'no-store'
    assert 'filename*=UTF-8' in downloaded.headers['content-disposition']
    report = await c.client.get(path + '/reconciliation.csv?limit=1&offset=1', headers=auth_headers(c.admin))
    rows = list(csv.reader(io.StringIO(report.text.lstrip('\ufeff'))))
    assert len(rows) == 4 and rows[2][11] == '-1'
    assert 'file_content' not in result and 'provider_response' not in result
    assert c.order.payment_status == PaymentStatus.PAID and c.order.invoice_status == InvoiceStatus.ISSUED
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == 'statement.imported')) == 1


@pytest.mark.asyncio
async def test_amount_alone_never_links_bank_row_and_manual_link_does_not_mark_paid(review_context):
    c = review_context
    c.client._transport.app.include_router(router)
    batch = FinancialStatementImport(source='esun', filename='bank.xls', checksum='0' * 64, file_content=b'original', uploaded_by_id=c.admin.id, rows=[
        {'row_number': 7, 'reference': '', 'external_id': '', 'kind': 'bank_credit', 'amount': '200', 'fee': None, 'payout': None,
         'occurred_at': '2030-01-02', 'state': '', 'tax': None, 'duplicate': False, 'manual_order_id': None}])
    c.session.add(batch)
    await c.session.commit()
    path = f'/v1/admin/financial-statements/{batch.id}'
    headers = auth_headers(c.admin)
    assert (await c.client.get(path, headers=headers)).json()['summary']['unmatched'] == 1
    linked = await c.client.put(path + '/rows/7/match', headers=headers, json={'order_id': c.order.id})
    assert linked.status_code == 200
    row = (await c.client.get(path, headers=headers)).json()['items'][0]
    assert row['result'] == 'difference' and '網站尚未確認收款' in row['reasons']
    assert c.order.payment_status != PaymentStatus.PAID
    assert (await c.client.put(path + '/rows/999/match', headers=headers, json={'order_id': c.order.id})).status_code == 404
    assert (await c.client.put(path + '/rows/7/match', headers=headers, json={'order_id': 'missing'})).status_code == 404
    await c.client.put(path + '/rows/7/match', headers=headers, json={'order_id': None})
    assert (await c.client.get(path, headers=headers)).json()['summary']['unmatched'] == 1


@pytest.mark.asyncio
async def test_invoice_tax_and_void_status_use_invoice_snapshot_and_do_not_update_website(review_context):
    c = review_context
    await mark_paid(c)
    invoice = {'發票號碼': 'AB12345678', '發票狀態': '作廢已確認', '總計': '200', '營業稅': '1', '應稅銷售額': '199', '免稅銷售額': '0'}
    response = await upload(c, 'invoice', statement_file('invoice', [invoice]))
    assert response.status_code == 201, response.text
    path = f"/v1/admin/financial-statements/{response.json()['id']}"
    row = (await c.client.get(path, headers=auth_headers(c.admin))).json()['items'][0]
    assert row['expected_amount'] == '200' and row['difference'] == '0'
    assert set(row['reasons']) == {'發票狀態不符', '發票稅額不符'}
    assert c.order.invoice_status == InvoiceStatus.ISSUED


@pytest.mark.asyncio
async def test_refund_requires_completed_record_and_does_not_use_pending_refund(review_context):
    c = review_context
    attempt = await mark_paid(c)
    c.session.add(Refund(order=c.order, payment_attempt=attempt, amount=200, status=RefundStatus.PENDING, requested_by_id=c.admin.id, reason='測試'))
    await c.session.commit()
    response = await upload(c, 'linepay', statement_file('linepay', [{'交易號碼': '1', '訂單號碼': attempt.merchant_trade_no, '支付總額': '-200'}]))
    path = f"/v1/admin/financial-statements/{response.json()['id']}"
    result = (await c.client.get(path, headers=auth_headers(c.admin))).json()
    assert result['summary']['difference'] == 1
    assert result['items'][0]['expected_amount'] is None


@pytest.mark.asyncio
async def test_all_statement_routes_are_admin_only(review_context):
    c = review_context
    c.client._transport.app.include_router(router)
    for headers, expected in [({}, 401), (auth_headers(c.buyer), 403)]:
        for path in ['', '/order-options', '/missing', '/missing/original', '/missing/reconciliation.csv']:
            assert (await c.client.get('/v1/admin/financial-statements' + path, headers=headers)).status_code == expected
        assert (await c.client.post('/v1/admin/financial-statements', headers=headers, data={'source': 'newebpay'}, files={'file': ('test.csv', b'invalid')})).status_code == expected
        assert (await c.client.put('/v1/admin/financial-statements/missing/rows/7/match', headers=headers, json={'order_id': None})).status_code == expected


@pytest.mark.asyncio
async def test_provider_transaction_number_does_not_match_a_website_order_number(review_context):
    c = review_context
    await mark_paid(c)
    content = statement_file('newebpay', [payment_row(reference='OUTSIDE-ORDER', external=c.order.order_number)])
    response = await upload(c, 'newebpay', content)
    path = f"/v1/admin/financial-statements/{response.json()['id']}"
    row = (await c.client.get(path, headers=auth_headers(c.admin))).json()['items'][0]
    assert row['result'] == 'unmatched' and row['order_number'] is None
