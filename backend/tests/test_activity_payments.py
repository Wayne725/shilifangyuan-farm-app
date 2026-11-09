from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import activity_payments as service
from app import jobs
from app.models import (Activity, ActivityCharge, ActivityRefundRequest, ActivityRegistration,
    ActivityRegistrationStatus as RegistrationStatus, ActivityStatus, Invoice, Membership, MembershipStatus,
    Notification, OutboxEvent, PaymentAttempt, PaymentStatus, PointTransaction, Refund, RefundStatus, User, UserRole)
from app.schemas import ActivityCancelRegistration, ActivityRegistrationCreate
from app.integrations.common import IntegrationResponseError
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_provider_aware_refund, PaymentApplicationError
from app.integrations.raygate import RayGateTransactionNotFoundError
from app.routers.activity_payments import router
from app.routers.community import community_router
from app.routers.payments import payments_router
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_integrations import payment_settings
from tests.test_order_payment_revisions import success_payload
from tests.test_raygate import FakeRayGateRefundAdapter


async def make_activity(session, *, capacity=1, price=300, cutoff_hours=48):
    session.autoflush = False
    now = datetime.now(timezone.utc)
    users = [User(email=f"activity-{uuid4().hex}@example.com", display_name=name, password_hash="test", user_role=UserRole.ADMIN if name == "admin" else UserRole.CUSTOMER) for name in ("buyer", "second", "third", "admin")]
    session.add_all(users)
    await session.flush()
    activity = Activity(created_by_id=users[3].id, title="活動收費測試", description="", location="合作社", capacity=capacity,
        starts_at=now+timedelta(days=5), ends_at=now+timedelta(days=5,hours=1), registration_deadline=now+timedelta(hours=cutoff_hours),
        member_price=100 if price else 0, non_member_price=price, payment_window_hours=24, status=ActivityStatus.PUBLISHED, registrations=[])
    session.add(activity)
    await session.commit()
    return SimpleNamespace(session=session, activity=activity, buyer=users[0], second=users[1], third=users[2], admin=users[3], now=now)


async def registration(c, user=None):
    user = user or c.buyer
    return await service.register(c.session, c.activity.id, user, ActivityRegistrationCreate(contact_name=user.display_name, contact_email=user.email))


async def pending_attempt(c, charge_id, *, provider="raygate"):
    charge = await c.session.get(ActivityCharge, charge_id)
    attempt = PaymentAttempt(activity_charge_id=charge.id, provider=provider, merchant_trade_no=f"ACT-{uuid4().hex[:20]}",
        provider_trade_no=f"RG-{uuid4().hex[:20]}", amount=charge.amount, status=PaymentStatus.PENDING, expires_at=charge.expires_at,
        checkout_payload={"redirect_url":"https://pay.example.test/calc/pay_encrypt/activity"})
    c.session.add(attempt)
    await c.session.commit()
    return attempt


async def paid(c):
    registered = await registration(c)
    attempt = await pending_attempt(c, registered.charge.id)
    result = await SQLAlchemyPaymentCallbackRepository(c.session).apply_raygate_payment_callback(uuid4().hex, success_payload(attempt))
    assert result == "paid"
    return registered, attempt


@pytest.mark.parametrize("member_state,amount", [(None,300),(MembershipStatus.ACTIVE,100),(MembershipStatus.TRAINEE,100)])
async def test_seat_creates_snapshot_charge_with_member_price_and_capped_deadline(database_session, member_state, amount):
    c=await make_activity(database_session,cutoff_hours=6)
    if member_state:
        database_session.add(Membership(user_id=c.buyer.id, member_number=f"M{uuid4().hex[:8]}", status=member_state))
        await database_session.commit()
    result=await registration(c)
    assert result.status == "pending_payment" and result.charge.amount == amount
    assert result.charge.contact_email == c.buyer.email
    assert service.aware(result.charge.expires_at) == service.aware(c.activity.registration_deadline)
    second=await registration(c,c.second)
    assert second.status == "waitlisted" and second.charge is None
    assert await database_session.scalar(select(func.count(ActivityCharge.id))) == 1
    again=await registration(c)
    assert again.id == result.id and again.charge.id == result.charge.id


