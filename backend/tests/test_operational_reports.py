from datetime import datetime, timedelta, timezone
import pytest
import time_machine
from sqlalchemy import func, select
from app.models import (
    Activity, ActivityRegistration, ActivityRegistrationStatus, ActivityStatus, AdminAudit, FulfillmentMethod,
    InvoiceBuyerType, InvoiceStatus, MealCancellationRequest, MembershipAdmissionRound, MembershipApplication,
    MembershipApplicationStatus, MembershipType, Order, OrderKind, Refund, RefundStatus, SalesChannel, new_id,
)
from app.routers.meal_cancellation_reports import meal_cancellation_reports_router
from app.routers.invoice_reports import invoice_reports_router
from app.routers.operations import operations_router
from tests.support import auth_headers
from tests.test_meal_cancellation_review import review_context, mark_paid, submit, review


def set_business_order(order, issue_after):
    order.contact_email = "school@example.com"
    order.meal_booking_type = "business"
    order.invoice_buyer_type = InvoiceBuyerType.COMPANY
    order.invoice_buyer_tax_id = "15989995"
    order.invoice_buyer_name = "測試學校"
    order.invoice_handling = "pickup_day"
    order.invoice_issue_after = issue_after


@pytest.mark.asyncio
async def test_admin_today_matches_combined_refund_order_queue_and_activity_counts(review_context):
    c = review_context
    c.client._transport.app.include_router(operations_router)
    await submit(c)
    regular = Order(order_number="TODAY-REGULAR", order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.REGULAR,
                    fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP, user=c.other, amount_total=80,
                    contact_email="buyer@example.com", membership_type_snapshot=MembershipType.NONMEMBER)
    active = Activity(created_by_id=c.admin.id, title="活動", location="合作社", status=ActivityStatus.PUBLISHED,
                      starts_at=c.now + timedelta(hours=1), ends_at=c.now + timedelta(hours=2),
                      registration_deadline=c.now, capacity=10)
    active.registrations = [
        ActivityRegistration(user_id=c.buyer.id, status=ActivityRegistrationStatus.REGISTERED, queue_position=1),
        ActivityRegistration(user_id=c.other.id, status=ActivityRegistrationStatus.CANCELLED, queue_position=2),
    ]
    set_business_order(c.order, c.now)
    c.session.add_all([
        MealCancellationRequest(order=regular, requested_by_id=c.other.id, reason="退單測試"),
        active,
        MembershipAdmissionRound(user_id=c.buyer.id, round_number=1, idempotency_key=new_id(), status="submitted"),
        MembershipApplication(user_id=c.other.id, status=MembershipApplicationStatus.NEEDS_SUPPLEMENT),
    ])
    await c.session.commit()
    headers = auth_headers(c.admin)
    path = "/v1/admin/today"
    assert (await c.client.get(path)).status_code == 401
    assert (await c.client.get(path, headers=auth_headers(c.buyer))).status_code == 403
    response = await c.client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    counts = response.json()
    refunds = (await c.client.get("/v1/admin/order-search?cancellation_pending=true", headers=headers)).json()
    unpaid = (await c.client.get("/v1/admin/order-search?business_unpaid=true", headers=headers)).json()
    assert counts["pending_order_cancellations"] == refunds["total"] == 2
    assert counts["unpaid_business_orders"] == unpaid["total"] == 1
    assert counts["pending_admissions"] == 2
    assert counts["active_activities"] == counts["active_activity_registrations"] == 1


@pytest.mark.asyncio
async def test_admin_invoice_followup_queue_matches_order_flag_at_taipei_midnight(review_context):
    c = review_context
    c.client._transport.app.include_router(operations_router)
    await mark_paid(c)
    midnight = datetime(2030, 1, 2, 16, tzinfo=timezone.utc)
    set_business_order(c.order, midnight - timedelta(days=1))
    c.order.fulfillment.pickup_at = midnight - timedelta(hours=6)
    c.order.paid_at = midnight - timedelta(hours=7)
    for issue_at, status, now, expected in [
        (midnight - timedelta(microseconds=1), InvoiceStatus.ISSUED, midnight, False),
        (midnight, InvoiceStatus.ISSUED, midnight, True),
        (None, InvoiceStatus.PENDING, midnight - timedelta(seconds=1), False),
        (None, InvoiceStatus.PENDING, midnight, True),
        (None, InvoiceStatus.ISSUED, midnight - timedelta(seconds=1), True),
    ]:
        c.order.invoice.invoice_date = issue_at
        c.order.invoice.status = status
        await c.session.commit()
        with time_machine.travel(now, tick=False):
            headers = auth_headers(c.admin)
            counts = (await c.client.get("/v1/admin/today", headers=headers)).json()
            results = (await c.client.get("/v1/admin/order-search?invoice_followup=true", headers=headers)).json()
            order = (await c.client.get("/v1/admin/order-search", headers=headers)).json()["items"][0]
            assert order["invoice_followup_required"] is expected
            assert counts["invoice_followups"] == results["total"] == int(expected)

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
