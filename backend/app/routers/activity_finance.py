from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import require_admin
from ..csv_export import csv_response
from ..database import get_session
from ..domain import aware
from ..meal_booking import TAIPEI
from ..models import ActivityCharge, ActivityRefundRequest, AdminAudit, PaymentAttempt, PaymentStatus, Refund, RefundStatus, User

router = APIRouter(prefix='/v1/admin/finance', tags=['activity-finance'])
RECEIVED = {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED, PaymentStatus.LATE_PAID_REFUND_REQUIRED}
PROCESSING_REFUNDS = {RefundStatus.PENDING, RefundStatus.FAILED}
PAYMENT_LABELS = {'pending': '待付款', 'paid': '已付款', 'late_paid_refund_required': '晚到款待退款審核',
                  'refund_pending': '退款處理中', 'refunded': '已退款', 'failed': '付款失敗', 'expired': '已逾期'}
MEMBERSHIP_LABELS = {'member': '正式社員', 'trainee': '見習社員', 'nonmember': '非社員'}
REFUND_LABELS = {'pending': '退款處理中', 'completed': '已退款', 'failed': '退款失敗'}
REVIEW_LABELS = {'pending_review': '待審核', 'approved': '已核准', 'rejected': '未核准', 'externally_refunded': '金流已退款'}


def filters(q, status, activity_id, starts_on, ends_on):
    if starts_on and ends_on and starts_on > ends_on:
        raise HTTPException(422, '結束日期不可早於開始日期')
    conditions = []
    if status != 'all':
        try:
            state = PaymentStatus(status)
        except ValueError:
            raise HTTPException(422, '活動付款狀態無效') from None
        conditions.append(ActivityCharge.payment_status == state)
    if activity_id:
        conditions.append(ActivityCharge.activity_id == activity_id)
    if term := q.strip():
        conditions.append(or_(*[field.icontains(term, autoescape=True) for field in
            [ActivityCharge.number, ActivityCharge.activity_title, ActivityCharge.contact_name, ActivityCharge.contact_email]]))
    if starts_on:
        conditions.append(ActivityCharge.created_at >= datetime.combine(starts_on, time.min, TAIPEI).astimezone(timezone.utc))
    if ends_on:
        conditions.append(ActivityCharge.created_at < datetime.combine(ends_on + timedelta(days=1), time.min, TAIPEI).astimezone(timezone.utc))
    return conditions


def charge_query(conditions):
    return select(ActivityCharge).where(*conditions).options(
        selectinload(ActivityCharge.payment_attempts), selectinload(ActivityCharge.refunds), selectinload(ActivityCharge.refund_requests)
    ).order_by(ActivityCharge.created_at.desc(), ActivityCharge.id)


def charge_row(charge):
    refunds = {item.payment_attempt_id: item for item in charge.refunds}
    requests = {item.payment_attempt_id: item for item in charge.refund_requests}
    received = sum(item.amount for item in charge.payment_attempts if item.status in RECEIVED)
    refunded = sum(item.amount for item in charge.refunds if item.status == RefundStatus.COMPLETED)
    return {
        'id': charge.id, 'number': charge.number, 'activity_id': charge.activity_id,
        'registration_id': charge.registration_id, 'registration_revision': charge.registration_revision,
        'activity_title': charge.activity_title, 'contact_name': charge.contact_name, 'contact_email': charge.contact_email,
        'membership_type': charge.membership_type.value, 'amount': charge.amount, 'payment_status': charge.payment_status.value,
        'expires_at': aware(charge.expires_at), 'paid_at': aware(charge.paid_at) if charge.paid_at else None,
        'created_at': aware(charge.created_at), 'updated_at': aware(charge.updated_at),
        'amount_received': received, 'refund_pending_review': sum(item.amount for item in charge.refund_requests if item.status == 'pending_review'),
        'refund_processing': sum(item.amount for item in charge.refunds if item.status in PROCESSING_REFUNDS),
        'amount_refunded': refunded, 'amount_net': received - refunded,
        'transactions': [{
            'id': item.id, 'merchant_trade_no': item.merchant_trade_no, 'provider': item.provider,
            'provider_trade_no': item.provider_trade_no, 'amount': item.amount, 'status': item.status.value,
            'paid_at': aware(item.paid_at) if item.paid_at else None,
            'refund_status': refunds[item.id].status.value if item.id in refunds else None,
            'refund_amount': refunds[item.id].amount if item.id in refunds else None,
            'refund_completed_at': aware(refunds[item.id].completed_at) if item.id in refunds and refunds[item.id].completed_at else None,
            'refund_review_status': requests[item.id].status if item.id in requests else None,
        } for item in sorted(charge.payment_attempts, key=lambda item: (aware(item.created_at), item.id))],
    }