async def test_fifo_promotion_creates_charge_and_no_promotion_after_cutoff(database_session):
    c=await make_activity(database_session)
    first=await registration(c); second=await registration(c,c.second); await registration(c,c.third)
    await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration())
    activity=await service.load_activity(database_session,c.activity.id)
    second_row=next(item for item in activity.registrations if item.id == second.id)
    assert second_row.status == RegistrationStatus.PENDING_PAYMENT and service.current_charge(second_row)
    assert next(item for item in activity.registrations if item.user_id == c.third.id).status == RegistrationStatus.WAITLISTED
    activity.registration_deadline=c.now-timedelta(seconds=1)
    await database_session.commit()
    await service.cancel_registration(database_session,c.activity.id,c.second,ActivityCancelRegistration())
    activity=await service.load_activity(database_session,c.activity.id)
    assert next(item for item in activity.registrations if item.user_id == c.third.id).status == RegistrationStatus.WAITLISTED


async def test_payment_success_is_idempotent_and_never_creates_invoice_or_purchase_points(database_session):
    c=await make_activity(database_session)
    registration_result,attempt=await paid(c)
    repo=SQLAlchemyPaymentCallbackRepository(database_session)
    assert await repo.apply_raygate_payment_callback("repeat-paid",success_payload(attempt)) == "duplicate"
    charge=await service.lock_activity_payment_subject(database_session,registration_result.charge.id)
    assert charge.payment_status == PaymentStatus.PAID and charge.registration.status == RegistrationStatus.REGISTERED
    assert await database_session.scalar(select(func.count(Invoice.id))) == 0
    assert await database_session.scalar(select(func.count(PointTransaction.id))) == 0
    assert await database_session.scalar(select(func.count(Notification.id)).where(Notification.data["dedupe_key"].as_string()==f"activity-paid:{attempt.id}")) == 1


@pytest.mark.parametrize("response",["timeout","pending","malformed","ecpay_zero"])
async def test_unknown_payment_retains_seat_and_requeries(database_session,monkeypatch,response):
    c=await make_activity(database_session)
    result=await registration(c); await registration(c,c.second)
    attempt=await pending_attempt(c,result.charge.id,provider="ecpay" if response=="ecpay_zero" else "raygate")
    charge=await database_session.get(ActivityCharge,result.charge.id)
    attempt.expires_at=charge.expires_at=c.now-timedelta(minutes=1)
    await database_session.commit()
    class Adapter:
        async def query_order(self,_):
            if response=="timeout": raise IntegrationResponseError("timeout")
            return {} if response=="malformed" else {"TradeStatus":"0","PaymentDisposition":"pending"}
    monkeypatch.setattr(jobs,"payment_adapter_from_settings",lambda *_:Adapter())
    assert await jobs._reconcile_expired_payments(database_session,make_test_settings(),c.now,10)==1
    await service.reconcile_charges(database_session,c.now,10)
    charge=await service.lock_activity_payment_subject(database_session,charge.id)
    assert charge.registration.status==RegistrationStatus.PENDING_PAYMENT
    assert charge.payment_status==PaymentStatus.PENDING
    attempt=await database_session.get(PaymentAttempt,attempt.id,populate_existing=True)
    assert attempt.unpaid_verified_at is None and attempt.settlement_unknown_since
    with pytest.raises(HTTPException) as exc:
        await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration())
    assert exc.value.status_code==409
    await database_session.rollback()


async def test_verified_unpaid_releases_once_and_late_payment_requires_admin_without_reclaiming_seat(database_session,monkeypatch):
    c=await make_activity(database_session)
    first=await registration(c); await registration(c,c.second)
    attempt=await pending_attempt(c,first.charge.id)
    charge=await database_session.get(ActivityCharge,first.charge.id)
    attempt.expires_at=charge.expires_at=c.now-timedelta(minutes=1)
    await database_session.commit()
    class Adapter:
        async def query_order(self,_): raise RayGateTransactionNotFoundError("RG999922")
    monkeypatch.setattr(jobs,"payment_adapter_from_settings",lambda *_:Adapter())
    await jobs._reconcile_expired_payments(database_session,make_test_settings(),c.now,10)
    activity=await service.load_activity(database_session,c.activity.id)
    assert next(item for item in activity.registrations if item.user_id==c.second.id).status==RegistrationStatus.PENDING_PAYMENT
    assert await SQLAlchemyPaymentCallbackRepository(database_session).apply_raygate_payment_callback("late-paid",success_payload(attempt))=="late_paid_refund_required"
    assert await database_session.scalar(select(func.count(Refund.id)))==0
    request=await database_session.scalar(select(ActivityRefundRequest))
    assert request.status=="pending_review" and request.payment_attempt_id==attempt.id
    activity=await service.load_activity(database_session,c.activity.id)
    assert next(item for item in activity.registrations if item.user_id==c.buyer.id).status==RegistrationStatus.CANCELLED
    assert next(item for item in activity.registrations if item.user_id==c.second.id).status==RegistrationStatus.PENDING_PAYMENT


