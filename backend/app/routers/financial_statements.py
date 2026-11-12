import hashlib
from datetime import date
from pathlib import Path
from decimal import Decimal
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import undefer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..auth import require_admin
from ..csv_export import csv_response
from ..database import get_session
from ..models import ActivityCharge, AdminAudit, FinancialStatementImport, Order, User, WalkInSale
from ..statement_formats import MAX_FILE_BYTES, SOURCES, parse_statement
from ..statement_reconciliation import reconcile_statement, statement_summary, filter_reconciliation
from ..accounting_exports import export_accounting, accounting_response
from ..order_categories import (
    FINANCIAL_ORDER_CATEGORY_LABELS, FinancialOrderCategory, financial_order_category,
    financial_order_category_condition,
)

router = APIRouter(prefix='/v1/admin/financial-statements', tags=['financial-statements'])
Source = Literal['newebpay', 'jkopay', 'esun', 'linepay', 'invoice']
SubjectType = Literal['all', 'order', 'activity']


def metadata(batch):
    return {'id': batch.id, 'source': batch.source, 'filename': batch.filename, 'row_count': batch.row_count,
            'skipped_rows': batch.skipped_rows, 'created_at': batch.created_at}


def audit(session, admin, action, batch, **data):
    session.add(AdminAudit(actor_id=admin.id, action=action, aggregate_type='financial_statement', aggregate_id=batch.id, data=data))


async def get_batch(session, batch_id, *, include_rows=False):
    query = select(FinancialStatementImport).where(FinancialStatementImport.id == batch_id)
    if include_rows:
        query = query.options(undefer(FinancialStatementImport.rows))
    batch = await session.scalar(query)
    if batch is None:
        raise HTTPException(404, '找不到匯入明細')
    return batch


@router.get('')
async def list_statements(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0), source: Source | None = None,
                          _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    query = select(FinancialStatementImport)
    count = select(func.count(FinancialStatementImport.id))
    if source:
        query = query.where(FinancialStatementImport.source == source)
        count = count.where(FinancialStatementImport.source == source)
    batches = await session.scalars(query.order_by(FinancialStatementImport.created_at.desc(), FinancialStatementImport.id).limit(limit).offset(offset))
    return {'items': [metadata(batch) for batch in batches], 'total': await session.scalar(count)}


