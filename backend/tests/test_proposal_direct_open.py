from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, GroupCampaign, OutboxEvent, ProposalStatus, VoteProposal
from tests.support import auth_headers
from tests.test_group_campaign_shipping import campaign_context, campaign_payload


@pytest.mark.asyncio
async def test_admin_quantity_settings_apply_to_proposal_campaign_and_lock_after_payment(campaign_context):
    from app.domain import apply_paid_quantity

    ctx = campaign_context
    client, session = ctx["client"], ctx["session"]
    admin = auth_headers(ctx["admin"])
    opened = await client.post(f"/v1/vote-proposals/{ctx['proposal'].id}/admin/open-direct", headers=admin,
                               json=campaign_payload(ctx["product"].id, min_paid_quantity=10, per_user_cap=5))
    assert opened.status_code == 201, opened.text
    campaign_id = opened.json()["id"]
    path = f"/v1/group-campaigns/{campaign_id}"
    settings = {"per_user_cap": 2, "min_paid_quantity": 4, "supply_cap": 12}
    assert (await client.patch(path, headers=auth_headers(ctx["proposer"]), json=settings)).status_code == 403
    updated = await client.patch(path, headers=admin, json=settings)
    assert updated.status_code == 200, updated.text
    persisted = (await client.get(path)).json()
    assert {key: persisted[key] for key in settings} == settings
    assert persisted["source_proposal_id"] == ctx["proposal"].id
    assert persisted["member_price"] == opened.json()["member_price"]
    too_many = await client.post(f"{path}/join", headers=auth_headers(ctx["proposer"]),
                                 json={"quantity": 3, "contact_email": "proposer@example.com"})
    assert too_many.status_code == 409 and "每人最多" in too_many.json()["detail"], too_many.text
    campaign = await session.get(GroupCampaign, campaign_id)
    apply_paid_quantity(campaign, 2, datetime.now(timezone.utc))
    await session.commit()
    assert (await client.get(path)).json()["decision_status"] == "recruiting"
    locked = await client.patch(path, headers=admin, json={"per_user_cap": 3, "min_paid_quantity": 6})
    assert locked.status_code == 409 and "首筆付款" in locked.json()["detail"]
    apply_paid_quantity(campaign, 2, datetime.now(timezone.utc))
    await session.commit()
    assert (await client.get(path)).json()["decision_status"] == "recruiting"


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [False, True])
async def test_admin_can_open_pending_proposal_without_votes_once(campaign_context, custom):
    ctx = campaign_context
    client, session = ctx["client"], ctx["session"]
    payload = {"target_type": "custom", "request_name": "其他團購", "request_details": "需要供應蔬菜箱"} if custom else {"target_type": "product", "target_id": ctx["product"].id}
    # Retire the fixture's open proposal so the product submission creates a new pending request.
    ctx["proposal"].status = ProposalStatus.REJECTED
    await session.commit()
    proposal = (await client.post("/v1/vote-proposals", headers=auth_headers(ctx["proposer"]), json=payload)).json()
    assert proposal["status"] == "pending_review" and proposal["vote_count"] == 0
    path = f"/v1/vote-proposals/{proposal['id']}/admin/open-direct"
    body = campaign_payload(ctx["product"].id, member_price=450, nonmember_price=500)
    assert (await client.post(path, json=body)).status_code == 401
    assert (await client.post(path, headers=auth_headers(ctx["proposer"]), json=body)).status_code == 403
    admin = auth_headers(ctx["admin"])
    assert (await client.post(path.replace("open-direct", "convert"), headers=admin, json=body)).status_code == 409
    opened = await client.post(path, headers=admin, json=body)
    assert opened.status_code == 201, opened.text
    campaign = opened.json()
    assert campaign["source_proposal_id"] == proposal["id"]
    assert campaign["member_price"] == 450 and campaign["decision_status"] == "recruiting"
    assert (await client.post(path, headers=admin, json=body)).status_code == 409
    assert await session.scalar(select(func.count()).select_from(GroupCampaign).where(GroupCampaign.source_proposal_id == proposal["id"])) == 1
    assert await session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_type == "group.opened")) == 1
    audit = await session.scalar(select(AdminAudit).where(AdminAudit.action == "proposal.open_direct"))
    assert audit.actor_id == ctx["admin"].id and audit.data["vote_count"] == 0


@pytest.mark.asyncio
async def test_direct_open_validates_target_schedule_and_closed_proposals(campaign_context):
    ctx = campaign_context
    proposal = ctx["proposal"]
    proposal.threshold = 100
    await ctx["session"].commit()
    path = f"/v1/vote-proposals/{proposal.id}/admin/open-direct"
    client, admin = ctx["client"], auth_headers(ctx["admin"])
    for changes in [{"target_id": "missing"}, {"source_proposal_id": "another"}, {"min_paid_quantity": 30, "supply_cap": 20}, {"deadline": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}]:
        response = await client.post(path, headers=admin, json=campaign_payload(ctx["product"].id, **changes))
        assert response.status_code == 422, response.text
    assert (await client.post(path, headers=admin, json=campaign_payload(ctx["product"].id))).status_code == 201
    proposal.status = ProposalStatus.REJECTED
    await ctx["session"].commit()
    assert (await client.post(path, headers=admin, json=campaign_payload(ctx["product"].id))).status_code == 409
