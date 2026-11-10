from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import pytest
import time_machine
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.invoice_timing import pickup_invoice_due
from app.models import FulfillmentState, InventoryReservation, InvoiceBuyerType, MealEventOffering, Order, OrderItem, PaymentMethod, PaymentStatus, PrintJob, ReservationStatus, SalesChannel
from app.order_summary import OrderSummaryFilters, build_print_summary
from app.printer_worker import process_print_job
from app.printing import enqueue_cancellation_print, format_receipt, order_print_snapshot, printer_fingerprint
from app.integrations.feie import configured_region
from app.summary_printing import format_summary_pages, summary_fingerprint
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_business_meals import business_body
from tests.test_meal_booking_policy import NOW as BOOKING_NOW, booking_context
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from tests.test_cash_payments import cash_context
from tests.test_feie_printing import print_context, read_job, set_job
from tests.test_meal_kitchen import change, kitchen_context, read as read_kitchen
from tests.test_order_summary import NOW, PARAMS, add_order, line, summary_context
from tests.test_quantity_amendment_printing import amend


async def make_business(c, *, snapshot=True):
    async with c.factory() as session:
        order = await session.scalar(select(Order).where(Order.id == c.order_id).options(
            selectinload(Order.items).selectinload(OrderItem.selected_options), selectinload(Order.fulfillment)))
        order.meal_booking_type = 'business'
        order.payment_method = PaymentMethod.ONLINE
        order.payment_status, order.paid_at = PaymentStatus.PENDING, None
        order.invoice_buyer_type = InvoiceBuyerType.COMPANY
        order.invoice_buyer_tax_id = '15989995'
        order.invoice_buyer_name = '會餐合成單位<QR>注入'
        order.invoice_handling = 'pickup_day'
        order.invoice_issue_after = pickup_invoice_due(order.fulfillment.pickup_at or order.fulfillment.pickup_starts_at)
        if snapshot:
            job = await session.get(PrintJob, c.job_id)
            job.snapshot = {**job.snapshot, **order_print_snapshot(order, '合成會餐客人')}
        await session.commit()


async def test_business_unpaid_preparation_prints_once_and_payment_does_not_reprint(print_context):
    c = print_context
    await make_business(c)
    assert await process_print_job(c.settings, factory=c.factory)
    job = await read_job(c)
    assert job.status == 'simulated'
    assert '會餐' in job.content and '未付款' in job.content and '15989995' in job.content
    assert '會餐合成單位' in job.content and '<QR>' not in job.content
    assert '發票' not in job.content
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_status, order.paid_at = PaymentStatus.PAID, datetime.now(timezone.utc)
        await session.commit()
    assert not await process_print_job(c.settings, factory=c.factory)
    async with c.factory() as session:
        assert await session.scalar(select(func.count(PrintJob.id)).where(PrintJob.order_id == c.order_id)) == 1


async def test_snapshot_business_flag_does_not_allow_standard_unpaid_order(print_context):
    c = print_context
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_method = PaymentMethod.ONLINE
        order.payment_status, order.paid_at = PaymentStatus.PENDING, None
        await session.commit()
    original = await read_job(c)
    await set_job(c, snapshot={**original.snapshot, 'meal_booking_type': 'business', 'payment_status': '未付款'})
    assert not await process_print_job(c.settings, factory=c.factory)
    assert (await read_job(c)).attempts == 0


@pytest.mark.parametrize('marker', ['manual', 'closed', 'picked_up', 'paid_without_time'])
async def test_business_preparation_exception_cannot_bypass_other_print_guards(print_context, marker):
    c = print_context
    await make_business(c)
    if marker == 'manual':
        job = await read_job(c)
        await set_job(c, snapshot={**job.snapshot, 'manual': True})
    else:
        async with c.factory() as session:
            order = await session.scalar(select(Order).where(Order.id == c.order_id).options(selectinload(Order.fulfillment)))
            if marker == 'closed':
                order.fulfillment.pickup_ends_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            elif marker == 'picked_up':
                order.fulfillment.status = FulfillmentState.PICKED_UP
            else:
                order.payment_status = PaymentStatus.PAID
            await session.commit()
    await process_print_job(c.settings, factory=c.factory)
    job = await read_job(c)
    assert job.status != 'simulated' and job.attempts == 0


