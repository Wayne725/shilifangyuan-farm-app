import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, GroupCampaign, OutboxEvent, Product, ProposalStatus, VoteProposal
from app.routers.catalog import catalog_router
from app.routers.orders import orders_router
from tests.support import auth_headers
from tests.test_group_campaign_shipping import campaign_context, campaign_payload


def new_product_payload(**overrides):
    return {
        "name": "高麗菜", "description": "需要一些高麗菜", "category": "當季蔬果",
        "unit": "顆", "member_price": 70, "nonmember_price": 80,
        "stock_quantity": 99, "tax_type": "tax_exempt", **overrides,
    }


def new_campaign_payload(**overrides):
    body = campaign_payload("unused", title="高麗菜共同購買", member_price=60, nonmember_price=75,
                            can_ship=False, new_product=new_product_payload())
    body.pop("target_id")
    return {**body, **overrides}


async def custom_proposal(ctx):
    response = await ctx["client"].post("/v1/vote-proposals", headers=auth_headers(ctx["proposer"]), json={
        "target_type": "custom", "request_name": "高麗菜", "request_details": "需要一些高麗菜",
    })
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("from_proposal", [False, True])
async def test_admin_creates_group_product_atomically_and_custom_proposal_is_not_rebound(campaign_context, from_proposal):
    ctx = campaign_context
    client, session = ctx["client"], ctx["session"]
    proposal = await custom_proposal(ctx) if from_proposal else None
    path = f"/v1/vote-proposals/{proposal['id']}/admin/open-direct" if proposal else "/v1/group-campaigns"
    body = new_campaign_payload()
    assert (await client.post(path, json=body)).status_code == 401
    assert (await client.post(path, headers=auth_headers(ctx["proposer"]), json=body)).status_code == 403
    assert await session.scalar(select(func.count()).select_from(Product)) == 1
    created = await client.post(path, headers=auth_headers(ctx["admin"]), json=body)
    assert created.status_code == 201, created.text
    campaign = created.json()
    product = await session.get(Product, campaign["target_id"])
    assert product.id != ctx["product"].id
    assert (product.name, product.unit, product.tax_type.value) == ("高麗菜", "顆", "tax_exempt")
    assert product.is_active is True and product.daily_sales_enabled is False
    assert (product.member_price, product.nonmember_price) == (70, 80)
    assert (campaign["member_price"], campaign["nonmember_price"]) == (60, 75)
    audit = await session.scalar(select(AdminAudit).where(AdminAudit.aggregate_id == product.id, AdminAudit.action == "product.create"))
    assert audit.actor_id == ctx["admin"].id
    if proposal:
        read_back = (await client.get(f"/v1/vote-proposals/{proposal['id']}")).json()
        assert read_back["target_name"] == "高麗菜" and read_back["target_id"] is None
        assert read_back["status"] == "converted"
        assert campaign["source_proposal_id"] == proposal["id"]
        assert (await client.post(path, headers=auth_headers(ctx["admin"]), json=body)).status_code == 409
        assert await session.scalar(select(func.count()).select_from(Product)) == 2
        assert await session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.event_type == "group.opened")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"target_id": "existing", "new_product": new_product_payload()},
    {"new_product": None},
    {"target_type": "bundle"},
    {"new_product": new_product_payload(name="   ")},
    {"new_product": new_product_payload(unit=" ")},
    {"new_product": new_product_payload(category="invalid")},
    {"new_product": new_product_payload(tax_type="invalid")},
    {"new_product": new_product_payload(member_price=100, nonmember_price=80)},
    {"new_product": new_product_payload(is_active=False)},
    {"new_product": new_product_payload(stock_quantity=-1)},
    {"new_product": new_product_payload(supplier_id="missing")},
    {"min_paid_quantity": 30, "supply_cap": 20},
    {"deadline": "2000-01-01T00:00:00+00:00"},
    {"source_proposal_id": "must-use-proposal-route"},
])
async def test_invalid_new_group_product_leaves_no_records(campaign_context, changes):
    ctx = campaign_context
    response = await ctx["client"].post("/v1/group-campaigns", headers=auth_headers(ctx["admin"]), json=new_campaign_payload(**changes))
    assert response.status_code == 422, response.text
    for model, count in ((Product, 1), (GroupCampaign, 0), (AdminAudit, 0), (OutboxEvent, 0)):
        assert await ctx["session"].scalar(select(func.count()).select_from(model)) == count


