from datetime import datetime, timezone

import pytest

from app.models import (
    Membership, MembershipStatus, MembershipType, Order, OrderItem, OrderKind,
    PaymentStatus, SalesChannel, TaxType, User, UserRole,
)
from app.routers.cooperative import cooperative_router
from tests.support import api_test_context, auth_headers


@pytest.fixture
async def surplus_context(database_session):
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    member = User(
        email="surplus-member@example.com", display_name="社員", password_hash="test",
        email_verified_at=now,
        membership=Membership(status=MembershipStatus.ACTIVE, member_number="SURPLUS-1", activated_at=now),
    )
    admin = User(email="surplus-admin@example.com", display_name="財務管理員", password_hash="test",
                 user_role=UserRole.ADMIN, email_verified_at=now)
    database_session.add_all([member, admin])
    await database_session.flush()
    database_session.add(Order(
        order_number="SURPLUS-ORDER", user_id=member.id, order_kind=OrderKind.REGULAR,
        sales_channel=SalesChannel.REGULAR, membership_type_snapshot=MembershipType.MEMBER,
        amount_total=100, contact_email=member.email, payment_status=PaymentStatus.PAID, paid_at=now,
        items=[OrderItem(product_name="商品", unit_label="份", quantity=1, unit_price=100,
                         subtotal=100, tax_type=TaxType.TAXABLE)],
    ))
    await database_session.commit()
    async with api_test_context(database_session, [cooperative_router]) as client:
        yield client, auth_headers(admin), auth_headers(member)


def period_request(label, starts_on="2026-01-01", ends_on="2026-12-31"):
    return {"label": label, "starts_on": starts_on, "ends_on": ends_on,
            "total_cost": 0, "reserve_percentage": 0}


@pytest.mark.asyncio
async def test_confirm_surplus_cannot_repeat_same_period_under_another_label(surplus_context):
    client, admin_headers, member_headers = surplus_context
    first = await client.post("/v1/admin/surplus/confirm", headers=admin_headers,
                              json=period_request("年度分配"))
    assert first.status_code == 201
    repeated = await client.post("/v1/admin/surplus/confirm", headers=admin_headers,
                                 json=period_request("年度分配重送"))
    assert repeated.status_code == 409
    history = await client.get("/v1/me/surplus-distributions", headers=member_headers)
    assert len(history.json()) == 1
    assert history.json()[0]["distribution_amount"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("starts_on,ends_on", [
    ("2025-12-01", "2026-01-01"),
    ("2026-12-31", "2027-01-02"),
    ("2026-03-01", "2026-05-01"),
    ("2025-01-01", "2027-12-31"),
])
async def test_confirm_surplus_rejects_overlapping_periods_without_changing_history(
    surplus_context, starts_on, ends_on,
):
    client, admin_headers, member_headers = surplus_context
    first = await client.post("/v1/admin/surplus/confirm", headers=admin_headers,
                              json=period_request("年度分配"))
    assert first.status_code == 201
    history_before = (await client.get("/v1/me/surplus-distributions", headers=member_headers)).json()
    overlap = await client.post("/v1/admin/surplus/confirm", headers=admin_headers,
                                json=period_request("重疊期間", starts_on, ends_on))
    assert overlap.status_code == 409
    assert "重疊" in overlap.json()["detail"]
    history_after = (await client.get("/v1/me/surplus-distributions", headers=member_headers)).json()
    assert history_after == history_before


@pytest.mark.asyncio
async def test_nonoverlapping_surplus_and_dry_run_keep_existing_behavior(surplus_context):
    client, admin_headers, member_headers = surplus_context
    first = await client.post("/v1/admin/surplus/confirm", headers=admin_headers,
                              json=period_request("年度分配"))
    assert first.status_code == 201
    preview = await client.post("/v1/admin/surplus/dry-run", headers=admin_headers,
                                json=period_request("重新預覽"))
    assert preview.status_code == 200
    assert preview.json()["distributable_surplus"] == 100
    adjacent = await client.post("/v1/admin/surplus/confirm", headers=admin_headers,
                                 json=period_request("前一年度", "2025-01-01", "2025-12-31"))
    assert adjacent.status_code == 201
    assert adjacent.json()["total_revenue"] == 0
    history = (await client.get("/v1/me/surplus-distributions", headers=member_headers)).json()
    assert len(history) == 1
    assert history[0]["distribution_amount"] == 100
