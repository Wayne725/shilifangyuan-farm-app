from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, union_all
from sqlalchemy.orm import selectinload

from .models import ActivityCharge, Invoice, InvoiceStatus, Order, PaymentAttempt, PaymentStatus, RefundStatus

RECEIVED = {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED}
PROVIDERS = {'newebpay': {'newebpay'}, 'jkopay': {'jkopay'}, 'linepay': {'linepay'}}


def manual_subject(record):
    if record.get('manual_subject_id'):
        return (record.get('manual_subject_type') or 'order', record['manual_subject_id'])
    if record.get('manual_order_id'):
        return ('order', record['manual_order_id'])
    return None


async def reconcile_statement(session, batch):
    references = {row['reference'] for row in batch.rows if row['reference']}
    external_ids = {row['external_id'] for row in batch.rows if row['external_id']}
    manual = {subject for row in batch.rows if (subject := manual_subject(row))}
    found = {}
    keys = list(references | external_ids | {subject[1] for subject in manual})
    for offset in range(0, len(keys), 400):
        chunk = keys[offset:offset + 400]
        selectors = [select(Order.id).where(Order.id.in_(chunk))] if manual else []
        if batch.source == 'invoice':
            selectors.append(select(Invoice.order_id).where(Invoice.invoice_number.in_(chunk)))
        else:
            selectors += [select(Order.id).where(Order.order_number.in_(chunk)),
                          select(PaymentAttempt.order_id).where(PaymentAttempt.merchant_trade_no.in_(chunk))]
            if providers := PROVIDERS.get(batch.source):
                selectors.append(select(PaymentAttempt.order_id).where(PaymentAttempt.provider.in_(providers), PaymentAttempt.provider_trade_no.in_(chunk)))
        orders = await session.scalars(select(Order).where(Order.id.in_(union_all(*selectors))).options(
            selectinload(Order.invoice), selectinload(Order.refunds), selectinload(Order.payment_attempts)))
        found.update((('order', order.id), order) for order in orders)
        if batch.source != 'invoice':
            selectors = [select(ActivityCharge.id).where(ActivityCharge.id.in_(chunk)),
                         select(ActivityCharge.id).where(ActivityCharge.number.in_(chunk)),
                         select(PaymentAttempt.activity_charge_id).where(PaymentAttempt.merchant_trade_no.in_(chunk))]
            if providers := PROVIDERS.get(batch.source):
                selectors.append(select(PaymentAttempt.activity_charge_id).where(PaymentAttempt.provider.in_(providers), PaymentAttempt.provider_trade_no.in_(chunk)))
            charges = await session.scalars(select(ActivityCharge).where(ActivityCharge.id.in_(union_all(*selectors))).options(
                selectinload(ActivityCharge.refunds), selectinload(ActivityCharge.payment_attempts)))
            found.update((('activity', charge.id), charge) for charge in charges)
    by_reference = defaultdict(set)
    by_external_id = defaultdict(set)
    for subject, entity in found.items():
        if batch.source == 'invoice':
            if entity.invoice and entity.invoice.invoice_number:
                by_reference[entity.invoice.invoice_number].add(subject)
        else:
            by_reference[entity.order_number if subject[0] == 'order' else entity.number].add(subject)
            for attempt in entity.payment_attempts:
                by_reference[attempt.merchant_trade_no].add(subject)
                if attempt.provider in PROVIDERS.get(batch.source, set()) and attempt.provider_trade_no:
                    by_external_id[attempt.provider_trade_no].add(subject)
    results = []
    for record in batch.rows:
        linked = manual_subject(record)
        candidate_ids = ({linked} if linked else by_reference[record['reference']] | by_external_id[record['external_id']])
        subject = next(iter(candidate_ids)) if len(candidate_ids) == 1 else None
        entity = found.get(subject)
        order = entity if subject and subject[0] == 'order' else None
        reasons = []
        amount = Decimal(record['amount'])
        expected = None
        state = None
        if record['duplicate']:
            reasons.append('同檔交易編號重複')
        if len(candidate_ids) > 1:
            reasons.append('找到多筆訂單或活動應收，請人工核對')
        elif not entity:
            reasons.append('尚未連結網站訂單或活動應收')
        elif record['kind'] == 'review':
            reasons.append('來源交易狀態需要人工核對')
        elif record['kind'] == 'invoice':
            invoice = order.invoice if order else None
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
            attempts = [item for item in entity.payment_attempts if item.merchant_trade_no == record['reference'] or
                        (item.provider in PROVIDERS.get(batch.source, set()) and item.provider_trade_no == record['external_id'])]
            attempt = attempts[0] if len(attempts) == 1 else None
            if len(attempts) > 1:
                reasons.append('交易編號對應不同付款紀錄')
            elif record['kind'] in {'refund', 'bank_debit'}:
                refunds = [refund for refund in entity.refunds if refund.status == RefundStatus.COMPLETED and
                           (attempt is None or refund.payment_attempt_id == attempt.id)]
                if len(refunds) == 1:
                    expected = Decimal(refunds[0].amount)
                    state = 'refunded'
                else:
                    reasons.append('需核對已完成退款紀錄')
            else:
                expected = Decimal(attempt.amount if attempt else order.amount_total if order else entity.amount)
                state = (attempt.status if attempt else entity.payment_status).value
                if (attempt.status if attempt else entity.payment_status) not in RECEIVED:
                    reasons.append('網站尚未確認收款')
                if not order and attempt is None and len([item for item in entity.payment_attempts if item.status in RECEIVED]) > 1:
                    reasons.append('活動應收有多筆實收，請以金流交易編號核對')
        difference = amount - expected if expected is not None else None
        if difference:
            reasons.append('交易金額不符')
        status = 'matched' if not reasons else 'unmatched' if not entity else 'difference'
        results.append({**record, 'order_id': order.id if order else None, 'order_number': order.order_number if order else None,
                        'sales_channel': order.sales_channel.value if order else None,
                        'subject_type': subject[0] if entity else None, 'subject_id': entity.id if entity else None,
                        'subject_number': (order.order_number if order else entity.number) if entity else None,
                        'expected_amount': str(expected) if expected is not None else None,
                        'difference': str(difference) if difference is not None else None, 'website_state': state,
                        'result': status, 'reasons': reasons})
    return results


def filter_reconciliation(rows, *, q='', order_category='all', subject_type='all', result='all'):
    term = q.strip().casefold()
    return [row for row in rows
            if (subject_type == 'all' or row.get('subject_type', 'order' if row.get('order_id') else None) == subject_type)
            and (order_category == 'all' or (row['sales_channel'] in {'regular', 'group'} if order_category == 'general' else row['sales_channel'] == 'meal_preorder'))
            and (result == 'all' or row['result'] == result)
            and (not term or term in ' '.join(str(row.get(key) or '') for key in ['external_id', 'reference', 'order_number', 'subject_number', 'occurred_at', 'state', 'website_state']).casefold() or term in ' '.join(row['reasons']).casefold())]


def statement_summary(rows):
    return {key: sum(row['result'] == key for row in rows) for key in ['matched', 'difference', 'unmatched']}