async def charge_summary(session, conditions):
    selected = select(ActivityCharge.id).where(*conditions)
    def total(model, condition):
        return select(func.coalesce(func.sum(model.amount), 0)).where(model.activity_charge_id.in_(selected), condition).scalar_subquery()

    statement = select(
        func.count(ActivityCharge.id).label('charge_count'), func.coalesce(func.sum(ActivityCharge.amount), 0).label('amount_due'),
        total(PaymentAttempt, PaymentAttempt.status.in_(RECEIVED)).label('amount_received'),
        total(ActivityRefundRequest, ActivityRefundRequest.status == 'pending_review').label('refund_pending_review'),
        total(Refund, Refund.status.in_(PROCESSING_REFUNDS)).label('refund_processing'),
        total(Refund, Refund.status == RefundStatus.COMPLETED).label('amount_refunded'),
    ).where(*conditions)
    result = dict((await session.execute(statement)).mappings().one())
    return {**result, 'amount_net': result['amount_received'] - result['amount_refunded']}


@router.get('/activity-charges')
async def list_activity_charges(q: str = Query(default='', max_length=120), status: str = Query(default='all', max_length=40),
                                activity_id: str | None = Query(default=None, max_length=36), starts_on: date | None = None, ends_on: date | None = None,
                                limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0),
                                _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    conditions = filters(q, status, activity_id, starts_on, ends_on)
    summary = await charge_summary(session, conditions)
    rows = await session.scalars(charge_query(conditions).limit(limit).offset(offset))
    return {'items': [charge_row(row) for row in rows], 'total': summary['charge_count'], 'summary': summary}


def local_time(value):
    return aware(value).astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M:%S') if value else ''

@router.get('/activity-charges.csv')
async def export_activity_charges(q: str = Query(default='', max_length=120), status: str = Query(default='all', max_length=40),
                                  activity_id: str | None = Query(default=None, max_length=36), starts_on: date | None = None, ends_on: date | None = None,
                                  admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    conditions = filters(q, status, activity_id, starts_on, ends_on)
    summary = await charge_summary(session, conditions)
    if summary['charge_count'] > 10000:
        raise HTTPException(422, '每次最多匯出10,000筆，請縮小日期範圍')
    charges = [charge_row(row) for row in await session.scalars(charge_query(conditions))]
    rows = []
    for charge in charges:
        for index, transaction in enumerate(charge['transactions'] or [None]):
            rows.append([
                charge['number'], charge['activity_title'], charge['contact_name'], charge['contact_email'], MEMBERSHIP_LABELS.get(charge['membership_type'], charge['membership_type']),
                local_time(charge['created_at']), local_time(charge['expires_at']), PAYMENT_LABELS.get(charge['payment_status'], charge['payment_status']),
                *[charge[key] if index == 0 else None for key in ['amount', 'amount_received', 'refund_pending_review', 'refund_processing', 'amount_refunded', 'amount_net']],
                transaction['merchant_trade_no'] if transaction else None, transaction['provider'] if transaction else None,
                transaction['provider_trade_no'] if transaction else None, PAYMENT_LABELS.get(transaction['status'], transaction['status']) if transaction else None,
                transaction['amount'] if transaction else None, local_time(transaction['paid_at']) if transaction else None,
                REVIEW_LABELS.get(transaction['refund_review_status'], transaction['refund_review_status']) if transaction else None,
                REFUND_LABELS.get(transaction['refund_status'], transaction['refund_status']) if transaction else None,
                transaction['refund_amount'] if transaction else None, local_time(transaction['refund_completed_at']) if transaction else None,
            ])
    session.add(AdminAudit(actor_id=admin.id, action='activity_finance.exported', aggregate_type='activity_finance', aggregate_id='charges',
        data={'q': q, 'status': status, 'activity_id': activity_id, 'starts_on': str(starts_on) if starts_on else None,
              'ends_on': str(ends_on) if ends_on else None, 'count': len(charges), 'transaction_rows': len(rows)}))
    await session.commit()
    return csv_response('activity-charges.csv', ['活動應收編號', '活動名稱', '聯絡姓名', '聯絡Email', '計價身分', '建立時間（台灣）', '付款期限（台灣）', '應收付款狀態',
        '原應收金額', '已實收', '待審退款', '退款處理中', '已退款', '淨收', '商店交易編號', '金流業者', '業者交易編號', '交易狀態', '交易金額', '付款時間（台灣）',
        '退款審核狀態', '退款執行狀態', '退款金額', '退款完成時間（台灣）'], rows)
