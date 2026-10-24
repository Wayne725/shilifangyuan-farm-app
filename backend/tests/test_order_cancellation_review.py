from sqlalchemy import func, select
import pytest

from app.models import AdminAudit, MealCancellationRequest, OutboxEvent, PaymentStatus, Refund, SalesChannel, FulfillmentState, FulfillmentStatus
from app.routers.orders import orders_router
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import seed_cancellation_order, mark_paid, submit


@pytest.fixture
async def cancellation_context(database_session):
    c = await seed_cancellation_order(database_session)
    c.order.sales_channel = SalesChannel.REGULAR
    c.order.contact_email = "review-buyer@example.com"
    await c.session.commit()
    async with api_test_context(c.session, [orders_router, operations_router], settings=make_test_settings()) as client:
        c.client = client
        yield c


@pytest.mark.parametrize("paid", [False, True])
@pytest.mark.asyncio
async def test_customers_cannot_cancel_and_admin_can_review_existing_pending_requests(cancellation_context, paid):
    c = cancellation_context
    if paid:
        await mark_paid(c)
    path = f"/v1/orders/{c.order.id}/cancel"
    assert (await c.client.post(path, headers=auth_headers(c.other), json={"reason": "非本人"})).status_code == 403
    response = await c.client.post(path, headers=auth_headers(c.buyer), json={"reason": "行程變更"})
    assert response.status_code == 403, response.text
    assert c.order.cancelled_at is None
    assert c.order.payment_status == (PaymentStatus.PAID if paid else PaymentStatus.PENDING)
    assert await c.session.scalar(select(func.count(Refund.id))) == 0
    assert await c.session.scalar(select(func.count(MealCancellationRequest.id))) == 0
    request = await submit(c)
    assert request["status"] == "pending"
    detail = await c.client.get(f"/v1/orders/{c.order.id}", headers=auth_headers(c.buyer))
    assert "cancel" not in detail.json()["available_actions"]
    assert await c.session.scalar(select(func.count(MealCancellationRequest.id))) == 1
    review_path = f"/v1/orders/{c.order.id}/admin/cancellation-requests/{request['id']}/review"
    body = {"decision": "approve", "reason": "已核對，准予退單"}
    assert (await c.client.post(review_path, headers=auth_headers(c.buyer), json=body)).status_code == 403
    approved = await c.client.post(review_path, headers=auth_headers(c.admin), json=body)
    assert approved.status_code == 200, approved.text
    assert approved.json()["cancellation_request"]["status"] == "approved"
    assert c.order.cancelled_at is not None
    assert c.order.payment_status == (PaymentStatus.REFUND_PENDING if paid else PaymentStatus.EXPIRED)
    assert await c.session.scalar(select(func.count(Refund.id))) == (1 if paid else 0)
    assert (await c.client.post(review_path, headers=auth_headers(c.admin), json=body)).status_code == 200
    assert await c.session.scalar(select(func.count(Refund.id))) == (1 if paid else 0)
    audits = list(await c.session.scalars(select(AdminAudit).where(AdminAudit.action == "order_cancellation.approved")))
    assert len(audits) == 1 and audits[0].actor_id == c.admin.id


@pytest.mark.asyncio
async def test_rejection_and_stale_approval_preserve_money_and_order(cancellation_context):
    c = cancellation_context
    await mark_paid(c)
    request = await submit(c, "既有退單申請")
    assert c.order.cancelled_at is None
    path = f"/v1/orders/{c.order.id}/admin/cancellation-requests/{request['id']}/review"
    c.order.fulfillment_status = FulfillmentStatus.PICKED_UP
    c.order.fulfillment.status = FulfillmentState.PICKED_UP
    await c.session.commit()
    assert (await c.client.post(path, headers=auth_headers(c.admin), json={"decision": "approve", "reason": "已領取後不能核准"})).status_code == 409
    result = await c.client.post(path, headers=auth_headers(c.admin), json={"decision": "reject", "reason": "商品已交付"})
    assert result.status_code == 200, result.text
    assert result.json()["cancellation_request"]["status"] == "rejected"
    assert c.order.payment_status == PaymentStatus.PAID and c.order.cancelled_at is None
    assert await c.session.scalar(select(func.count(Refund.id))) == 0
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 0


@pytest.mark.asyncio
async def test_historical_pending_filter_returns_matching_order(cancellation_context):
    c = cancellation_context
    path = f"/v1/orders/{c.order.id}/cancel"
    request = await submit(c, "既有第一次申請")
    review = f"/v1/orders/{c.order.id}/admin/cancellation-requests/{request['id']}/review"
    assert (await c.client.post(review, headers=auth_headers(c.admin), json={"decision": "reject", "reason": "資料不足"})).status_code == 200
    assert (await c.client.get("/v1/admin/order-search", params={"cancellation_pending": "true"}, headers=auth_headers(c.admin))).json()["total"] == 0
    next_request = await submit(c, "既有補充資料申請")
    assert next_request["id"] != request["id"]
    found = await c.client.get("/v1/admin/order-search", params={"cancellation_pending": "true", "q": c.order.order_number}, headers=auth_headers(c.admin))
    assert found.status_code == 200, found.text
    assert found.json()["total"] == 1
    assert found.json()["items"][0]["cancellation_request"]["status"] == "pending"
    assert (await c.client.get("/v1/admin/order-search", params={"cancellation_pending": "true"}, headers=auth_headers(c.buyer))).status_code == 403