async def test_known_unsent_business_retry_refreshes_payment_label(print_context):
    c = print_context
    await make_business(c)
    original = await read_job(c)
    await set_job(c, content=format_receipt(original.snapshot), attempts=1)
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.payment_status, order.paid_at = PaymentStatus.PAID, datetime.now(timezone.utc)
        await session.commit()
    assert await process_print_job(c.settings, factory=c.factory)
    job = await read_job(c)
    assert job.status == 'simulated' and '已付款' in job.content and '未付款' not in job.content


async def test_business_amendment_and_cancellation_are_distinct_and_deduplicated(print_context):
    c = print_context
    await make_business(c)
    assert await process_print_job(c.settings, factory=c.factory)
    jobs = await amend(c, revision=2, quantity=3)
    assert len(jobs) == 2
    assert next(job for job in jobs if job.id != c.job_id).kind == 'amendment'
    assert await process_print_job(c.settings, factory=c.factory)
    async with c.factory() as session:
        order = await session.get(Order, c.order_id)
        order.cancelled_at = datetime.now(timezone.utc)
        order.cancellation_reason = '會餐取消'
        await enqueue_cancellation_print(session, order, c.settings)
        await enqueue_cancellation_print(session, order, c.settings)
        await session.commit()
        assert await session.scalar(select(func.count(PrintJob.id)).where(PrintJob.order_id == c.order_id, PrintJob.kind == 'cancellation')) == 1
    assert await process_print_job(c.settings, factory=c.factory)
    async with c.factory() as session:
        cancellation = await session.scalar(select(PrintJob).where(PrintJob.order_id == c.order_id, PrintJob.kind == 'cancellation'))
        assert cancellation.status == 'simulated' and '取消通知' in cancellation.content and '會餐' in cancellation.content


async def test_pending_business_is_visible_and_preparable_in_kitchen_without_general_unpaid(kitchen_context):
    c = kitchen_context
    c.order.meal_booking_type = 'business'
    c.order.payment_method = PaymentMethod.ONLINE
    c.order.payment_status, c.order.paid_at = PaymentStatus.PENDING, None
    c.order.invoice_buyer_type = InvoiceBuyerType.COMPANY
    c.order.invoice_buyer_tax_id, c.order.invoice_buyer_name = '15989995', '合成會餐單位'
    c.order.invoice_handling = 'pickup_day'
    c.order.invoice_issue_after = pickup_invoice_due(c.order.fulfillment.pickup_at)
    c.order.fulfillment.pickup_ends_at = c.event.pickup_ends_at
    c.order.fulfillment.status = FulfillmentState.PENDING_CONFIRMATION
    await c.session.commit()
    result = await read_kitchen(c)
    business = next(item for item in result['items'] if item['id'] == c.order.id)
    assert business['meal_booking_type'] == 'business' and business['payment_status'] == 'pending'
    assert business['invoice_buyer_tax_id'] == '15989995'
    assert any(item['meal_booking_type'] == 'business' and item['payment_status'] == 'pending' for item in result['products'])
    assert (await change(c, 'preparing')).status_code == 200


async def test_summary_explicitly_includes_unpaid_business_and_keeps_default_paid_only(summary_context):
    c = summary_context
    paid = add_order(c, 'PAID', [line(2, offering=c.offerings[0])], channel=SalesChannel.MEAL_PREORDER)
    paid.paid_at = NOW
    general = add_order(c, 'UNPAID', [line(20, offering=c.offerings[0])], channel=SalesChannel.MEAL_PREORDER, payment=PaymentStatus.PENDING)
    business = add_order(c, 'BUSINESS', [line(3, offering=c.offerings[0])], channel=SalesChannel.MEAL_PREORDER, payment=PaymentStatus.PENDING)
    business.meal_booking_type = 'business'
    business.payment_method = PaymentMethod.ONLINE
    business.invoice_buyer_type = InvoiceBuyerType.COMPANY
    business.invoice_buyer_tax_id, business.invoice_buyer_name = '15989995', '合成單位'
    business.invoice_handling, business.invoice_issue_after = 'pickup_day', NOW
    await c.session.commit()
    filters = OrderSummaryFilters(**PARAMS, order_category='meal')
    default = await build_print_summary(c.session, filters)
    assert default['summary']['quantity'] == 2
    report = await build_print_summary(c.session, filters.model_copy(update={'include_unpaid_business': True}))
    assert report['summary']['quantity'] == 5
    assert report['summary']['paid_order_count'] == 1 and report['summary']['pending_business_order_count'] == 1
    assert len(report['products']) == 2
    pages = ''.join(format_summary_pages(report, '2030-01-02 10:00:00', 'synthetic'))
    assert '會餐' in pages and '未付款' in pages


