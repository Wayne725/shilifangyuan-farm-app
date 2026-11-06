import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import jobs
from app.integrations.ecpay import build_check_mac_value
from app.models import GroupCampaign, InventoryReservation, Order, PaymentAttempt, PaymentStatus, Refund
from app.routers.groups import groups_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context
from tests.test_group_checkout_contract import join_body, prepare_group
from tests.test_integrations import payment_settings, signed_payment_callback


async def request(factory, method, path, *, headers, body=None, data=None):
    async with factory() as session:
        async with api_test_context(session, [groups_router, orders_router, payments_router], settings=payment_settings()) as client:
            return await client.request(method, path, headers=headers, json=body, data=data)


async def setup_group(factory, *, cap=10):
    factory.configure(autoflush=False)
    async with factory() as session:
        context = await prepare_group(session)
        campaign = await session.get(GroupCampaign, context.campaign_id)
        campaign.per_user_cap = cap
        await session.commit()
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_same_group_submission_parallel_requests_keep_one_order_and_one_content(postgres_sessions, changed):
    c = await setup_group(postgres_sessions)
    body = join_body(client_request_id=str(uuid4()), expected_total=100)
    second = {**body, "contact_email": "changed@example.com"} if changed else body
    path = f"/v1/group-campaigns/{c.campaign_id}/join"
    replies = await asyncio.wait_for(asyncio.gather(*(
        request(postgres_sessions, "POST", path, headers=c.buyer_headers[0], body=value) for value in [body, second]
    )), 15)
    assert sorted(reply.status_code for reply in replies) == ([201, 409] if changed else [201, 201]), [reply.text for reply in replies]
    accepted = [reply.json() for reply in replies if reply.status_code == 201]
    assert len({item["id"] for item in accepted}) == 1
    if changed:
        assert next(reply for reply in replies if reply.status_code == 409).headers["X-Error-Code"] == "idempotency_conflict"
    async with postgres_sessions() as session:
        orders = list(await session.scalars(select(Order).where(Order.group_campaign_id == c.campaign_id)))
        assert len(orders) == 1
        assert orders[0].client_request_id == body["client_request_id"]
        assert await session.scalar(select(func.count(PaymentAttempt.id))) == 0
        assert await session.scalar(select(func.count(InventoryReservation.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["edit", "payment"])
async def test_admin_core_edit_and_payment_share_campaign_parent_lock(postgres_sessions, first):
    c = await setup_group(postgres_sessions)
    created = await request(postgres_sessions, "POST", f"/v1/group-campaigns/{c.campaign_id}/join",
        headers=c.buyer_headers[0], body=join_body())
    assert created.status_code == 201, created.text
    entered = {name: asyncio.Event() for name in ["edit", "payment"]}
    release = asyncio.Event()
    engine = postgres_sessions.kw["bind"]

    class RacingSession(AsyncSession):
        async def after_lock(self, statement):
            descriptions = getattr(statement, "column_descriptions", [])
            if (not self.info.get("parent_seen") and getattr(statement, "_for_update_arg", None) is not None
                and descriptions and descriptions[0].get("entity") is GroupCampaign):
                self.info["parent_seen"] = True
                operation = self.info["operation"]
                entered[operation].set()
                if operation == first:
                    await asyncio.wait_for(release.wait(), 10)
        async def execute(self, statement, *args, **kwargs):
            value = await super().execute(statement, *args, **kwargs)
            await self.after_lock(statement)
            return value
        async def scalar(self, statement, *args, **kwargs):
            value = await super().scalar(statement, *args, **kwargs)
            await self.after_lock(statement)
            return value

    async def perform(operation):
        async with RacingSession(engine, expire_on_commit=False, autoflush=False, info={"operation": operation}) as session:
            async with api_test_context(session, [groups_router, payments_router], settings=payment_settings()) as client:
                if operation == "edit":
                    return await client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
                        json={"target_id": c.product_ids[1]})
                return await client.post(f"/v1/orders/{created.json()['id']}/payment-attempts", headers=c.buyer_headers[0])

    other = "payment" if first == "edit" else "edit"
    leader = asyncio.create_task(perform(first))
    await asyncio.wait_for(entered[first].wait(), 10)
    follower = asyncio.create_task(perform(other))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(entered[other].wait(), .1)
    finally:
        release.set()
    replies = await asyncio.wait_for(asyncio.gather(leader, follower), 20)
    results = dict(zip([first, other], replies))
    assert results["edit"].status_code == (200 if first == "edit" else 409), results["edit"].text
    assert results["payment"].status_code == (409 if first == "edit" else 201), results["payment"].text
    async with postgres_sessions() as session:
        campaign = await session.get(GroupCampaign, c.campaign_id)
        assert campaign.target_id == c.product_ids[1 if first == "edit" else 0]
        assert campaign.reserved_quantity == (0 if first == "edit" else 1)
        assert campaign.paid_quantity == 0


@pytest.mark.asyncio
async def test_verified_unpaid_then_edit_never_applies_late_old_target_payment_to_new_campaign(postgres_sessions):
    c = await setup_group(postgres_sessions)
    created = await request(postgres_sessions, "POST", f"/v1/group-campaigns/{c.campaign_id}/join",
        headers=c.buyer_headers[0], body=join_body())
    assert created.status_code == 201, created.text
    payment = await request(postgres_sessions, "POST", f"/v1/orders/{created.json()['id']}/payment-attempts", headers=c.buyer_headers[0])
    assert payment.status_code == 201, payment.text
    attempt_id = payment.json()["attempt_id"]
    now = datetime.now(timezone.utc)
    async with postgres_sessions() as session:
        attempt = await session.get(PaymentAttempt, attempt_id)
        attempt.expires_at = now - timedelta(minutes=1)
        await session.commit()
        # Use the existing verified-unpaid transaction path; no provider is contacted.
        assert await jobs._record_group_payment_verification(session, attempt_id, now, verified_unpaid=True,
            response={"isolated_test": "verified_unpaid"})
        await session.refresh(attempt)
        assert attempt.status == PaymentStatus.EXPIRED and attempt.unpaid_verified_at is not None
        payload = signed_payment_callback(attempt, now - timedelta(minutes=2))
    edited = await request(postgres_sessions, "PATCH", f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        body={"target_id": c.product_ids[1]})
    assert edited.status_code == 200, edited.text
    payload["TradeNo"] = f"GROUP{uuid4().hex[:15]}"
    settings = payment_settings()
    payload["CheckMacValue"] = build_check_mac_value(payload, settings.ecpay_payment_hash_key, settings.ecpay_payment_hash_iv)
    for _ in range(2):
        response = await request(postgres_sessions, "POST", "/webhooks/ecpay/payment", headers={}, data=payload)
        assert response.status_code == 200, response.text
    async with postgres_sessions() as session:
        campaign = await session.get(GroupCampaign, c.campaign_id)
        order = await session.get(Order, created.json()["id"])
        assert (campaign.target_id, campaign.paid_quantity, campaign.reserved_quantity) == (c.product_ids[1], 0, 0)
        assert order.payment_status == PaymentStatus.LATE_PAID_REFUND_REQUIRED
        refunds = list(await session.scalars(select(Refund).where(Refund.payment_attempt_id == attempt_id)))
        assert len(refunds) == 1 and refunds[0].amount == 100


@pytest.mark.asyncio
async def test_unknown_expired_payment_retains_reservation_and_blocks_edit_and_retry(postgres_sessions):
    c = await setup_group(postgres_sessions, cap=1)
    created = await request(postgres_sessions, "POST", f"/v1/group-campaigns/{c.campaign_id}/join",
        headers=c.buyer_headers[0], body=join_body())
    assert created.status_code == 201, created.text
    payment = await request(postgres_sessions, "POST", f"/v1/orders/{created.json()['id']}/payment-attempts", headers=c.buyer_headers[0])
    assert payment.status_code == 201, payment.text
    now = datetime.now(timezone.utc)
    async with postgres_sessions() as session:
        attempt = await session.get(PaymentAttempt, payment.json()["attempt_id"])
        attempt.expires_at = now - timedelta(minutes=1)
        await session.commit()
        assert await jobs._record_group_payment_verification(session, attempt.id, now, verified_unpaid=False,
            response={"isolated_test": "unknown"})
    edited = await request(postgres_sessions, "PATCH", f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        body={"target_id": c.product_ids[1]})
    retry = await request(postgres_sessions, "POST", f"/v1/orders/{created.json()['id']}/payment-attempts", headers=c.buyer_headers[0])
    another = await request(postgres_sessions, "POST", f"/v1/group-campaigns/{c.campaign_id}/join",
        headers=c.buyer_headers[0], body=join_body(client_request_id=str(uuid4())))
    assert [edited.status_code, retry.status_code, another.status_code] == [409, 409, 409]
    async with postgres_sessions() as session:
        campaign = await session.get(GroupCampaign, c.campaign_id)
        assert (campaign.target_id, campaign.paid_quantity, campaign.reserved_quantity) == (c.product_ids[0], 0, 1)
        reservations = list(await session.scalars(select(InventoryReservation)))
        assert len(reservations) == 1 and reservations[0].status.value == "active"
        assert await session.scalar(select(func.count(PaymentAttempt.id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["regular", "group"])
async def test_same_user_request_id_cannot_cross_regular_and_group_orders(postgres_sessions, first):
    c = await setup_group(postgres_sessions)
    request_id = str(uuid4())
    bodies = {"group": join_body(client_request_id=request_id, expected_total=100),
        "regular": {"client_request_id": request_id, "expected_total": 100,
            "items": [{"product_id": c.product_ids[0], "quantity": 1}], "contact_email": "group-buyer@example.com"}}
    paths = {"group": f"/v1/group-campaigns/{c.campaign_id}/join", "regular": "/v1/orders"}
    other = "group" if first == "regular" else "regular"
    created = await request(postgres_sessions, "POST", paths[first], headers=c.buyer_headers[0], body=bodies[first])
    assert created.status_code == 201, created.text
    blocked = await request(postgres_sessions, "POST", paths[other], headers=c.buyer_headers[0], body=bodies[other])
    assert blocked.status_code == 409, blocked.text
    assert blocked.headers["X-Error-Code"] == "idempotency_conflict"
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(Order.id))) == 1
