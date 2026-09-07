import json
from datetime import datetime, timezone

import pytest

from app.integrations.common import HTTPResponse
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.models import Invoice, InvoiceBuyerType, InvoiceStatus, PaymentStatus, Refund, RefundStatus, UserRole
from app.routers.invoices import invoices_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_integrations import make_regular_order

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('uncertain', [False, True])
@pytest.mark.parametrize('buyer_type', [InvoiceBuyerType.PERSONAL, InvoiceBuyerType.COMPANY])
async def test_void_after_refund_uses_provider_snapshot_and_never_resubmits_uncertain_request(database_session, monkeypatch, uncertain, buyer_type):
    import app.routers.invoices as routes

    admin, _product, order = await make_regular_order(database_session)
    admin.user_role = UserRole.ADMIN
    order.payment_status = PaymentStatus.REFUNDED
    invoice = Invoice(order=order, relate_number='INVTEST', provider='fanyu', status=InvoiceStatus.VOID_PENDING,
                      invoice_number='AB12345678', invoice_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
                      total_amount=200, buyer_type=buyer_type, buyer_tax_id='24536806' if buyer_type == InvoiceBuyerType.COMPANY else None,
                      provider_request={'sellerID': '15989995'})
    refund = Refund(order_id=order.id, amount=200, status=RefundStatus.COMPLETED, reason='測試退款', requested_by_id=admin.id)
    database_session.add_all([invoice, refund])
    await database_session.commit()
    submitted = []
    cancelled = False

    async def transport(url, envelope, _headers, _timeout):
        nonlocal cancelled
        path = url.rsplit('/', 1)[-1]
        if path == 'cancelInvoice':
            submitted.append(envelope['reqData'])
            if uncertain:
                raise TimeoutError('isolated provider timeout')
            cancelled = True
            data = {'invNo': 'AB12345678', 'cancelDate': '20260906', 'cancelTime': '11:00:00'}
        else:
            assert path == 'queryInvoice'
            data = {'invNo': 'AB12345678', 'invDate': '20260905', 'invTime': '08:00:00', 'status': '1' if cancelled else '0'}
        return HTTPResponse(200, json.dumps({'statusCode': '0', 'respData': data}), {})

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(routes, 'invoice_adapter_from_settings', lambda _: adapter)
    async with api_test_context(database_session, [invoices_router], settings=make_test_settings(invoice_provider='fanyu')) as client:
        path = f'/v1/admin/orders/{order.id}/invoice/void'
        invalid = await client.post(path, headers=auth_headers(admin), json={'reason': ' '})
        assert invalid.status_code == 422
        first = await client.post(path, headers=auth_headers(admin), json={'reason': '全額退款'})
        assert first.status_code in {200, 202}, first.text
        assert first.json()['status'] == ('void_pending' if uncertain else 'voided')
        second = await client.post(path, headers=auth_headers(admin), json={'reason': '再次核對'})
        assert second.status_code in {200, 202}, second.text
        assert len(submitted) == 1
        assert submitted[0] == {'orderID': 'INVTEST', 'process_type': 'B' if buyer_type == InvoiceBuyerType.COMPANY else 'C', 'sellerID': '15989995', 'buyerID': invoice.buyer_tax_id or '00000000',
                                'invNo': 'AB12345678', 'invDate': '20260905', 'cancelReason': '全額退款', 'remark': '', 'notifyEmail': ''}


@pytest.mark.parametrize('case', ['customer', 'unpaid', 'partial_refund', 'wrong_seller', 'wrong_number', 'not_found', 'missing_date', 'status_conflict'])
async def test_void_rejects_unsafe_order_or_unconfirmed_provider_identity(database_session, monkeypatch, case):
    import app.routers.invoices as routes

    admin, _product, order = await make_regular_order(database_session)
    admin.user_role = UserRole.CUSTOMER if case == 'customer' else UserRole.ADMIN
    order.payment_status = PaymentStatus.PENDING if case == 'unpaid' else PaymentStatus.REFUNDED
    invoice = Invoice(order=order, relate_number='SAFETYTEST', provider='fanyu', status=InvoiceStatus.VOIDED if case == 'status_conflict' else InvoiceStatus.VOID_PENDING,
                      invoice_number='AB12345678', invoice_date=datetime(2026, 9, 5, tzinfo=timezone.utc),
                      total_amount=200, provider_status='0', provider_request={'sellerID': 'wrong' if case == 'wrong_seller' else '15989995'})
    refund = Refund(order_id=order.id, amount=100 if case == 'partial_refund' else 200, status=RefundStatus.COMPLETED, reason='隔離測試', requested_by_id=admin.id)
    database_session.add_all([invoice, refund])
    await database_session.commit()
    calls = []

    async def transport(url, _envelope, _headers, _timeout):
        calls.append(url)
        assert url.endswith('/queryInvoice'), 'Unsafe request attempted to cancel invoice'
        data = {'invNo': 'XY87654321' if case == 'wrong_number' else 'AB12345678', 'invDate': '' if case == 'missing_date' else '20260905', 'status': '0'}
        if case == 'not_found':
            data = {}
        return HTTPResponse(200, json.dumps({'statusCode': '0', 'respData': data}), {})

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    monkeypatch.setattr(routes, 'invoice_adapter_from_settings', lambda _: adapter)
    async with api_test_context(database_session, [invoices_router], settings=make_test_settings(invoice_provider='fanyu')) as client:
        response = await client.post(f'/v1/admin/orders/{order.id}/invoice/void', headers=auth_headers(admin), json={'reason': '測試阻擋'})
        assert response.status_code in ({403} if case == 'customer' else {409, 502}), response.text
        assert all(url.endswith('/queryInvoice') for url in calls)