async def test_standard_summary_v2_fingerprint_survives_business_fields(summary_context):
    c = summary_context
    paid = add_order(c, 'V2-STANDARD', [line(2, offering=c.offerings[0])], channel=SalesChannel.MEAL_PREORDER)
    paid.paid_at = NOW
    await c.session.commit()
    report = await build_print_summary(c.session, OrderSummaryFilters(**PARAMS))
    legacy = json.loads(json.dumps(report))
    legacy['filters'].pop('include_unpaid_business', None)
    for product in legacy['products']:
        product.pop('meal_booking_type', None)
        product.pop('payment_status', None)
        key = json.loads(product['key'])
        product['key'] = json.dumps(key[:6], ensure_ascii=False, separators=(',', ':'))
    settings = make_test_settings()
    payload = {'report': legacy, 'printer': printer_fingerprint(settings), 'region': configured_region(settings),
               'width': settings.printer_line_width, 'mode': 'dry_run', 'format_version': 2}
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert summary_fingerprint(report, settings, format_version=2) == expected


@pytest.mark.parametrize('quantity,price,expected_tax', [(3, 100, 14), (5, 109, 26)])
@time_machine.travel(BOOKING_NOW, tick=False)
async def test_business_quantity_revision_updates_long_hold_and_emits_one_amendment(booking_context, quantity, price, expected_tax):
    _client, session, buyer, _admin, event, body = booking_context
    event.offerings[0].price = price
    await session.commit()
    settings = make_test_settings(printer_enabled=True, printer_dry_run=True)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with api_test_context(session, [meals_router, orders_router], settings=settings) as client:
        created = await client.post(f'/v1/meal-events/{event.id}/orders', json=business_body(body), headers=auth_headers(buyer))
        assert created.status_code == 201, created.text
        order_id = created.json()['id']
        order = await session.get(Order, order_id)
        item_id = order.items[0].id
        assert await process_print_job(settings, factory=factory)
        quote_path = f'/v1/orders/{order_id}/quantity-amendments/quote'
        invalid = await client.post(quote_path, headers=auth_headers(buyer), json={'expected_revision': 1, 'items': [{'order_item_id': item_id, 'quantity': 1}]})
        assert invalid.status_code == 422 and '會餐至少' in invalid.text, invalid.text
        payload = {'expected_revision': 1, 'items': [{'order_item_id': item_id, 'quantity': quantity}]}
        quote = await client.post(quote_path, headers=auth_headers(buyer), json=payload)
        assert quote.status_code == 200, quote.text
        assert quote.json()['tax_amount'] == expected_tax
        payload.update(client_request_id=str(uuid4()), expected_total=quote.json()['amount_total'])
        for _ in range(2):
            confirmed = await client.post(f'/v1/orders/{order_id}/quantity-amendments', headers=auth_headers(buyer), json=payload)
            assert confirmed.status_code == 200, confirmed.text
        offering = await session.get(MealEventOffering, body['items'][0]['offering_id'], populate_existing=True)
        assert offering.reserved_quantity == quantity
        holds = list(await session.scalars(select(InventoryReservation).where(InventoryReservation.order_id == order_id, InventoryReservation.status == ReservationStatus.ACTIVE)))
        assert len(holds) == 1 and holds[0].quantity == quantity and holds[0].payment_attempt_id is None
        assert await process_print_job(settings, factory=factory)
        async with factory() as check:
            jobs = list(await check.scalars(select(PrintJob).where(PrintJob.order_id == order_id)))
            assert len(jobs) == 2 and {job.kind for job in jobs} == {'order', 'amendment'}
            assert all(job.status == 'simulated' for job in jobs)
