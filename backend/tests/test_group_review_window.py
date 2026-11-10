from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

import app.jobs as jobs
from app.domain import apply_paid_quantity, order_amendment_blocked_reason, order_available_actions, remove_paid_quantity
from app.group_review import campaign_has_unsettled_payments
from app.integrations.common import IntegrationResponseError
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt, PaymentApplicationError
from app.integrations.raygate import RayGateTransactionNotFoundError
from app.models import (AdminAudit, GroupCampaign, GroupDecisionStatus, GroupIntakeStatus, InventoryReservation,
                        Notification, OrderKind, OutboxEvent, PaymentStatus, Refund, ReservationStatus, SalesChannel,
                        TargetType, User, UserRole)
from app.routers.groups import groups_router
from app.routers.orders import load_order
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_order_payment_revisions import make_revised_order, success_payload


async def group_fixture(session, *, paid=False, cutoff=True):
    user, product, order, old, attempt = await make_revised_order(session, current_paid=paid)
    now = datetime.now(timezone.utc)
    admin = User(email="admin@example.com", display_name="審核管理員", password_hash="test", user_role=UserRole.ADMIN)
    campaign = GroupCampaign(target_type=TargetType.PRODUCT, target_id=product.id, title="共同購買測試", created_by=admin,
        member_price=100, nonmember_price=100, min_paid_quantity=4, supply_cap=20, per_user_cap=5,
        paid_quantity=3 if paid else 0, reserved_quantity=0 if paid else 3,
        deadline=now + timedelta(hours=-1 if cutoff else 1), confirmation_deadline=now + timedelta(days=3),
        estimated_pickup_start=now + timedelta(days=4), estimated_pickup_end=now + timedelta(days=5),
        decision_status=GroupDecisionStatus.RECRUITING, intake_status=GroupIntakeStatus.OPEN)
    session.add(campaign)
    await session.flush()
    order.order_kind, order.sales_channel, order.group_campaign = OrderKind.GROUP, SalesChannel.GROUP, campaign
    order.contact_email = "buyer@example.com"
    for reservation in await session.scalars(select(InventoryReservation).where(InventoryReservation.order_id == order.id)):
        reservation.source_product_id = None
        reservation.group_campaign_id = campaign.id
    attempt.expires_at = now - timedelta(minutes=1)
    old.next_reconcile_at = now + timedelta(days=1)
    await session.commit()
    return admin, campaign, user, order, old, attempt


@pytest.mark.parametrize("failure", ["timeout", "malformed", "pending", "ecpay_zero"])
@pytest.mark.parametrize("status", [PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.FAILED])
async def test_unknown_current_payment_is_retained_and_rechecked(database_session, monkeypatch, failure, status):
    session = database_session
    admin, campaign, _, order, _, attempt = await group_fixture(session)
    attempt.status = status
    if failure == "ecpay_zero":
        attempt.provider = "ecpay"
    await session.commit()
    class Adapter:
        async def query_order(self, _):
            if failure == "timeout":
                raise IntegrationResponseError("timeout")
            return {} if failure == "malformed" else {"TradeStatus": "0", "PaymentDisposition": "pending"}
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *_: Adapter())
    headers = auth_headers(admin)
    campaign_id = campaign.id
    now = datetime.now(timezone.utc)
    assert await jobs._reconcile_expired_payments(session, make_test_settings(), now, 10) == 1
    await session.refresh(attempt)
    await session.refresh(campaign)
    assert attempt.status == status and attempt.unpaid_verified_at is None
    assert attempt.settlement_unknown_since is not None
    assert campaign.reserved_quantity == 3
    assert await campaign_has_unsettled_payments(session, campaign)
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 10)
    assert campaign.intake_status == GroupIntakeStatus.SETTLING
    async with api_test_context(session, [groups_router]) as client:
        response = await client.post(f"/v1/group-campaigns/{campaign_id}/admin/confirm", headers=headers, json={
            "final_pickup_at": (now + timedelta(days=4)).isoformat(), "expected_threshold_version": 0,
            "allow_below_threshold": True, "reason": "例外"})
        assert response.status_code == 409
    assert await session.scalar(select(func.count(Refund.id))) == 0
    attempt.next_reconcile_at = now - timedelta(seconds=1)
    await session.commit()
    assert await jobs._reconcile_expired_payments(session, make_test_settings(), now, 10) == 1


