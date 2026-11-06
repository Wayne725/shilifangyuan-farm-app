import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import (Activity, ActivityCharge, ActivityRefundRequest, ActivityRegistration, ActivityRegistrationStatus,
                        ActivityStatus, FinancialStatementImport, MembershipType, PaymentAttempt, PaymentStatus, Refund, RefundStatus)
from app.routers.activity_finance import router as finance_router
from app.routers.financial_statements import router as statement_router
from tests.support import auth_headers
from tests.test_financial_statements import payment_row, statement_file, upload
from tests.test_meal_cancellation_review import review_context


@pytest.fixture
async def activity_finance_context(review_context):
    c = review_context
    c.client._transport.app.include_router(finance_router)
    c.client._transport.app.include_router(statement_router)
    activity = Activity(title='共作活動', description='', created_by_id=c.admin.id, location='合作社',
                        starts_at=c.now + timedelta(days=5), ends_at=c.now + timedelta(days=5, hours=2),
                        registration_deadline=c.now + timedelta(days=3), capacity=3, status=ActivityStatus.PUBLISHED)
    c.session.add(activity)
    await c.session.flush()
    charges = []
    for index, (user, amount, state) in enumerate([(c.buyer, 300, PaymentStatus.PAID), (c.other, 500, PaymentStatus.REFUNDED), (c.admin, 200, PaymentStatus.PENDING)]):
        registration = ActivityRegistration(activity_id=activity.id, user_id=user.id, status=ActivityRegistrationStatus.REGISTERED,
                                            queue_position=index + 1, contact_name=user.display_name, contact_email=user.email)
        c.session.add(registration)
        await c.session.flush()
        charge = ActivityCharge(number=f'ACT-TEST-{index}', activity_id=activity.id, registration_id=registration.id, registration_revision=1,
                                user_id=user.id, activity_title=activity.title, contact_name=user.display_name, contact_email=user.email,
                                membership_type=MembershipType.NONMEMBER, amount=amount, payment_status=state,
                                expires_at=c.now + timedelta(days=1), created_at=c.now + timedelta(seconds=index))
        c.session.add(charge)
        charges.append(charge)
    await c.session.flush()
    first = PaymentAttempt(activity_charge_id=charges[0].id, provider='newebpay', merchant_trade_no='ACT-PAY-1', provider_trade_no='EXT-ACT-1',
                           amount=300, status=PaymentStatus.PAID, paid_at=c.now, expires_at=c.now + timedelta(days=1), provider_response={'secret':'never disclose'})
    late = PaymentAttempt(activity_charge_id=charges[0].id, provider='newebpay', merchant_trade_no='ACT-LATE-1', provider_trade_no='EXT-LATE-1',
                          amount=300, status=PaymentStatus.LATE_PAID_REFUND_REQUIRED, paid_at=c.now, expires_at=c.now - timedelta(hours=1))
    second = PaymentAttempt(activity_charge_id=charges[1].id, provider='newebpay', merchant_trade_no='ACT-PAY-2', provider_trade_no='EXT-ACT-2',
                            amount=500, status=PaymentStatus.REFUNDED, paid_at=c.now, expires_at=c.now + timedelta(days=1))
    c.session.add_all([first, late, second])
    await c.session.flush()
    c.session.add_all([
        Refund(activity_charge_id=charges[1].id, payment_attempt_id=second.id, amount=500, status=RefundStatus.COMPLETED,
               requested_by_id=c.admin.id, reason='核准退費', purpose='activity_cancellation', completed_at=c.now),
        ActivityRefundRequest(activity_charge_id=charges[0].id, payment_attempt_id=late.id, amount=300, kind='late_payment',
                              reason='晚到款', status='pending_review'),
    ])
    await c.session.commit()
    c.activity, c.charges, c.attempts = activity, charges, [first, late, second]
    return c


@pytest.mark.asyncio
async def test_activity_summary_counts_all_received_attempts_and_only_completed_refunds(activity_finance_context):
    c = activity_finance_context
    response = await c.client.get('/v1/admin/finance/activity-charges?limit=1', headers=auth_headers(c.admin))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['total'] == 3 and len(result['items']) == 1
    assert result['summary'] == {'charge_count': 3, 'amount_due': 1000, 'amount_received': 1100,
                                 'refund_pending_review': 300, 'refund_processing': 0, 'amount_refunded': 500, 'amount_net': 600}
    single = (await c.client.get('/v1/admin/finance/activity-charges?q=ACT-TEST-0', headers=auth_headers(c.admin))).json()
    assert single['summary']['charge_count'] == 1 and single['summary']['amount_received'] == 600
    assert len(single['items'][0]['transactions']) == 2
    assert 'secret' not in str(single) and 'provider_response' not in str(single)
    assert single['items'][0]['refund_pending_review'] == 300


