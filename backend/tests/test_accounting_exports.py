import csv
import io
from datetime import datetime, timezone

import openpyxl
import pytest
import xlrd
import xlwt
from sqlalchemy import func, select

from app.accounting_exports import batch_rows, render_export, transaction_date
from app.models import AdminAudit, InvoiceStatus
from app.routers.financial_statements import router
from app.statement_formats import SOURCES, parse_statement
from tests.support import auth_headers
from tests.test_financial_statements import payment_row, statement_file, upload
from tests.test_meal_cancellation_review import review_context, mark_paid


def spreadsheet_values(content, source):
    if source == 'newebpay':
        return list(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
    if source == 'esun':
        return [row for row in xlrd.open_workbook(file_contents=content).sheet_by_index(0).get_rows()]
    book = openpyxl.load_workbook(io.BytesIO(content))
    assert book.sheetnames == [SOURCES[source][2]]
    assert not any(cell.data_type == 'f' for row in book.active for cell in row)
    return list(book.active.values)


@pytest.mark.parametrize('source', ['newebpay', 'jkopay', 'linepay', 'invoice'])
def test_accounting_export_preserves_template_columns_values_and_filters_dates(source):
    rows = {
        'newebpay': [{**payment_row(), '訂單交易日期': '2030-01-02 23:59:59'}, {**payment_row(external='2'), '訂單交易日期': '2030-01-03 00:00:00'}],
        'jkopay': [{'編號': '1', '交易時間': datetime(2030, 1, 2, 23, 59), '訂單編號': '00001234567890123456', '交易行為': '支付', '訂單金額': 200, '支付金額': 200}],
        'linepay': [{'交易號碼': '00001234567890123456', '交易日期': '2030-01-02 23:59:59', '支付總額': '-200', '手續費合計': '-2.12345678901234567', '預計撥款金額': '-197.87654321098765433'}],
        'invoice': [{'發票號碼': 'AB12345678', '發票日期': '2030-01-02 23:59:59', '發票狀態': '開立已確認', '總計': '200', '應稅銷售額': '200', '免稅銷售額': '0', '營業稅': '0', '買方統一編號': '00123456', 'MIG訊息類別': 'C0401'}],
    }[source]
    original = statement_file(source, rows)
    start = end = datetime(2030, 1, 2).date()
    selected = batch_rows(original, 'file' + SOURCES[source][1], source, start, end)
    assert len(selected) == 1
    body = render_export(source, selected, original, start, end)
    table = spreadsheet_values(body, source)
    assert [str(value).strip() for value in table[0]] == SOURCES[source][3].split('|')
    exported, _ = parse_statement(body, 'export' + SOURCES[source][1], source)
    assert len(exported) == 1
    assert exported[0]['external_id'] == parse_statement(original, 'file' + SOURCES[source][1], source)[0][0]['external_id']
    if source == 'jkopay':
        assert table[-1][0] == '總計' and table[-1][6] == 200
        assert isinstance(table[1][1], datetime)
    if source == 'linepay':
        assert table[1][23] == '-2.12345678901234567' and table[1][24] == '-197.87654321098765433'
    if source == 'invoice':
        assert table[0][17] == '匯率\n' and table[1][5] == '00123456' and table[1][24] == 'C0401'
    if source == 'newebpay':
        assert body.startswith(b'\xef\xbb\xbf') and b'\r\n' in body


def test_bank_export_is_real_xls_preserves_balance_and_recalculates_period_totals():
    book = xlwt.Workbook()
    sheet = book.add_sheet('交易明細查詢')
    for row, values in enumerate([['交易名稱', '交易明細查詢'], ['查詢人員', '測試'], ['顧客ID', '測試'], ['帳號', '00001'], ['查詢區間', '舊期間']]):
        for column, value in enumerate(values):
            sheet.write(row, column, value)
    for column, value in enumerate(SOURCES['esun'][3].split('|')):
        sheet.write(5, column, value)
    for row, values in enumerate([
        ['1', '2030/01/02', '2030/01/02', '12:00:00', '存款', '0', '1200', '2500', 'ORDER1', '0000000000001'],
        ['2', '2030/01/03', '2030/01/03', '12:00:00', '存款', '0', '200', '2700', 'ORDER2', '0000000000001'],
    ], 6):
        for column, value in enumerate(values):
            sheet.write(row, column, value)
    output = io.BytesIO(); book.save(output)
    original = output.getvalue()
    day = datetime(2030, 1, 2).date()
    selected = batch_rows(original, 'bank.xls', 'esun', day, day)
    body = render_export('esun', selected, original, day, day)
    assert body.startswith(b'\xd0\xcf\x11\xe0')
    exported = xlrd.open_workbook(file_contents=body).sheet_by_name('交易明細查詢')
    assert exported.row_values(5) == SOURCES['esun'][3].split('|')
    assert exported.cell_value(6, 7) == '2500'
    assert exported.cell_value(6, 9) == '0000000000001'
    assert exported.cell_value(4, 1) == '2030/01/02 00:00:00 ~ 2030/01/02 23:59:59'
    assert exported.cell_value(8, 3) == '1' and exported.cell_value(8, 6) == '1200'


@pytest.mark.asyncio
async def test_website_invoice_exports_27_columns_by_invoice_date_not_order_date(review_context):
    c = review_context
    await mark_paid(c)
    invoice = c.order.invoice
    invoice.invoice_date = datetime(2030, 1, 31, 16, tzinfo=timezone.utc)
    invoice.sales_amount = 200
    invoice.provider_request = {'sellerID': '00123456', 'buyerID': '0000000000', 'salesAmount': '0', 'freetaxSalesamount': '200', 'zerotaxSalesamount': '0', 'taxType': '3', 'carrierType': 'EG0478', 'carrierID1': 'buyer@example.test', 'carrierID2': 'buyer@example.test'}
    await c.session.commit()
    c.client._transport.app.include_router(router)
    url = '/v1/admin/financial-statements/accounting-export?source=invoice&starts_on=2030-02-01&ends_on=2030-02-01'
    response = await c.client.get(url, headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text if response.status_code != 200 else ''
    table = spreadsheet_values(response.content, 'invoice')
    assert len(table) == 2 and len(table[0]) == 27
    assert table[1][0] == 'AB12345678' and table[1][3] == '已開立' and table[1][4] == '2030-02-01 00:00:00'
    assert table[1][7] == '00123456' and table[1][13] == '200' and table[1][16] == '免稅'
    assert all(table[1][column] is None for column in [9, 17, 22, 24, 25, 26])
    assert 'no-store' in response.headers['cache-control']
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == 'accounting.exported')) == 1
    assert (await c.client.get(url.replace('2030-02-01', '2030-01-31'), headers=auth_headers(c.admin))).status_code == 404