async def test_verified_absence_releases_once_then_closes_zero_paid_campaign(database_session, monkeypatch):
    session = database_session
    _, campaign, _, _, _, attempt = await group_fixture(session)
    class Adapter:
        async def query_order(self, _):
            raise RayGateTransactionNotFoundError("RG999922")
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *_: Adapter())
    now = datetime.now(timezone.utc)
    await jobs._reconcile_expired_payments(session, make_test_settings(), now, 10)
    await session.refresh(campaign)
    assert campaign.reserved_quantity == 0
    assert attempt.unpaid_verified_at is not None and attempt.status == PaymentStatus.EXPIRED
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 10)
    assert campaign.decision_status == GroupDecisionStatus.FAILED_UNMET and campaign.intake_status == GroupIntakeStatus.CLOSED
    attempt.next_reconcile_at = now - timedelta(seconds=1)
    await session.commit()
    await jobs._reconcile_expired_payments(session, make_test_settings(), now, 10)
    await session.refresh(campaign)
    assert campaign.reserved_quantity == 0


async def test_unknown_without_reservation_counter_still_blocks_review(database_session):
    session = database_session
    _, campaign, _, _, _, _ = await group_fixture(session)
    campaign.reserved_quantity = 0
    await session.commit()
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.intake_status == GroupIntakeStatus.SETTLING


async def test_expired_unknown_prevents_amendment_and_new_checkout(database_session):
    session = database_session
    _, _, user, order, _, _ = await group_fixture(session, cutoff=False)
    order = await load_order(session, order.id, refresh=True)
    assert "核對" in order_amendment_blocked_reason(order)
    assert "pay" not in order_available_actions(order)
    with pytest.raises(PaymentApplicationError, match="核對"):
        await create_payment_attempt(session, order.id, user, make_test_settings())


@pytest.mark.parametrize("paid_quantity", [0, 3, 4])
async def test_confirmation_requires_version_settlement_and_exception(database_session, paid_quantity):
    session = database_session
    admin, campaign, _, _, _, _ = await group_fixture(session, paid=True)
    campaign.paid_quantity = paid_quantity
    campaign.confirmation_deadline = datetime.now(timezone.utc) - timedelta(hours=1)
    await session.commit()
    now = datetime.now(timezone.utc)
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 10)
    async with api_test_context(session, [groups_router]) as client:
        path = f"/v1/group-campaigns/{campaign.id}/admin/confirm"
        body = {"final_pickup_at": (now + timedelta(days=4)).isoformat(), "expected_threshold_version": 999}
        assert (await client.post(path, headers=auth_headers(admin), json=body)).status_code == 409
        body["expected_threshold_version"] = campaign.threshold_version
        response = await client.post(path, headers=auth_headers(admin), json=body)
        if paid_quantity < 4:
            assert response.status_code == 409
            body.update(allow_below_threshold=True, reason="  ")
            response = await client.post(path, headers=auth_headers(admin), json=body)
        if paid_quantity == 0:
            assert response.status_code == 409
            return
        assert response.status_code == 200, response.text
        assert response.json()["confirmed_paid_quantity"] == paid_quantity
        assert response.json()["confirmation_exception_reason"] is None
        assert campaign.confirmation_exception_reason == ("" if paid_quantity == 3 else None)
        assert (await client.post(path, headers=auth_headers(admin), json=body)).status_code == 409
    remove_paid_quantity(campaign, 1, now)
    assert campaign.decision_status == GroupDecisionStatus.CONFIRMED
    assert campaign.confirmed_paid_quantity == paid_quantity


async def test_review_overdue_reminds_once_and_does_not_refund(database_session):
    session = database_session
    _, campaign, _, order, _, _ = await group_fixture(session, paid=True)
    campaign.confirmation_deadline = datetime.now(timezone.utc) - timedelta(days=3)
    await session.commit()
    for _ in range(3):
        await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION
    assert order.payment_status == PaymentStatus.PAID
    assert await session.scalar(select(func.count(Refund.id))) == 0
    assert await session.scalar(select(func.count(Notification.id)).where(Notification.event_type == "group.review_overdue")) == 1
    assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "send_email")) == 1


