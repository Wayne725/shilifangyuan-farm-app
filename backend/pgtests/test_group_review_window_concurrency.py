import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.jobs as jobs
import app.routers.groups as groups
import app.routers.orders as orders
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.models import (GroupCampaign, GroupDecisionStatus, GroupIntakeStatus, Order, OutboxEvent,
                        PaymentStatus, Refund, PointTransaction)
from app.routers.groups import groups_router
from app.routers.orders import orders_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_group_review_window import group_fixture
from tests.test_order_payment_revisions import success_payload


async def ordered_pair(first, operations, entered, release):
    second = next(key for key in operations if key != first)
    lead = asyncio.create_task(operations[first]())
    await asyncio.wait_for(entered[first].wait(), 10)
    follower = asyncio.create_task(operations[second]())
    try:
        await asyncio.wait_for(entered[second].wait(), .15)
        raise AssertionError("follower bypassed campaign lock")
    except TimeoutError:
        pass
    finally:
        release.set()
    return await asyncio.wait_for(asyncio.gather(lead, follower), 20)


@pytest.mark.parametrize("first", ["callback", "confirm"])
async def test_unknown_payment_callback_and_confirmation_are_serialized(postgres_sessions, monkeypatch, first):
    async with postgres_sessions() as session:
        admin, campaign, _, order, _, attempt = await group_fixture(session)
        campaign.min_paid_quantity = 3
        attempt.settlement_unknown_since = datetime.now(timezone.utc)
        payload = success_payload(attempt)
        payload["PaymentDate"] = (attempt.expires_at - timedelta(seconds=1)).isoformat()
        await session.commit()
        campaign_id, order_id, admin_headers = campaign.id, order.id, auth_headers(admin)
    entered = {key: asyncio.Event() for key in ("callback", "confirm")}
    release = asyncio.Event()
    original_success = SQLAlchemyPaymentCallbackRepository._apply_success
    original_reconcile = groups.reconcile_campaign_state
    async def gated_success(self, *args, **kwargs):
        entered["callback"].set()
        if first == "callback": await release.wait()
        return await original_success(self, *args, **kwargs)
    async def gated_reconcile(*args, **kwargs):
        entered["confirm"].set()
        if first == "confirm": await release.wait()
        return await original_reconcile(*args, **kwargs)
    monkeypatch.setattr(SQLAlchemyPaymentCallbackRepository, "_apply_success", gated_success)
    monkeypatch.setattr(groups, "reconcile_campaign_state", gated_reconcile)
    async def callback():
        async with postgres_sessions() as session:
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("review-current-paid", payload)
    async def confirm(version=0):
        async with postgres_sessions() as session:
            async with api_test_context(session, [groups_router]) as client:
                return await client.post(f"/v1/group-campaigns/{campaign_id}/admin/confirm", headers=admin_headers,
                    json={"expected_threshold_version": version, "final_pickup_at": (datetime.now(timezone.utc)+timedelta(days=4)).isoformat()})
    results = await ordered_pair(first, {"callback": callback, "confirm": confirm}, entered, release)
    assert "paid" in results
    assert next(result for result in results if result != "paid").status_code == 409
    assert (await confirm(1)).status_code == 200
    async with postgres_sessions() as session:
        campaign = await session.get(GroupCampaign, campaign_id)
        assert campaign.paid_quantity == campaign.confirmed_paid_quantity == 3 and campaign.reserved_quantity == 0
        assert (await session.get(Order, order_id)).payment_status == PaymentStatus.PAID
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == 1
        assert await session.scalar(select(func.sum(PointTransaction.amount))) == 3


