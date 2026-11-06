from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import GroupCampaign, Order, PaymentAttempt, PaymentStatus
from tests.test_group_checkout_contract import group_checkout, join


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_changed_group_contract_blocks_pay_and_amend_in_buyer_and_admin_reads(group_checkout, legacy):
    c = group_checkout
    response = await join(c)
    assert response.status_code == 201, response.text
    order_id = response.json()["id"]
    if legacy:
        order = await c.session.get(Order, order_id)
        order.group_contract_fingerprint = None
        await c.session.commit()
    changed = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"target_id": c.product_ids[1]})
    assert changed.status_code == 200, changed.text

    for headers in [c.buyer_headers[0], c.admin_headers]:
        single = await c.client.get(f"/v1/orders/{order_id}", headers=headers)
        listing = await c.client.get("/v1/orders", headers=headers)
        assert single.status_code == listing.status_code == 200
        listed = next(row for row in listing.json() if row["id"] == order_id)
        for row in [single.json(), listed]:
            assert "pay" not in row["available_actions"]
            assert "amend_quantities" not in row["available_actions"]
            assert row["group_contract_changed"] is True
            assert "重新確認" in row["amendment_blocked_reason"]
            assert row["items"][0]["product_id"] == c.product_ids[0]
            assert row["amount_total"] == 100
    amendment = await c.client.post(f"/v1/orders/{order_id}/quantity-amendments/quote",
        headers=c.buyer_headers[0], json={"expected_revision": response.json()["revision"],
            "items": [{"order_item_id": response.json()["items"][0]["id"], "quantity": 2}]})
    assert amendment.status_code == 409, amendment.text
    assert "重新確認" in amendment.json()["detail"]


@pytest.mark.asyncio
async def test_group_metadata_change_keeps_existing_payment_and_amendment_available(group_checkout):
    c = group_checkout
    created = await join(c)
    changed = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"title": "只有標題更新"})
    assert created.status_code == 201 and changed.status_code == 200
    response = await c.client.get(f"/v1/orders/{created.json()['id']}", headers=c.buyer_headers[0])
    assert response.status_code == 200
    assert response.json().get("group_contract_changed", False) is False
    assert {"pay", "amend_quantities"} <= set(response.json()["available_actions"])


@pytest.mark.asyncio
async def test_historical_paid_group_mismatch_does_not_offer_buy_again_or_remove_admin_refund(group_checkout):
    c = group_checkout
    created = await join(c)
    order = await c.session.get(Order, created.json()["id"])
    changed = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
        json={"target_id": c.product_ids[1]})
    assert changed.status_code == 200
    order.payment_status = PaymentStatus.PAID
    await c.session.commit()
    response = await c.client.get(f"/v1/orders/{order.id}", headers=c.admin_headers)
    assert response.status_code == 200
    assert response.json().get("group_contract_changed", False) is False
    assert "refund" in response.json()["available_actions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("expired", [False, True])
async def test_unknown_group_payment_is_explicit_even_for_legacy_mismatched_contract(group_checkout, changed, expired):
    c = group_checkout
    created = await join(c)
    order = await c.session.get(Order, created.json()["id"])
    payment = await c.client.post(f"/v1/orders/{order.id}/payment-attempts", headers=c.buyer_headers[0])
    assert payment.status_code == 201, payment.text
    attempt = await c.session.scalar(select(PaymentAttempt).where(PaymentAttempt.order_id == order.id))
    now = datetime.now(timezone.utc)
    attempt.expires_at = now + timedelta(minutes=-1 if expired else 5)
    if not expired:
        attempt.settlement_unknown_since = now
    if changed:
        # Earlier releases admitted this state; the archived probe exercises that real API path.
        campaign = await c.session.get(GroupCampaign, c.campaign_id)
        campaign.target_id = c.product_ids[1]
        order.group_contract_fingerprint = None
    await c.session.commit()
    response = await c.client.get(f"/v1/orders/{order.id}", headers=c.buyer_headers[0])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["group_payment_uncertain"] is True
    assert data["group_contract_changed"] is changed
    assert "pay" not in data["available_actions"]
    assert "amend_quantities" not in data["available_actions"]
    assert "待核對" in data["amendment_blocked_reason"]
    campaign = await c.session.get(GroupCampaign, c.campaign_id)
    assert campaign.reserved_quantity == 1
    assert attempt.unpaid_verified_at is None