async def test_review_schedule_edit_is_atomic_versioned_and_audited(database_session):
    session = database_session
    admin, campaign, _, _, _, _ = await group_fixture(session, paid=True)
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    async with api_test_context(session, [groups_router]) as client:
        original = (await client.get(f"/v1/group-campaigns/{campaign.id}")).json()
        path = f"/v1/group-campaigns/{campaign.id}/admin/review-deadline"
        now = datetime.now(timezone.utc)
        body = {"confirmation_deadline": (now + timedelta(days=5)).isoformat(), "expected_confirmation_deadline": original["confirmation_deadline"],
                "expected_updated_at": original["updated_at"], "reason": "供貨期調整"}
        assert (await client.patch(path, headers=auth_headers(admin), json=body)).status_code == 422
        body.update(estimated_pickup_start=(now + timedelta(days=6)).isoformat(), estimated_pickup_end=(now + timedelta(days=7)).isoformat())
        response = await client.patch(path, headers=auth_headers(admin), json=body)
        assert response.status_code == 200, response.text
        assert (await client.patch(path, headers=auth_headers(admin), json=body)).status_code == 409
    assert await session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "campaign.review_deadline")) == 1


@pytest.mark.parametrize("legacy", [False, True])
async def test_late_payment_cannot_change_new_confirmed_snapshot(database_session, legacy):
    session = database_session
    _, campaign, _, order, _, attempt = await group_fixture(session)
    campaign.decision_status = GroupDecisionStatus.CONFIRMED
    campaign.legacy_confirmed_intake = legacy
    campaign.confirmed_paid_quantity = 0
    campaign.intake_status = GroupIntakeStatus.CLOSED
    payload = success_payload(attempt)
    payload["PaymentDate"] = (attempt.expires_at - timedelta(seconds=1)).isoformat()
    await session.commit()
    await SQLAlchemyPaymentCallbackRepository(session).apply_query_result(attempt, payload)
    await session.refresh(campaign)
    assert campaign.paid_quantity == (3 if legacy else 0)
    assert campaign.confirmed_paid_quantity == 0
    assert order.payment_status == (PaymentStatus.PAID if legacy else PaymentStatus.LATE_PAID_REFUND_REQUIRED)


async def test_domain_outcome_email_uses_order_contact_and_deduplicates(database_session):
    session = database_session
    _, campaign, user, order, _, _ = await group_fixture(session, paid=True)
    order.contact_email = "checkout@example.com"
    user.notification_email = "profile@example.com"
    event = OutboxEvent(event_type="group.confirmed", aggregate_type="group_campaign", aggregate_id=campaign.id,
        payload={"user_ids": [user.id], "final_pickup_at": campaign.estimated_pickup_start.isoformat()})
    session.add(event)
    await session.commit()
    for _ in range(2):
        await jobs._process_domain_event(session, event)
    emails = list(await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "send_email")))
    assert len(emails) == 1 and emails[0].payload["to_email"] == "checkout@example.com"
    assert "台灣" in emails[0].payload["text_content"]


async def test_cancelled_unknown_payment_does_not_block_other_orders_and_late_success_refunds(database_session):
    from app.models import FulfillmentStatus
    session = database_session
    _, campaign, _, order, _, attempt = await group_fixture(session)
    order.cancelled_at = datetime.now(timezone.utc)
    order.fulfillment_status = FulfillmentStatus.CANCELLED
    payload = success_payload(attempt)
    payload["PaymentDate"] = (attempt.expires_at - timedelta(seconds=1)).isoformat()
    await session.commit()
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.decision_status == GroupDecisionStatus.FAILED_UNMET
    await SQLAlchemyPaymentCallbackRepository(session).apply_query_result(attempt, payload)
    await session.refresh(campaign)
    assert campaign.paid_quantity == 0 and order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert await session.scalar(select(func.count(Refund.id))) == 1


async def test_current_payment_is_settled_despite_obsolete_unknown_attempt(database_session):
    session = database_session
    _, campaign, _, _, old, _ = await group_fixture(session, paid=True)
    old.status = PaymentStatus.PENDING
    old.settlement_unknown_since = datetime.now(timezone.utc)
    await session.commit()
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.decision_status == GroupDecisionStatus.PENDING_CONFIRMATION
    assert campaign.intake_status == GroupIntakeStatus.CLOSED


async def test_reconciliation_rotates_past_limit_without_changing_review_version(database_session):
    session = database_session
    admin, first, _, _, _, _ = await group_fixture(session)
    now = datetime.now(timezone.utc)
    second = GroupCampaign(target_type=first.target_type, target_id=first.target_id, title="第二團", created_by=admin,
        member_price=100, nonmember_price=100, min_paid_quantity=2, supply_cap=20, per_user_cap=5,
        deadline=now - timedelta(minutes=1), confirmation_deadline=now + timedelta(days=3),
        estimated_pickup_start=now + timedelta(days=4), estimated_pickup_end=now + timedelta(days=5))
    session.add(second)
    await session.commit()
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 1)
    assert first.intake_status == GroupIntakeStatus.SETTLING
    first_version = first.updated_at
    await jobs._reconcile_campaigns(session, make_test_settings(), now + timedelta(seconds=1), 1)
    assert second.decision_status == GroupDecisionStatus.FAILED_UNMET
    await jobs._reconcile_campaigns(session, make_test_settings(), now + timedelta(seconds=2), 1)
    await session.refresh(first)
    from app.domain import aware
    assert aware(first.updated_at) == aware(first_version)


