import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.integrations.ecpay import build_check_mac_value
from app.integrations.payment_service import create_payment_attempt
from app.models import (InventoryReservation, MemberProposal, MemberProposalStatus, MemberProposalVote, Membership,
                        MembershipStatus, Order, OutboxEvent, PointAccount, PointTransaction, Product, User, BadgeDefinition)
from app.routers import community, cooperative, orders, payments, proposals
from app.schemas import OrderCreate
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import auth_headers
from tests.test_integrations import payment_settings, signed_payment_callback


@asynccontextmanager
async def data_api(session):
    app = FastAPI()
    for router in (cooperative.cooperative_router, community.community_router, payments.payments_router, proposals.proposals_router):
        app.include_router(router)
    async def sessions():
        yield session
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_settings] = payment_settings
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["points_and_badges", "two_votes", "two_payments", "vote_and_payment"])
async def test_first_point_account_is_atomic_across_real_api_transactions(postgres_sessions, source):
    postgres_sessions.configure(autoflush=False)
    async with postgres_sessions() as session:
        engine = session.bind
        now = datetime.now(timezone.utc)
        member = User(email="points-concurrent@example.com", display_name="社員", password_hash="test",
                      membership=Membership(status=MembershipStatus.ACTIVE, member_number="POINTS-PG", activated_at=now))
        session.add(member)
        await session.commit()
        products = [Product(slug=f"point-source-{index}", name=f"積點商品{index}", category="測試", unit="份",
                            member_price=100, nonmember_price=100, stock_quantity=10) for index in range(2)]
        governance = [MemberProposal(created_by_id=member.id, title=f"投票{index}", body="治理提案",
                                     status=MemberProposalStatus.VOTING, minimum_voters=1,
                                     discussion_ends_at=now-timedelta(hours=1), voting_ends_at=now+timedelta(hours=1)) for index in range(2)]
        session.add_all([*products, *governance])
        await session.commit()
        payloads = []
        if source in {"two_payments", "vote_and_payment"}:
            for index, product in enumerate(products):
                created = await orders.create_order(OrderCreate(items=[{"product_id": product.id, "quantity": 1}], contact_email=member.email), member, session, payment_settings())
                attempt = await create_payment_attempt(session, created.id, member, payment_settings())
                payload = signed_payment_callback(attempt, now)
                payload["TradeNo"] = f"POINTS{index}{uuid4().hex[:16]}"
                settings = payment_settings()
                payload["CheckMacValue"] = build_check_mac_value(payload, settings.ecpay_payment_hash_key, settings.ecpay_payment_hash_iv)
                payloads.append(payload)
        member_id, headers = member.id, auth_headers(member)
        proposal_ids = [proposal.id for proposal in governance]

    both_read, first_committed = asyncio.Event(), asyncio.Event()
    missing_reads = 0
    class RacingSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            nonlocal missing_reads
            value = await super().scalar(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", [])
            if descriptions and descriptions[0].get("entity") is PointAccount and value is None:
                missing_reads += 1
                if missing_reads == 2:
                    both_read.set()
                await asyncio.wait_for(both_read.wait(), 10)
                if self.info["slot"] == 2:
                    await asyncio.wait_for(first_committed.wait(), 10)
            return value
        async def commit(self):
            await super().commit()
            if self.info["slot"] == 1:
                first_committed.set()

    async def perform(slot):
        async with RacingSession(engine, expire_on_commit=False, autoflush=False, info={"slot": slot}) as session:
            async with data_api(session) as client:
                if source == "points_and_badges":
                    return await client.get("/v1/me/points" if slot == 1 else "/v1/me/badges", headers=headers)
                if source == "two_votes" or (source == "vote_and_payment" and slot == 1):
                    return await client.put(f"/v1/member-proposals/{proposal_ids[slot-1]}/vote", headers=headers, json={"choice": "yes"})
                return await client.post("/webhooks/ecpay/payment", data=payloads[slot-1])
    replies = await asyncio.wait_for(asyncio.gather(perform(1), perform(2)), 25)
    assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(PointAccount.id)).where(PointAccount.user_id == member_id)) == 1
        ledger = list(await session.scalars(select(PointTransaction)))
        assert len(ledger) == (0 if source == "points_and_badges" else 2)
        assert len({(row.source_type, row.reference_id) for row in ledger}) == len(ledger)
        assert sum(row.amount for row in ledger) == len(ledger)
        paid = await session.scalar(select(func.count(Order.id)).where(Order.payment_status == "paid"))
        assert paid == (2 if source == "two_payments" else 1 if source == "vote_and_payment" else 0)
        votes = await session.scalar(select(func.count(MemberProposalVote.id)))
        assert votes == (2 if source == "two_votes" else 1 if source == "vote_and_payment" else 0)
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")) == paid
        assert await session.scalar(select(func.count(InventoryReservation.id)).where(InventoryReservation.status == "consumed")) == paid


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_definition", [False, True])
async def test_badge_definition_initialization_is_atomic_across_different_members(postgres_sessions, existing_definition):
    postgres_sessions.configure(autoflush=False)
    async with postgres_sessions() as session:
        engine = session.bind
        members = [User(email=f"badge-initialize-{index}@example.com", display_name=f"社員{index}", password_hash="test",
                        membership=Membership(status=MembershipStatus.ACTIVE, member_number=f"BADGE-INIT-{index}",
                                              activated_at=datetime.now(timezone.utc))) for index in range(2)]
        session.add_all(members)
        if existing_definition:
            session.add(BadgeDefinition(key="points_100", name="自訂徽章名稱", description="保留自訂說明", rule={"custom": True}))
        await session.commit()
        headers = [auth_headers(member) for member in members]
    missing = 0
    both_read, first_committed = asyncio.Event(), asyncio.Event()
    class BadgeSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            nonlocal missing
            value = await super().scalar(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", [])
            if descriptions and descriptions[0].get("entity") is BadgeDefinition and value is None:
                missing += 1
                if missing == 2:
                    both_read.set()
                await asyncio.wait_for(both_read.wait(), 10)
                if self.info["slot"] == 2:
                    await asyncio.wait_for(first_committed.wait(), 10)
            return value
        async def commit(self):
            await super().commit()
            if self.info["slot"] == 1:
                first_committed.set()
    async def perform(slot):
        async with BadgeSession(engine, expire_on_commit=False, autoflush=False, info={"slot": slot}) as session:
            async with data_api(session) as client:
                return await client.get("/v1/me/badges", headers=headers[slot-1])
    replies = await asyncio.wait_for(asyncio.gather(perform(1), perform(2)), 25)
    assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(PointAccount.id))) == 2
        assert await session.scalar(select(func.count(BadgeDefinition.id))) == 4
        if existing_definition:
            original = await session.scalar(select(BadgeDefinition).where(BadgeDefinition.key == "points_100"))
            assert original.name == "自訂徽章名稱" and original.rule == {"custom": True}
            assert original.description == "保留自訂說明"
