import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice_service import InvoiceApplicationError, issue_paid_order_invoice
from app.invoice_timing import TAIPEI, pickup_invoice_due
from app.jobs import _process_domain_event, _process_invoice_event
from app.meal_finance import MealSalesFilters, meal_sales_details, meal_sales_report
from app.models import (
    AdminAudit, InventoryReservation, Invoice, InvoiceStatus, MembershipType, Notification,
    Order, OrderNumberCounter, OutboxEvent, OutboxStatus, PaymentAttempt, PaymentStatus, PointTransaction, PrintJob, Refund, RefundStatus,
    User, UserRole, WalkInSale,
)
from app.routers.invoices import invoices_router
from app.routers.operations import operations_router
from app.routers.orders import orders_router
from app.routers.walk_in_sales import walk_in_sales_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings


@pytest.fixture
async def walk_in_context(database_session):
    session = database_session
    admin = User(email="counter-admin@example.com", display_name="合成收款員", password_hash="test",
        user_role=UserRole.ADMIN, membership_type=MembershipType.MEMBER)
    customer = User(email="other-buyer@example.com", display_name="其他買家", password_hash="test")
    session.add_all([admin, customer])
    await session.commit()
    settings = make_test_settings()
    async with api_test_context(session, [walk_in_sales_router, orders_router, operations_router, invoices_router], settings=settings) as client:
        yield SimpleNamespace(session=session, client=client, admin=admin, customer=customer, settings=settings)


def payload(**overrides):
    return {"client_request_id": str(uuid4()), "product_name": "現場餐盒", "unit_price": 105,
        "quantity": 2, "contact_email": "walk-in@example.com", "customer_name": "合成現場客人",
        "reason": "已核對實收現金", "confirm_received": True, "confirm_not_invoiced_elsewhere": True, **overrides}


async def post(c, body, actor=None):
    return await c.client.post("/v1/admin/walk-in-sales", json=body, headers=auth_headers(actor or c.admin))


@pytest.mark.asyncio
async def test_guest_collection_is_durable_and_is_not_operator_consumption(walk_in_context):
    c = walk_in_context
    body = payload()
    first = await post(c, body)
    assert first.status_code == 201, first.text
    assert first.headers["cache-control"] == "no-store"
    repeated = await post(c, body)
    assert repeated.status_code == 200 and repeated.json() == first.json()
    recovered = await c.client.get(f"/v1/admin/walk-in-sales/request/{body['client_request_id']}", headers=auth_headers(c.admin))
    assert recovered.status_code == 200 and recovered.json() == first.json()
    assert recovered.headers["cache-control"] == "no-store"
    order = await c.session.get(Order, first.json()["order_id"])
    assert order.user_id is None and order.customer_kind == "walk_in"
    assert order.membership_type_snapshot == MembershipType.NONMEMBER and not order.member_tax_exempt_snapshot
    assert order.amount_total == 210 and order.tax_amount == 10 and order.invoice_status == InvoiceStatus.PENDING
    for model, expected in [(Order, 1), (WalkInSale, 1), (PaymentAttempt, 1), (AdminAudit, 1),
        (OutboxEvent, 1), (InventoryReservation, 0), (PointTransaction, 0), (PrintJob, 0), (Invoice, 0)]:
        assert await c.session.scalar(select(func.count()).select_from(model)) == expected
    assert await c.session.scalar(select(OrderNumberCounter.last_value)) == 1
    assert (await post(c, {**body, "unit_price": 200})).status_code == 409
    assert await c.session.scalar(select(func.count()).select_from(PaymentAttempt)) == 1
    assert (await c.client.get(f"/v1/orders/{order.id}", headers=auth_headers(c.customer))).status_code == 404
    assert (await c.client.get("/v1/orders", headers=auth_headers(c.customer))).json() == []
    listing = await c.client.get("/v1/admin/order-search?q=合成現場", headers=auth_headers(c.admin))
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["customer_name"] == "合成現場客人"
    assert listing.json()["items"][0]["customer_kind"] == "walk_in"
    assert listing.json()["items"][0]["available_actions"] == ["refund_walk_in"]


