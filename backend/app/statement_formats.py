import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from itertools import islice
from zipfile import ZipFile

import openpyxl
import xlrd

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000
SOURCES = {
    'newebpay': ('藍新金流', '.csv', None, '商店代號|訂單交易日期|藍新金流交易序號|商店訂單編號|主支付方式|主支付方式對應碼|收單機構|門市代號|主支付金額|主支付手續費|主支付款項保管銀行|訂單總金額|金流串接廠商|支付日期|交易截止日|訂單支付狀態|預計撥款日|是否撥款|IP位址|主支付狀態|撥款單位|支付型態'),
    'jkopay': ('街口支付', '.xlsx', '店家交易明細', '編號|交易時間|訂單編號|廠商端訂單編號|店舖名稱|交易行為|訂單金額|支付金額|街口幣折抵|官方街口券折抵|店家街口券折抵|店家回饋街口幣|自訂代碼'),
    'esun': ('玉山銀行', '.xls', '交易明細查詢', '序號|帳務日期|實際交易日期|實際交易時間|摘要|提|存|餘額|備註|轉出入銀行代號/帳號'),
    'linepay': ('LINE Pay', '.xlsx', 'Settlement Transactions', '交易號碼|原始交易號碼|訂單號碼|交易日期|請款日期|預計撥款日|付款方式|服務提供者|商家 ID|分店名稱|裝置ID|商品名稱|商品金額|點數折抵金額|商品金額折扣(商家)|優惠券折扣(商家)|商店優惠|商品金額折扣(LINE Pay)|優惠券折扣(LINE Pay)|LINE Pay優惠|支付總額|手續費|營業稅|手續費合計|預計撥款金額'),
    'invoice': ('發票明細', '.xlsx', 'Invoice', '發票號碼|買受人註記|格式代號|發票狀態|發票日期|買方統一編號|買方名稱|賣方統一編號|賣方名稱|寄送日期|銷售額合計|應稅銷售額|零稅銷售額|免稅銷售額|營業稅|總計|課稅別|匯率|載具類別編號|載具號碼1|載具號碼2|總備註|開立確認時間|最後異動時間|MIG訊息類別|傳送方統編|傳送方名稱'),
}


def text(value):
    if value is None:
        return ''
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def money(value, *, optional=False):
    value = text(value).replace(',', '')
    if not value and optional:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError('金額必須是數字') from None
    if not amount.is_finite() or abs(amount) > Decimal('999999999999') or amount.as_tuple().exponent < -18:
        raise ValueError('金額超過範圍或小數位數過多')
    return format(amount, 'f')


