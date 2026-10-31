import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import MemberProposal, Membership, MembershipStatus, ProposalOption, User
from app.routers.community import community_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers


@pytest.mark.asyncio
@pytest.mark.parametrize("different_content", [False, True])
async def test_concurrent_proposal_creation_preserves_one_draft(postgres_sessions, different_content):
    async with postgres_sessions() as session:
        author = User(email="proposal-concurrent@example.com", display_name="社員", password_hash="test",
                      membership=Membership(status=MembershipStatus.ACTIVE, member_number="PG-PROPOSAL", activated_at=datetime.now(timezone.utc)))
        session.add(author)
        await session.commit()
        headers = auth_headers(author)
    request_id = str(uuid4())
    gate = asyncio.Event()

    async def create(index):
        await gate.wait()
        async with postgres_sessions() as session:
            async with api_test_context(session, [community_router]) as client:
                return await client.post("/v1/member-proposals", headers=headers, json={
                    "client_request_id": request_id, "title": f"同時提案{index if different_content else 0}", "body": "討論內容",
                    "proposal_type": "multiple_choice", "options": [{"label": "甲"}, {"label": "乙"}],
                })

    tasks = [asyncio.create_task(create(index)) for index in range(8)]
    gate.set()
    responses = await asyncio.wait_for(asyncio.gather(*tasks), 20)
    accepted = [response for response in responses if response.status_code == 201]
    assert len(accepted) == (1 if different_content else 8), [response.text for response in responses]
    assert all(response.status_code in {201, 409} for response in responses)
    assert len({response.json()["id"] for response in accepted}) == 1
    async with postgres_sessions() as session:
        assert await session.scalar(select(func.count(MemberProposal.id))) == 1
        assert await session.scalar(select(func.count(ProposalOption.id))) == 2