@pytest.mark.asyncio
async def test_batch_export_permissions_source_validation_no_data_and_audit(review_context):
    c = review_context
    batch = (await upload(c, 'newebpay', statement_file('newebpay', [{**payment_row(), '訂單交易日期': '2030-01-02'}]))).json()
    url = f"/v1/admin/financial-statements/accounting-export?source=newebpay&starts_on=2030-01-02&ends_on=2030-01-02&batch_id={batch['id']}"
    for headers, expected in [({}, 401), (auth_headers(c.buyer), 403), (auth_headers(c.admin), 200)]:
        assert (await c.client.get(url, headers=headers)).status_code == expected
    response = await c.client.get(url, headers=auth_headers(c.admin))
    assert len(spreadsheet_values(response.content, 'newebpay')) == 2
    assert (await c.client.get(url.replace('source=newebpay', 'source=invoice'), headers=auth_headers(c.admin))).status_code == 422
    assert (await c.client.get(url.replace('starts_on=2030-01-02', 'starts_on=2030-01-03'), headers=auth_headers(c.admin))).status_code == 422
    assert (await c.client.get(url.replace('&batch_id=' + batch['id'], ''), headers=auth_headers(c.admin))).status_code == 409
    assert (await c.client.get(url.replace('2030-01-02', '2030-01-04'), headers=auth_headers(c.admin))).status_code == 404
    listing = await c.client.get('/v1/admin/financial-statements?source=invoice', headers=auth_headers(c.admin))
    assert listing.json() == {'items': [], 'total': 0}


@pytest.mark.asyncio
async def test_invoice_export_blocks_unknown_tax_snapshot_and_does_not_fake_zero(review_context):
    c = review_context
    await mark_paid(c)
    c.client._transport.app.include_router(router)
    url = f'/v1/admin/financial-statements/accounting-export?source=invoice&starts_on={c.now.date()}&ends_on={c.now.date()}'
    response = await c.client.get(url, headers=auth_headers(c.admin))
    assert response.status_code == 409 and '稅別金額快照' in response.text
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == 'accounting.exported')) == 0


def test_roc_dates_are_read_explicitly():
    assert transaction_date('119/01/02 12:00:00').isoformat() == '2030-01-02'
    with pytest.raises(ValueError):
        transaction_date('unknown')