async def test_reregistration_version_is_not_revived_by_old_payment(database_session):
    c=await make_activity(database_session)
    first=await registration(c)
    attempt=await pending_attempt(c,first.charge.id)
    attempt.unpaid_verified_at=c.now; attempt.status=PaymentStatus.EXPIRED
    await database_session.commit()
    await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration())
    second=await registration(c)
    assert second.revision==2 and second.charge.id!=first.charge.id
    await SQLAlchemyPaymentCallbackRepository(database_session).apply_raygate_payment_callback("old-paid",success_payload(attempt))
    activity=await service.load_activity(database_session,c.activity.id)
    row=activity.registrations[0]
    assert row.revision==2 and row.status==RegistrationStatus.PENDING_PAYMENT
    assert service.current_charge(row).payment_status==PaymentStatus.PENDING


async def test_paid_cancel_requires_review_versions_then_worker_full_refund(database_session,monkeypatch):
    c=await make_activity(database_session)
    first,attempt=await paid(c); await registration(c,c.second)
    result=await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration(reason="無法參加"))
    assert result.status=="registered" and result.refund_request.status=="pending_review"
    assert await database_session.scalar(select(func.count(Refund.id)))==0
    request=result.refund_request
    async with api_test_context(database_session,[router,community_router,payments_router],settings=make_test_settings()) as client:
        path=f"/v1/admin/activity-refund-requests/{request.id}/review"
        body={"decision":"approve","expected_version":request.version,"expected_amount":request.amount,"reason":"  "}
        assert (await client.post(path,json=body,headers=auth_headers(c.buyer))).status_code==403
        bad=await client.post(path,json={**body,"expected_amount":request.amount+1},headers=auth_headers(c.admin)); assert bad.status_code==409
        approved=await client.post(path,json=body,headers=auth_headers(c.admin)); assert approved.status_code==200,approved.text
        assert (await client.post(path,json=body,headers=auth_headers(c.admin))).status_code==409
    refund=await database_session.scalar(select(Refund))
    assert refund.status==RefundStatus.PENDING and refund.amount==attempt.amount and refund.activity_charge_id==first.charge.id
    reviewed = await database_session.get(ActivityRefundRequest, request.id)
    assert refund.reason == "" and reviewed.review_reason == ""
    activity=await service.load_activity(database_session,c.activity.id)
    assert next(item for item in activity.registrations if item.user_id==c.second.id).status==RegistrationStatus.PENDING_PAYMENT
    fake=FakeRayGateRefundAdapter(success_payload(attempt))
    monkeypatch.setattr(jobs,"refund_adapter_from_settings",lambda *_:fake)
    event=await database_session.scalar(select(OutboxEvent).where(OutboxEvent.event_type=="refund.requested"))
    await jobs._process_refund_event(database_session,event,make_test_settings(payment_provider="raygate"))
    await database_session.refresh(refund)
    assert refund.status==RefundStatus.COMPLETED
    assert await database_session.scalar(select(func.count(Invoice.id)))==0


async def test_refund_cannot_be_created_without_approval(database_session):
    c=await make_activity(database_session)
    first,attempt=await paid(c)
    charge=await service.lock_activity_payment_subject(database_session,first.charge.id)
    with pytest.raises(PaymentApplicationError,match="核准"):
        await create_provider_aware_refund(database_session,activity_charge=charge,payment_attempt=attempt,amount=attempt.amount,reason="繞過審核",requested_by_id=c.admin.id)


