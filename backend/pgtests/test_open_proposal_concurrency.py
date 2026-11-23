import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupBundle, GroupBundleItem, OutboxEvent, Product, ProposalStatus, User, UserRole, VoteProposal
from pgtests.test_point_account_concurrency import data_api
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import auth_headers


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["product", "bundle", "custom"])
async def test_two_admins_cannot_create_duplicate_open_target(postgres_sessions, target):
    postgres_sessions.configure(autoflush=False)
    async with postgres_sessions() as session:
        engine = session.bind
        authors = [User(email=f"target-author-{index}@example.com", display_name=f"管理員{index}", password_hash="test", user_role=UserRole.ADMIN) for index in range(2)]
        product = Product(slug="open-target", name="共同購買商品", category="測試", unit="份", member_price=100, nonmember_price=100, stock_quantity=10)
        session.add_all([*authors, product])
        await session.flush()
        bundle = GroupBundle(name="共同購買套組", member_price=100, nonmember_price=100,
                             items=[GroupBundleItem(product_id=product.id, quantity=1, position=0)])
        session.add(bundle)
        await session.commit()
        body = {"target_type": "custom", "request_name": "自訂需求", "request_details": "各自獨立提出"} if target == "custom" else {"target_type": target, "target_id": product.id if target == "product" else bundle.id}
    both_read, first_committed = asyncio.Event(), asyncio.Event()
    reads = 0
    class RacingSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            nonlocal reads
            descriptions = getattr(statement, "column_descriptions", [])
            if (descriptions and descriptions[0].get("entity") in {Product, GroupBundle}
                and getattr(statement, "_for_update_arg", None) is not None):
                self.info["target_locked"] = True
            value = await super().scalar(statement, *args, **kwargs)
            # A locked target prevents the second empty read; do not wait behind our own lock.
            if target != "custom" and descriptions and descriptions[0].get("entity") is VoteProposal and value is None and not self.info.get("target_locked"):
                reads += 1
                if reads == 2:
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
                return await client.post("/v1/vote-proposals", headers=auth_headers(authors[slot-1]), json=body)
    replies = await asyncio.wait_for(asyncio.gather(perform(1), perform(2)), 25)
    assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
    assert (replies[0].json()["id"] == replies[1].json()["id"]) is (target != "custom")
    async with postgres_sessions() as session:
        expected = 2 if target == "custom" else 1
        assert await session.scalar(select(func.count(VoteProposal.id))) == expected
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "proposal.review_requested")) == expected


@pytest.mark.asyncio
async def test_expired_target_close_and_recreate_have_no_commit_window(postgres_sessions):
    postgres_sessions.configure(autoflush=False)
    async with postgres_sessions() as session:
        engine = session.bind
        author = User(email="clock-author@example.com", display_name="原作者", password_hash="test", user_role=UserRole.ADMIN)
        other = User(email="clock-other@example.com", display_name="另一管理員", password_hash="test", user_role=UserRole.ADMIN)
        product = Product(slug="clock-target", name="截止商品", category="測試", unit="份", member_price=100, nonmember_price=100, stock_quantity=10)
        session.add_all([author, other, product])
        await session.flush()
        expired = VoteProposal(proposer_id=author.id, target_type="product", target_id=product.id, target_name_snapshot=product.name,
                               status=ProposalStatus.VOTING, threshold=10, deadline=datetime.now(timezone.utc)-timedelta(hours=1))
        session.add(expired)
        await session.commit()
        body, headers = {"target_type": "product", "target_id": product.id}, [auth_headers(author), auth_headers(other)]
    closed, target_attempt, second_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    commits = [0, 0]
    class ClockSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            descriptions = getattr(statement, "column_descriptions", [])
            if self.info["slot"] == 2 and descriptions and descriptions[0].get("entity") is Product:
                target_attempt.set()
            return await super().scalar(statement, *args, **kwargs)
        async def flush(self, *args, **kwargs):
            await super().flush(*args, **kwargs)
            if self.info["slot"] == 1 and not closed.is_set() and any(isinstance(row, VoteProposal) and row.status == ProposalStatus.ENDED_UNMET for row in self.identity_map.values()):
                closed.set()
                await asyncio.wait_for(target_attempt.wait(), 10)
        async def commit(self):
            await super().commit()
            slot = self.info["slot"]
            commits[slot-1] += 1
            if slot == 1 and not closed.is_set():
                closed.set()
                await asyncio.wait_for(second_done.wait(), 10)
    async def perform(slot):
        if slot == 2:
            await asyncio.wait_for(closed.wait(), 10)
        try:
            async with ClockSession(engine, expire_on_commit=False, autoflush=False, info={"slot": slot}) as session:
                async with data_api(session) as client:
                    return await client.post("/v1/vote-proposals", headers=headers[slot-1], json=body)
        finally:
            if slot == 2:
                second_done.set()
    replies = await asyncio.wait_for(asyncio.gather(perform(1), perform(2)), 25)
    assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
    assert replies[0].json()["id"] == replies[1].json()["id"]
    assert commits[0] == 1
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(VoteProposal.id)).where(VoteProposal.status == ProposalStatus.PENDING_REVIEW)) == 1
        assert (await session.get(VoteProposal, expired.id)).status == ProposalStatus.ENDED_UNMET


