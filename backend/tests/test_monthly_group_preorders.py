from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

import app.jobs as jobs
from app.domain import apply_paid_quantity, aware, order_available_actions, remove_paid_quantity
from app.group_review import group_contract_fingerprint
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.models import (
    AdminAudit, FulfillmentStatus, GroupCampaign, GroupDecisionStatus, GroupIntakeStatus,
    Notification, Order, OutboxEvent, PaymentStatus, PointTransaction, Refund,
)
from app.routers.groups import groups_router
from app.routers.orders import load_order, orders_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_group_campaign_shipping import campaign_context, campaign_payload
from tests.test_group_review_window import group_fixture
from tests.test_order_payment_revisions import success_payload


@pytest.mark.parametrize("old_minimum", [None, 30])
async def test_admin_publishes_monthly_preorder_without_threshold_or_review(campaign_context, old_minimum):
    ctx = campaign_context
    body = campaign_payload(ctx["product"].id, can_ship=False)
    body.pop("min_paid_quantity")
    if old_minimum is not None:
        body["min_paid_quantity"] = old_minimum
    created = await ctx["client"].post("/v1/group-campaigns", headers=auth_headers(ctx["admin"]), json=body)

    assert created.status_code == 201, created.text
    actual = created.json()
    assert actual["preorder_only"] is True
    assert actual["decision_status"] == "confirmed"
    assert actual["intake_status"] == "open"
    assert actual["min_paid_quantity"] == 1
    assert actual["can_confirm"] is False and actual["can_edit_review_deadline"] is False
    assert actual["confirmation_deadline"] is None
    stored = await ctx["session"].get(GroupCampaign, actual["id"])
    assert stored.legacy_confirmed_intake is True


async def make_preorder(session, *, cutoff=True, paid=False):
    values = await group_fixture(session, cutoff=cutoff, paid=paid)
    campaign = values[1]
    campaign.preorder_only = True
    campaign.decision_status = GroupDecisionStatus.CONFIRMED
    campaign.legacy_confirmed_intake = True
    await session.commit()
    return values


async def test_preorder_payment_below_old_minimum_fulfills_and_issues_once(database_session):
    session = database_session
    admin, campaign, _, order, _, attempt = await make_preorder(session, cutoff=False)
    campaign.legacy_confirmed_intake = False
    attempt.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    fingerprint = group_contract_fingerprint(campaign)
    order.group_contract_fingerprint = fingerprint
    await session.commit()
    repository = SQLAlchemyPaymentCallbackRepository(session)
    payload = success_payload(attempt)

    assert await repository.apply_raygate_payment_callback("preorder-paid", payload) == "paid"
    assert await repository.apply_raygate_payment_callback("preorder-paid", payload) == "duplicate"
    await session.refresh(campaign)
    assert campaign.min_paid_quantity == 4 and campaign.paid_quantity == 3
    assert campaign.decision_status == GroupDecisionStatus.CONFIRMED
    assert campaign.intake_status == GroupIntakeStatus.OPEN
    assert group_contract_fingerprint(campaign) == fingerprint
    current = await load_order(session, order.id, refresh=True)
    assert "start_preparing" in order_available_actions(current, viewer_is_admin=True)
    async with api_test_context(session, [orders_router]) as client:
        response = await client.post(f"/v1/orders/{order.id}/fulfillment", headers=auth_headers(admin), json={"status": "preparing"})
    assert response.status_code == 200, response.text
    assert response.json()["fulfillment_status"] == "preparing"
    assert await session.scalar(select(func.count(Refund.id))) == 0
    assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == 1
    assert await session.scalar(select(func.sum(PointTransaction.amount))) == 3


async def test_empty_preorder_closes_without_failed_threshold_or_refunds(campaign_context):
    ctx = campaign_context
    response = await ctx["client"].post("/v1/group-campaigns", headers=auth_headers(ctx["admin"]), json=campaign_payload(ctx["product"].id))
    assert response.status_code == 201, response.text
    session = ctx["session"]
    campaign = await session.get(GroupCampaign, response.json()["id"])
    campaign.deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    campaign.min_paid_quantity = 15
    await session.commit()

    for _ in range(2):
        await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)

    await session.refresh(campaign)
    assert campaign.decision_status == GroupDecisionStatus.CONFIRMED
    assert campaign.intake_status == GroupIntakeStatus.CLOSED
    assert campaign.paid_quantity == 0
    assert await session.scalar(select(func.count(Refund.id))) == 0
    assert await session.scalar(select(func.count(Notification.id)).where(Notification.event_type.in_({"group.review_ready", "group.review_overdue", "group_threshold_reached"}))) == 0