async def test_price_edit_is_versioned_and_does_not_change_old_charge(database_session):
    c=await make_activity(database_session,capacity=2)
    first=await registration(c)
    async with api_test_context(database_session,[router,community_router]) as client:
        data={"member_price":200,"non_member_price":600,"payment_window_hours":12,"expected_updated_at":service.aware(c.activity.updated_at).isoformat()}
        path=f"/v1/admin/activities/{c.activity.id}/payment-settings"
        response=await client.patch(path,json=data,headers=auth_headers(c.admin)); assert response.status_code==200,response.text
        assert (await client.patch(path,json=data,headers=auth_headers(c.admin))).status_code==409
    second=await registration(c,c.second)
    assert second.charge.amount==600
    assert (await database_session.get(ActivityCharge,first.charge.id)).amount==300


async def test_batch_cancel_preview_stale_and_late_receipt_not_preapproved(database_session):
    c=await make_activity(database_session,capacity=2)
    first,attempt=await paid(c)
    second=await registration(c,c.second)
    pending=await pending_attempt(c,second.charge.id)
    async with api_test_context(database_session,[router,community_router]) as client:
        headers=auth_headers(c.admin)
        preview=(await client.post(f"/v1/admin/activities/{c.activity.id}/cancellation-preview",headers=headers)).json()
        assert preview["paid_count"]==1 and preview["refund_amount"]==300
        path=f"/v1/admin/activities/{c.activity.id}/cancel"
        body={**preview,"reason":"天候取消","approve_refunds":False}
        assert (await client.post(path,json=body,headers=headers)).status_code==409
        response=await client.post(path,json={**body,"approve_refunds":True},headers=headers); assert response.status_code==200,response.text
    assert await database_session.scalar(select(func.count(Refund.id)))==1
    await SQLAlchemyPaymentCallbackRepository(database_session).apply_raygate_payment_callback("after-cancel-paid",success_payload(pending))
    assert await database_session.scalar(select(func.count(Refund.id)))==1
    late_request=await database_session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id==pending.id))
    assert late_request.status=="pending_review"


async def test_charge_owner_history_and_payment_return_routing(database_session):
    c=await make_activity(database_session)
    first=await registration(c)
    attempt=await pending_attempt(c,first.charge.id)
    waiter=await registration(c,c.second)
    nearer_activity=Activity(created_by_id=c.admin.id,title="新報名的較近活動",description="",location="合作社",capacity=1,
        starts_at=c.now+timedelta(days=3),ends_at=c.now+timedelta(days=3,hours=1),registration_deadline=c.now+timedelta(days=1),
        member_price=100,non_member_price=300,payment_window_hours=24,status=ActivityStatus.PUBLISHED,registrations=[])
    database_session.add(nearer_activity)
    await database_session.commit()
    latest=await service.register(database_session,nearer_activity.id,c.buyer,ActivityRegistrationCreate(contact_name=c.buyer.display_name,contact_email=c.buyer.email))
    first_row=await database_session.get(ActivityRegistration,first.id)
    latest_row=await database_session.get(ActivityRegistration,latest.id)
    first_row.registered_at=c.now-timedelta(days=1)
    latest_row.registered_at=c.now
    old_charge=await database_session.get(ActivityCharge,first.charge.id)
    latest_charge=await database_session.get(ActivityCharge,latest.charge.id)
    old_charge.created_at=first_row.registered_at
    latest_charge.created_at=latest_row.registered_at
    await database_session.commit()
    async with api_test_context(database_session,[router,community_router,payments_router],settings=payment_settings()) as client:
        for path in (f"/v1/activity-charges/{first.charge.id}",f"/v1/payment-attempts/{attempt.id}"):
            assert (await client.get(path,headers=auth_headers(c.second))).status_code==404
            assert (await client.get(path)).status_code==401
            assert (await client.get(path,headers=auth_headers(c.buyer))).status_code==200
        status=(await client.get(f"/v1/payment-attempts/{attempt.id}",headers=auth_headers(c.buyer))).json()
        assert status["activity_charge_id"]==first.charge.id and status["order_id"] is None
        c.activity.status=ActivityStatus.COMPLETED
        await database_session.commit()
        history=(await client.get("/v1/activity-registrations",headers=auth_headers(c.buyer))).json()
        assert [row["id"] for row in history]==[latest.id,first.id]
        assert history[1]["charge"]["id"]==first.charge.id
        charges=(await client.get("/v1/activity-charges",headers=auth_headers(c.buyer))).json()
        assert [row["id"] for row in charges]==[latest.charge.id,first.charge.id]
        queue=(await client.get(f"/v1/admin/activities/{c.activity.id}/registrations",headers=auth_headers(c.admin))).json()
        assert [row["id"] for row in queue]==[first.id,waiter.id]
        assert queue[1]["status"]=="waitlisted"
    from app.routers.payments import _payment_result_location
    location=_payment_result_location(make_test_settings(),"paid",order_id=None,membership_charge_id=None,activity_charge_id=first.charge.id,attempt_id=attempt.id)
    assert "/activities/registrations?" in location and first.charge.id in location


