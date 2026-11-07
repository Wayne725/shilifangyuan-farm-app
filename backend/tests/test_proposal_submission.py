from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import MemberProposal, MemberProposalStatus, OutboxEvent
from tests.support import auth_headers
from tests.test_security_remediation import proposal_context


def payload(**changes):
    return {"client_request_id": str(uuid4()), "title": "送審恢復", "body": "同一份治理提案", **changes}


@pytest.mark.asyncio
async def test_create_response_lost_can_recover_same_draft_and_submitted_status(proposal_context):
    client, session, _, author, _, _ = proposal_context
    body = payload()
    first = await client.post("/v1/member-proposals", headers=auth_headers(author), json=body)
    assert first.status_code == 201, first.text
    retry = await client.post("/v1/member-proposals", headers=auth_headers(author), json=body)
    assert retry.status_code == 201 and retry.json()["id"] == first.json()["id"]
    assert (await client.post(f'/v1/member-proposals/{first.json()["id"]}/submit', headers=auth_headers(author))).status_code == 200
    submitted = await client.post("/v1/member-proposals", headers=auth_headers(author), json=body)
    assert submitted.status_code == 201
    assert submitted.json()["id"] == first.json()["id"]
    assert submitted.json()["status"] == "pending_review"
    assert await session.scalar(select(func.count(MemberProposal.id)).where(MemberProposal.title == body["title"])) == 1
    assert await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "member_proposal.pending_review")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"title": "另一標題"}, {"body": "另一內容"}, {"proposal_type": "multiple_choice", "options": [{"label": "一"}, {"label": "二"}]}])
async def test_reusing_proposal_key_with_changed_content_is_rejected(proposal_context, changes):
    client, session, _, author, _, _ = proposal_context
    body = payload()
    original = await client.post("/v1/member-proposals", headers=auth_headers(author), json=body)
    conflict = await client.post("/v1/member-proposals", headers=auth_headers(author), json={**body, **changes})
    assert conflict.status_code == 409
    stored = await session.get(MemberProposal, original.json()["id"])
    assert stored.title == body["title"] and stored.body == body["body"]
    assert stored.status == MemberProposalStatus.DRAFT


@pytest.mark.asyncio
async def test_proposal_keys_scoped_by_author_and_legacy_creation_stays_available(proposal_context):
    client, _, _, author, member, _ = proposal_context
    body = payload()
    ids = []
    for user in (author, member):
        response = await client.post("/v1/member-proposals", headers=auth_headers(user), json=body)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    assert ids[0] != ids[1]
    legacy = {key: value for key, value in body.items() if key != "client_request_id"}
    for _ in range(2):
        response = await client.post("/v1/member-proposals", headers=auth_headers(author), json=legacy)
        assert response.status_code == 201
        ids.append(response.json()["id"])
    assert len(set(ids)) == 4


@pytest.mark.asyncio
async def test_multiple_choice_retry_preserves_trimmed_options_order(proposal_context):
    client, _, _, author, _, _ = proposal_context
    body = payload(proposal_type="multiple_choice", options=[{"label": " 一 "}, {"label": " 二 "}])
    first = await client.post("/v1/member-proposals", headers=auth_headers(author), json=body)
    retry = await client.post("/v1/member-proposals", headers=auth_headers(author), json={**body, "options": [{"label": "一"}, {"label": "二"}]})
    assert retry.status_code == 201 and retry.json()["id"] == first.json()["id"]
    assert [option["label"] for option in retry.json()["options"]] == ["一", "二"]
    reordered = await client.post("/v1/member-proposals", headers=auth_headers(author), json={**body, "options": [{"label": "二"}, {"label": "一"}]})
    assert reordered.status_code == 409
