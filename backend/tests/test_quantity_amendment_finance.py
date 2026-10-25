import pytest
from app.models import PaymentStatus, Refund, RefundStatus
from sqlalchemy import select
from tests.test_meal_finance import finance_context, report, FIRST
from tests.support import auth_headers


async def test_obsolete_receipts_and_refunds_remain_visible_separate_from_current_total(finance_context):
    c = finance_context
    c.order.revision = 2
    c.order.amount_total = 640
    old, current = c.attempts
    old.order_revision = 1
    old.amount = 320
    old.superseded_at = FIRST
    old.status = PaymentStatus.REFUNDED
    current.order_revision = 2
    current.amount = 640
    refund = await c.session.scalar(select(Refund).where(Refund.payment_attempt_id == current.id))
    refund.payment_attempt = old
    refund.purpose = 'obsolete_order_revision'
    await c.session.commit()
    result = await report(c)
    assert result['summary']['received'] == 960
    assert result['summary']['refunded'] == 320
    assert result['summary']['net'] == 640
    detail = next(row for row in result['orders']['items'] if row['id'] == c.order.id)
    assert detail['order_amount'] == 640 and detail['payment_status'] == 'paid'
    assert {row['order_revision']: row['amount'] for row in detail['receipts']} == {1: 320, 2: 640}
    assert next(row for row in detail['receipts'] if row['order_revision'] == 1)['obsolete_revision']
    csv = await c.client.get('/v1/admin/finance/meal-sales.csv',
        params={'starts_on': '2030-01-02', 'ends_on': '2030-01-02'}, headers=auth_headers(c.admin))
    assert csv.status_code == 200
    assert '目前訂單版本' in csv.text and '實收交易紀錄' in csv.text
    assert '第1版' in csv.text and '第2版' in csv.text and '舊版付款' in csv.text
    assert 'private-provider-token' not in csv.text


@pytest.mark.parametrize("old_revision, superseded_at", [(1, FIRST), (2, None)])
async def test_obsolete_unknown_receipt_date_never_borrows_current_payment_date(finance_context, old_revision, superseded_at):
    c = finance_context
    old, current = c.attempts
    c.order.revision = 2
    c.order.amount_total = 640
    old.order_revision = old_revision
    old.paid_at = None
    old.status = PaymentStatus.REFUNDED
    old.superseded_at = superseded_at
    current.order_revision = 2
    current.amount = 640
    await c.session.commit()
    result = await report(c)
    assert result['summary']['received'] == 640
    detail = next(row for row in result['orders']['items'] if row['id'] == c.order.id)
    unknown = next(row for row in detail['receipts'] if row['amount'] == 320)
    assert unknown['received_at'] is None and unknown['obsolete_revision']
    csv = await c.client.get('/v1/admin/finance/meal-sales.csv',
        params={'starts_on': '2030-01-02', 'ends_on': '2030-01-02'}, headers=auth_headers(c.admin))
    assert '收款日期待核對' in csv.text
