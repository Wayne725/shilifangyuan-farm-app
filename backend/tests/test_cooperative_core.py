from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import (
    Membership,
    MembershipStatus,
    MembershipType,
    Order,
    OrderItem,
    OrderKind,
    PaymentStatus,
    SalesChannel,
    TaxType,
    User,
    UserRole,
)
from app.routers.cooperative import cooperative_router
from tests.support import api_test_context, auth_headers, fast_password_hash


@pytest.fixture
async def cooperative_context(database_session):
    async with api_test_context(
        database_session,
        [cooperative_router],
    ) as client:
        session = database_session
        now = datetime.now(timezone.utc)
        password_hash = fast_password_hash("fixture-pass-123")
        admin = User(email="finance-admin@example.com", display_name="財務管理員", password_hash=password_hash, user_role=UserRole.ADMIN, email_verified_at=now)
        member_a = User(email="core-a@example.com", display_name="社員甲", password_hash=password_hash, email_verified_at=now, membership=Membership(status=MembershipStatus.ACTIVE, member_number="CORE-1", activated_at=now))
        member_b = User(email="core-b@example.com", display_name="社員乙", password_hash=password_hash, email_verified_at=now, membership=Membership(status=MembershipStatus.ACTIVE, member_number="CORE-2", activated_at=now))
        customer = User(email="core-c@example.com", display_name="非社員", password_hash=password_hash, email_verified_at=now)
        session.add_all([admin, member_a, member_b, customer])
        await session.flush()

        for number, user, membership_type, amount, payment_status in [
            (1, member_a, MembershipType.MEMBER, 300, PaymentStatus.PAID),
            (2, member_b, MembershipType.MEMBER, 100, PaymentStatus.PAID),
            (3, customer, MembershipType.NONMEMBER, 200, PaymentStatus.PAID),
            (4, member_a, MembershipType.MEMBER, 999, PaymentStatus.REFUNDED),
        ]:
            order = Order(order_number=f"CORE-{number}", order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.REGULAR, user_id=user.id, membership_type_snapshot=membership_type, amount_total=amount, contact_email=user.email, payment_status=payment_status, paid_at=now)
            session.add(order)
            await session.flush()
            session.add(OrderItem(order_id=order.id, product_name=f"品項 {number}", unit_label="份", quantity=1, unit_price=amount, subtotal=amount, tax_type=TaxType.TAXABLE if number == 3 else TaxType.TAX_EXEMPT))
        await session.commit()

        yield {
            "client": client,
            "session": session,
            "admin": admin,
            "member_a": member_a,
            "member_b": member_b,
            "customer": customer,
        }


@pytest.mark.asyncio
async def test_finance_monitor_tax_and_irreversible_surplus(cooperative_context) -> None:
    client = cooperative_context["client"]
    admin_headers = auth_headers(cooperative_context["admin"])
    query = "starts_on=2020-01-01&ends_on=2030-12-31"
    monitor = await client.get(f"/v1/admin/finance/nonmember-sales?{query}", headers=admin_headers)
    assert monitor.status_code == 200
    assert monitor.json()["total_revenue"] == 600
    assert monitor.json()["nonmember_revenue"] == 200
    assert monitor.json()["level"] == "limit"
    assert monitor.json()["transactions_blocked"] is False

    tax = await client.get(f"/v1/admin/finance/tax-ledger?{query}", headers=admin_headers)
    assert tax.status_code == 200
    assert sum(row["sales_amount"] for row in tax.json()["rows"]) == 600

    body = {"label": "2026 年度", "starts_on": "2020-01-01", "ends_on": "2030-12-31", "total_cost": 100, "reserve_percentage": 50}
    dry_run = await client.post("/v1/admin/surplus/dry-run", json=body, headers=admin_headers)
    assert dry_run.status_code == 200
    result = dry_run.json()
    assert result["reserve_amount"] == 250
    assert result["distributable_surplus"] == 250
    assert sorted(item["distribution_amount"] for item in result["distributions"]) == [62, 188]

    confirmed = await client.post("/v1/admin/surplus/confirm", json=body, headers=admin_headers)
    assert confirmed.status_code == 201
    repeated = await client.post("/v1/admin/surplus/confirm", json=body, headers=admin_headers)
    assert repeated.status_code == 409
    mine = await client.get("/v1/me/surplus-distributions", headers=auth_headers(cooperative_context["member_a"]))
    assert mine.status_code == 200
    assert mine.json()[0]["distribution_amount"] == 188