@pytest.mark.asyncio
async def test_existing_pending_target_privacy_and_public_reuse_are_consistent(postgres_sessions):
    postgres_sessions.configure(autoflush=False)
    async with postgres_sessions() as session:
        author = User(email="pending-owner@example.com", display_name="提案人", password_hash="test")
        reader = User(email="pending-reader@example.com", display_name="買家", password_hash="test")
        admin = User(email="pending-admin@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        product = Product(slug="private-target", name="待審商品", category="測試", unit="份", member_price=100, nonmember_price=100, stock_quantity=10)
        session.add_all([author, reader, admin, product])
        await session.commit()
        proposal = VoteProposal(proposer_id=author.id, target_type="product", target_id=product.id,
                                target_name_snapshot=product.name, status=ProposalStatus.PENDING_REVIEW)
        session.add(proposal)
        await session.commit()
        body = {"target_type": "product", "target_id": product.id}
    async def request(user, method="POST", path="/v1/vote-proposals", payload=body):
        async with postgres_sessions() as session:
            async with data_api(session) as client:
                return await client.request(method, path, headers=auth_headers(user), json=payload)
    proposal_id = proposal.id
    first = await request(author, "GET", f"/v1/vote-proposals/{proposal_id}", None)
    assert first.status_code == 200
    hidden = await request(reader, "GET", f"/v1/vote-proposals/{proposal_id}", None)
    assert hidden.status_code == 404
    conflict = await request(reader)
    assert conflict.status_code == 403
    assert proposal_id not in conflict.text and author.id not in conflict.text
    assert (await request(author)).status_code == 403
    recovered = await request(admin)
    assert recovered.status_code == 200 and recovered.json()["id"] == proposal_id
    approved = await request(admin, path=f"/v1/vote-proposals/{proposal_id}/admin/approve", payload={"threshold": 1})
    assert approved.status_code == 200
    assert (await request(reader)).status_code == 403
    public = await request(reader, "GET", f"/v1/vote-proposals/{proposal_id}", None)
    assert public.status_code == 200 and public.json()["id"] == proposal_id
    voted = await request(reader, "PUT", f"/v1/vote-proposals/{proposal_id}/vote", {"estimated_quantity": 1})
    assert voted.status_code == 200
    async with postgres_sessions() as session:
        proposal = await session.get(VoteProposal, proposal_id)
        proposal.status = ProposalStatus.CONVERSION_PENDING
        proposal.deadline = datetime.now(timezone.utc)-timedelta(seconds=1)
        proposal.conversion_deadline = datetime.now(timezone.utc)+timedelta(hours=1)
        await session.commit()
    conversion = await request(reader, "GET", f"/v1/vote-proposals/{proposal_id}", None)
    assert conversion.status_code == 200 and conversion.json()["id"] == proposal_id
    assert conversion.json()["status"] == "conversion_pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("same_author", [False, True])
async def test_partial_unique_upsert_recovers_unlocked_writer_for_admin(postgres_sessions, same_author):
    postgres_sessions.configure(autoflush=False)
    async with postgres_sessions() as session:
        engine = session.bind
        requester = User(email="upsert-requester@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
        other = User(email="upsert-writer@example.com", display_name="另一買家", password_hash="test")
        product = Product(slug="upsert-target", name="競態商品", category="測試", unit="份", member_price=100, nonmember_price=100, stock_quantity=10)
        session.add_all([requester, other, product])
        await session.commit()
        body = {"target_type": "product", "target_id": product.id}
    winner_id = None
    class LateWriterSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            nonlocal winner_id
            value = await super().scalar(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", [])
            if winner_id is None and descriptions and descriptions[0].get("entity") is VoteProposal and value is None:
                # A rolling-version writer may not yet participate in the new target lock.
                async with postgres_sessions() as writer:
                    winner = VoteProposal(proposer_id=requester.id if same_author else other.id, target_type="product",
                                          target_id=product.id, target_name_snapshot=product.name, status=ProposalStatus.PENDING_REVIEW)
                    writer.add(winner)
                    await writer.flush()
                    writer.add(OutboxEvent(event_type="proposal.review_requested", aggregate_type="vote_proposal", aggregate_id=winner.id,
                                           payload={"proposer_id": winner.proposer_id, "target_name": product.name}))
                    await writer.commit()
                    winner_id = winner.id
            return value
    async with LateWriterSession(engine, expire_on_commit=False, autoflush=False) as session:
        async with data_api(session) as client:
            reply = await client.post("/v1/vote-proposals", headers=auth_headers(requester), json=body)
    assert reply.status_code == 200, reply.text
    assert reply.json()["id"] == winner_id
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(VoteProposal.id))) == 1
        assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "proposal.review_requested")) == 1
