import io
import re
from copy import copy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import openpyxl
import xlrd
import xlwt
from fastapi import HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import undefer
from starlette.concurrency import run_in_threadpool

from .domain import aware
from .csv_export import csv_response
from .meal_booking import TAIPEI
from .models import FinancialStatementImport, Invoice, InvoiceStatus, Order
from .order_categories import order_category_condition
from .statement_reconciliation import reconcile_statement, filter_reconciliation
from .statement_formats import SOURCES, _tables, parse_statement, text


def local_time(value):
    return aware(value).astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M:%S') if value else ''


def transaction_date(value):
    if isinstance(value, datetime):
        return value.astimezone(TAIPEI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    match = re.match(r'^(\d{3,4})[-/](\d{1,2})[-/](\d{1,2})(?:\D|$)', text(value))
    if not match:
        raise ValueError('交易日期無法辨識，請確認原始明細')
    year, month, day = map(int, match.groups())
    return date(year + 1911 if year < 1911 else year, month, day)


def validate_period(start, end):
    if start > end:
        raise HTTPException(422, '結束日期不可早於開始日期')


async def website_invoices(session, start, end, order_category='all'):
    invoices = list(await session.scalars(select(Invoice).join(Order, Order.id == Invoice.order_id).where(
        order_category_condition(order_category),
        Invoice.invoice_number.is_not(None),
        Invoice.invoice_date >= datetime.combine(start, time.min, TAIPEI).astimezone(timezone.utc),
        Invoice.invoice_date < datetime.combine(end + timedelta(days=1), time.min, TAIPEI).astimezone(timezone.utc),
        Invoice.status.in_([InvoiceStatus.ISSUED, InvoiceStatus.VOID_PENDING, InvoiceStatus.VOIDED]),
    ).order_by(Invoice.invoice_date, Invoice.invoice_number).limit(10001)))
    if len(invoices) > 10000:
        raise HTTPException(422, '每次最多匯出 10,000 筆，請縮小日期範圍')
    rows = []
    for invoice in invoices:
        request = invoice.provider_request or {}
        tax_type = text(request.get('taxType') or request.get('TaxType'))
        taxable = request.get('salesAmount')
        exempt = request.get('freetaxSalesamount')
        zero = request.get('zerotaxSalesamount')
        if taxable is None and tax_type in {'1', '3'}:
            taxable = invoice.sales_amount if tax_type == '1' else 0
            exempt = invoice.sales_amount if tax_type == '3' else 0
            zero = 0
        if any(value is None for value in [taxable, exempt, zero]):
            raise HTTPException(409, f'發票 {invoice.invoice_number} 缺少稅別金額快照，請先匯入發票明細再匯出')
        sales = sum(Decimal(str(value)) for value in [taxable, exempt, zero])
        if sales + invoice.tax_amount != invoice.total_amount:
            raise HTTPException(409, f'發票 {invoice.invoice_number} 金額與稅額不一致，請先核對發票')
        rows.append([
            invoice.invoice_number, '', '35',
            {InvoiceStatus.ISSUED: '已開立', InvoiceStatus.VOID_PENDING: '作廢處理中', InvoiceStatus.VOIDED: '已作廢'}[invoice.status],
            local_time(invoice.invoice_date), invoice.buyer_tax_id or request.get('buyerID', ''),
            invoice.buyer_name or request.get('buyerName', ''), request.get('sellerID', ''), request.get('sellerName', ''), '',
            str(sales), str(taxable), str(zero), str(exempt), str(invoice.tax_amount), str(invoice.total_amount),
            {'1': '應稅', '2': '零稅率', '3': '免稅', '9': '混合應稅與免稅或零稅率'}.get(tax_type, ''),
            '', request.get('carrierType', ''), request.get('carrierID1', ''), request.get('carrierID2', ''),
            request.get('mainRemark') or request.get('InvoiceRemark', ''), '', local_time(invoice.updated_at), '', '', '',
        ])
    if not rows:
        raise HTTPException(404, '期間內沒有已取得發票號碼的發票')
    return rows


def batch_rows(content, filename, source, start, end):
    records, _ = parse_statement(content, filename, source)
    table = _tables(content, source)
    date_columns = {'newebpay': 1, 'jkopay': 1, 'esun': 2, 'linepay': 3, 'invoice': 4}
    selected = []
    for record in records:
        values = list(table[record['row_number'] - 1][:len(SOURCES[source][3].split('|'))])
        try:
            day = transaction_date(values[date_columns[source]])
        except ValueError as exc:
            raise HTTPException(422, f"第 {record['row_number']} 列：{exc}") from None
        if start <= day <= end:
            selected.append((record['row_number'], values))
    if not selected:
        raise HTTPException(404, '此明細檔在所選期間沒有資料')
    return selected


def _string_cell(sheet, row, column, value):
    cell = sheet.cell(row, column, value)
    if isinstance(value, str):
        cell.data_type = 's'
    return cell


def xlsx_export(source, selected, original=None):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = SOURCES[source][2]
    columns = SOURCES[source][3].split('|')
    if source == 'invoice':
        columns[17] = '匯率\n'
    source_book = openpyxl.load_workbook(io.BytesIO(original), keep_links=False) if original else None
    source_sheet = source_book[sheet.title] if source_book else None
    if source_sheet:
        sheet.sheet_format = copy(source_sheet.sheet_format)
        for key, dimension in source_sheet.column_dimensions.items():
            sheet.column_dimensions[key] = copy(dimension)
    for column, value in enumerate(columns, 1):
        _string_cell(sheet, 1, column, value)
        if not source_sheet:
            sheet.column_dimensions[openpyxl.utils.get_column_letter(column)].width = 22
            sheet.cell(1, column).font = openpyxl.styles.Font(bold=True)
    for output_row, (source_row, values) in enumerate(selected, 2):
        if source_sheet:
            sheet.row_dimensions[output_row].height = source_sheet.row_dimensions[source_row].height
        for column, value in enumerate(values, 1):
            # Copy styles through public descriptors so IDs belong to the new workbook.
            cell = _string_cell(sheet, output_row, column, value)
            if source_sheet:
                original_cell = source_sheet.cell(source_row, column)
                for attribute in ['font', 'fill', 'border', 'alignment', 'protection']:
                    setattr(cell, attribute, copy(getattr(original_cell, attribute)))
                cell.number_format = original_cell.number_format
    if source_sheet:
        for column in range(1, len(columns) + 1):
            cell, original_cell = sheet.cell(1, column), source_sheet.cell(1, column)
            for attribute in ['font', 'fill', 'border', 'alignment', 'protection']:
                setattr(cell, attribute, copy(getattr(original_cell, attribute)))
            cell.number_format = original_cell.number_format
        sheet.row_dimensions[1].height = source_sheet.row_dimensions[1].height
        source_book.close()
    if source == 'jkopay':
        sheet.append(['總計', '', '', '', '', '', *[sum(Decimal(text(values[column]).replace(',', '') or '0') for _, values in selected) for column in range(6, 12)], ''])
        for column in range(7, 13):
            sheet.cell(sheet.max_row, column).number_format = '#,##0'
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = f'A1:{openpyxl.utils.get_column_letter(len(columns))}{len(selected) + 1}'
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def xls_export(selected, original, start, end):
    source_book = xlrd.open_workbook(file_contents=original, formatting_info=True)
    source_sheet = source_book.sheet_by_name(SOURCES['esun'][2])
    book = xlwt.Workbook()
    sheet = book.add_sheet(source_sheet.name)
    for column in range(10):
        sheet.col(column).width = source_sheet.colinfo_map.get(column).width if column in source_sheet.colinfo_map else 5000
    # Preserve the account heading, but replace the period and totals for this export.
    for row in range(5):
        for column in range(10):
            value = source_sheet.cell_value(row, column)
            if value and (row, column) != (4, 1):
                sheet.write(row, column, value)
    sheet.write(4, 1, f'{start:%Y/%m/%d} 00:00:00 ~ {end:%Y/%m/%d} 23:59:59')
    for column, value in enumerate(SOURCES['esun'][3].split('|')):
        sheet.write(5, column, value, xlwt.easyxf('font: bold on;'))
    for output_row, (_, values) in enumerate(selected, 6):
        for column, value in enumerate(values):
            sheet.write(output_row, column, str(output_row - 5) if column == 0 else value)
    footer = len(selected) + 6
    sheet.write(footer, 5, '提')
    sheet.write(footer, 6, '存')
    for column, value in [(1, '總計'), (2, '共'), (3, str(len(selected))), (4, '筆')]:
        sheet.write(footer + 1, column, value)
    for column in [5, 6]:
        total = sum(Decimal(text(values[column]).replace(',', '') or '0') for _, values in selected)
        sheet.write(footer + 1, column, str(total))
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


async def export_accounting(session, source, start, end, batch_id, order_category='all'):
    validate_period(start, end)
    if batch_id:
        batch = await session.scalar(select(FinancialStatementImport).where(FinancialStatementImport.id == batch_id).options(undefer(FinancialStatementImport.file_content), undefer(FinancialStatementImport.rows)))
        if batch is None:
            raise HTTPException(404, '找不到匯入明細')
        if batch.source != source:
            raise HTTPException(422, '明細檔與匯出格式不符')
        content = batch.file_content
        selected = await run_in_threadpool(batch_rows, content, batch.filename, source, start, end)
        if order_category != 'all':
            matched_rows = filter_reconciliation(await reconcile_statement(session, batch), order_category=order_category)
            row_numbers = {row['row_number'] for row in matched_rows}
            selected = [row for row in selected if row[0] in row_numbers]
            if not selected:
                raise HTTPException(404, '所選期間與訂單分類沒有已連結的明細')
    elif source == 'invoice':
        content = None
        selected = list(enumerate(await website_invoices(session, start, end, order_category), 2))
    else:
        raise HTTPException(409, '請先選擇已匯入的金流或銀行明細檔')
    body = await run_in_threadpool(render_export, source, selected, content, start, end)
    return body, len(selected)


def render_export(source, selected, content, start, end):
    if source == 'newebpay':
        rows = [[Decimal(text(value).replace(',', '')) if column in {8, 9, 11} and text(value) else value
                 for column, value in enumerate(values)] for _, values in selected]
        body = csv_response('statement.csv', SOURCES[source][3].split('|'), rows).body
    elif source == 'esun':
        body = xls_export(selected, content, start, end)
    else:
        body = xlsx_export(source, selected, content)
    return body


def accounting_response(body, source, start, end):
    extension = SOURCES[source][1]
    mime = {'.csv': 'text/csv', '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}[extension]
    return Response(body, media_type=mime, headers={
        'Content-Disposition': f'attachment; filename="{source}-{start}-{end}{extension}"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })
