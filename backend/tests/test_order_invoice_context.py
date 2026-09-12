from datetime import datetime, timedelta, timezone

import pytest

from app.models import GroupCampaign, Meal, MealEvent, MealEventOffering, MealEventStatus, Order, TargetType
from app.routers.groups import groups_router
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from tests.support import api_test_context, auth_headers, fanyu_test_context, make_test_settings
from tests.test_integrations import make_regular_order


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["regular", "group", "meal"])
@pytest.mark.parametrize("base_url", ['https://webtest.einvoice.com.tw/einv', 'https://api01.einvoice.com.tw/einv'])
async def test_each_checkout_persists_server_invoice_context_before_payment(database_session, channel, base_url):
    user, product, _ = await make_regular_order(database_session)
    user.email = "buyer@example.com"
    now = datetime.now(timezone.utc)
    payload = {"contact_email": user.email, "invoice_provider_context": {"provider": "attacker"}}
    path = "/v1/orders"
    if channel == "regular":
        payload["items"] = [{"product_id": product.id, "quantity": 1}]
    elif channel == "group":
        campaign = GroupCampaign(
            target_type=TargetType.PRODUCT, target_id=product.id, title="隔離團購",
            member_price=100, nonmember_price=100, min_paid_quantity=1, supply_cap=5,
            deadline=now + timedelta(days=1), estimated_pickup_start=now + timedelta(days=2),
            estimated_pickup_end=now + timedelta(days=3), created_by_id=user.id,
        )
        database_session.add(campaign)
        await database_session.flush()
        path = f"/v1/group-campaigns/{campaign.id}/join"
        payload["quantity"] = 1
    else:
        event = MealEvent(
            title="隔離便當", location="測試取餐點", status=MealEventStatus.PUBLISHED,
            ordering_starts_at=now - timedelta(days=1), ordering_ends_at=now + timedelta(days=1),
            pickup_starts_at=now + timedelta(days=2), pickup_ends_at=now + timedelta(days=3),
            created_by_id=user.id,
        )
        offering = MealEventOffering(
            event=event, meal=Meal(slug="context-test", name="測試便當", price=100), price=100, capacity=5,
        )
        database_session.add(offering)
        await database_session.flush()
        path = f"/v1/meal-events/{event.id}/orders"
        payload["items"] = [{"offering_id": offering.id, "quantity": 1}]
    await database_session.commit()
    context = fanyu_test_context()
    context['base_url'] = base_url
    settings = make_test_settings(
        invoice_provider="fanyu",
        **{f"fanyu_invoice_{key}": value for key, value in context.items() if key in {"base_url", "company_id", "seller_id", "user_id"}},
    )
    async with api_test_context(database_session, [orders_router, groups_router, meals_router], settings=settings) as client:
        response = await client.post(path, json=payload, headers=auth_headers(user))
    assert response.status_code == 201, response.text
    assert "invoice_provider_context" not in response.json()
    order = await database_session.get(Order, response.json()["id"])
    assert order.invoice_provider_context == context
    assert order.payment_status.value == "pending"
