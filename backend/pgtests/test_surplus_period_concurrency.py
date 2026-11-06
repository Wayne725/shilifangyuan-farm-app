import asyncio
from datetime import datetime, timezone

import pytest

from app.models import (
    Membership, MembershipStatus, MembershipType, Order, OrderItem, OrderKind,
    PaymentStatus, SalesChannel, TaxType, User, UserRole,
)
from app.routers.cooperative import cooperative_router
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.support import api_test_context, auth_headers
from tests.test_surplus_period_protection import period_request


@pytest.fixture
async def surplus_pg(postgres_sessions):
    postgres_sessions.configure(autoflush=False)
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    async with postgres_sessions() as session:
        member = User(email="pg-surplus-member@example.com", display_name="社員", password_hash="test",
                      email_verified_at=now, membership=Membership(status=MembershipStatus.ACTIVE,
                      member_number="PG-SURPLUS-1", activated_at=now))
        admins = [User(email=f"pg-surplus-admin-{i}@example.com", display_name=f"財務管理員{i}",
                       password_hash="test", user_role=UserRole.ADMIN, email_verified_at=now) for i in range(2)]
        session.add_all([member, *admins])
        await session.flush()
        session.add(Order(
            order_number="PG-SURPLUS-ORDER", user_id=member.id, order_kind=OrderKind.REGULAR,
            sales_channel=SalesChannel.REGULAR, membership_type_snapshot=MembershipType.MEMBER,
            amount_total=100, contact_email=member.email, payment_status=PaymentStatus.PAID, paid_at=now,
            items=[OrderItem(product_name="商品", unit_label="份", quantity=1, unit_price=100,
                             subtotal=100, tax_type=TaxType.TAXABLE)],
        ))
        await session.commit()
        return postgres_sessions, [auth_headers(admin) for admin in admins], auth_headers(member)


async def request(factory, method, path, headers, body=None):
    async with factory() as session:
        async with api_test_context(session, [cooperative_router]) as client:
            return await client.request(method, path, headers=headers, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_label", [True, False])
@pytest.mark.parametrize("second_period", [
    ("2026-01-01", "2026-12-31"),
    ("2026-06-01", "2027-03-01"),
])
async def test_two_admins_cannot_confirm_overlapping_surplus_periods(surplus_pg, second_period, same_label):
    factory, admins, member_headers = surplus_pg
    gate = asyncio.Event()

    async def confirm(headers, body):
        await gate.wait()
        return await request(factory, "POST", "/v1/admin/surplus/confirm", headers, body)

    tasks = [asyncio.create_task(confirm(admins[0], period_request("年度分配"))),
             asyncio.create_task(confirm(admins[1], period_request(
                 "年度分配" if same_label else "另一管理員分配", *second_period)))]
    gate.set()
    responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
    assert sorted(response.status_code for response in responses) == [201, 409]
    history = await request(factory, "GET", "/v1/me/surplus-distributions", member_headers)
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert history.json()[0]["distribution_amount"] == 100


@pytest.mark.asyncio
async def test_two_admins_can_confirm_nonoverlapping_surplus_periods(surplus_pg):
    factory, admins, member_headers = surplus_pg
    responses = await asyncio.wait_for(asyncio.gather(
        request(factory, "POST", "/v1/admin/surplus/confirm", admins[0], period_request("年度分配")),
        request(factory, "POST", "/v1/admin/surplus/confirm", admins[1],
                period_request("前一年度", "2025-01-01", "2025-12-31")),
    ), timeout=20)
    assert [response.status_code for response in responses] == [201, 201]
    assert sorted(response.json()["total_revenue"] for response in responses) == [0, 100]
    history = await request(factory, "GET", "/v1/me/surplus-distributions", member_headers)
    assert len(history.json()) == 1
    assert history.json()[0]["distribution_amount"] == 100