@pytest.mark.parametrize("first", ["refund", "confirm"])
async def test_individual_refund_never_reverses_confirmed_campaign(postgres_sessions, monkeypatch, first):
    async with postgres_sessions() as session:
        admin, campaign, _, order, _, _ = await group_fixture(session, paid=True)
        campaign.min_paid_quantity = 3
        campaign.decision_status, campaign.intake_status = GroupDecisionStatus.PENDING_CONFIRMATION, GroupIntakeStatus.CLOSED
        await session.commit()
        campaign_id, order_id, headers = campaign.id, order.id, auth_headers(admin)
    entered = {key: asyncio.Event() for key in ("refund", "confirm")}
    release = asyncio.Event()
    original_refund, original_reconcile = orders.request_order_refund, groups.reconcile_campaign_state
    async def gated_refund(*args, **kwargs):
        entered["refund"].set()
        if first == "refund": await release.wait()
        return await original_refund(*args, **kwargs)
    async def gated_reconcile(*args, **kwargs):
        entered["confirm"].set()
        if first == "confirm": await release.wait()
        return await original_reconcile(*args, **kwargs)
    monkeypatch.setattr(orders, "request_order_refund", gated_refund)
    monkeypatch.setattr(groups, "reconcile_campaign_state", gated_reconcile)
    async def post(kind):
        async with postgres_sessions() as session:
            async with api_test_context(session, [groups_router, orders_router]) as client:
                if kind == "refund":
                    return await client.post(f"/v1/orders/{order_id}/admin/refund", headers=headers, json={"reason": "個別訂單取消"})
                return await client.post(f"/v1/group-campaigns/{campaign_id}/admin/confirm", headers=headers,
                    json={"expected_threshold_version": 0, "final_pickup_at": (datetime.now(timezone.utc)+timedelta(days=4)).isoformat()})
    results = await ordered_pair(first, {"refund": lambda: post("refund"), "confirm": lambda: post("confirm")}, entered, release)
    assert results[0].status_code == 200, results[0].text
    assert results[1].status_code == (409 if first == "refund" else 200), results[1].text
    async with postgres_sessions() as session:
        campaign = await session.get(GroupCampaign, campaign_id)
        assert campaign.decision_status == (GroupDecisionStatus.CONFIRMED if first == "confirm" else GroupDecisionStatus.PENDING_CONFIRMATION)
        assert campaign.confirmed_paid_quantity == (3 if first == "confirm" else None)
        assert campaign.paid_quantity == 0 and campaign.threshold_version == 1
        assert await session.scalar(select(func.count(Refund.id))) == 1


@pytest.mark.parametrize("first", ["obsolete", "reconcile"])
async def test_obsolete_payment_and_cutoff_do_not_change_current_paid_order(postgres_sessions, monkeypatch, first):
    async with postgres_sessions() as session:
        _, campaign, _, order, old, _ = await group_fixture(session, paid=True)
        payload, campaign_id, order_id = success_payload(old), campaign.id, order.id
    entered = {key: asyncio.Event() for key in ("obsolete", "reconcile")}
    release = asyncio.Event()
    original_success, original_reconcile = SQLAlchemyPaymentCallbackRepository._apply_success, jobs.reconcile_campaign_state
    async def gated_success(self, *args, **kwargs):
        entered["obsolete"].set()
        if first == "obsolete": await release.wait()
        return await original_success(self, *args, **kwargs)
    async def gated_reconcile(*args, **kwargs):
        entered["reconcile"].set()
        if first == "reconcile": await release.wait()
        return await original_reconcile(*args, **kwargs)
    monkeypatch.setattr(SQLAlchemyPaymentCallbackRepository, "_apply_success", gated_success)
    monkeypatch.setattr(jobs, "reconcile_campaign_state", gated_reconcile)
    async def obsolete():
        async with postgres_sessions() as session:
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("review-old-paid", payload)
    async def reconcile():
        async with postgres_sessions() as session:
            return await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    await ordered_pair(first, {"obsolete": obsolete, "reconcile": reconcile}, entered, release)
    async with postgres_sessions() as session:
        campaign = await session.get(GroupCampaign, campaign_id)
        order = await session.get(Order, order_id)
        assert campaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION and campaign.paid_quantity == 3
        assert order.payment_status == PaymentStatus.PAID and order.amount_total == 300
        refund = await session.scalar(select(Refund))
        assert refund.purpose == "obsolete_order_revision" and refund.amount == 200