@pytest.mark.asyncio
async def test_activity_filters_dates_escape_and_csv_do_not_duplicate_charge_totals(activity_finance_context):
    c = activity_finance_context
    headers = auth_headers(c.admin)
    base = '/v1/admin/finance/activity-charges'
    assert (await c.client.get(base+'?status=pending', headers=headers)).json()['summary']['amount_due'] == 200
    assert (await c.client.get(base+'?q=%25', headers=headers)).json()['total'] == 0
    assert (await c.client.get(base+'?status=invalid', headers=headers)).status_code == 422
    assert (await c.client.get(base+'?starts_on=2030-01-02&ends_on=2030-01-01', headers=headers)).status_code == 422
    c.charges[0].created_at = datetime(2030, 1, 1, 16, 0, tzinfo=timezone.utc)
    c.charges[0].activity_title = '=HYPERLINK("https://invalid.test")'
    await c.session.commit()
    data = (await c.client.get(base+'?starts_on=2030-01-02&ends_on=2030-01-02', headers=headers)).json()
    assert data['total'] == 1 and data['items'][0]['id'] == c.charges[0].id
    exported = await c.client.get(base+'.csv?q=ACT-TEST-0&limit=1', headers=headers)
    rows = list(csv.reader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert len(rows) == 3 and rows[1][1].startswith("'=")
    assert sum(int(row[8] or 0) for row in rows[1:]) == 300
    assert sum(int(row[9] or 0) for row in rows[1:]) == 600
    assert rows[1][5] == '2030-01-02 00:00:00'
    assert rows[1][4] == '非社員' and rows[1][7] == '已付款'
    assert datetime.fromisoformat(data['items'][0]['created_at']).utcoffset() == timedelta(0)
    assert exported.headers['cache-control'] == 'no-store'


@pytest.mark.asyncio
async def test_activity_transactions_put_latest_first_without_changing_balances(activity_finance_context):
    c = activity_finance_context
    c.attempts[0].created_at = c.now
    c.attempts[1].created_at = c.now + timedelta(minutes=1)
    await c.session.commit()
    path = '/v1/admin/finance/activity-charges?q=ACT-TEST-0'
    result = (await c.client.get(path, headers=auth_headers(c.admin))).json()
    assert [item['merchant_trade_no'] for item in result['items'][0]['transactions']] == ['ACT-LATE-1', 'ACT-PAY-1']
    assert result['summary']['amount_received'] == 600
    assert result['summary']['refund_pending_review'] == 300
    assert result['summary']['amount_net'] == 600
    c.attempts[1].created_at = c.now
    await c.session.commit()
    tied = (await c.client.get(path, headers=auth_headers(c.admin))).json()
    assert [item['id'] for item in tied['items'][0]['transactions']] == sorted([item.id for item in c.attempts[:2]], reverse=True)


@pytest.mark.asyncio
async def test_activity_finance_admin_only(activity_finance_context):
    c = activity_finance_context
    for headers, expected in [({}, 401), (auth_headers(c.buyer), 403)]:
        for path in ['/v1/admin/finance/activity-charges', '/v1/admin/finance/activity-charges.csv', '/v1/admin/financial-statements/subject-options?subject_type=activity']:
            assert (await c.client.get(path, headers=headers)).status_code == expected


@pytest.mark.asyncio
async def test_statement_matches_activity_by_transaction_and_keeps_order_fields_empty(activity_finance_context):
    c = activity_finance_context
    original = statement_file('newebpay', [payment_row(reference='ACT-PAY-1', amount='300', external='EXT-ACT-1') | {'訂單交易日期':'2030-01-02'},
                                           payment_row(reference='ACT-LATE-1', amount='300', external='EXT-LATE-1') | {'訂單交易日期':'2030-01-02'}])
    response = await upload(c, 'newebpay', original)
    path = '/v1/admin/financial-statements/' + response.json()['id']
    headers = auth_headers(c.admin)
    result = (await c.client.get(path, headers=headers)).json()
    assert result['summary'] == {'matched':2,'difference':0,'unmatched':0}
    assert all(item['subject_type'] == 'activity' and item['subject_id'] == c.charges[0].id and item['order_id'] is None and item['sales_channel'] is None for item in result['items'])
    assert (await c.client.get(path+'?subject_type=order', headers=headers)).json()['total'] == 0
    assert (await c.client.get(path+'?order_category=general', headers=headers)).json()['total'] == 0
    assert (await c.client.get(path+'?subject_type=activity&q=ACT-TEST-0', headers=headers)).json()['total'] == 2
    report = await c.client.get(path+'/reconciliation.csv?subject_type=activity', headers=headers)
    assert '活動應收' in report.text and 'ACT-TEST-0' in report.text
    export = await c.client.get('/v1/admin/financial-statements/accounting-export', headers=headers,
        params={'source':'newebpay','starts_on':'2030-01-01','ends_on':'2030-01-03','batch_id':response.json()['id'],'subject_type':'activity'})
    assert export.status_code == 200, export.text


@pytest.mark.asyncio
async def test_bank_manual_activity_link_is_read_only_and_cannot_link_invoice(activity_finance_context):
    c = activity_finance_context
    record = {'row_number':7,'reference':'','external_id':'','kind':'bank_credit','amount':'200','fee':None,'payout':None,
              'occurred_at':'2030-01-02','state':'','tax':None,'duplicate':False,'manual_order_id':None}
    batch = FinancialStatementImport(source='esun', filename='bank.xls', checksum='activity-test', file_content=b'original',
                                     uploaded_by_id=c.admin.id, rows=[record])
    c.session.add(batch);await c.session.commit()
    base = f'/v1/admin/financial-statements/{batch.id}'
    headers = auth_headers(c.admin)
    payload = {'subject_type':'activity','subject_id':c.charges[2].id}
    assert (await c.client.put(base+'/rows/7/match', headers=headers, json=payload)).status_code == 200
    item = (await c.client.get(base, headers=headers)).json()['items'][0]
    assert item['result'] == 'difference' and '網站尚未確認收款' in item['reasons']
    assert c.charges[2].payment_status == PaymentStatus.PENDING
    assert (await c.client.put(base+'/rows/7/match', headers=headers, json={**payload,'order_id':c.order.id})).status_code == 422
    assert (await c.client.put(base+'/rows/7/match', headers=headers, json={'subject_id':None})).status_code == 200
    assert (await c.client.get(base, headers=headers)).json()['summary']['unmatched'] == 1
    batch.source = 'invoice';await c.session.commit()
    assert (await c.client.put(base+'/rows/7/match', headers=headers, json=payload)).status_code == 422
    assert (await c.client.get('/v1/admin/financial-statements/accounting-export?source=invoice&subject_type=activity&starts_on=2030-01-01&ends_on=2030-01-03', headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_activity_multiple_receipts_require_transaction_identity_and_refund_completion(activity_finance_context):
    c = activity_finance_context
    original = statement_file('newebpay', [payment_row(reference=c.charges[0].number, amount='300', external='unrelated')])
    response = await upload(c, 'newebpay', original)
    headers = auth_headers(c.admin)
    result = (await c.client.get('/v1/admin/financial-statements/'+response.json()['id'], headers=headers)).json()
    assert result['items'][0]['result'] == 'difference'
    assert '多筆實收' in ' '.join(result['items'][0]['reasons'])
    refund_file = statement_file('linepay', [{'交易號碼':'1','訂單號碼':'ACT-LATE-1','支付總額':'-300'},
                                          {'交易號碼':'2','訂單號碼':'ACT-PAY-2','支付總額':'-500'}])
    response = await upload(c, 'linepay', refund_file)
    items = (await c.client.get('/v1/admin/financial-statements/'+response.json()['id'], headers=headers)).json()['items']
    assert items[0]['result'] == 'difference' and items[0]['expected_amount'] is None
    assert items[1]['result'] == 'matched' and items[1]['expected_amount'] == '500'


@pytest.mark.asyncio
async def test_cross_subject_reference_collision_stays_unmatched(activity_finance_context):
    c = activity_finance_context
    c.order.order_number = 'ACT-PAY-1'
    await c.session.commit()
    response = await upload(c, 'newebpay', statement_file('newebpay', [payment_row(reference='ACT-PAY-1', amount='300', external='collision')]))
    path = '/v1/admin/financial-statements/' + response.json()['id']
    row = (await c.client.get(path, headers=auth_headers(c.admin))).json()['items'][0]
    assert row['result'] == 'unmatched' and row['subject_id'] is None
    assert any('多筆' in reason for reason in row['reasons'])


@pytest.mark.asyncio
async def test_failed_refund_remains_processing_and_is_not_netted(activity_finance_context):
    c = activity_finance_context
    request = await c.session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id == c.attempts[1].id))
    request.status = 'approved'
    c.session.add(Refund(activity_charge_id=c.charges[0].id, payment_attempt_id=c.attempts[1].id,
                         amount=300, status=RefundStatus.FAILED, requested_by_id=c.admin.id, reason='金流暫時失敗'))
    await c.session.commit()
    result = (await c.client.get('/v1/admin/finance/activity-charges?q=ACT-TEST-0', headers=auth_headers(c.admin))).json()
    assert result['summary']['refund_processing'] == 300 and result['summary']['refund_pending_review'] == 0
    assert result['summary']['amount_net'] == 600 and result['summary']['amount_refunded'] == 0


@pytest.mark.asyncio
async def test_external_refund_closes_review_without_double_counting(activity_finance_context):
    c = activity_finance_context
    attempt = c.attempts[1]
    request = await c.session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id == attempt.id))
    request.status = 'externally_refunded'
    attempt.status = PaymentStatus.REFUNDED
    c.session.add(Refund(activity_charge_id=c.charges[0].id, payment_attempt_id=attempt.id,
                        amount=300, status=RefundStatus.COMPLETED, reason='金流已退款', requested_by_id=c.buyer.id, completed_at=c.now))
    await c.session.commit()
    path = '/v1/admin/finance/activity-charges'
    headers = auth_headers(c.admin)
    result = (await c.client.get(path+'?q=ACT-TEST-0', headers=headers)).json()
    assert result['summary']['refund_pending_review'] == 0
    assert result['summary']['amount_received'] == 600
    assert result['summary']['amount_refunded'] == 300
    assert result['summary']['amount_net'] == 300
    exported = await c.client.get(path+'.csv?q=ACT-TEST-0', headers=headers)
    assert '金流已退款' in exported.text
