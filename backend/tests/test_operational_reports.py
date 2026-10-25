from datetime import timedelta
import pytest
from sqlalchemy import func, select
from app.models import AdminAudit, Refund, RefundStatus
from app.routers.meal_cancellation_reports import meal_cancellation_reports_router
from app.routers.invoice_reports import invoice_reports_router
from tests.support import auth_headers
from tests.test_meal_cancellation_review import review_context, mark_paid, submit, review

@pytest.mark.asyncio
async def test_cancellation_summary_distinguishes_review_and_actual_refunds(review_context):
    c = review_context
    c.client._transport.app.include_router(meal_cancellation_reports_router)
    await mark_paid(c)
    request = await submit(c)
    path = '/v1/admin/meal-cancellation-summary'
    assert (await c.client.get(path, headers=auth_headers(c.buyer))).status_code == 403
    pending = (await c.client.get(path, headers=auth_headers(c.admin))).json()
    assert pending['pending'] == 1 and pending['pending_amount'] == 200 and pending['refunded'] == 0
    assert (await review(c, request)).status_code == 200
    approved = (await c.client.get(path, headers=auth_headers(c.admin))).json()
    assert approved['approved'] == 1 and approved['approved_amount'] == 200
    assert approved['refunded'] == 0 and approved['refund_pending'] == 200
    refund = await c.session.scalar(select(Refund))
    refund.status = RefundStatus.COMPLETED
    refund.completed_at = c.now
    await c.session.commit()
    complete = (await c.client.get(path, headers=auth_headers(c.admin))).json()
    assert complete['refunded'] == 200 and complete['refund_pending'] == 0
    assert (await c.client.get(path+'?search=no-match', headers=auth_headers(c.admin))).json()['requests'] == 0
    assert (await c.client.get(path+'?starts_on=2030-01-01&ends_on=2020-01-01', headers=auth_headers(c.admin))).status_code == 422
    exported = await c.client.get('/v1/admin/meal-cancellations.csv?review_status=approved', headers=auth_headers(c.admin))
    assert exported.status_code == 200 and c.order.order_number in exported.text and '已核准' in exported.text

@pytest.mark.asyncio
async def test_invoice_register_search_is_read_only_and_export_audited(review_context):
    c = review_context
    c.client._transport.app.include_router(invoice_reports_router)
    await mark_paid(c)
    headers = auth_headers(c.admin)
    assert (await c.client.get('/v1/admin/invoice-register', headers=auth_headers(c.buyer))).status_code == 403
    result = await c.client.get('/v1/admin/invoice-register?q=AB12345678&status=issued', headers=headers)
    assert result.status_code == 200 and result.json()['total'] == 1
    assert result.json()['items'][0]['amount'] == 200
    assert (await c.client.get('/v1/admin/invoice-register?q=missing', headers=headers)).json()['total'] == 0
    exported = await c.client.get('/v1/admin/invoice-register.csv?q=AB12345678', headers=headers)
    assert exported.status_code == 200 and 'AB12345678' in exported.text
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == 'invoice.register_exported')) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("obsolete_status", list(RefundStatus))
async def test_cancellation_report_excludes_refunds_for_obsolete_order_payments(review_context, obsolete_status):
    import csv
    import io

    c = review_context
    c.client._transport.app.include_router(meal_cancellation_reports_router)
    await mark_paid(c)
    request = await submit(c)
    assert (await review(c, request)).status_code == 200
    c.session.add(Refund(
        order=c.order, amount=900, status=obsolete_status,
        purpose="obsolete_order_revision", reason="舊版本付款退款",
        requested_by_id=c.admin.id,
    ))
    await c.session.commit()
    result = await c.client.get('/v1/admin/meal-cancellation-summary', headers=auth_headers(c.admin))
    assert result.status_code == 200, result.text
    summary = result.json()
    assert summary['approved_amount'] == 200
    assert summary['refund_pending'] == 200
    assert summary['refunded'] == 0
    assert summary['refund_failed'] == 0
    exported = await c.client.get('/v1/admin/meal-cancellations.csv?review_status=approved', headers=auth_headers(c.admin))
    row = next(csv.DictReader(io.StringIO(exported.text.lstrip('\ufeff'))))
    assert row['退款處理中金額'] == '200'
    assert row['已完成退款金額'] == '0'
