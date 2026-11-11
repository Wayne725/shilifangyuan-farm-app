from datetime import date, datetime, time, timedelta
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, case, func, select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from ..auth import require_admin
from ..csv_export import csv_response
from ..database import get_session
from ..invoice_tax_summary import invoice_tax_summary
from ..domain import aware
from ..meal_booking import TAIPEI
from ..models import AdminAudit, Order, Invoice, InvoiceStatus, User, WalkInSale
from ..order_categories import (
    FINANCIAL_ORDER_CATEGORY_LABELS, FinancialOrderCategory,
    financial_order_category, financial_order_category_condition,
)

invoice_reports_router = APIRouter(prefix='/v1/admin', tags=['invoice-reports'])


@invoice_reports_router.get('/manual-invoice-records')
async def read_manual_invoice_records(
    response: Response,
    limit: int = Query(default=10, ge=1, le=30),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    response.headers['Cache-Control'] = 'no-store'
    arranged_handling = AdminAudit.data['handling'].as_string()
    collected_handling = AdminAudit.data['invoice_handling'].as_string()
    manual_record = and_(
        AdminAudit.aggregate_type == 'order',
        or_(
            and_(AdminAudit.action == 'invoice.arranged', arranged_handling.in_(['on_payment', 'pickup_day'])),
            and_(AdminAudit.action == 'walk_in_sale.collected', collected_handling.in_(['on_payment', 'pickup_day'])),
        ),
    )
    total = await session.scalar(
        select(func.count(AdminAudit.id)).select_from(AdminAudit)
        .join(Order, Order.id == AdminAudit.aggregate_id).where(manual_record)
    )
    query = (
        select(
            AdminAudit.id,
            Order.id.label('order_id'),
            Order.order_number,
            AdminAudit.created_at.label('recorded_at'),
            User.display_name.label('operator_name'),
            AdminAudit.action,
            WalkInSale.sale_category,
            case((AdminAudit.action == 'invoice.arranged', arranged_handling), else_=collected_handling).label('handling'),
            case(
                (AdminAudit.action == 'walk_in_sale.collected', func.coalesce(AdminAudit.data['amount'].as_integer(), Order.amount_total)),
                else_=Order.amount_total,
            ).label('amount'),
            AdminAudit.reason,
            Order.invoice_status,
            Invoice.invoice_number,
            Invoice.invoice_date,
            Order.invoice_issue_after.label('issue_after'),
            AdminAudit.data['issue_after'].as_string().label('requested_issue_after'),
        )
        .select_from(AdminAudit)
        .join(Order, Order.id == AdminAudit.aggregate_id)
        .outerjoin(User, User.id == AdminAudit.actor_id)
        .outerjoin(Invoice, Invoice.order_id == Order.id)
        .outerjoin(WalkInSale, WalkInSale.order_id == Order.id)
        .where(manual_record)
        .order_by(AdminAudit.created_at.desc(), AdminAudit.id.desc())
        .limit(limit).offset(offset)
    )
    rows = (await session.execute(query)).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        for field in ('recorded_at', 'invoice_date', 'issue_after'):
            item[field] = aware(item[field]) if item[field] else None
        items.append(item)
    return {'items': items, 'total': total}


async def invoice_rows(session, q, status, start, end, order_category='all'):
    if start and end and start > end: raise HTTPException(422, '結束日期不可早於開始日期')
    query = (
        select(Order, financial_order_category().label('financial_order_category'))
        .select_from(Order)
        .outerjoin(Invoice, Invoice.order_id == Order.id)
        .outerjoin(WalkInSale, WalkInSale.order_id == Order.id)
        .options(selectinload(Order.invoice))
        .where(financial_order_category_condition(order_category))
    )
    if q.strip(): query = query.where(or_(Order.order_number.icontains(q.strip(), autoescape=True), Invoice.invoice_number.icontains(q.strip(), autoescape=True), Order.contact_email.icontains(q.strip(), autoescape=True)))
    if status: query = query.where(Order.invoice_status == status)
    if start: query = query.where(Order.created_at >= datetime.combine(start, time.min, TAIPEI))
    if end: query = query.where(Order.created_at < datetime.combine(end + timedelta(days=1), time.min, TAIPEI))
    orders = await session.execute(query.order_by(Order.created_at.desc(), Order.id))
    rows = []
    for order, category in orders:
        invoice = order.invoice
        rows.append({
            'order_id': order.id, 'order_number': order.order_number, 'sales_channel': order.sales_channel.value,
            'financial_order_category': category, 'financial_order_category_label': FINANCIAL_ORDER_CATEGORY_LABELS[category],
            'email': order.contact_email, 'created_at': order.created_at,
            'invoice_number': invoice.invoice_number if invoice else None,
            'invoice_date': invoice.invoice_date if invoice else None,
            'status': order.invoice_status.value, 'payment_status': order.payment_status.value,
            'amount': invoice.total_amount if invoice else order.amount_total,
            'tax_amount': invoice.tax_amount if invoice else order.tax_amount,
            'estimated_tax_amount': order.tax_amount, 'invoice_tax': invoice_tax_summary(invoice),
            'provider': invoice.provider if invoice else '',
        })
    return rows

@invoice_reports_router.get('/invoice-register')
async def read_invoice_register(q: str = Query(default='', max_length=120), status: InvoiceStatus | None = None, starts_on: date | None = None, ends_on: date | None = None, order_category: FinancialOrderCategory = 'all', limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0), _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = await invoice_rows(session, q, status, starts_on, ends_on, order_category)
    return {'items': rows[offset:offset+limit], 'total': len(rows)}

@invoice_reports_router.get('/invoice-register.csv')
async def export_invoice_register(q: str = Query(default='', max_length=120), status: InvoiceStatus | None = None, starts_on: date | None = None, ends_on: date | None = None, order_category: FinancialOrderCategory = 'all', admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = await invoice_rows(session, q, status, starts_on, ends_on, order_category)
    session.add(AdminAudit(actor_id=admin.id, action='invoice.register_exported', aggregate_type='invoice_register', aggregate_id=str(uuid4()), data={'count': len(rows)}))
    await session.commit()
    local = lambda value: aware(value).astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M') if value else ''
    return csv_response('invoices.csv', ['訂單編號','Email','訂單日期','發票號碼','發票日期','發票狀態','付款狀態','含稅金額','稅額','發票供應商','訂單分類'], [[r['order_number'],r['email'],local(r['created_at']),r['invoice_number'],local(r['invoice_date']),r['status'],r['payment_status'],r['amount'],r['tax_amount'],r['provider'],r['financial_order_category_label']] for r in rows])