@pytest.mark.asyncio
async def test_fixed_product_proposal_cannot_replace_target_with_new_product(campaign_context):
    ctx = campaign_context
    response = await ctx["client"].post(f"/v1/vote-proposals/{ctx['proposal'].id}/admin/open-direct",
                                        headers=auth_headers(ctx["admin"]), json=new_campaign_payload())
    assert response.status_code == 422 and "目標與提案不一致" in response.json()["detail"]
    assert await ctx["session"].scalar(select(func.count()).select_from(Product)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("from_proposal", [False, True])
async def test_failed_campaign_commit_rolls_back_product_proposal_audit_and_outbox(campaign_context, monkeypatch, from_proposal):
    ctx = campaign_context
    client, session = ctx["client"], ctx["session"]
    proposal = await custom_proposal(ctx) if from_proposal else None
    path = f"/v1/vote-proposals/{proposal['id']}/admin/open-direct" if proposal else "/v1/group-campaigns"
    baseline_audits = await session.scalar(select(func.count()).select_from(AdminAudit))
    baseline_outbox = await session.scalar(select(func.count()).select_from(OutboxEvent))

    async def fail_commit():
        assert await session.scalar(select(func.count()).select_from(Product)) == 2
        assert await session.scalar(select(func.count()).select_from(GroupCampaign)) == 1
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await client.post(path, headers=auth_headers(ctx["admin"]), json=new_campaign_payload())
    for model, count in ((Product, 1), (GroupCampaign, 0), (AdminAudit, baseline_audits), (OutboxEvent, baseline_outbox)):
        assert await session.scalar(select(func.count()).select_from(model)) == count
    if proposal:
        persisted = await session.get(VoteProposal, proposal["id"])
        assert persisted.status == ProposalStatus.PENDING_REVIEW


@pytest.mark.asyncio
async def test_group_only_product_is_visible_for_units_but_regular_checkout_requires_admin_listing(campaign_context):
    ctx = campaign_context
    client = ctx["client"]
    client._transport.app.include_router(catalog_router)
    client._transport.app.include_router(orders_router)
    admin, customer = auth_headers(ctx["admin"]), auth_headers(ctx["proposer"])
    opened = await client.post("/v1/group-campaigns", headers=admin, json=new_campaign_payload())
    assert opened.status_code == 201, opened.text
    campaign_id, product_id = opened.json()["id"], opened.json()["target_id"]
    catalog = (await client.get("/v1/products")).json()
    product = next(product for product in catalog if product["id"] == product_id)
    assert product["unit"] == "顆" and product["daily_sales_enabled"] is False
    assert next(product for product in catalog if product["id"] == ctx["product"].id)["daily_sales_enabled"] is True
    regular = {"items": [{"product_id": product_id, "quantity": 2}], "contact_email": "buyer@example.com"}
    for path in ("/v1/orders/quote", "/v1/orders"):
        blocked = await client.post(path, headers=customer, json=regular)
        assert blocked.status_code == 422 and "未開放日常選購" in blocked.json()["detail"], blocked.text
    group_body = {"quantity": 2, "contact_email": "buyer@example.com"}
    assert (await client.post(f"/v1/group-campaigns/{campaign_id}/quote", headers=customer, json=group_body)).status_code == 200
    joined = await client.post(f"/v1/group-campaigns/{campaign_id}/join", headers=customer, json=group_body)
    assert joined.status_code == 201, joined.text
    item = joined.json()["items"][0]
    assert item["product_name"] == "高麗菜" and item["unit_label"] == "顆"
    amendment = await client.post(f"/v1/orders/{joined.json()['id']}/quantity-amendments/quote", headers=customer, json={
        "expected_revision": joined.json()["revision"],
        "items": [{"order_item_id": item["id"], "quantity": 3}],
    })
    assert amendment.status_code == 200, amendment.text
    update = {"daily_sales_enabled": True}
    assert (await client.patch(f"/v1/products/{product_id}", headers=customer, json=update)).status_code == 403
    enabled = await client.patch(f"/v1/products/{product_id}", headers=admin, json=update)
    assert enabled.status_code == 200 and enabled.json()["daily_sales_enabled"] is True
    assert (await client.post("/v1/orders/quote", headers=customer, json=regular)).status_code == 200
    assert (await client.post("/v1/orders", headers=customer, json=regular)).status_code == 201


@pytest.mark.asyncio
async def test_regular_unpaid_order_cannot_increase_after_daily_delisting_but_can_reduce(database_session):
    from tests.support import api_test_context, make_test_settings
    from tests.test_order_quantity_amendments import make_regular_order, submit

    buyer, product, order = await make_regular_order(database_session)
    product.daily_sales_enabled = False
    await database_session.commit()
    async with api_test_context(database_session, [orders_router], settings=make_test_settings()) as client:
        increased = await submit(client, buyer, order, 3)
        assert increased.status_code == 409, increased.text
        assert order.items[0].quantity == 2
        reduced = await submit(client, buyer, order, 1, total=100)
        assert reduced.status_code == 200, reduced.text
        assert reduced.json()["items"][0]["quantity"] == 1
