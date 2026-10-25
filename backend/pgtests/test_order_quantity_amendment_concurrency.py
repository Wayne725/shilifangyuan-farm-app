import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import GroupCampaign, Order, OrderAmendment, OrderItem, OrderKind, SalesChannel, TargetType, TaxType, User
from app.order_amendments import prepare_quantity_amendment
from app.schemas import OrderQuantityAmendmentConfirm
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import make_test_settings
from tests.test_integrations import make_regular_order


async def mutate(factory, user_id, order_id, item_id, quantity):
    async with factory() as session:
        user = await session.get(User, user_id)
        try:
            order, quote = await prepare_quantity_amendment(session, order_id, OrderQuantityAmendmentConfirm(
                expected_revision=1, expected_total=quantity * 100, client_request_id=uuid4(),
                items=[{"order_item_id": item_id, "quantity": quantity}],
            ), user, make_test_settings(), confirm=True)
            await session.commit()
            return 200
        except HTTPException as exc:
            await session.rollback()
            return exc.status_code


async def test_two_tabs_cannot_both_commit_same_order_revision(postgres_sessions):
    async with postgres_sessions() as session:
        user, product, order = await make_regular_order(session)
        ids = user.id, order.id, order.items[0].id
    results = await asyncio.wait_for(asyncio.gather(
        mutate(postgres_sessions, *ids, 1), mutate(postgres_sessions, *ids, 3),
    ), timeout=10)
    assert sorted(results) == [200, 409]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(OrderAmendment.id))) == 1
        current = await session.get(Order, ids[1])
        assert current.revision == 2 and current.amount_total in {100, 300}


async def test_two_orders_for_one_buyer_cannot_exceed_campaign_cap(postgres_sessions):
    async with postgres_sessions() as session:
        user, product, order = await make_regular_order(session)
        now = datetime.now(timezone.utc)
        campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=product.id, title="並行限購",
            member_price=100, nonmember_price=120, min_paid_quantity=3, supply_cap=20, per_user_cap=5,
            deadline=now+timedelta(days=1), estimated_pickup_start=now+timedelta(days=2), estimated_pickup_end=now+timedelta(days=3),
            created_by_id=user.id)
        session.add(campaign)
        await session.flush()
        order.order_kind = OrderKind.GROUP
        order.sales_channel = SalesChannel.GROUP
        order.group_campaign = campaign
        other = Order(order_number="OTHER-CAP", order_kind=OrderKind.GROUP, sales_channel=SalesChannel.GROUP,
            user_id=user.id, group_campaign=campaign, membership_type_snapshot=user.membership_type,
            amount_total=200, contact_email=user.email, items=[OrderItem(source_product_id=product.id,
            product_name=product.name, unit_label="包", quantity=2, unit_price=100, subtotal=200, tax_type=TaxType.TAXABLE)])
        session.add(other)
        await session.commit()
        arguments = [(user.id, row.id, row.items[0].id, 3) for row in [order, other]]
        campaign_id = campaign.id
    results = await asyncio.wait_for(asyncio.gather(*(mutate(postgres_sessions, *args) for args in arguments)), timeout=10)
    assert sorted(results) == [200, 409]
    async with postgres_sessions() as session:
        quantity = await session.scalar(select(func.sum(OrderItem.quantity)).join(Order).where(Order.group_campaign_id == campaign_id))
        assert quantity == 5
        assert await session.scalar(select(func.count(OrderAmendment.id))) == 1