@pytest.mark.asyncio
async def test_sales_report_separates_product_revenue_by_checkout_identity(
    cooperative_context,
) -> None:
    session = cooperative_context["session"]
    now = datetime.now(timezone.utc)
    trainee = User(
        email="core-trainee@example.com",
        display_name="實習社員",
        password_hash=fast_password_hash("trainee-pass-123"),
        email_verified_at=now,
        membership=Membership(
            status=MembershipStatus.TRAINEE,
            trainee_number="SLF-T-2026-0088",
        ),
    )
    session.add(trainee)
    await session.flush()
    extra_orders = [
        ("CORE-TRAINEE", trainee, MembershipType.TRAINEE, 190, 150),
        (
            "CORE-NONMEMBER-SHIPPING",
            cooperative_context["customer"],
            MembershipType.NONMEMBER,
            260,
            200,
        ),
    ]
    for number, user, identity, amount_total, product_subtotal in extra_orders:
        order = Order(
            order_number=number,
            order_kind=OrderKind.REGULAR,
            sales_channel=SalesChannel.REGULAR,
            user_id=user.id,
            membership_type_snapshot=identity,
            amount_total=amount_total,
            contact_email=user.email,
            payment_status=PaymentStatus.PAID,
            paid_at=now,
        )
        session.add(order)
        await session.flush()
        session.add(
            OrderItem(
                order_id=order.id,
                product_name=number,
                unit_label="份",
                quantity=1,
                unit_price=product_subtotal,
                subtotal=product_subtotal,
                tax_type=TaxType.TAXABLE,
            )
        )
    await session.commit()

    query = "starts_on=2020-01-01&ends_on=2030-12-31"
    response = await cooperative_context["client"].get(
        f"/v1/admin/finance/nonmember-sales?{query}",
        headers=auth_headers(cooperative_context["admin"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_revenue"] == 950
    assert body["nonmember_revenue"] == 400
    assert body["trainee_revenue"] == 150
    assert body["member_revenue"] == 400
    assert body["nonmember_ratio"] == pytest.approx(400 / 950)
    assert body["trainee_ratio"] == pytest.approx(150 / 950)
    assert body["member_ratio"] == pytest.approx(400 / 950)
    assert body["sales_breakdown"] == [
        {
            "membership_type": "nonmember",
            "revenue": 400,
            "ratio": pytest.approx(400 / 950),
        },
        {
            "membership_type": "trainee",
            "revenue": 150,
            "ratio": pytest.approx(150 / 950),
        },
        {
            "membership_type": "member",
            "revenue": 400,
            "ratio": pytest.approx(400 / 950),
        },
    ]


@pytest.mark.asyncio
async def test_points_and_wish_launch(cooperative_context) -> None:
    client = cooperative_context["client"]
    member = cooperative_context["member_a"]
    wish = await client.post("/v1/wishes", json={"name": "新作物", "description": "希望共同採購"}, headers=auth_headers(member))
    assert wish.status_code == 201
    wish_id = wish.json()["id"]
    launched = await client.put(f"/v1/admin/wishes/{wish_id}/status", json={"status": "launched", "launched_product_id": "product-demo", "proposer_points": 120}, headers=auth_headers(cooperative_context["admin"]))
    assert launched.status_code == 200
    points = await client.get("/v1/me/points", headers=auth_headers(member))
    assert points.json()["balance"] == 120