@pytest.mark.asyncio
async def test_admin_only_and_validation_fail_before_creating_any_collection(walk_in_context):
    c = walk_in_context
    body = payload()
    assert (await post(c, body, c.customer)).status_code == 403
    assert (await c.client.post("/v1/admin/walk-in-sales", json=body)).status_code == 401
    for overrides in [{"confirm_received": False}, {"confirm_not_invoiced_elsewhere": False},
        {"quantity": 0}, {"unit_price": -1}, {"unit_price": True}, {"unit_price": 1.5},
        {"product_name": " "}, {"contact_email": None}, {"invoice_handling": "pickup_day"},
        {"invoice_handling": "pickup_day", "pickup_date": "2000-01-01"},
        {"preferences": {"invoice_buyer_type": "company", "invoice_buyer_tax_id": "11111111"}},
        {"user_id": c.admin.id}, {"member_tax_exempt_snapshot": True}]:
        failed = await post(c, {**body, **overrides})
        assert failed.status_code == 422, failed.text
    for path in [f"/v1/admin/walk-in-sales/request/{body['client_request_id']}", f"/v1/admin/walk-in-sales/{uuid4()}"]:
        assert (await c.client.get(path, headers=auth_headers(c.customer))).status_code == 403
        assert (await c.client.get(path, headers=auth_headers(c.admin))).status_code == 404
    assert await c.session.scalar(select(func.count()).select_from(Order)) == 0


@pytest.mark.asyncio
async def test_collection_without_invoice_can_omit_email_and_report_keeps_guest(walk_in_context):
    c = walk_in_context
    created = await post(c, payload(invoice_handling="external", contact_email=None, tax_type="tax_exempt"))
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["tax_amount"] == 0 and data["invoice_status"] == "external"
    assert data["contact_email"] is None and data["invoice_print_status"] == "not_supported"
    assert await c.session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    listing = await c.client.get("/v1/admin/order-search", headers=auth_headers(c.admin))
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["contact_email"] is None
    today = datetime.now(timezone.utc).astimezone(TAIPEI).date()
    filters = MealSalesFilters(starts_on=today, ends_on=today, sales_channel="all", search="合成現場")
    details = await meal_sales_details(c.session, filters)
    assert details["total"] == 1 and details["items"][0]["period_received"] == 210
    assert details["items"][0]["customer_name"] == "合成現場客人"
    report = await meal_sales_report(c.session, filters)
    assert report["summary"]["received"] == 210


@pytest.mark.asyncio
async def test_pickup_day_prevents_early_issue_and_fulfillment_does_not_notify_operator(walk_in_context):
    c = walk_in_context
    tomorrow = datetime.now(timezone.utc).astimezone(TAIPEI).date() + timedelta(days=1)
    response = await post(c, payload(invoice_handling="pickup_day", pickup_date=tomorrow.isoformat()))
    assert response.status_code == 201, response.text
    order_id = response.json()["order_id"]
    from app.routers.orders import load_order
    order = await load_order(c.session, order_id)
    assert order.fulfillment.pickup_at.date() == tomorrow
    jobs = list(await c.session.scalars(select(OutboxEvent)))
    assert len(jobs) == 1 and jobs[0].available_at.replace(tzinfo=timezone.utc) == pickup_invoice_due(order.fulfillment.pickup_at)
    with pytest.raises(InvoiceApplicationError, match="尚未到"):
        await issue_paid_order_invoice(c.session, order_id, object())
    assert "mark_cash_unpaid" not in (await c.client.get(f"/v1/orders/{order_id}", headers=auth_headers(c.admin))).json()["available_actions"]
    progressed = await c.client.patch(f"/v1/orders/{order_id}/admin/fulfillment", json={"status": "preparing"}, headers=auth_headers(c.admin))
    assert progressed.status_code == 200, progressed.text
    assert await c.session.scalar(select(func.count()).select_from(Notification)) == 0
    assert await c.session.scalar(select(func.count()).select_from(InventoryReservation)) == 0


