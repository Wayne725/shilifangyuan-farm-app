import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import activity_payments as service
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository
from app.models import (Activity, ActivityCharge, ActivityRefundRequest, ActivityRegistration,
    ActivityRegistrationStatus as RegistrationStatus, ActivityStatus, Invoice, Notification,
    OutboxEvent, PaymentAttempt, PaymentStatus, PointTransaction, Refund, RefundStatus, User)
from app.routers.activity_payments import router
from app.routers.community import community_router
from app.schemas import ActivityCancelRegistration, ActivityRegistrationCreate
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_activity_payments import make_activity, paid, pending_attempt, registration
from tests.test_order_payment_revisions import success_payload
from tests.test_raygate_regressions import _callback_payload


@pytest.fixture(autouse=True)
async def production_flush_mode(postgres_sessions):
    postgres_sessions.configure(autoflush=False)


async def serialized_pair(monkeypatch, first, operations):
    entered = {name: asyncio.Event() for name in operations}
    release = asyncio.Event()
    original_load, original_lock = service.load_activity, service.lock_activity_payment_subject

    async def gate(session):
        name = session.info.get("operation")
        if name in entered and not entered[name].is_set():
            entered[name].set()
            if name == first:
                await release.wait()

    async def load(session, *args, **kwargs):
        result = await original_load(session, *args, **kwargs)
        if kwargs.get("lock"):
            await gate(session)
        return result

    async def lock(session, *args, **kwargs):
        result = await original_lock(session, *args, **kwargs)
        await gate(session)
        return result

    monkeypatch.setattr(service, "load_activity", load)
    monkeypatch.setattr(service, "lock_activity_payment_subject", lock)
    second = next(name for name in operations if name != first)
    lead = asyncio.create_task(operations[first]())
    try:
        await asyncio.wait_for(entered[first].wait(), 10)
        follower = asyncio.create_task(operations[second]())
        try:
            await asyncio.wait_for(entered[second].wait(), .15)
            raise AssertionError("第二項操作繞過活動資料鎖")
        except TimeoutError:
            pass
        finally:
            release.set()
        return dict(zip((first, second), await asyncio.wait_for(asyncio.gather(lead, follower), 20)))
    finally:
        release.set()


@pytest.mark.parametrize("same_user", [False, True])
async def test_last_seat_concurrent_registration_creates_only_one_charge(postgres_sessions, monkeypatch, same_user):
    async with postgres_sessions() as session:
        c = await make_activity(session)
        activity_id = c.activity.id
        user_ids = {"first": c.buyer.id, "second": c.buyer.id if same_user else c.second.id}

    async def register(name):
        async with postgres_sessions() as session:
            session.info["operation"] = name
            user = await session.get(User, user_ids[name])
            return await service.register(session, activity_id, user,
                ActivityRegistrationCreate(contact_name=user.display_name, contact_email=user.email))

    results = await serialized_pair(monkeypatch, "first", {name: lambda name=name: register(name) for name in user_ids})
    assert results["first"].status == "pending_payment"
    assert results["second"].status == ("pending_payment" if same_user else "waitlisted")
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(ActivityCharge.id))) == 1
        assert await session.scalar(select(func.count(ActivityRegistration.id)).where(ActivityRegistration.status.in_(service.OCCUPIED))) == 1
        assert await session.scalar(select(func.count(Notification.id))) == 1


