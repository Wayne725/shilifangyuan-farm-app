import pytest
from sqlalchemy import select

from app.models import AdminAudit, GroupCampaign, Order, OrderFulfillment, PaymentStatus
from tests.support import auth_headers
from tests.test_group_campaign_shipping import campaign_context, campaign_payload

SLOTS = [
    {"id": "fri", "weekday": 4, "starts_at": "11:30", "ends_at": "13:00"},
    {"id": "wed", "weekday": 2, "starts_at": "11:30", "ends_at": "13:00"},
]


async def create_campaign(ctx, **overrides):
    payload = campaign_payload(ctx["product"].id, can_ship=False, shipping_temperature=None,
                               allowed_shipping_channels=[], **overrides)
    return await ctx["client"].post("/v1/group-campaigns", json=payload, headers=auth_headers(ctx["admin"]))


def buyer_headers(ctx):
    return auth_headers(ctx["proposer"])


async def join(ctx, campaign_id, headers, **extra):
    return await ctx["client"].post(f"/v1/group-campaigns/{campaign_id}/join", headers=headers, json={
        "quantity": 2, "contact_email": "slot@example.com", "fulfillment_method": "cooperative_pickup", **extra,
    })


@pytest.mark.asyncio
async def test_campaign_requires_and_sorts_pickup_slots(campaign_context):
    ctx = campaign_context
    missing = await create_campaign(ctx, pickup_slots=[])
    assert missing.status_code == 422
    assert "取貨時段" in missing.text
    for bad in ([{"weekday": 4, "starts_at": "13:00", "ends_at": "11:30"}],
                [{"weekday": 4, "starts_at": "11:30", "ends_at": "13:00"}] * 2,
                [{"weekday": 7, "starts_at": "11:30", "ends_at": "13:00"}]):
        assert (await create_campaign(ctx, pickup_slots=bad)).status_code == 422
    created = await create_campaign(ctx, pickup_slots=SLOTS)
    assert created.status_code == 201, created.text
    assert [slot["label"] for slot in created.json()["pickup_slots"]] == ["每週三 11:30~13:00", "每週五 11:30~13:00"]
    public = await ctx["client"].get(f"/v1/group-campaigns/{created.json()['id']}")
    assert [slot["id"] for slot in public.json()["pickup_slots"]] == ["wed", "fri"]


@pytest.mark.asyncio
async def test_pickup_order_requires_valid_slot_and_keeps_its_label(campaign_context):
    ctx = campaign_context
    campaign_id = (await create_campaign(ctx, pickup_slots=SLOTS)).json()["id"]
    headers, admin = buyer_headers(ctx), auth_headers(ctx["admin"])
    assert (await join(ctx, campaign_id, headers)).status_code == 422
    assert (await join(ctx, campaign_id, headers, pickup_slot_id="mon")).status_code == 422
    created = await join(ctx, campaign_id, headers, pickup_slot_id="wed")
    assert created.status_code == 201, created.text
    assert created.json()["fulfillment"]["pickup_slot_label"] == "每週三 11:30~13:00"

    edited = await ctx["client"].patch(f"/v1/group-campaigns/{campaign_id}", headers=admin, json={
        "pickup_slots": [{"id": "wed", "weekday": 2, "starts_at": "12:00", "ends_at": "14:00"}],
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["pickup_slots"][0]["label"] == "每週三 12:00~14:00"
    audit = await ctx["session"].scalar(select(AdminAudit).where(AdminAudit.action == "campaign.update"))
    assert audit.data["changed_fields"] == ["pickup_slots"]
    ctx["session"].expire_all()
    stored = await ctx["session"].scalar(select(OrderFulfillment).where(OrderFulfillment.order_id == created.json()["id"]))
    assert stored.pickup_slot_label == "每週三 11:30~13:00"
    assert (await ctx["client"].patch(f"/v1/group-campaigns/{campaign_id}", headers=admin,
                                      json={"pickup_slots": []})).status_code == 422


@pytest.mark.asyncio
async def test_campaign_without_slots_cannot_take_pickup_orders(campaign_context):
    ctx = campaign_context
    campaign_id = (await create_campaign(ctx, pickup_slots=SLOTS)).json()["id"]
    campaign = await ctx["session"].get(GroupCampaign, campaign_id)
    campaign.pickup_slots = []
    await ctx["session"].commit()
    response = await join(ctx, campaign_id, buyer_headers(ctx), pickup_slot_id="wed")
    assert response.status_code == 422
    assert "尚未設定取貨時段" in response.text


@pytest.mark.asyncio
async def test_admin_summary_groups_paid_orders_by_slot(campaign_context):
    ctx = campaign_context
    campaign_id = (await create_campaign(ctx, pickup_slots=SLOTS)).json()["id"]
    headers = buyer_headers(ctx)
    paid = await join(ctx, campaign_id, headers, pickup_slot_id="fri")
    unpaid = await join(ctx, campaign_id, headers, pickup_slot_id="wed", quantity=1)
    assert paid.status_code == unpaid.status_code == 201, (paid.text, unpaid.text)
    order = await ctx["session"].get(Order, paid.json()["id"])
    order.payment_status = PaymentStatus.PAID
    await ctx["session"].commit()
    path = f"/v1/group-campaigns/{campaign_id}/admin/pickup-slot-summary"
    assert (await ctx["client"].get(path, headers=headers)).status_code == 403
    summary = (await ctx["client"].get(path, headers=auth_headers(ctx["admin"]))).json()
    assert [(row["label"], row["order_count"], row["quantity"]) for row in summary] == [
        ("每週三 11:30~13:00", 0, 0), ("每週五 11:30~13:00", 1, 2),
    ]
    assert summary[1]["orders"][0]["order_number"] == paid.json()["order_number"]