@router.get('/accounting-export')
async def download_accounting(source: Source, starts_on: date, ends_on: date, batch_id: str | None = Query(default=None, max_length=36),
                              order_category: FinancialOrderCategory = 'all', subject_type: SubjectType = 'all',
                              admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    body, count = await export_accounting(session, source, starts_on, ends_on, batch_id, order_category, subject_type)
    session.add(AdminAudit(actor_id=admin.id, action='accounting.exported', aggregate_type='accounting_export', aggregate_id=str(uuid4()),
        data={'source': source, 'starts_on': starts_on.isoformat(), 'ends_on': ends_on.isoformat(), 'batch_id': batch_id, 'order_category': order_category, 'subject_type': subject_type, 'count': count}))
    await session.commit()
    return accounting_response(body, source, starts_on, ends_on)


@router.post('', status_code=201)
async def import_statement(source: Source = Form(), file: UploadFile = File(), admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    content = await file.read(MAX_FILE_BYTES + 1)
    filename = Path((file.filename or '').replace('\\', '/')).name[:255]
    checksum = hashlib.sha256(content).hexdigest()
    existing = await session.scalar(select(FinancialStatementImport).where(FinancialStatementImport.source == source, FinancialStatementImport.checksum == checksum))
    if existing:
        return {**metadata(existing), 'already_imported': True}
    try:
        rows, skipped = await run_in_threadpool(parse_statement, content, filename, source)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    batch = FinancialStatementImport(id=str(uuid4()), source=source, filename=filename, checksum=checksum,
                                     file_content=content, rows=rows, row_count=len(rows), skipped_rows=skipped, uploaded_by_id=admin.id)
    session.add(batch)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(FinancialStatementImport).where(FinancialStatementImport.source == source, FinancialStatementImport.checksum == checksum))
        if existing is None:
            raise
        return {**metadata(existing), 'already_imported': True}
    audit(session, admin, 'statement.imported', batch, source=source, count=len(rows), checksum=checksum)
    await session.commit()
    return {**metadata(batch), 'already_imported': False}


@router.get('/order-options')
async def order_options(q: str = Query(default='', max_length=120), order_category: FinancialOrderCategory = 'all', _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    query = (select(Order, financial_order_category()).select_from(Order)
             .outerjoin(WalkInSale, WalkInSale.order_id == Order.id)
             .where(Order.order_number.icontains(q.strip(), autoescape=True), financial_order_category_condition(order_category))
             .order_by(Order.created_at.desc(), Order.id).limit(20))
    return [{
        'id': order.id, 'order_number': order.order_number, 'sales_channel': order.sales_channel.value,
        'financial_order_category': category, 'financial_order_category_label': FINANCIAL_ORDER_CATEGORY_LABELS[category],
        'amount': order.amount_total, 'created_at': order.created_at,
    } for order, category in await session.execute(query)]


@router.get('/subject-options')
async def subject_options(subject_type: Literal['order', 'activity'] = 'order', q: str = Query(default='', max_length=120),
                          order_category: FinancialOrderCategory = 'all', _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if subject_type == 'order':
        rows = await order_options(q, order_category, _admin, session)
        return [{**row, 'subject_type': 'order', 'subject_number': row['order_number']} for row in rows]
    query = select(ActivityCharge).where(ActivityCharge.number.icontains(q.strip(), autoescape=True)).order_by(ActivityCharge.created_at.desc(), ActivityCharge.id).limit(20)
    return [{'id': row.id, 'subject_type': 'activity', 'subject_number': row.number, 'sales_channel': None,
             'amount': row.amount, 'created_at': row.created_at} for row in await session.scalars(query)]


@router.get('/{batch_id}')
async def statement_detail(batch_id: str, result: Literal['all', 'matched', 'difference', 'unmatched'] = 'all', q: str = Query(default='', max_length=120), order_category: FinancialOrderCategory = 'all', subject_type: SubjectType = 'all', limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0), _admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    batch = await get_batch(session, batch_id, include_rows=True)
    rows = await reconcile_statement(session, batch)
    filtered = filter_reconciliation(rows, q=q, order_category=order_category, subject_type=subject_type, result=result)
    return {**metadata(batch), 'summary': statement_summary(filtered), 'items': filtered[offset:offset + limit], 'total': len(filtered)}


class LinkOrder(BaseModel):
    order_id: str | None = Field(default=None, max_length=36)
    subject_type: Literal['order', 'activity'] | None = None
    subject_id: str | None = Field(default=None, max_length=36)

    @model_validator(mode='after')
    def validate_subject(self):
        if 'order_id' in self.model_fields_set and ({'subject_type', 'subject_id'} & self.model_fields_set):
            raise ValueError('請只指定一個對帳來源')
        if self.subject_id and self.subject_type is None:
            raise ValueError('請選擇對帳來源類型')
        return self


@router.put('/{batch_id}/rows/{row_number}/match')
async def link_statement_order(batch_id: str, row_number: int, payload: LinkOrder, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    batch = await session.scalar(select(FinancialStatementImport).where(FinancialStatementImport.id == batch_id).options(undefer(FinancialStatementImport.rows)).with_for_update())
    if batch is None:
        raise HTTPException(404, '找不到匯入明細')
    index = next((i for i, row in enumerate(batch.rows) if row['row_number'] == row_number), None)
    if index is None:
        raise HTTPException(404, '找不到明細列')
    subject_type = payload.subject_type or 'order'
    subject_id = payload.order_id if 'order_id' in payload.model_fields_set else payload.subject_id
    if subject_id and batch.source == 'invoice' and subject_type == 'activity':
        raise HTTPException(422, '活動憑證由主辦單位開立，不能連結商品發票')
    entity_type = Order if subject_type == 'order' else ActivityCharge
    if subject_id and await session.get(entity_type, subject_id) is None:
        raise HTTPException(404, '找不到網站訂單' if subject_type == 'order' else '找不到活動應收')
    previous = batch.rows[index]
    batch.rows = [dict(row, manual_order_id=subject_id if subject_type == 'order' else None,
                       manual_subject_type=subject_type if subject_id else None, manual_subject_id=subject_id)
                  if i == index else row for i, row in enumerate(batch.rows)]
    audit(session, admin, 'statement.order_linked' if subject_type == 'order' else 'statement.activity_linked', batch,
          row_number=row_number, previous_order_id=previous.get('manual_order_id'), order_id=subject_id if subject_type == 'order' else None,
          previous_subject_type=previous.get('manual_subject_type'), previous_subject_id=previous.get('manual_subject_id'),
          subject_type=subject_type if subject_id else None, subject_id=subject_id)
    await session.commit()
    return {'row_number': row_number, 'order_id': subject_id if subject_type == 'order' else None,
            'subject_type': subject_type if subject_id else None, 'subject_id': subject_id}



@router.get('/{batch_id}/original')
async def download_original(batch_id: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    batch = await get_batch(session, batch_id)
    content = await session.scalar(select(FinancialStatementImport.file_content).where(FinancialStatementImport.id == batch_id))
    audit(session, admin, 'statement.original_exported', batch)
    await session.commit()
    extension = SOURCES[batch.source][1]
    mime = {'.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.csv': 'text/csv'}[extension]
    return Response(content, media_type=mime, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': f"attachment; filename=statement{extension}; filename*=UTF-8''{quote(batch.filename)}"})


@router.get('/{batch_id}/reconciliation.csv')
async def export_reconciliation(batch_id: str, result: Literal['all', 'matched', 'difference', 'unmatched'] = 'all', q: str = Query(default='', max_length=120), order_category: FinancialOrderCategory = 'all', subject_type: SubjectType = 'all', admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    batch = await get_batch(session, batch_id, include_rows=True)
    rows = filter_reconciliation(await reconcile_statement(session, batch), q=q, order_category=order_category, subject_type=subject_type, result=result)
    audit(session, admin, 'statement.reconciliation_exported', batch, count=len(rows), **statement_summary(rows))
    await session.commit()
    labels = {'matched': '吻合', 'difference': '有差異', 'unmatched': '待連結'}
    return csv_response('statement-reconciliation.csv', ['來源', '原檔列號', '交易或發票編號', '來源訂單編號或備註', '交易日期', '交易類別', '來源金額', '來源手續費', '來源預計撥款', '網站訂單編號', '網站核對金額', '差額', '來源狀態', '網站狀態', '對帳結果', '差異原因', '訂單分類', '對帳來源', '來源編號'],
        [[SOURCES[batch.source][0], row['row_number'], row['external_id'], row['reference'], row['occurred_at'], row['kind'],
          *[Decimal(row[key]) if row[key] is not None else None for key in ['amount', 'fee', 'payout']], row['order_number'],
          *[Decimal(row[key]) if row[key] is not None else None for key in ['expected_amount', 'difference']],
          row['state'], row['website_state'], labels[row['result']], '、'.join(row['reasons']),
          '活動應收' if row['subject_type'] == 'activity' else row['financial_order_category_label'] or '未連結訂單',
          row['subject_type'], row['subject_number']] for row in rows])
