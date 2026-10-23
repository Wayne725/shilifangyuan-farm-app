import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    GroupCampaign, GroupDecisionStatus, GroupIntakeStatus, InventoryReservation,
    MealCancellationRequest, Order, OrderKind, OutboxEvent, PaymentStatus, Product,
    Refund, ReservationStatus, SalesChannel, TargetType,
)
from app.routers.groups import groups_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_meal_cancellation_review import mark_paid, seed_cancellation_order
from tests.test_integrations import payment_settings


async def seed_group(factory, *, paid, historical_request):
    async with factory() as session:
        c = await seed_cancellation_order(session)
        if paid:
            await mark_paid(c)
        product = Product(slug="group-phone-race", name="共同購買白米", category="米・雜糧", unit="包",
            member_price=100, nonmember_price=100, stock_quantity=3)
        session.add(product)
        await session.flush()
        campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=product.id, title="電話退單競爭測試",
            member_price=100, nonmember_price=100, min_paid_quantity=1, supply_cap=5,
            paid_quantity=2 if paid else 0, reserved_quantity=0 if paid else 2,
            deadline=c.now + timedelta(days=1), estimated_pickup_start=c.now + timedelta(days=2),
            estimated_pickup_end=c.now + timedelta(days=3), decision_status=GroupDecisionStatus.CONFIRMED,
            intake_status=GroupIntakeStatus.OPEN, confirmed_at=c.now - timedelta(hours=2), created_by_id=c.admin.id)
        session.add(campaign)
        await session.flush()
        c.order.sales_channel = SalesChannel.GROUP
        c.order.order_kind = OrderKind.GROUP
        c.order.meal_event = None
        c.order.group_campaign = campaign
        c.order.contact_email = "group-race@example.com"
        item = c.order.items[0]
        item.source_meal_offering_id = None
        item.source_product_id = product.id
        item.product_name = product.name
        item.unit_label = product.unit
        c.reservation.source_meal_offering_id = None
        c.reservation.group_campaign_id = campaign.id
        c.offering.paid_quantity = c.offering.reserved_quantity = 0
        request = None
        if historical_request:
            request = MealCancellationRequest(order=c.order, requested_by_id=c.buyer.id, reason="歷史申請")
            session.add(request)
        await session.commit()
        return c, campaign.id, product.id, request.id if request else None


@pytest.mark.parametrize("first", ["single", "campaign"])
@pytest.mark.parametrize("operation,paid", [("cancel", True), ("cancel", False), ("review", True), ("refund", True), ("payment", False)])
@pytest.mark.asyncio
async def test_single_order_and_whole_campaign_cancellation_share_parent_first_lock_order(
    postgres_sessions, monkeypatch, first, operation, paid,
):
    import app.routers.groups as groups
    import app.routers.orders as orders
    import app.integrations.payment_service as payment_service

    c, campaign_id, product_id, request_id = await seed_group(postgres_sessions, paid=paid, historical_request=operation == "review")
    if operation == "payment":
        async with postgres_sessions() as session:
            reservation = await session.get(InventoryReservation, c.reservation.id)
            campaign = await session.get(GroupCampaign, campaign_id)
            reservation.status = ReservationStatus.RELEASED
            campaign.reserved_quantity = 0
            await session.commit()
    entered = {"single": asyncio.Event(), "campaign": asyncio.Event()}
    release = asyncio.Event()
    single_module = payment_service if operation == "payment" else orders
    single_name = "ensure_order_payment_runtime_enabled" if operation == "payment" else "request_order_refund" if operation == "refund" else "_cancel_approved_order"
    original_single = getattr(single_module, single_name)
    original_campaign = groups.close_campaign_with_refunds

    async def gated_single(*args, **kwargs):
        entered["single"].set()
        if first == "single":
            await release.wait()
        return await original_single(*args, **kwargs)

    async def gated_campaign(*args, **kwargs):
        entered["campaign"].set()
        if first == "campaign":
            await release.wait()
        return await original_campaign(*args, **kwargs)

    monkeypatch.setattr(single_module, single_name, gated_single)
    monkeypatch.setattr(groups, "close_campaign_with_refunds", gated_campaign)

    async def post(kind):
        if kind == "campaign":
            path = f"/v1/group-campaigns/{campaign_id}/admin/cancel"
            body = {"reason": "電話確認取消整團"}
        elif operation == "review":
            path = f"/v1/orders/{c.order.id}/admin/cancellation-requests/{request_id}/review"
            body = {"decision": "approve", "reason": "電話確認歷史退單"}
        elif operation == "payment":
            path = f"/v1/orders/{c.order.id}/payment-attempts"
            body = {}
        else:
            path = f"/v1/orders/{c.order.id}/admin/{operation}"
            body = {"reason": "電話確認單張退單"}
        async with postgres_sessions() as session:
            async with api_test_context(session, [orders_router, groups_router, payments_router], settings=payment_settings() if operation == "payment" else make_test_settings()) as client:
                actor = c.buyer if kind == "single" and operation == "payment" else c.admin
                return await client.post(path, json=body, headers=auth_headers(actor))

    second = "campaign" if first == "single" else "single"
    lead = asyncio.create_task(post(first))
    await asyncio.wait_for(entered[first].wait(), timeout=5)
    follower = asyncio.create_task(post(second))
    try:
        await asyncio.wait_for(entered[second].wait(), timeout=0.3)
    except TimeoutError:
        pass
    release.set()
    results = await asyncio.wait_for(asyncio.gather(lead, follower, return_exceptions=True), timeout=15)
    assert not any(isinstance(result, Exception) for result in results), [str(result) for result in results]
    assert results[0].status_code == (201 if first == "single" and operation == "payment" else 200), results[0].text
    assert results[1].status_code in {200, 409}, results[1].text
    async with postgres_sessions() as session:
        campaign = await session.get(GroupCampaign, campaign_id)
        order = await session.get(Order, c.order.id)
        product = await session.get(Product, product_id)
        reservation = await session.get(InventoryReservation, c.reservation.id)
        assert campaign.decision_status == GroupDecisionStatus.CANCELLED
        assert campaign.paid_quantity == campaign.reserved_quantity == 0
        assert campaign.threshold_version == int(paid)
        assert order.payment_status == (PaymentStatus.REFUND_PENDING if paid else PaymentStatus.EXPIRED)
        assert reservation.status == ReservationStatus.RELEASED
        assert await session.scalar(select(func.count(InventoryReservation.id)).where(InventoryReservation.status == ReservationStatus.ACTIVE)) == 0
        assert product.stock_quantity == 3
        assert await session.scalar(select(func.count(Refund.id))) == int(paid)
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == int(paid)