async def test_duplicate_receipt_refund_does_not_hide_original_paid_cancellation(database_session):
    c=await make_activity(database_session)
    first,original=await paid(c)
    duplicate=await pending_attempt(c,first.charge.id)
    repo=SQLAlchemyPaymentCallbackRepository(database_session)
    assert await repo.apply_raygate_payment_callback("duplicate-money",success_payload(duplicate))=="late_paid_refund_required"
    request=await database_session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id==duplicate.id))
    charge=await service.lock_activity_payment_subject(database_session,first.charge.id)
    await service.approve_refund(database_session,request,charge,c.admin,reason="退還重複款")
    await database_session.commit()
    payload={**success_payload(duplicate),"PaymentDisposition":"refunded","RayGateStatus":"3","RayGateAssociatedOrderID":"DUP-REFUND-1"}
    assert await repo.apply_raygate_payment_callback("duplicate-refunded",payload)=="refunded"
    activity=await service.load_activity(database_session,c.activity.id)
    data=service.registration_read(activity.registrations[0],activity)
    assert data.status=="registered" and data.charge.payment_status=="paid"
    assert "request_refund" in data.available_actions
    cancellation=await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration())
    assert cancellation.refund_request.payment_attempt_id==original.id


async def test_external_refund_closes_pending_review_without_forging_approval(database_session):
    c=await make_activity(database_session)
    first,attempt=await paid(c)
    request=(await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration())).refund_request
    payload={**success_payload(attempt),"PaymentDisposition":"refunded","RayGateStatus":"3","RayGateAssociatedOrderID":"EXT-REFUND-1"}
    await SQLAlchemyPaymentCallbackRepository(database_session).apply_raygate_payment_callback("external-refund",payload)
    record=await database_session.get(ActivityRefundRequest,request.id,populate_existing=True)
    assert record.status=="externally_refunded" and record.reviewed_by_id is None
    assert record.version==request.version+1
    assert (await database_session.scalar(select(Refund))).purpose=="activity_external_refund"


async def test_timezone_input_validation_and_read_offsets(database_session):
    from pydantic import ValidationError
    from app.schemas import ActivityCreate
    c=await make_activity(database_session)
    with pytest.raises(ValidationError):
        ActivityCreate(title="bad",location="test",capacity=1,starts_at=c.now.replace(tzinfo=None),ends_at=c.now+timedelta(hours=1),registration_deadline=c.now)
    await registration(c)
    async with api_test_context(database_session,[router,community_router]) as client:
        data=(await client.get(f"/v1/activities/{c.activity.id}",headers=auth_headers(c.buyer))).json()
        for key in ("starts_at","ends_at","registration_deadline","updated_at"):
            assert data[key].endswith(("Z","+00:00"))
        history=(await client.get("/v1/activity-registrations",headers=auth_headers(c.buyer))).json()[0]
        assert history["starts_at"].endswith(("Z","+00:00")) and history["registered_at"].endswith(("Z","+00:00"))


async def test_actual_checkout_creation_reuses_attempt_and_blocks_old_seat(database_session):
    c=await make_activity(database_session)
    first=await registration(c)
    async with api_test_context(database_session,[router,payments_router],settings=payment_settings()) as client:
        path=f"/v1/activity-charges/{first.charge.id}/payment-attempts"
        created=await client.post(path,headers=auth_headers(c.buyer)); assert created.status_code==201,created.text
        repeated=await client.post(path,headers=auth_headers(c.buyer)); assert repeated.json()["id"]==created.json()["id"]
        checkout=await client.get(f"/payments/{created.json()['id']}/checkout")
        assert checkout.status_code==200 and "ecpay" in checkout.text.lower()
        charge=await service.lock_activity_payment_subject(database_session,first.charge.id)
        charge.registration.status=RegistrationStatus.CANCELLED
        await database_session.commit()
        assert (await client.get(f"/payments/{created.json()['id']}/checkout")).status_code==410


