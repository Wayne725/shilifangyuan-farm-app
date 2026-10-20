from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, union_all
from sqlalchemy.orm import selectinload

from .models import Invoice, InvoiceStatus, Order, PaymentAttempt, PaymentStatus, RefundStatus

RECEIVED = {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED}
PROVIDERS = {'newebpay': {'newebpay'}, 'jkopay': {'jkopay'}, 'linepay': {'linepay'}}


async def reconcile_statement(session, batch):
    references = {row['reference'] for row in batch.rows if row['reference']}
    external_ids = {row['external_id'] for row in batch.rows if row['external_id']}
    manual_ids = {row['manual_order_id'] for row in batch.rows if row['manual_order_id']}
    found = {}
    keys = list(references | external_ids | manual_ids)
    for offset in range(0, len(keys), 400):
        chunk = keys[offset:offset + 400]
        selectors = [select(Order.id).where(Order.id.in_(chunk))] if manual_ids else []
        if batch.source == 'invoice':
            selectors.append(select(Invoice.order_id).where(Invoice.invoice_number.in_(chunk)))
        else:
            selectors += [select(Order.id).where(Order.order_number.in_(chunk)),
                          select(PaymentAttempt.order_id).where(PaymentAttempt.merchant_trade_no.in_(chunk))]
            if providers := PROVIDERS.get(batch.source):
                selectors.append(select(PaymentAttempt.order_id).where(PaymentAttempt.provider.in_(providers), PaymentAttempt.provider_trade_no.in_(chunk)))
        orders = await session.scalars(select(Order).where(Order.id.in_(union_all(*selectors))).options(
            selectinload(Order.invoice), selectinload(Order.refunds), selectinload(Order.payment_attempts)))
        found.update((order.id, order) for order in orders)
    by_reference = defaultdict(set)
    by_external_id = defaultdict(set)
    for order in found.values():
        if batch.source == 'invoice':
            if order.invoice and order.invoice.invoice_number:
                by_reference[order.invoice.invoice_number].add(order.id)
        else:
            by_reference[order.order_number].add(order.id)
            for attempt in order.payment_attempts:
                by_reference[attempt.merchant_trade_no].add(order.id)
                if attempt.provider in PROVIDERS.get(batch.source, set()) and attempt.provider_trade_no:
                    by_external_id[attempt.provider_trade_no].add(order.id)
    results = []
    for record in batch.rows:
        candidate_ids = ({record['manual_order_id']} if record['manual_order_id'] else
                         by_reference[record['reference']] | by_external_id[record['external_id']])
        order = found.get(next(iter(candidate_ids))) if len(candidate_ids) == 1 else None
        reasons = []
        amount = Decimal(record['amount'])
        expected = None
        state = None
        if record['duplicate']:
            reasons.append('同檔交易編號重複')
        if len(candidate_ids) > 1:
            reasons.append('找到多筆訂單，請人工核對')
        elif not order:
            reasons.append('尚未連結網站訂單')
        elif record['kind'] == 'review':
            reasons.append('來源交易狀態需要人工核對')
        elif record['kind'] == 'invoice':
            invoice = order.invoice
            if not invoice or invoice.invoice_number != record['external_id']:
                reasons.append('網站發票號碼不符')
            else:
                expected = Decimal(invoice.total_amount)
                state = invoice.status.value
                source_status = (InvoiceStatus.VOIDED if record['state'] == '作廢已確認' else
                                 InvoiceStatus.ISSUED if record['state'] == '開立已確認' else None)
                if source_status is None or invoice.status != source_status:
                    reasons.append('發票狀態不符')
                if Decimal(record['tax']) != invoice.tax_amount:
                    reasons.append('發票稅額不符')
                request = invoice.provider_request or {}
                if invoice.provider == 'fanyu':
                    for field, key in [('taxable', 'salesAmount'), ('exempt', 'freetaxSalesamount')]:
                        if key in request and Decimal(record[field]) != Decimal(str(request[key])):
                            reasons.append('發票銷售額分類不符')
                            break
        else:
            attempts = [item for item in order.payment_attempts if item.merchant_trade_no == record['reference'] or
                        (item.provider in PROVIDERS.get(batch.source, set()) and item.provider_trade_no == record['external_id'])]
            attempt = attempts[0] if len(attempts) == 1 else None
            if len(attempts) > 1:
                reasons.append('交易編號對應不同付款紀錄')
            elif record['kind'] in {'refund', 'bank_debit'}:
                refunds = [refund for refund in order.refunds if refund.status == RefundStatus.COMPLETED and
                           (attempt is None or refund.payment_attempt_id == attempt.id)]
                if len(refunds) == 1:
                    expected = Decimal(refunds[0].amount)
                    state = 'refunded'
                else:
                    reasons.append('需核對已完成退款紀錄')
            else:
                expected = Decimal(attempt.amount if attempt else order.amount_total)
                state = (attempt.status if attempt else order.payment_status).value
                if (attempt.status if attempt else order.payment_status) not in RECEIVED:
                    reasons.append('網站尚未確認收款')
        difference = amount - expected if expected is not None else None
        if difference:
            reasons.append('交易金額不符')
        status = 'matched' if not reasons else 'unmatched' if not order else 'difference'
        results.append({**record, 'order_id': order.id if order else None, 'order_number': order.order_number if order else None,
                        'expected_amount': str(expected) if expected is not None else None,
                        'difference': str(difference) if difference is not None else None, 'website_state': state,
                        'result': status, 'reasons': reasons})
    return results


def statement_summary(rows):
    return {key: sum(row['result'] == key for row in rows) for key in ['matched', 'difference', 'unmatched']}