@pytest.mark.parametrize("contact,notification,expected", [
    ("wrong", "profile@example.com", "profile@example.com"),
    (" ", "broken", "buyer@example.com"),
])
async def test_outcome_email_fallback_skips_invalid_addresses(database_session, contact, notification, expected):
    session = database_session
    _, campaign, user, order, _, _ = await group_fixture(session, paid=True)
    order.contact_email, user.notification_email, user.email = contact, notification, "buyer@example.com"
    event = OutboxEvent(event_type="group.confirmed", aggregate_type="group_campaign", aggregate_id=campaign.id, payload={"user_ids": [user.id]})
    session.add(event)
    await session.commit()
    await jobs._process_domain_event(session, event)
    email = await session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "send_email"))
    assert email.payload["to_email"] == expected


async def test_ecpay_zero_or_unsigned_failure_is_never_verified_unpaid(database_session):
    from app.integrations.ecpay import build_check_mac_value
    _, _, _, _, _, attempt = await group_fixture(database_session)
    adapter = SimpleNamespace(settings=SimpleNamespace(merchant_id="3002607", hash_key="5294y06JbISpM5x9", hash_iv="v77hoKGq4kWxNNIS"))
    data = {"MerchantID": "3002607", "MerchantTradeNo": attempt.merchant_trade_no, "TradeAmt": str(attempt.amount), "TradeStatus": "10200095"}
    assert not jobs._verified_ecpay_unpaid(adapter, attempt, data)
    data["CheckMacValue"] = build_check_mac_value(data, adapter.settings.hash_key, adapter.settings.hash_iv)
    assert jobs._verified_ecpay_unpaid(adapter, attempt, data)
    wrong = {**data, "MerchantTradeNo": "other"}
    wrong["CheckMacValue"] = build_check_mac_value(wrong, adapter.settings.hash_key, adapter.settings.hash_iv)
    assert not jobs._verified_ecpay_unpaid(adapter, attempt, wrong)
    pending = {**data, "RtnCode": "0", "TradeStatus": "0"}
    pending["CheckMacValue"] = build_check_mac_value(pending, adapter.settings.hash_key, adapter.settings.hash_iv)
    assert not jobs._verified_ecpay_unpaid(adapter, attempt, pending)


async def test_new_pending_evidence_revokes_old_unpaid_verification(database_session, monkeypatch):
    session = database_session
    _, campaign, _, _, _, attempt = await group_fixture(session)
    now = datetime.now(timezone.utc)
    await jobs._record_group_payment_verification(session, attempt.id, now, verified_unpaid=True)
    await jobs._reconcile_campaigns(session, make_test_settings(), now, 10)
    assert campaign.intake_status == GroupIntakeStatus.CLOSED
    await jobs._record_group_payment_verification(session, attempt.id, now + timedelta(seconds=1), verified_unpaid=False,
                                                 response={"PaymentDisposition": "pending", "TradeStatus": "0"})
    assert attempt.unpaid_verified_at is None
    await jobs._reconcile_campaigns(session, make_test_settings(), now + timedelta(seconds=1), 10)
    assert campaign.intake_status == GroupIntakeStatus.SETTLING
    assert campaign.decision_status == GroupDecisionStatus.FAILED_UNMET


async def test_unsettled_campaign_notifies_admin_once_without_waiting_for_review_deadline(database_session):
    session = database_session
    _, campaign, _, _, _, _ = await group_fixture(session)
    for _ in range(2):
        await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.intake_status == GroupIntakeStatus.SETTLING
    assert await session.scalar(select(func.count(Notification.id)).where(Notification.event_type == "group.settlement_pending")) == 1


