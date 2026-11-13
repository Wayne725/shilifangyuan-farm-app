from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.integrations.invoice import InvoiceIssueResult
from app.integrations.invoice_service import issue_paid_order_invoice
from app.jobs import _reconcile_expired_cash_orders, _reconcile_meal_events
from app.models import InventoryReservation, Invoice, Meal, MealEvent, MealEventOffering, MealEventStatus, MealOption, MealOptionGroup, Notification, Order, OutboxEvent, PrintJob, Refund, ReservationStatus, User, UserRole
from app.printer_worker import process_print_job
from app.printing import open_receipt_content
from app.routers.meals import meals_router
from app.routers.operations import operations_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from app.routers.printers import printers_router
from tests.support import api_test_context, auth_headers, fanyu_test_context, make_test_settings, prepare_test_invoice, tomorrow_meal_timeline

from tests.test_business_meals import business_body


TAIPEI = ZoneInfo("Asia/Taipei")
BOOKED_AT = datetime(2030, 1, 1, 10, tzinfo=TAIPEI)


@pytest.fixture
async def business_cash(database_session):
    session = database_session
    buyer = User(email="business-cash@example.com", display_name="會餐買家", password_hash="test")
    admin = User(email="business-cash-admin@example.com", display_name="會餐管理員", password_hash="test", user_role=UserRole.ADMIN)
    session.add_all([buyer, admin])
    await session.flush()
    event = MealEvent(
        title="會餐現金測試", location="合作社", booking_policy="special", business_only=True, allow_cash_payment=False,
        **tomorrow_meal_timeline(BOOKED_AT),
        meal_period="lunch", status=MealEventStatus.PUBLISHED, created_by=admin,
        offerings=[MealEventOffering(meal=Meal(slug="business-cash", name="會餐便當", price=100), price=100, capacity=10)],
    )
    session.add(event)
    await session.commit()
    settings = make_test_settings(printer_enabled=True, printer_dry_run=True, printer_region="tw")
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with api_test_context(session, [meals_router, orders_router, operations_router, payments_router, printers_router], settings=settings) as client:
        yield SimpleNamespace(client=client, session=session, buyer=buyer, admin=admin, event=event,
                              factory=factory, settings=settings, offering_id=event.offerings[0].id)