@pytest.mark.parametrize("provider",["raygate","ecpay"])
async def test_pending_and_simulated_callbacks_invalidate_verified_unpaid_proof(database_session,provider):
    c=await make_activity(database_session)
    first=await registration(c)
    attempt=await pending_attempt(c,first.charge.id,provider=provider)
    attempt.unpaid_verified_at=c.now
    await database_session.commit()
    repo=SQLAlchemyPaymentCallbackRepository(database_session)
    payload=success_payload(attempt)
    if provider=="raygate": payload.update(PaymentDisposition="pending",TradeStatus="0")
    else: payload.update(SimulatePaid="1")
    await repo.apply_payment_callback(provider,"activity-pending",payload)
    await database_session.refresh(attempt)
    assert attempt.unpaid_verified_at is None and attempt.status==PaymentStatus.PENDING
    attempt.unpaid_verified_at=c.now
    await database_session.commit()
    assert await repo.apply_payment_callback(provider,"activity-pending",payload)=="duplicate"
    await database_session.refresh(attempt)
    assert attempt.unpaid_verified_at is None


@pytest.mark.parametrize("status",[PaymentStatus.EXPIRED,PaymentStatus.FAILED])
async def test_unknown_activity_attempt_beyond_provider_window_still_reconciles(database_session,monkeypatch,status):
    c=await make_activity(database_session)
    first=await registration(c)
    attempt=await pending_attempt(c,first.charge.id)
    attempt.status=status; attempt.expires_at=c.now-timedelta(days=5); attempt.unpaid_verified_at=None
    await database_session.commit()
    class Adapter:
        calls=0
        async def query_order(self,_):
            self.calls+=1
            return {"TradeStatus":"0","PaymentDisposition":"pending"}
    adapter=Adapter()
    monkeypatch.setattr(jobs,"payment_adapter_from_settings",lambda *_:adapter)
    assert await jobs._reconcile_expired_payments(database_session,make_test_settings(),c.now,10)==1
    assert adapter.calls==1
    await database_session.refresh(attempt)
    assert attempt.status==status and attempt.unpaid_verified_at is None


async def test_automatic_activity_completion_preserves_unknown_charge_until_actual_result(database_session):
    c=await make_activity(database_session)
    first=await registration(c)
    attempt=await pending_attempt(c,first.charge.id)
    c.activity.starts_at=c.now-timedelta(hours=2); c.activity.ends_at=c.now-timedelta(hours=1)
    c.activity.registration_deadline=c.now-timedelta(hours=3)
    await database_session.commit()
    assert await jobs._reconcile_activities(database_session,c.now,10)==1
    charge=await service.lock_activity_payment_subject(database_session,first.charge.id)
    assert charge.activity.status==ActivityStatus.COMPLETED
    assert charge.registration.status==RegistrationStatus.PENDING_PAYMENT
    assert charge.payment_status==PaymentStatus.PENDING
    await SQLAlchemyPaymentCallbackRepository(database_session).apply_raygate_payment_callback("completed-activity-paid",success_payload(attempt))
    charge=await service.lock_activity_payment_subject(database_session,first.charge.id)
    assert charge.registration.status==RegistrationStatus.CANCELLED
    assert charge.payment_status==PaymentStatus.LATE_PAID_REFUND_REQUIRED
    assert await database_session.scalar(select(func.count(Refund.id)))==0


async def test_missing_legacy_waitlist_contact_preserves_fifo_and_can_be_completed(database_session):
    c=await make_activity(database_session,price=0)
    await registration(c)
    c.second.email=None
    await database_session.commit()
    waiting=await service.register(database_session,c.activity.id,c.second)
    c.activity.member_price=100; c.activity.non_member_price=300
    await database_session.commit()
    await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration())
    activity=await service.load_activity(database_session,c.activity.id)
    row=next(item for item in activity.registrations if item.id==waiting.id)
    assert row.status==RegistrationStatus.WAITLISTED
    assert "update_contact" in service.registration_read(row,activity).available_actions
    newcomer=await registration(c,c.third)
    assert newcomer.status=="waitlisted"
    updated=await service.register(database_session,c.activity.id,c.second,ActivityRegistrationCreate(contact_name="補齊聯絡",contact_email="waiter@example.com"))
    assert updated.status=="pending_payment" and updated.charge.contact_email=="waiter@example.com"


