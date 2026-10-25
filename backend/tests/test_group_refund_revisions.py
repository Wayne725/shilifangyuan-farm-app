from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.jobs as jobs
from app.models import (
    GroupCampaign, GroupDecisionStatus, GroupIntakeStatus, InventoryReservation,
    OrderKind, OutboxEvent, PaymentStatus, Refund, RefundStatus, ReservationStatus,
    SalesChannel, TargetType, User, UserRole,
)
from app.routers.groups import groups_router
from app.routers.orders import load_order, request_order_refund
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_order_payment_revisions import make_revised_order, success_payload
from tests.test_raygate import FakeRayGateRefundAdapter


async def make_revised_group(session, old_refund_status, decision):
    buyer, product, order, old, latest = await make_revised_order(session, current_paid=True)
    now = datetime.now(timezone.utc)
    admin = User(email="group-revision-admin@example.test", display_name="管理員",
                 password_hash="test", user_role=UserRole.ADMIN)
    campaign = GroupCampaign(
        target_type=TargetType.PRODUCT, target_id=product.id, title="修改數量後的共同購買",
        member_price=100, nonmember_price=100, min_paid_quantity=4, supply_cap=20,
        per_user_cap=5, paid_quantity=3, reserved_quantity=0,
        deadline=now - timedelta(hours=2),
        estimated_pickup_start=now + timedelta(days=1),
        estimated_pickup_end=now + timedelta(days=2),
        confirmation_deadline=now - timedelta(hours=1), decision_status=decision,
        intake_status=GroupIntakeStatus.CLOSED, created_by=admin,
    )
    session.add(campaign)
    await session.flush()
    order.order_kind = OrderKind.GROUP
    order.sales_channel = SalesChannel.GROUP
    order.group_campaign = campaign
    for reservation in await session.scalars(select(InventoryReservation).where(InventoryReservation.order_id == order.id)):
        reservation.source_product_id = None
        reservation.group_campaign_id = campaign.id
    old.status = PaymentStatus.REFUNDED if old_refund_status == RefundStatus.COMPLETED else PaymentStatus.REFUND_PENDING
    old.paid_at = now
    old_refund = Refund(
        order=order, payment_attempt=old, provider=old.provider, amount=old.amount,
        purpose="obsolete_order_revision", status=old_refund_status,
        reason="退回舊版付款", requested_by_id=buyer.id,
        completed_at=now if old_refund_status == RefundStatus.COMPLETED else None,
    )
    session.add(old_refund)
    await session.commit()
    return admin, campaign, order, old, latest, old_refund


@pytest.mark.parametrize("old_refund_status", [RefundStatus.PENDING, RefundStatus.COMPLETED])
@pytest.mark.parametrize("path", ["failed", "expired", "admin_reject", "admin_cancel"])
async def test_group_closure_refunds_current_revision_despite_obsolete_refund(database_session, old_refund_status, path):
    session = database_session
    initial = {"failed": GroupDecisionStatus.RECRUITING, "expired": GroupDecisionStatus.PENDING_CONFIRMATION,
               "admin_reject": GroupDecisionStatus.PENDING_CONFIRMATION, "admin_cancel": GroupDecisionStatus.CONFIRMED}[path]
    admin, campaign, order, old, latest, old_refund = await make_revised_group(session, old_refund_status, initial)
    if path in {"failed", "expired"}:
        await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 100)
        assert campaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION
        assert order.payment_status == PaymentStatus.PAID
        assert await session.scalar(select(func.count(Refund.id)).where(Refund.purpose == "order_cancellation")) == 0
        async with api_test_context(session, [groups_router]) as client:
            response = await client.post(f"/v1/group-campaigns/{campaign.id}/admin/cancel", headers=auth_headers(admin), json={"reason": "管理員核對後取消"})
            assert response.status_code == 200, response.text
        assert await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 100) == 0
    else:
        async with api_test_context(session, [groups_router]) as client:
            response = await client.post(f"/v1/group-campaigns/{campaign.id}/admin/{path.removeprefix('admin_')}",
                                        headers=auth_headers(admin), json={"reason": "管理員整團取消"})
            assert response.status_code == 200, response.text
    current_refunds = list(await session.scalars(select(Refund).where(
        Refund.order_id == order.id, Refund.purpose == "order_cancellation")))
    assert len(current_refunds) == 1
    refund = current_refunds[0]
    assert refund.payment_attempt_id == latest.id
    assert refund.provider == "raygate" and refund.amount == 300
    assert refund.status == RefundStatus.PENDING
    assert latest.status == PaymentStatus.REFUND_PENDING
    assert order.payment_status == PaymentStatus.REFUND_PENDING
    assert campaign.paid_quantity == 0
    assert await session.scalar(select(func.count(OutboxEvent.id)).where(
        OutboxEvent.event_type == "refund.requested", OutboxEvent.aggregate_id == refund.id)) == 1
    assert all(row.status == ReservationStatus.RELEASED for row in await session.scalars(
        select(InventoryReservation).where(InventoryReservation.order_id == order.id)))
    await session.refresh(old_refund)
    assert old_refund.status == old_refund_status and old_refund.amount == 200
    assert old.amount == 200 and latest.amount == 300 and order.revision == 2


async def test_legacy_order_refund_event_does_not_choose_obsolete_pending_refund(database_session, monkeypatch):
    session = database_session
    admin, _, order, _, latest, old_refund = await make_revised_group(
        session, RefundStatus.PENDING, GroupDecisionStatus.CONFIRMED)
    order = await load_order(session, order.id, refresh=True)
    await request_order_refund(session, order, admin, "整單取消")
    await session.commit()
    current_refund = await session.scalar(select(Refund).where(
        Refund.order_id == order.id, Refund.purpose == "order_cancellation"))
    old_refund.created_at = current_refund.created_at + timedelta(seconds=1)
    event = OutboxEvent(event_type="refund.requested", aggregate_type="order", aggregate_id=order.id, payload={})
    session.add(event)
    await session.commit()
    adapter = FakeRayGateRefundAdapter(success_payload(latest))
    monkeypatch.setattr(jobs, "refund_adapter_from_settings", lambda *_: adapter)
    await jobs._process_refund_event(session, event, make_test_settings(payment_provider="raygate"))
    await session.refresh(current_refund)
    await session.refresh(old_refund)
    assert current_refund.status == RefundStatus.COMPLETED
    assert old_refund.status == RefundStatus.PENDING
    assert [call[1]["provider_order_id"] for call in adapter.calls if call[0] == "refund"] == [latest.provider_trade_no]
