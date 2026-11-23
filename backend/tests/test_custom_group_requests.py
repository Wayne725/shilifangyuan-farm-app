import pytest
from sqlalchemy import func, select

from app.domain import aware
from app.models import Membership, MembershipStatus, OutboxEvent, Vote, VoteProposal
from tests.support import auth_headers
from tests.test_group_campaign_shipping import campaign_context, campaign_payload


@pytest.mark.asyncio
async def test_custom_request_review_vote_and_conversion_keeps_request(campaign_context):
    context = campaign_context
    client, admin, proposer = context["client"], auth_headers(context["admin"]), auth_headers(context["proposer"])
    payload = {"target_type": "custom", "request_name": "  想團購無糖茶  ", "request_details": "茶葉種類：烏龍\n每組兩瓶"}
    assert (await client.post("/v1/vote-proposals", json=payload)).status_code == 401
    created = await client.post("/v1/vote-proposals", headers=admin, json=payload)
    assert created.status_code == 200, created.text
    proposal = created.json()
    assert proposal["target_id"] is None
    assert proposal["target_name"] == "想團購無糖茶"
    assert proposal["request_details"] == payload["request_details"]
    assert proposal["is_mine"] is True and "proposer_id" not in proposal
    path = f"/v1/vote-proposals/{proposal['id']}"
    assert (await client.get(path)).status_code == 404
    assert (await client.post(path + "/admin/approve", headers=proposer, json={"threshold": 1})).status_code == 403
    approved = await client.post(path + "/admin/approve", headers=admin, json={"threshold": 1})
    assert approved.status_code == 200, approved.text
    assert (await client.get(path)).json()["request_details"] == payload["request_details"]
    vote = await client.put(path + "/vote", headers=proposer, json={"estimated_quantity": 2})
    assert vote.status_code == 200, vote.text
    invalid = await client.post(path + "/admin/convert", headers=admin, json=campaign_payload("missing-product"))
    assert invalid.status_code == 422
    converted = await client.post(path + "/admin/convert", headers=admin, json=campaign_payload(context["product"].id))
    assert converted.status_code == 201, converted.text
    assert converted.json()["source_proposal_id"] == proposal["id"]
    assert (await client.get(path)).json()["target_name"] == "想團購無糖茶"
    assert (await client.get(path)).json()["status"] == "converted"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"target_type": "custom", "request_name": "名稱"},
    {"target_type": "custom", "request_name": " ", "request_details": "內容"},
    {"target_type": "custom", "request_name": "名稱", "request_details": "內容", "target_id": "product"},
    {"target_type": "product"},
    {"target_type": "product", "target_id": "product", "request_details": "內容"},
])
async def test_invalid_custom_request_is_rejected(campaign_context, payload):
    response = await campaign_context["client"].post("/v1/vote-proposals", headers=auth_headers(campaign_context["admin"]), json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_different_custom_requests_are_not_deduplicated(campaign_context):
    client, headers = campaign_context["client"], auth_headers(campaign_context["admin"])
    ids = []
    for name in ["茶葉", "果醬"]:
        response = await client.post("/v1/vote-proposals", headers=headers, json={"target_type": "custom", "request_name": name, "request_details": "其他供應需求"})
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])
    assert ids[0] != ids[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("membership_status", [None, MembershipStatus.TRAINEE, MembershipStatus.ACTIVE])
@pytest.mark.parametrize("target", ["custom", "product"])
async def test_nonadmin_cannot_create_proposal_or_change_existing_proposal(campaign_context, membership_status, target):
    ctx = campaign_context
    client, session, buyer = ctx["client"], ctx["session"], ctx["proposer"]
    if membership_status is not None:
        session.add(Membership(user_id=buyer.id, status=membership_status))
        await session.commit()
    payload = ({"target_type": "custom", "request_name": "茶葉", "request_details": "其他供應需求"}
               if target == "custom" else {"target_type": "product", "target_id": ctx["product"].id})
    models = (VoteProposal, OutboxEvent, Vote)
    before = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    proposal = ctx["proposal"]
    original = (proposal.status, proposal.threshold, aware(proposal.deadline))

    response = await client.post("/v1/vote-proposals", headers=auth_headers(buyer), json=payload)

    assert response.status_code == 403, response.text
    assert [await session.scalar(select(func.count()).select_from(model)) for model in models] == before
    await session.refresh(proposal)
    assert (proposal.status, proposal.threshold, aware(proposal.deadline)) == original


@pytest.mark.asyncio
async def test_historical_buyer_proposal_remains_readable_and_votable(campaign_context):
    ctx = campaign_context
    client, buyer = ctx["client"], auth_headers(ctx["proposer"])
    path = f"/v1/vote-proposals/{ctx['proposal'].id}"

    assert (await client.get(path)).status_code == 200
    assert ctx["proposal"].id in [proposal["id"] for proposal in (await client.get("/v1/vote-proposals")).json()]
    voted = await client.put(path + "/vote", headers=buyer, json={"estimated_quantity": 2})
    assert voted.status_code == 200, voted.text
    assert voted.json()["is_mine"] is True and "proposer_id" not in voted.json()
    assert voted.json()["my_vote_quantity"] == 2
