import pytest
from fastapi import HTTPException
from app.models import PaymentStatus, ShipmentStatus
from app.routers.logistics import _load_order, quote_quantity_amendment_shipping
from tests.support import auth_headers
from tests.test_logistics_router import logistics_context, selection_payload

async def select_shipping(c):
    response = await c['client'].post(f"/v1/orders/{c['order'].id}/logistics/selection",
        headers=auth_headers(c['customer']), json=selection_payload())
    assert response.status_code == 201
    return response.json()['selection_url'].rsplit('/', 2)[-2]

async def test_amendment_shipping_requotes_threshold_without_mutating(logistics_context):
    c = logistics_context
    await select_shipping(c)
    order = await _load_order(c['session'], c['order'].id, lock=True)
    ids = {item.id for item in order.items}
    assert await quote_quantity_amendment_shipping(c['session'], order, 1400, ids) == 160
    assert await quote_quantity_amendment_shipping(c['session'], order, 2800, ids) == 0
    assert order.amount_total == 1560
    assert order.items[0].quantity == 1
    order.fulfillment.shipment.status = ShipmentStatus.CREATED
    with pytest.raises(HTTPException, match='物流已開始處理'):
        await quote_quantity_amendment_shipping(c['session'], order, 2800, ids)

async def test_selection_callback_reprices_latest_quantity_and_preserves_revision(logistics_context):
    c = logistics_context
    token = await select_shipping(c)
    order = await _load_order(c['session'], c['order'].id, lock=True)
    order.revision = 2
    order.items[0].quantity = 2
    order.items[0].subtotal = 2800
    order.amount_total = 2800
    order.fulfillment.shipment.shipping_fee = 0
    await c['session'].commit()
    response = await c['client'].post('/logistics/selection-result',
        params={'order_id': order.id, 'token': token}, json={}, follow_redirects=False)
    assert response.status_code == 303, response.text
    order = await _load_order(c['session'], order.id, lock=True)
    assert order.amount_total == 2800
    assert order.revision == 2
    assert order.items[0].quantity == 2
    assert order.fulfillment.shipment.shipping_fee == 0

async def test_selection_callback_cannot_change_paid_order(logistics_context):
    c = logistics_context
    token = await select_shipping(c)
    order = await _load_order(c['session'], c['order'].id, lock=True)
    order.payment_status = PaymentStatus.PAID
    await c['session'].commit()
    response = await c['client'].post('/logistics/selection-result',
        params={'order_id': order.id, 'token': token}, json={}, follow_redirects=False)
    assert response.status_code == 409
    assert order.amount_total == 1560