@pytest.mark.asyncio
async def test_guest_invoice_worker_issues_once_with_real_adapter_fake_transport(walk_in_context, monkeypatch):
    c = walk_in_context
    calls = []
    async def transport(url, envelope, *_args):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/queryInvoice"):
            return HTTPResponse(200, '{"statusCode":"3","statusDesc":"查無資料","respData":{}}', {})
        assert envelope["reqData"]["printMark"] == "N"
        assert envelope["reqData"]["carrierID1"] == "/AB12+-."
        assert envelope["reqData"]["totalAmount"] == "210"
        return HTTPResponse(200, json.dumps({"statusCode": "0", "respData": {"invNo": "AB12345678", "invDate": "20261106", "randomNumber": "1234"}}), {})
    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport)
    created = await post(c, payload(preferences={"invoice_carrier_type": "mobile_barcode", "invoice_carrier_value": "/ab12+-."}))
    assert created.status_code == 201, created.text
    order = await c.session.get(Order, created.json()["order_id"])
    order.invoice_provider_context = adapter.binding_context
    await c.session.commit()
    monkeypatch.setattr("app.jobs.invoice_adapter_from_settings", lambda *_: adapter)
    event = await c.session.scalar(select(OutboxEvent))
    await _process_invoice_event(c.session, c.settings, event)
    await _process_invoice_event(c.session, c.settings, event)
    assert calls == ["queryInvoice", "openInvoice"]
    assert await c.session.scalar(select(func.count()).select_from(Invoice)) == 1
    assert await c.session.scalar(select(func.count()).select_from(Notification)) == 0
    assert await c.session.scalar(select(func.count()).select_from(PrintJob)) == 0
    assert (await c.client.get(f"/v1/admin/walk-in-sales/{order.id}", headers=auth_headers(c.admin))).json()["invoice_status"] == "issued"
    refunded = await c.client.post(f"/v1/admin/walk-in-sales/{order.id}/refund", headers=auth_headers(c.admin),
        json={"amount": 210, "reason": "客人現場退回商品，已退還現金", "confirm_returned": True})
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["invoice_status"] == "void_pending"
    adjustment = await c.session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "invoice.adjustment_required"))
    assert adjustment.payload["auto_void"] is True
    assert calls == ["queryInvoice", "openInvoice"]


@pytest.mark.asyncio
async def test_walk_in_full_refund_is_audited_and_retry_safe(walk_in_context):
    c = walk_in_context
    created = await post(c, payload())
    order_id = created.json()["order_id"]
    endpoint = f"/v1/admin/walk-in-sales/{order_id}/refund"
    body = {"amount": 210, "reason": "已退還客人全部現金", "confirm_returned": True}
    assert (await c.client.post(endpoint, headers=auth_headers(c.customer), json=body)).status_code == 403
    assert (await c.client.post(endpoint, headers=auth_headers(c.admin), json={**body, "amount": 200})).status_code == 409
    assert (await c.client.post(endpoint, headers=auth_headers(c.admin), json={**body, "confirm_returned": False})).status_code == 422
    for _ in range(2):
        refunded = await c.client.post(endpoint, headers=auth_headers(c.admin), json=body)
        assert refunded.status_code == 200, refunded.text
        assert refunded.json()["payment_status"] == "refunded"
    assert await c.session.scalar(select(func.count()).select_from(Refund)) == 1
    assert await c.session.scalar(select(Refund.status)) == RefundStatus.COMPLETED
    assert await c.session.scalar(select(PaymentAttempt.status)) == PaymentStatus.REFUNDED
    assert await c.session.scalar(select(func.count()).select_from(AdminAudit).where(AdminAudit.action == "order.cash_refund")) == 1
    job = await c.session.scalar(select(OutboxEvent))
    assert job.status == OutboxStatus.COMPLETED
    await _process_invoice_event(c.session, c.settings, job)
    assert await c.session.scalar(select(func.count()).select_from(Invoice)) == 0
    assert await c.session.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.asyncio
