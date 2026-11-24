import pytest
from sqlalchemy import func, select

from app.models import Membership, MembershipStatus, Order, TaxType, User
from tests.support import auth_headers
from tests.test_group_campaign_shipping import campaign_context, campaign_payload


async def open_campaign(ctx):
    created = await ctx["client"].post("/v1/group-campaigns", headers=auth_headers(ctx["admin"]), json=campaign_payload(
        ctx["product"].id, can_ship=False, shipping_temperature=None, allowed_shipping_channels=[]))
    assert created.status_code == 201, created.text
    return created.json()["id"]


def body(**changes):
    return {"quantity": 1, "contact_email": "member@example.com", "pickup_slot_id": "fri", **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, MembershipStatus.TRAINEE, MembershipStatus.SUSPENDED, MembershipStatus.PENDING_PAYMENT])
async def test_only_active_members_can_quote_or_join(campaign_context, status):
    ctx = campaign_context
    campaign_id = await open_campaign(ctx)
    buyer = User(email="outsider@example.com", display_name="非社員", password_hash="test",
                 membership=Membership(status=status) if status else None)
    ctx["session"].add(buyer)
    await ctx["session"].commit()
    for suffix in ("quote", "join"):
        response = await ctx["client"].post(f"/v1/group-campaigns/{campaign_id}/{suffix}", headers=auth_headers(buyer), json=body())
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "共同購買僅限正式社員參加"
    assert await ctx["session"].scalar(select(func.count(Order.id))) == 0


@pytest.mark.asyncio
async def test_members_get_exempt_personal_invoice_and_cannot_use_company_invoice(campaign_context):
    ctx = campaign_context
    campaign_id = await open_campaign(ctx)
    headers = auth_headers(ctx["proposer"])
    company = body(invoice_buyer_type="company", invoice_buyer_tax_id="12345675", invoice_buyer_name="測試公司",
                   invoice_buyer_email="company@example.com")
    for suffix in ("quote", "join"):
        rejected = await ctx["client"].post(f"/v1/group-campaigns/{campaign_id}/{suffix}", headers=headers, json=company)
        assert rejected.status_code == 422, rejected.text
    quote = await ctx["client"].post(f"/v1/group-campaigns/{campaign_id}/quote", headers=headers, json=body())
    assert quote.status_code == 200, quote.text
    assert quote.json()["member_tax_exempt"] is True and quote.json()["tax_amount"] == 0
    joined = await ctx["client"].post(f"/v1/group-campaigns/{campaign_id}/join", headers=headers, json=body())
    assert joined.status_code == 201, joined.text
    order = await ctx["session"].get(Order, joined.json()["id"])
    assert order.member_tax_exempt_snapshot is True
    await ctx["session"].refresh(order, ["items"])
    assert order.items[0].tax_type == TaxType.TAX_EXEMPT
    assert order.amount_total == ctx["product"].member_price
