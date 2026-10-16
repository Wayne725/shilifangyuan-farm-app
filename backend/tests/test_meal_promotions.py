from datetime import timedelta
import pytest
from sqlalchemy import select
from app.models import Order, Promotion
from app.routers.promotions import promotions_router
from app.integrations.invoice_service import invoice_request_from_order
from tests.support import auth_headers
from tests.test_meal_booking_policy import booking_context, NOW


def rule(**overrides):
    return dict(name='滿額折抵', code='MEAL20', enabled=True, minimum_amount=200, discount_kind='fixed', discount_value=20, starts_at=(NOW-timedelta(days=1)).isoformat(), ends_at=(NOW+timedelta(days=1)).isoformat(), max_uses=2, per_user_limit=1, **overrides)

@pytest.mark.asyncio
async def test_promotion_exact_invoice_price_snapshot_and_per_user_cap(booking_context):
    client, session, buyer, admin, event, body = booking_context
    client._transport.app.include_router(promotions_router)
    assert (await client.post('/v1/admin/promotions', json=rule(), headers=auth_headers(buyer))).status_code == 403
    response = await client.post('/v1/admin/promotions', json=rule(), headers=auth_headers(admin))
    assert response.status_code == 201, response.text
    body['items'][0]['quantity'] = 3
    body['coupon_code'] = 'meal20'
    quote = await client.post(f'/v1/meal-events/{event.id}/quote', json=body, headers=auth_headers(buyer))
    assert quote.json()['amount_total'] == 280
    assert quote.json()['original_amount'] == 300
    assert sum(line['subtotal'] for line in quote.json()['items']) == 280
    body['expected_total'] = 279
    assert (await client.post(f'/v1/meal-events/{event.id}/orders', json=body, headers=auth_headers(buyer))).status_code == 409
    body['expected_total'] = 280
    created = await client.post(f'/v1/meal-events/{event.id}/orders', json=body, headers=auth_headers(buyer))
    assert created.status_code == 201, created.text
    order = await session.get(Order, created.json()['id'])
    await session.refresh(order.fulfillment, ['shipment'])
    request = invoice_request_from_order(order, 'TESTPROMO')
    assert sum(line.amount for line in request.items) == 280
    assert sum(line.quantity for line in request.items) == 3
    assert order.items[0].unit_price == 100
    stored = await session.get(Promotion, response.json()['id'])
    stored.discount_value = 30
    await session.commit()
    assert order.promotion_snapshot['discount'] == 20 and order.amount_total == 280
    capped = await client.post(f'/v1/meal-events/{event.id}/quote', json=body, headers=auth_headers(buyer))
    assert capped.status_code == 409
    assert (await client.get('/v1/admin/promotions', headers=auth_headers(admin))).json()[0]['uses'] == 1

@pytest.mark.asyncio
async def test_automatic_best_discount_no_stacking_and_disabled_rule_rejected(booking_context):
    client, session, buyer, admin, event, body = booking_context
    client._transport.app.include_router(promotions_router)
    for name, kind, value in [('折20', 'fixed', 20), ('折10%', 'percent', 10)]:
        data = rule(); data.update(name=name, code=None, discount_kind=kind, discount_value=value)
        assert (await client.post('/v1/admin/promotions', json=data, headers=auth_headers(admin))).status_code == 201
    body['items'][0]['quantity'] = 3
    quote = await client.post(f'/v1/meal-events/{event.id}/quote', json=body, headers=auth_headers(buyer))
    assert quote.status_code == 200 and quote.json()['amount_total'] == 270
    assert quote.json()['promotion']['name'] == '折10%'
    body['coupon_code'] = 'UNKNOWN'
    assert (await client.post(f'/v1/meal-events/{event.id}/quote', json=body)).status_code == 409
