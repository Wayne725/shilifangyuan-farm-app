from datetime import date, datetime, time, timedelta
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from ..auth import require_admin
from ..csv_export import csv_response
from ..database import get_session
from ..domain import aware
from ..meal_booking import TAIPEI
from ..models import AdminAudit, Order, Invoice, InvoiceStatus, User

invoice_reports_router = APIRouter(prefix='/v1/admin', tags=['invoice-reports'])

async def invoice_rows(session, q, status, start, end):
    if start and end and start > end: raise HTTPException(422, '結束日期不可早於開始日期')
    query = select(Order).outerjoin(Invoice).options(selectinload(Order.invoice))
    if q.strip(): query = query.where(or_(Order.order_number.icontains(q.strip(), autoescape=True), Invoice.invoice_number.icontains(q.strip(), autoescape=True), Order.contact_email.icontains(q.strip(), autoescape=True)))
    if status: query = query.where(Order.invoice_status == status)
    if start: query = query.where(Order.created_at >= datetime.combine(start, time.min, TAIPEI))
    if end: query = query.where(Order.created_at < datetime.combine(end + timedelta(days=1), time.min, TAIPEI))
    orders = list(await session.scalars(query.order_by(Order.created_at.desc(), Order.id)))
    return [{'order_id': row.id, 'order_number': row.order_number, 'email': row.contact_email, 'created_at': row.created_at, 'invoice_number': row.invoice.invoice_number if row.invoice else None, 'invoice_date': row.invoice.invoice_date if row.invoice else None, 'status': row.invoice_status.value, 'payment_status': row.payment_status.value, 'amount': row.invoice.total_amount if row.invoice else row.amount_total, 'tax_amount': row.invoice.tax_amount if row.invoice else row.tax_amount, 'provider': row.invoice.provider if row.invoice else ''} for row in orders]

@invoice_reports_router.get('/invoice-register')
async def read_invoice_register(q: str = Query(default='', max_length=120), status: InvoiceStatus | None = None, starts_on: date | None = None, ends_on: date | None = None, limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0), _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = await invoice_rows(session, q, status, starts_on, ends_on)
    return {'items': rows[offset:offset+limit], 'total': len(rows)}

@invoice_reports_router.get('/invoice-register.csv')
async def export_invoice_register(q: str = Query(default='', max_length=120), status: InvoiceStatus | None = None, starts_on: date | None = None, ends_on: date | None = None, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = await invoice_rows(session, q, status, starts_on, ends_on)
    session.add(AdminAudit(actor_id=admin.id, action='invoice.register_exported', aggregate_type='invoice_register', aggregate_id=str(uuid4()), data={'count': len(rows)}))
    await session.commit()
    local = lambda value: aware(value).astimezone(TAIPEI).strftime('%Y-%m-%d %H:%M') if value else ''
    return csv_response('invoices.csv', ['訂單編號','Email','訂單日期','發票號碼','發票日期','發票狀態','付款狀態','含稅金額','稅額','發票供應商'], [[r['order_number'],r['email'],local(r['created_at']),r['invoice_number'],local(r['invoice_date']),r['status'],r['payment_status'],r['amount'],r['tax_amount'],r['provider']] for r in rows])