async def test_preorder_cutoff_keeps_unknown_payment_then_accepts_verified_timely_callback(database_session):
    session = database_session
    _, campaign, _, order, _, attempt = await make_preorder(session)
    attempt.settlement_unknown_since = datetime.now(timezone.utc)
    await session.commit()
    for _ in range(2):
        await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.decision_status == GroupDecisionStatus.CONFIRMED
    assert campaign.intake_status == GroupIntakeStatus.SETTLING
    assert campaign.reserved_quantity == 3
    assert await session.scalar(select(func.count(Refund.id))) == 0
    assert await session.scalar(select(func.count(Notification.id)).where(Notification.event_type == "group.settlement_pending")) == 1
    payload = success_payload(attempt)
    payload["PaymentDate"] = (attempt.expires_at - timedelta(seconds=1)).isoformat()
    repository = SQLAlchemyPaymentCallbackRepository(session)
    assert await repository.apply_raygate_payment_callback("preorder-delayed-callback", payload) == "paid"
    assert await repository.apply_raygate_payment_callback("preorder-delayed-callback", payload) == "duplicate"
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    await session.refresh(campaign)
    await session.refresh(order)
    assert campaign.intake_status == GroupIntakeStatus.CLOSED
    assert campaign.paid_quantity == 3 and campaign.reserved_quantity == 0
    assert order.payment_status == PaymentStatus.PAID
    assert await session.scalar(select(func.count(Refund.id))) == 0
    assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == 1


@pytest.mark.parametrize("cancelled", [False, True])
async def test_preorder_late_or_cancelled_payment_still_refunds_once(database_session, cancelled):
    session = database_session
    _, campaign, _, order, _, attempt = await make_preorder(session)
    payload = success_payload(attempt)
    if cancelled:
        order.cancelled_at = datetime.now(timezone.utc)
        order.fulfillment_status = FulfillmentStatus.CANCELLED
        payload["PaymentDate"] = (attempt.expires_at - timedelta(seconds=1)).isoformat()
    await session.commit()
    repository = SQLAlchemyPaymentCallbackRepository(session)
    assert await repository.apply_raygate_payment_callback("preorder-rejected-payment", payload) == "late_paid_refund_required"
    assert await repository.apply_raygate_payment_callback("preorder-rejected-payment", payload) == "duplicate"
    await session.refresh(campaign)
    assert campaign.paid_quantity == 0
    assert order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert await session.scalar(select(func.count(Refund.id))) == 1
    assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == 0


async def test_preorder_disables_old_review_endpoints_and_keeps_admin_cancellation(database_session):
    session = database_session
    admin, campaign, _, order, _, _ = await make_preorder(session, paid=True)
    path = f"/v1/group-campaigns/{campaign.id}"
    headers = auth_headers(admin)
    now = datetime.now(timezone.utc)
    async with api_test_context(session, [groups_router]) as client:
        confirm = await client.post(path + "/admin/confirm", headers=headers, json={"expected_threshold_version": 0, "final_pickup_at": (now + timedelta(days=4)).isoformat()})
        assert confirm.status_code == 409
        reject = await client.post(path + "/admin/reject", headers=headers, json={"reason": "不再使用成團審核"})
        assert reject.status_code == 409
        reminder = await client.patch(path + "/admin/review-deadline", headers=headers, json={
            "confirmation_deadline": (now + timedelta(days=3)).isoformat(),
            "expected_confirmation_deadline": aware(campaign.confirmation_deadline).isoformat(),
            "expected_updated_at": aware(campaign.updated_at).isoformat(),
        })
        assert reminder.status_code == 409
        cancelled = await client.post(path + "/admin/cancel", headers=headers, json={"reason": "供應方取消供貨"})
        assert cancelled.status_code == 200, cancelled.text
        assert (await client.post(path + "/admin/cancel", headers=headers, json={})).status_code == 409
    assert campaign.decision_status == GroupDecisionStatus.CANCELLED
    assert order.payment_status == PaymentStatus.REFUND_PENDING
    assert await session.scalar(select(func.count(Refund.id))) == 1
    assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "campaign.cancelled")) == 1