def _tables(content, source):
    _, extension, sheet_name, _ = SOURCES[source]
    if extension == '.csv':
        try:
            decoded = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded = content.decode('cp950')
        return list(islice(csv.reader(io.StringIO(decoded)), MAX_ROWS + 31))
    if extension == '.xls':
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
        try:
            sheet = book.sheet_by_name(sheet_name)
            if sheet.nrows > MAX_ROWS + 30 or sheet.ncols > 32:
                raise ValueError('明細超過 10,000 筆或欄數過多')
            return [sheet.row_values(i) for i in range(sheet.nrows)]
        finally:
            book.release_resources()
    with ZipFile(io.BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 30 * 1024 * 1024 or len(archive.infolist()) > 200:
            raise ValueError('Excel 解壓後超過檔案限制')
        if any(info.filename.lower().endswith('vbaproject.bin') for info in archive.infolist()):
            raise ValueError('請使用不含巨集的原始明細')
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
    try:
        sheet = book[sheet_name]
        if sheet.max_row > MAX_ROWS + 30 or sheet.max_column > 32:
            raise ValueError('明細超過 10,000 筆或欄數過多')
        return list(islice(sheet.values, MAX_ROWS + 31))
    finally:
        book.close()


def parse_statement(content: bytes, filename: str, source: str):
    if source not in SOURCES:
        raise ValueError('請選擇明細來源')
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError('請選擇 5 MB 以內的明細檔')
    extension = SOURCES[source][1]
    if Path(filename).suffix.lower() != extension:
        raise ValueError(f'此來源請使用 {extension} 原始檔')
    try:
        table = _tables(content, source)
    except ValueError:
        raise
    except Exception:
        raise ValueError('無法讀取明細，請使用原始下載檔及正確的來源') from None
    columns = SOURCES[source][3].split('|')
    header = next((i for i, row in enumerate(table[:20]) if [text(v) for v in row[:len(columns)]] == columns), None)
    if header is None:
        raise ValueError('欄位名稱或順序與此來源樣板不符，請確認明細來源')
    if len(table) > MAX_ROWS + 30:
        raise ValueError('每次最多匯入 10,000 筆明細')
    records = []
    references = set()
    skipped = 0
    for row_number, row in enumerate(table[header + 1:], header + 2):
        values = [text(v) for v in row]
        if not any(values) or any(v in {'總計', '合計'} for v in values[:2]) or (source == 'esun' and not values[0].isdigit()):
            skipped += 1
            continue
        if len(values) < len(columns) or any(len(v) > 2000 for v in values):
            raise ValueError(f'第 {row_number} 列欄位不完整或文字過長')
        if any(v.startswith('=') for v in values):
            raise ValueError(f'第 {row_number} 列含公式，請提供原始交易數值')
        data = dict(zip(columns, values))
        try:
            record = _record(data, source)
        except ValueError as exc:
            raise ValueError(f'第 {row_number} 列：{exc}') from None
        record.update(row_number=row_number, manual_order_id=None)
        key = (record['external_id'], record['kind'])
        record['duplicate'] = bool(key[0] and key in references)
        references.add(key)
        records.append(record)
    if not records:
        raise ValueError('檔案沒有可匯入的交易明細')
    if len(records) > MAX_ROWS:
        raise ValueError('每次最多匯入 10,000 筆明細')
    return records, skipped


def _record(data, source):
    record = dict(external_id='', reference='', kind='receipt', amount=None, fee=None, payout=None,
                  occurred_at='', state='', tax=None, taxable=None, exempt=None)
    if source == 'newebpay':
        state = data['訂單支付狀態']
        kind = 'receipt' if state == '已付款' else 'review'
        record.update(external_id=data['藍新金流交易序號'], reference=data['商店訂單編號'], kind=kind,
                      amount=money(data['訂單總金額']), fee=money(data['主支付手續費'], optional=True),
                      occurred_at=data['支付日期'] or data['訂單交易日期'], state=state)
    elif source == 'jkopay':
        state = data['交易行為']
        kind = 'receipt' if state in {'支付', '付款'} else 'refund' if state in {'退款', '退貨'} else 'review'
        record.update(external_id=data['訂單編號'], reference=data['廠商端訂單編號'], kind=kind,
                      amount=money(data['訂單金額']), occurred_at=data['交易時間'], state=state)
    elif source == 'linepay':
        amount = Decimal(money(data['支付總額']))
        record.update(external_id=data['交易號碼'], reference=data['訂單號碼'], kind='refund' if amount < 0 else 'receipt',
                      amount=str(abs(amount)), fee=money(data['手續費合計'], optional=True),
                      payout=money(data['預計撥款金額'], optional=True), occurred_at=data['交易日期'])
    elif source == 'esun':
        debit, credit = (Decimal(money(data[key]) or '0') for key in ['提', '存'])
        if debit < 0 or credit < 0 or (debit and credit):
            raise ValueError('支出與存入金額須為非負，且只能有一項金額')
        record.update(reference=data['備註'], kind='bank_debit' if debit else 'bank_credit',
                      amount=str(debit or credit), occurred_at=f"{data['實際交易日期']} {data['實際交易時間']}")
    else:
        record.update(external_id=data['發票號碼'], reference=data['發票號碼'], kind='invoice', amount=money(data['總計']),
                      occurred_at=data['發票日期'], state=data['發票狀態'], tax=money(data['營業稅']),
                      taxable=money(data['應稅銷售額']), exempt=money(data['免稅銷售額']))
        if not re.fullmatch(r'[A-Za-z]{2}\d{8}', record['external_id']):
            raise ValueError('發票號碼格式不正確')
    return record