async def test_guest_cancel_notification_and_uncertain_invoice_refund_remain_safe(walk_in_context):
    c = walk_in_context
    tomorrow = datetime.now(timezone.utc).astimezone(TAIPEI).date() + timedelta(days=1)
    created = await post(c, payload(invoice_handling="pickup_day", pickup_date=tomorrow.isoformat()))
    order_id = created.json()["order_id"]
    cancel = await c.client.post(f"/v1/orders/{order_id}/admin/cancel", headers=auth_headers(c.admin), json={"reason": "客人取消現場預訂"})
    assert cancel.status_code == 200, cancel.text
    event = await c.session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "order.cancelled"))
    assert event is not None
    await _process_domain_event(c.session, event)
    refunded = await c.client.post(f"/v1/admin/walk-in-sales/{order_id}/refund", headers=auth_headers(c.admin), json={"amount": 210, "reason": "現金已退", "confirm_returned": True})
    assert refunded.status_code == 200, refunded.text
    cross_retry = await c.client.post(f"/v1/orders/{order_id}/admin/cash-refund", headers=auth_headers(c.admin), json={"amount": 210, "reason": "查核原退款"})
    assert cross_retry.status_code == 200, cross_retry.text
    assert await c.session.scalar(select(func.count()).select_from(Refund).where(Refund.order_id == order_id)) == 1
    assert await c.session.scalar(select(func.count()).select_from(Notification)) == 0
    created = await post(c, payload())
    uncertain_id = created.json()["order_id"]
    c.session.add(Invoice(order_id=uncertain_id, relate_number="INVUNCERTAIN", provider="fanyu", status=InvoiceStatus.PENDING))
    await c.session.commit()
    uncertain_refund = await c.client.post(f"/v1/admin/walk-in-sales/{uncertain_id}/refund", headers=auth_headers(c.admin), json={"amount": 210, "reason": "現金已退，開票結果待核對", "confirm_returned": True})
    assert uncertain_refund.status_code == 200, uncertain_refund.text
    assert uncertain_refund.json()["invoice_status"] == "void_pending"
    adjustment = await c.session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "invoice.adjustment_required"))
    assert adjustment.payload["provider_status_uncertain"] is True
    arrange = await c.client.post(f"/v1/admin/orders/{uncertain_id}/invoice/issue", headers=auth_headers(c.admin),
        json={"handling": "on_payment", "reason": "不可重開", "confirm_not_invoiced_elsewhere": True})
    assert arrange.status_code == 409


@pytest.mark.asyncio
async def test_failed_collection_rolls_back_and_same_request_can_be_retried(walk_in_context, monkeypatch):
    c = walk_in_context
    body = payload()
    from app.routers import walk_in_sales
    real_enqueue = walk_in_sales.enqueue_invoice_issue
    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("synthetic failure before commit")
    monkeypatch.setattr(walk_in_sales, "enqueue_invoice_issue", fail_enqueue)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await post(c, body)
    await c.session.rollback()
    await c.session.refresh(c.admin)
    for model in (Order, WalkInSale, PaymentAttempt, OutboxEvent, OrderNumberCounter):
        assert await c.session.scalar(select(func.count()).select_from(model)) == 0
    monkeypatch.setattr(walk_in_sales, "enqueue_invoice_issue", real_enqueue)
    retried = await post(c, body)
    assert retried.status_code == 201, retried.text
    assert retried.json()["order_number"].endswith("01")


@pytest.mark.asyncio
async def test_regular_checkout_keeps_account_owner_price_and_stock_after_guest_sale(walk_in_context):
    c = walk_in_context
    from app.models import Product, TaxType
    product = Product(slug="walk-in-regression-rice", name="一般商品", category="米食", unit="包",
        member_price=100, nonmember_price=120, stock_quantity=9, tax_type=TaxType.TAXABLE)
    c.session.add(product)
    await c.session.commit()
    guest = await post(c, payload())
    assert guest.status_code == 201
    regular = await c.client.post("/v1/orders", headers=auth_headers(c.customer), json={
        "items": [{"product_id": product.id, "quantity": 1}], "contact_email": c.customer.email})
    assert regular.status_code == 201, regular.text
    regular_order = await c.session.get(Order, regular.json()["id"])
    assert regular_order.customer_kind == "account" and regular_order.user_id == c.customer.id
    assert regular.json()["payment_status"] == "pending" and regular.json()["amount_total"] == 120
    assert regular.json()["order_number"].endswith("02")
    assert product.stock_quantity == 9
    owned = await c.client.get("/v1/orders", headers=auth_headers(c.customer))
    assert [row["id"] for row in owned.json()] == [regular_order.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [{"user_id": "operator"}, {"membership_type_snapshot": MembershipType.MEMBER}, {"member_tax_exempt_snapshot": True}, {"customer_kind": "account"}])
async def test_database_rejects_guest_identity_and_tax_misclassification(walk_in_context, mutation):
    c = walk_in_context
    created = await post(c, payload())
    order_id = created.json()["order_id"]
    with pytest.raises(IntegrityError):
        async with c.session.begin_nested():
            await c.session.execute(update(Order).where(Order.id == order_id).values(**mutation))