async def test_preorder_capacity_release_reopens_without_overriding_manual_pause(database_session):
    session = database_session
    _, campaign, _, _, _, _ = await make_preorder(session, cutoff=False)
    campaign.legacy_confirmed_intake = False
    campaign.supply_cap = 3
    now = datetime.now(timezone.utc)
    apply_paid_quantity(campaign, 3, now)
    assert campaign.intake_status == GroupIntakeStatus.FULL
    remove_paid_quantity(campaign, 1, now)
    assert campaign.intake_status == GroupIntakeStatus.OPEN
    campaign.intake_status = GroupIntakeStatus.PAUSED
    remove_paid_quantity(campaign, 1, now)
    assert campaign.intake_status == GroupIntakeStatus.PAUSED
    assert campaign.decision_status == GroupDecisionStatus.CONFIRMED
    await session.commit()


async def test_paid_preorder_pickup_edit_is_audited_notified_and_does_not_change_contract(database_session):
    session = database_session
    admin, campaign, _, order, _, _ = await make_preorder(session, paid=True)
    original_final = datetime.now(timezone.utc) + timedelta(days=5)
    campaign.final_pickup_at = original_final
    await session.commit()
    original_contract = group_contract_fingerprint(campaign)
    path = f"/v1/group-campaigns/{campaign.id}"
    now = datetime.now(timezone.utc)
    changes = {"estimated_pickup_start": (now + timedelta(days=6)).isoformat(),
               "estimated_pickup_end": (now + timedelta(days=7)).isoformat()}
    async with api_test_context(session, [groups_router]) as client:
        assert (await client.patch(path, headers=auth_headers(admin), json=changes)).status_code == 409
        versioned = {**changes, "expected_updated_at": aware(campaign.updated_at).isoformat()}
        edited = await client.patch(path, headers=auth_headers(admin), json=versioned)
        assert edited.status_code == 200, edited.text
        assert (await client.patch(path, headers=auth_headers(admin), json=versioned)).status_code == 409
        unchanged = {**changes, "expected_updated_at": edited.json()["updated_at"]}
        assert (await client.patch(path, headers=auth_headers(admin), json=unchanged)).status_code == 200
    assert group_contract_fingerprint(campaign) == original_contract
    assert order.payment_status == PaymentStatus.PAID and order.amount_total == 300
    assert campaign.min_paid_quantity == 4 and campaign.paid_quantity == 3
    assert campaign.final_pickup_at is None
    audits = list(await session.scalars(select(AdminAudit).where(AdminAudit.action == "campaign.update")))
    assert len(audits) == 1 and audits[0].actor_id == admin.id
    assert "before" in audits[0].data and "after" in audits[0].data
    assert datetime.fromisoformat(audits[0].data["before"]["final_pickup_at"]) == original_final
    assert audits[0].data["after"]["final_pickup_at"] is None
    events = list(await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "group.pickup_updated")))
    assert len(events) == 1 and events[0].payload["user_ids"] == [order.user_id]


@pytest.mark.parametrize("decision", [GroupDecisionStatus.CANCELLED, GroupDecisionStatus.REJECTED,
                                      GroupDecisionStatus.FAILED_UNMET, GroupDecisionStatus.EXPIRED_UNCONFIRMED])
async def test_terminal_preorder_cannot_change_pickup_or_clear_final(database_session, decision):
    session = database_session
    admin, campaign, _, _, _, _ = await make_preorder(session, paid=True)
    now = datetime.now(timezone.utc)
    campaign.decision_status = decision
    campaign.intake_status = GroupIntakeStatus.CLOSED
    campaign.final_pickup_at = now + timedelta(days=5)
    await session.commit()
    before_times = tuple(aware(value) for value in (campaign.estimated_pickup_start, campaign.estimated_pickup_end, campaign.final_pickup_at))
    models = (AdminAudit, OutboxEvent)
    before_counts = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    body = {"estimated_pickup_start": (now + timedelta(days=6)).isoformat(),
            "estimated_pickup_end": (now + timedelta(days=7)).isoformat(),
            "expected_updated_at": aware(campaign.updated_at).isoformat()}
    async with api_test_context(session, [groups_router]) as client:
        response = await client.patch(f"/v1/group-campaigns/{campaign.id}", headers=auth_headers(admin), json=body)
    assert response.status_code == 409, response.text
    await session.refresh(campaign)
    assert campaign.decision_status == decision
    assert tuple(aware(value) for value in (campaign.estimated_pickup_start, campaign.estimated_pickup_end, campaign.final_pickup_at)) == before_times
    assert [await session.scalar(select(func.count()).select_from(model)) for model in models] == before_counts