async def test_payment_attempt_timeout_does_not_shorten_seat_window_and_allows_verified_retry(database_session):
    c=await make_activity(database_session)
    first=await registration(c)
    settings=payment_settings()
    attempt=await service.create_payment_attempt(database_session,first.charge.id,c.buyer,settings)
    assert service.aware(attempt.expires_at) < service.aware(first.charge.expires_at)
    first_id=attempt.id
    attempt.expires_at=c.now-timedelta(minutes=1)
    await database_session.commit()
    await service.record_payment_verification(database_session,attempt.id,c.now,verified_unpaid=True)
    charge=await service.lock_activity_payment_subject(database_session,first.charge.id)
    assert charge.payment_status==PaymentStatus.PENDING and charge.registration.status==RegistrationStatus.PENDING_PAYMENT
    second=await service.create_payment_attempt(database_session,charge.id,c.buyer,settings)
    assert second.id!=first_id and second.amount==first.charge.amount
    assert service.aware(charge.expires_at)==service.aware(first.charge.expires_at)


async def test_activity_owner_refresh_and_provider_return_keep_activity_subject(database_session,monkeypatch):
    from app.routers import payments
    c=await make_activity(database_session)
    first=await registration(c)
    attempt=await pending_attempt(c,first.charge.id)
    class Adapter:
        async def query_order(self,_): return success_payload(attempt)
    monkeypatch.setattr(payments,"raygate_payment_adapter_from_settings",lambda *_:Adapter())
    async with api_test_context(database_session,[router,payments_router],settings=make_test_settings(payment_provider="raygate")) as client:
        refreshed=await client.post(f"/v1/payment-attempts/{attempt.id}/refresh",headers=auth_headers(c.buyer))
        assert refreshed.status_code==200 and refreshed.json()["status"]=="paid",refreshed.text
        returned=await client.get(f"/payments/raygate/result?attempt_id={attempt.id}")
        assert returned.status_code==303,returned.text
        assert "/activities/registrations?" in returned.headers["location"]
        assert first.charge.id in returned.headers["location"] and attempt.id in returned.headers["location"]


async def test_refund_worker_does_not_execute_unapproved_activity_refund(database_session,monkeypatch):
    c=await make_activity(database_session)
    first,attempt=await paid(c)
    refund=Refund(activity_charge_id=first.charge.id,payment_attempt_id=attempt.id,provider="raygate",amount=attempt.amount,
        status=RefundStatus.PENDING,purpose="activity_cancellation",reason="未核准資料",requested_by_id=c.admin.id)
    database_session.add(refund); await database_session.flush()
    event=OutboxEvent(event_type="refund.requested",aggregate_type="refund",aggregate_id=refund.id,payload={"refund_id":refund.id})
    database_session.add(event); await database_session.commit()
    def unexpected(*_): raise AssertionError("不應呼叫金流")
    monkeypatch.setattr(jobs,"refund_adapter_from_settings",unexpected)
    with pytest.raises(ValueError,match="尚未核准"):
        await jobs._process_refund_event(database_session,event,make_test_settings(payment_provider="raygate"))


async def test_refund_submission_unknown_only_queries_and_never_blindly_resends(database_session,monkeypatch):
    c=await make_activity(database_session)
    first,attempt=await paid(c)
    result=await service.cancel_registration(database_session,c.activity.id,c.buyer,ActivityCancelRegistration())
    request=await database_session.get(ActivityRefundRequest,result.refund_request.id)
    charge=await service.lock_activity_payment_subject(database_session,first.charge.id)
    await service.approve_refund(database_session,request,charge,c.admin,reason="核准退款")
    await database_session.commit()
    event=await database_session.scalar(select(OutboxEvent).where(OutboxEvent.event_type=="refund.requested"))
    event_id=event.id
    class Adapter:
        refund_calls=0
        async def query_order(self,_): return success_payload(attempt)
        async def refund(self,**kwargs):
            self.refund_calls+=1
            raise IntegrationResponseError("送出後逾時")
    adapter=Adapter(); monkeypatch.setattr(jobs,"refund_adapter_from_settings",lambda *_:adapter)
    with pytest.raises(IntegrationResponseError):
        await jobs._process_refund_event(database_session,event,make_test_settings(payment_provider="raygate"))
    await database_session.rollback()
    event=await database_session.get(OutboxEvent,event_id)
    with pytest.raises(ValueError,match="不會自動重送"):
        await jobs._process_refund_event(database_session,event,make_test_settings(payment_provider="raygate"))
    assert adapter.refund_calls==1