async def book(c, **overrides):
    body = business_body({
        "contact_email": c.buyer.email, "contact_phone": "0912345678",
        "items": [{"offering_id": c.offering_id, "quantity": 1}],
    }, **{"payment_method": "cash_on_pickup", **overrides})
    with time_machine.travel(BOOKED_AT, tick=False):
        response = await c.client.post(f"/v1/meal-events/{c.event.id}/orders", headers=auth_headers(c.buyer), json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def collect(c, order, **overrides):
    return await c.client.post(f"/v1/orders/{order['id']}/admin/cash-payment", headers=auth_headers(c.admin),
                               json={"amount": 200, **overrides})


async def test_cash_order_remains_collectable_after_service_and_issues_company_invoice(business_cash):
    c = business_cash
    order = await book(c)
    assert order["payment_method"] == "cash_on_pickup"
    assert order["invoice_handling"] == "pickup_day"
    assert order["invoice_status"] == "not_eligible"
    assert "pay" not in order["available_actions"]
    with time_machine.travel(BOOKED_AT, tick=False):
        assert (await collect(c, order)).status_code == 409
        assert await process_print_job(c.settings, factory=c.factory)
    initial = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == order["id"]))
    assert initial.status == "simulated"
    assert "現金付款・未收 NT$200" in open_receipt_content(initial.content, order["id"], c.settings)
    reservation = await c.session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == order["id"]))
    assert reservation.expires_at.replace(tzinfo=timezone.utc) == datetime.fromisoformat(order["payment_closes_at"])
    pickup_end = c.event.pickup_ends_at.replace(tzinfo=timezone.utc)
    await _reconcile_meal_events(c.session, pickup_end, 10)
    assert (await c.session.get(Order, order["id"])).payment_status.value == "pending"
    late_afternoon = pickup_end + timedelta(hours=2)
    with time_machine.travel(late_afternoon, tick=False):
        headers = auth_headers(c.admin)
        assert (await c.client.get("/v1/admin/today", headers=headers)).json()["today_business_cash_uncollected"] == 1
        queue = (await c.client.get("/v1/admin/meal-orders?work_queue=today_business_cash_uncollected", headers=headers)).json()
        assert queue["items"][0]["order"]["id"] == order["id"]
        assert "confirm_cash_payment" in queue["items"][0]["order"]["available_actions"]
        assert (await collect(c, order, collection_method="qr")).status_code == 409
        assert (await collect(c, order, invoice_handling="external")).status_code == 409
        response = await collect(c, order)
        assert response.status_code == 200, response.text
        assert response.json()["invoice_status"] == "pending"
        assert response.json()["payment_status"] == "paid"
        assert (await collect(c, order)).json()["paid_at"] == response.json()["paid_at"]
        assert (await c.client.get("/v1/admin/today", headers=headers)).json()["today_business_cash_uncollected"] == 0
        reprint = await c.client.post(f"/v1/admin/printers/jobs/{initial.id}/reprint", headers=headers,
                                      json={"request_id": str(uuid4())})
        assert reprint.status_code == 201, reprint.text
        assert reprint.json()["kind"] == "reference"
        assert await process_print_job(c.settings, factory=c.factory)
        replacement = await c.session.get(PrintJob, reprint.json()["id"])
        await c.session.refresh(replacement)
        assert replacement.status == "simulated"
        content = open_receipt_content(replacement.content, order["id"], c.settings)
        assert "現金已收" in content and "請勿製作" in content
        outbox = await c.session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == order["id"],
                                        OutboxEvent.event_type == "invoice.issue_requested"))
        assert outbox is not None
        stored = await c.session.get(Order, order["id"])
        stored.invoice_provider_context = fanyu_test_context()
        await c.session.commit()

        class FanyuInvoice:
            provider_name = "fanyu"
            binding_context = fanyu_test_context()

            def prepare_invoice(self, request):
                assert request.buyer_type == "company"
                assert request.buyer_tax_id == "15989995"
                return prepare_test_invoice(request, provider=self.provider_name)

            async def query_invoice(self, relate_number, *, buyer_type):
                assert buyer_type == "company"
                return {"RtnCode": 0, "RtnMsg": "not found"}

            async def issue_prepared_invoice(self, request):
                return InvoiceIssueResult(
                    relate_number=request.relate_number, invoice_number="AB12345678",
                    invoice_date=late_afternoon.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
                    random_number="1234", raw={},
                )

        await issue_paid_order_invoice(c.session, order["id"], FanyuInvoice())
        invoice = await c.session.scalar(select(Invoice).where(Invoice.order_id == order["id"]))
        assert invoice.invoice_date.replace(tzinfo=timezone.utc).astimezone(TAIPEI).date() == late_afternoon.astimezone(TAIPEI).date()


async def test_cash_disaster_preview_and_cancellation_separate_paid_and_unpaid(business_cash):
    c = business_cash
    paid, unpaid = await book(c), await book(c)
    pickup_day = datetime.combine(BOOKED_AT.date() + timedelta(days=1), time(10), TAIPEI)
    body = {"service_date": pickup_day.date().isoformat(), "periods": ["lunch"]}
    with time_machine.travel(pickup_day, tick=False):
        headers = auth_headers(c.admin)
        response = await collect(c, paid)
        assert response.status_code == 200, response.text
        preview = await c.client.post("/v1/admin/meal-disaster/preview", headers=headers, json=body)
        assert preview.status_code == 200, preview.text
        event = preview.json()["events"][0]
        assert (event["order_count"], event["cash_paid_count"], event["cash_paid_total"]) == (2, 1, 200)
        result = await c.client.post("/v1/admin/meal-disaster/cancel", headers=headers, json={
            **body, "preview_token": preview.json()["preview_token"],
        })
        assert result.status_code == 200, result.text
        assert result.json()["cash_paid_count"] == 1 and result.json()["cash_paid_total"] == 200
        jobs = list(await c.session.scalars(select(PrintJob).where(PrintJob.order_id.in_([paid["id"], unpaid["id"]]))))
        assert {job.order_id for job in jobs} == {paid["id"], unpaid["id"]}
        assert all(job.kind == "order" and job.status == "failed" and job.last_error == "order_cancelled_before_send" for job in jobs)
        refund = await c.session.scalar(select(Refund))
        assert refund.order_id == paid["id"] and refund.provider == "onsite" and refund.status.value == "pending"
        notice = await c.session.scalar(select(Notification).where(Notification.event_type == "meal_event_cancelled"))
        assert notice.data["order_id"] == unpaid["id"] and "已取消" in notice.body
        response = await c.client.post(f"/v1/orders/{paid['id']}/admin/cash-refund", headers=headers, json={"amount": 200})
        assert response.status_code == 200 and response.json()["payment_status"] == "refunded"