@pytest.mark.parametrize("duplicate", [False, True])
async def test_provider_query_pending_revokes_unpaid_proof_even_for_duplicate_event(database_session, duplicate):
    from tests.test_raygate_regressions import _callback_payload
    session = database_session
    _, campaign, _, order, _, attempt = await group_fixture(session)
    repository = SQLAlchemyPaymentCallbackRepository(session)
    payload = _callback_payload(attempt, status=1, return_code="0000", message="交易進行中")
    if duplicate:
        await repository.apply_query_result(attempt, payload)
    await jobs._record_group_payment_verification(session, attempt.id, datetime.now(timezone.utc), verified_unpaid=True)
    assert attempt.unpaid_verified_at is not None
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.decision_status == GroupDecisionStatus.FAILED_UNMET
    await repository.apply_query_result(attempt, payload)
    assert attempt.unpaid_verified_at is None and attempt.settlement_unknown_since is not None
    assert await campaign_has_unsettled_payments(session, campaign)
    await jobs._reconcile_campaigns(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert campaign.decision_status == GroupDecisionStatus.FAILED_UNMET
    assert campaign.intake_status == GroupIntakeStatus.SETTLING


async def test_verified_ecpay_absence_worker_releases_current_reservation(database_session, monkeypatch):
    from app.integrations.ecpay import build_check_mac_value
    session = database_session
    _, campaign, _, _, _, attempt = await group_fixture(session)
    attempt.provider = "ecpay"
    await session.commit()
    class Adapter:
        settings = SimpleNamespace(merchant_id="3002607", hash_key="5294y06JbISpM5x9", hash_iv="v77hoKGq4kWxNNIS")
        async def query_order(self, merchant_trade_no):
            payload = {"MerchantID": self.settings.merchant_id, "MerchantTradeNo": merchant_trade_no,
                       "TradeAmt": str(attempt.amount), "TradeStatus": "10200095"}
            payload["CheckMacValue"] = build_check_mac_value(payload, self.settings.hash_key, self.settings.hash_iv)
            return payload
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *_: Adapter())
    await jobs._reconcile_expired_payments(session, make_test_settings(), datetime.now(timezone.utc), 10)
    assert attempt.unpaid_verified_at is not None and attempt.status == PaymentStatus.EXPIRED
    await session.refresh(campaign)
    assert campaign.reserved_quantity == 0


async def test_group_simulated_ecpay_callback_keeps_unknown_reservation(database_session):
    session = database_session
    _, campaign, _, _, _, attempt = await group_fixture(session)
    attempt.provider = "ecpay"
    await session.commit()
    payload = {"MerchantTradeNo": attempt.merchant_trade_no, "TradeAmt": str(attempt.amount), "RtnCode": "1", "SimulatePaid": "1"}
    result = await SQLAlchemyPaymentCallbackRepository(session).apply_ecpay_payment_callback("group-simulate", payload)
    assert result == "simulated_payment_rejected"
    assert attempt.status == PaymentStatus.PENDING and attempt.settlement_unknown_since is not None
    assert attempt.unpaid_verified_at is None
    await session.refresh(campaign)
    assert campaign.reserved_quantity == 3
    assert await campaign_has_unsettled_payments(session, campaign)


async def test_paid_order_old_failed_attempt_is_not_requeried_forever(database_session, monkeypatch):
    session = database_session
    _, _, _, _, _, attempt = await group_fixture(session, paid=True)
    attempt.status = PaymentStatus.FAILED
    attempt.expires_at = datetime.now(timezone.utc) - timedelta(days=5)
    await session.commit()
    class Adapter:
        async def query_order(self, _):
            raise AssertionError("terminal paid order must not enter unbounded reconciliation")
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", lambda *_: Adapter())
    assert await jobs._reconcile_expired_payments(session, make_test_settings(), datetime.now(timezone.utc), 10) == 0


async def test_checkout_reservation_fills_campaign_then_release_reopens(database_session):
    from app.integrations.payment_service import release_attempt_reservations
    from tests.test_integrations import payment_settings
    session = database_session
    _, campaign, user, order, _, attempt = await group_fixture(session, cutoff=False)
    await jobs._record_group_payment_verification(session, attempt.id, datetime.now(timezone.utc), verified_unpaid=True)
    campaign.supply_cap = campaign.min_paid_quantity = 3
    await session.commit()
    created = await create_payment_attempt(session, order.id, user, payment_settings())
    assert campaign.intake_status == GroupIntakeStatus.FULL and campaign.reserved_quantity == 3
    assert (await create_payment_attempt(session, order.id, user, payment_settings())).id == created.id
    await release_attempt_reservations(session, created)
    await session.commit()
    assert campaign.intake_status == GroupIntakeStatus.OPEN and campaign.reserved_quantity == 0