async def test_unattempted_expiry_promotes_and_preserves_snapshots_and_notification_email(database_session):
    c=await make_activity(database_session)
    c.buyer.notification_email="notification@example.com"
    c.buyer.email="account@example.com"
    await database_session.commit()
    first=await service.register(database_session,c.activity.id,c.buyer)
    await registration(c,c.second)
    assert first.charge.contact_email=="notification@example.com"
    charge=await database_session.get(ActivityCharge,first.charge.id)
    charge.expires_at=c.now-timedelta(minutes=1)
    await database_session.commit()
    await service.reconcile_charges(database_session,c.now,10)
    activity=await service.load_activity(database_session,c.activity.id)
    assert next(item for item in activity.registrations if item.user_id==c.second.id).status==RegistrationStatus.PENDING_PAYMENT
    assert (await database_session.get(ActivityCharge,first.charge.id)).payment_status==PaymentStatus.EXPIRED


@pytest.mark.parametrize("duplicate_approved", [False, True])
async def test_refunding_accepted_payment_releases_seat_despite_unreturned_duplicate(database_session, duplicate_approved):
    c = await make_activity(database_session)
    first, original = await paid(c)
    waiter = await registration(c, c.second)
    duplicate = await pending_attempt(c, first.charge.id)
    repo = SQLAlchemyPaymentCallbackRepository(database_session)
    await repo.apply_raygate_payment_callback("duplicate-received", success_payload(duplicate))
    request = await database_session.scalar(select(ActivityRefundRequest).where(ActivityRefundRequest.payment_attempt_id == duplicate.id))
    if duplicate_approved:
        charge = await service.lock_activity_payment_subject(database_session, first.charge.id)
        await service.approve_refund(database_session, request, charge, c.admin, reason="退還重複款")
        await database_session.commit()
    payload = {**success_payload(original), "PaymentDisposition": "refunded", "RayGateStatus": "3", "RayGateAssociatedOrderID": "ORIGINAL-EXTERNAL-REFUND"}
    await repo.apply_raygate_payment_callback("original-refunded", payload)
    activity = await service.load_activity(database_session, c.activity.id)
    buyer = next(item for item in activity.registrations if item.id == first.id)
    promoted = next(item for item in activity.registrations if item.id == waiter.id)
    assert buyer.status == RegistrationStatus.CANCELLED
    assert service.current_charge(buyer).payment_status == (PaymentStatus.REFUND_PENDING if duplicate_approved else PaymentStatus.LATE_PAID_REFUND_REQUIRED)
    assert promoted.status == RegistrationStatus.PENDING_PAYMENT
    assert service.current_charge(promoted).amount == c.activity.non_member_price
    await database_session.refresh(request)
    assert request.status == ("approved" if duplicate_approved else "pending_review")
    assert await database_session.scalar(select(func.count(ActivityCharge.id))) == 2


@pytest.mark.parametrize("closed_state", ["cutoff", "completed"])
async def test_closed_activity_history_does_not_offer_unavailable_contact_update(database_session, closed_state):
    c = await make_activity(database_session)
    await registration(c)
    waiting = await registration(c, c.second)
    if closed_state == "completed":
        c.activity.status = ActivityStatus.COMPLETED
    else:
        c.activity.registration_deadline = c.now - timedelta(seconds=1)
    await database_session.commit()
    async with api_test_context(database_session, [router]) as client:
        result = await client.get("/v1/activity-registrations", headers=auth_headers(c.second))
    assert result.status_code == 200
    item = next(item for item in result.json() if item["id"] == waiting.id)
    assert item["status"] == "waitlisted"
    assert "update_contact" not in item["available_actions"]