@pytest.mark.parametrize("first", ["callback", "expiry"])
async def test_verified_expiry_and_late_callback_never_reclaim_promoted_seat(postgres_sessions, monkeypatch, first):
    async with postgres_sessions() as session:
        c = await make_activity(session)
        primary = await registration(c)
        waiter = await registration(c, c.second)
        attempt = await pending_attempt(c, primary.charge.id)
        charge = await session.get(ActivityCharge, primary.charge.id)
        charge.expires_at = attempt.expires_at = c.now - timedelta(minutes=1)
        payload = success_payload(attempt)
        payload["PaymentDate"] = (charge.expires_at - timedelta(seconds=1)).isoformat()
        await session.commit()
        attempt_id, primary_id, waiter_id, now = attempt.id, primary.id, waiter.id, c.now

    async def callback():
        async with postgres_sessions() as session:
            session.info["operation"] = "callback"
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("concurrent-expiry-paid", payload)

    async def expiry():
        async with postgres_sessions() as session:
            session.info["operation"] = "expiry"
            return await service.record_payment_verification(session, attempt_id, now, verified_unpaid=True)

    results = await serialized_pair(monkeypatch, first, {"callback": callback, "expiry": expiry})
    assert results["callback"] == ("paid" if first == "callback" else "late_paid_refund_required")
    async with postgres_sessions() as session:
        primary = await session.get(ActivityRegistration, primary_id)
        waiter = await session.get(ActivityRegistration, waiter_id)
        assert primary.status == (RegistrationStatus.REGISTERED if first == "callback" else RegistrationStatus.CANCELLED)
        assert waiter.status == (RegistrationStatus.WAITLISTED if first == "callback" else RegistrationStatus.PENDING_PAYMENT)
        assert await session.scalar(select(func.count(ActivityRegistration.id)).where(ActivityRegistration.status.in_(service.OCCUPIED))) == 1
        assert await session.scalar(select(func.count(Refund.id))) == 0
        assert await session.scalar(select(func.count(ActivityRefundRequest.id))) == (0 if first == "callback" else 1)
        assert await session.scalar(select(func.count(Invoice.id))) == 0
        assert await session.scalar(select(func.count(PointTransaction.id))) == 0


@pytest.mark.parametrize("first", ["callback", "cancel"])
async def test_batch_cancel_preview_serializes_receipt_and_never_preapproves_late_payment(postgres_sessions, monkeypatch, first):
    async with postgres_sessions() as session:
        c = await make_activity(session, capacity=2)
        await paid(c)
        second = await registration(c, c.second)
        pending = await pending_attempt(c, second.charge.id)
        activity = await service.load_activity(session, c.activity.id, lock=True)
        preview = await service.cancellation_preview(session, activity)
        preview["expected_updated_at"] = preview["expected_updated_at"].isoformat()
        body = {**preview, "reason": "天候取消", "approve_refunds": True}
        activity_id, pending_id, headers, payload = c.activity.id, pending.id, auth_headers(c.admin), success_payload(pending)
        await session.commit()

    async def callback():
        async with postgres_sessions() as session:
            session.info["operation"] = "callback"
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("concurrent-cancel-paid", payload)

    async def cancel():
        async with postgres_sessions() as session:
            session.info["operation"] = "cancel"
            async with api_test_context(session, [router, community_router]) as client:
                return await client.post(f"/v1/admin/activities/{activity_id}/cancel", headers=headers, json=body)

    results = await serialized_pair(monkeypatch, first, {"callback": callback, "cancel": cancel})
    assert results["cancel"].status_code == (409 if first == "callback" else 200), results["cancel"].text
    assert results["callback"] == ("paid" if first == "callback" else "late_paid_refund_required")
    async with postgres_sessions() as session:
        activity = await session.get(Activity, activity_id)
        assert activity.status == (ActivityStatus.PUBLISHED if first == "callback" else ActivityStatus.CANCELLED)
        assert await session.scalar(select(func.count(Refund.id))) == (0 if first == "callback" else 1)
        if first == "cancel":
            request = await session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id == pending_id))
            assert request.status == "pending_review"
            assert await session.scalar(select(func.count(ActivityRegistration.id)).where(ActivityRegistration.status.in_(service.OCCUPIED))) == 0


async def test_concurrent_refund_reviews_create_one_refund_and_one_promotion(postgres_sessions, monkeypatch):
    async with postgres_sessions() as session:
        c = await make_activity(session)
        await paid(c)
        waiter = await registration(c, c.second)
        cancelled = await service.cancel_registration(session, c.activity.id, c.buyer, ActivityCancelRegistration(reason="無法參加"))
        request = cancelled.refund_request
        body = {"decision": "approve", "expected_version": request.version, "expected_amount": request.amount, "reason": "核准整筆退款"}
        request_id, waiter_id, headers = request.id, waiter.id, auth_headers(c.admin)

    async def review(name):
        async with postgres_sessions() as session:
            session.info["operation"] = name
            async with api_test_context(session, [router]) as client:
                return await client.post(f"/v1/admin/activity-refund-requests/{request_id}/review", headers=headers, json=body)

    results = await serialized_pair(monkeypatch, "first", {name: lambda name=name: review(name) for name in ("first", "second")})
    assert results["first"].status_code == 200, results["first"].text
    assert results["second"].status_code == 409, results["second"].text
    async with postgres_sessions() as session:
        refunds = list(await session.scalars(select(Refund)))
        assert len(refunds) == 1 and refunds[0].status == RefundStatus.PENDING and refunds[0].amount == 300
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 1
        assert (await session.get(ActivityRegistration, waiter_id)).status == RegistrationStatus.PENDING_PAYMENT
        assert await session.scalar(select(func.count(ActivityCharge.id))) == 2