async def test_cash_after_taipei_midnight_is_followup_not_cancelled(business_cash):
    c = business_cash
    order = await book(c)
    cutoff = datetime.fromisoformat(order["payment_closes_at"])
    with time_machine.travel(cutoff, tick=False):
        assert (await collect(c, order)).status_code == 409
        await _reconcile_expired_cash_orders(c.session, cutoff, 10)
        response = await c.client.get(f"/v1/meal-orders/{order['id']}", headers=auth_headers(c.buyer))
        assert response.json()["payment_status"] == "expired" and response.json()["cancelled_at"] is None
        assert response.json()["payment_followup_required"]
        queue = (await c.client.get("/v1/admin/meal-orders?cash_uncollected=true", headers=auth_headers(c.admin))).json()
        assert queue["total"] == 1
        reservation = await c.session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == order["id"]))
        assert reservation.status == ReservationStatus.RELEASED
        assert await process_print_job(c.settings, factory=c.factory)
        print_job = await c.session.scalar(select(PrintJob).where(PrintJob.order_id == order["id"]))
        assert print_job.status == "failed" and print_job.attempts == 0


async def test_shared_offering_choices_with_autoflush_disabled_keep_capacity_consistent(business_cash):
    c = business_cash
    c.session.autoflush = False
    offering = await c.session.get(MealEventOffering, c.offering_id)
    group = MealOptionGroup(meal_id=offering.meal_id, name="主食", min_selections=1, options=[
        MealOption(name="米飯"), MealOption(name="麵"),
    ])
    c.session.add(group)
    await c.session.commit()
    portions = [
        {"offering_id": c.offering_id, "quantity": 1, "option_ids": [option.id]}
        for option in group.options
    ]
    first, second = await book(c, items=portions), await book(c, items=portions)
    await c.session.refresh(offering)
    assert (offering.reserved_quantity, offering.paid_quantity) == (4, 0)
    held = await c.session.scalar(select(InventoryReservation).where(InventoryReservation.order_id == first["id"]))
    assert held.quantity == 2
    # Preexisting orders can have a separate reservation for each main choice.
    held.quantity = 1
    c.session.add(InventoryReservation(
        order_id=first["id"], source_meal_offering_id=c.offering_id, quantity=1,
        status=ReservationStatus.ACTIVE, expires_at=held.expires_at,
    ))
    await c.session.commit()
    payment_opens = datetime.fromisoformat(first["payment_opens_at"])
    with time_machine.travel(payment_opens + timedelta(hours=12), tick=False):
        paid = await collect(c, first)
        assert paid.status_code == 200, paid.text
        await c.session.refresh(offering)
        assert (offering.reserved_quantity, offering.paid_quantity) == (2, 2)
        correction = await c.client.post(
            f"/v1/orders/{first['id']}/admin/cash-unpaid", headers=auth_headers(c.admin), json={"amount": 200},
        )
        assert correction.status_code == 200, correction.text
        await c.session.refresh(offering)
        assert (offering.reserved_quantity, offering.paid_quantity) == (4, 0)
    cutoff = datetime.fromisoformat(first["payment_closes_at"])
    with time_machine.travel(cutoff, tick=False):
        await _reconcile_expired_cash_orders(c.session, cutoff, 10)
    await c.session.refresh(offering)
    assert (offering.reserved_quantity, offering.paid_quantity) == (0, 0)
    for order in (first, second):
        stored = await c.session.get(Order, order["id"])
        assert stored.payment_status.value == "expired"
        reservations = await c.session.scalars(select(InventoryReservation).where(InventoryReservation.order_id == stored.id))
        assert all(reservation.status == ReservationStatus.RELEASED for reservation in reservations)