async def test_concurrent_identical_callbacks_publish_paid_notification_once(postgres_sessions, monkeypatch):
    async with postgres_sessions() as session:
        c = await make_activity(session)
        first = await registration(c)
        attempt = await pending_attempt(c, first.charge.id)
        attempt_id, payload = attempt.id, success_payload(attempt)

    async def callback(name):
        async with postgres_sessions() as session:
            session.info["operation"] = name
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("identical-paid", payload)

    results = await serialized_pair(monkeypatch, "first", {name: lambda name=name: callback(name) for name in ("first", "second")})
    assert results == {"first": "paid", "second": "duplicate"}
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Notification.id)).where(Notification.data["dedupe_key"].as_string() == f"activity-paid:{attempt_id}")) == 1
        assert await session.scalar(select(func.count(Invoice.id))) == 0
        assert await session.scalar(select(func.count(PointTransaction.id))) == 0


@pytest.mark.parametrize("first", ["external", "approve"])
async def test_external_refund_and_admin_approval_preserve_fact_without_double_refund(postgres_sessions, monkeypatch, first):
    async with postgres_sessions() as session:
        c = await make_activity(session)
        _, attempt = await paid(c)
        cancelled = await service.cancel_registration(session, c.activity.id, c.buyer, ActivityCancelRegistration(reason="無法參加"))
        request = cancelled.refund_request
        body = {"decision": "approve", "expected_version": request.version, "expected_amount": request.amount, "reason": "核准整筆退款"}
        payload = _callback_payload(attempt, status=3, return_code="0000", message="退款完成", associated_order_id="activity-external-refund")
        request_id, attempt_id, charge_id, headers = request.id, attempt.id, attempt.activity_charge_id, auth_headers(c.admin)

    async def external():
        async with postgres_sessions() as session:
            session.info["operation"] = "external"
            return await SQLAlchemyPaymentCallbackRepository(session).apply_raygate_payment_callback("external-refunded", payload)

    async def approve():
        async with postgres_sessions() as session:
            session.info["operation"] = "approve"
            async with api_test_context(session, [router]) as client:
                return await client.post(f"/v1/admin/activity-refund-requests/{request_id}/review", headers=headers, json=body)

    results = await serialized_pair(monkeypatch, first, {"external": external, "approve": approve})
    assert results["external"] == "refunded"
    assert results["approve"].status_code == (409 if first == "external" else 200), results["approve"].text
    async with postgres_sessions() as session:
        request = await session.get(ActivityRefundRequest, request_id)
        assert request.status == ("externally_refunded" if first == "external" else "approved")
        assert (request.reviewed_by_id is None) == (first == "external")
        assert (await session.get(ActivityCharge, charge_id)).payment_status == PaymentStatus.REFUNDED
        assert (await session.get(PaymentAttempt, attempt_id)).status == PaymentStatus.REFUNDED
        refunds = list(await session.scalars(select(Refund)))
        assert len(refunds) == 1 and refunds[0].status == RefundStatus.COMPLETED
        events = list(await session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "refund.requested")))
        assert len(events) == (0 if first == "external" else 1)
        if events:
            from app import jobs
            await jobs._process_refund_event(session, events[0], make_test_settings(payment_provider="raygate"))
        assert await session.scalar(select(func.count(Refund.id))) == 1
        assert await session.scalar(select(func.count(Notification.id)).where(Notification.data["dedupe_key"].as_string() == f"activity-refunded:{refunds[0].id}")) == 1
