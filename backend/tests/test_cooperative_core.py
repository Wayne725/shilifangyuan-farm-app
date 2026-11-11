from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app import main
from app.auth import create_token
from app.config import get_settings
from app.database import get_session
from app.models import (
    AdminAudit, PointTransaction,
    Membership,
    MembershipStatus,
    MembershipType,
    Notification,
    Order,
    OrderItem,
    OrderKind,
    PaymentStatus,
    Product,
    SalesChannel,
    TaxType,
    User,
    UserRole,
)
from app.routers.cooperative import cooperative_router
from tests.support import api_test_context, auth_headers, fast_password_hash, make_test_settings


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
async def test_tax_ledger_read_and_csv_share_scope_and_reject_unauthorized(
    cooperative_context, monkeypatch,
) -> None:
    settings = make_test_settings(reconciliation_on_request_enabled=False)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    application = main.create_app()

    async def session_override():
        yield cooperative_context["session"]

    application.dependency_overrides[get_session] = session_override
    application.dependency_overrides[get_settings] = lambda: settings

    def headers(user, valid_for: timedelta) -> dict[str, str]:
        token = create_token(user, "access", valid_for, settings)
        return {"Authorization": f"Bearer {token}"}

    admin = cooperative_context["admin"]
    authorized = headers(admin, timedelta(minutes=5))
    expired = headers(admin, -timedelta(minutes=5))
    customer = headers(cooperative_context["customer"], timedelta(minutes=5))
    path = "/v1/admin/finance/tax-ledger"
    period = {"starts_on": "2030-01-01", "ends_on": "2030-01-01"}
    reversed_period = {"starts_on": "2030-01-02", "ends_on": period["starts_on"]}
    midnight = datetime(2030, 1, 1, tzinfo=timezone.utc)
    next_midnight = midnight + timedelta(days=1)
    orders = (await cooperative_context["session"].scalars(select(Order).order_by(Order.order_number))).all()
    for order, paid_at in zip(orders, (
        midnight - timedelta(microseconds=1), midnight, next_midnight - timedelta(microseconds=1),
        midnight + timedelta(hours=4),
    ), strict=True):
        order.paid_at = paid_at
    await cooperative_context["session"].commit()

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url=settings.app_base_url,
    ) as client:
        for url in (path, f"{path}.csv"):
            for credentials, expected in (({}, 401), (expired, 401), (customer, 403)):
                blocked = await client.get(url, params=period, headers=credentials)
                assert blocked.status_code == expected
                assert blocked.headers["cache-control"] == "no-store"
                assert "content-disposition" not in blocked.headers
                assert "rows" not in blocked.json()
            invalid_period = await client.get(url, params=reversed_period, headers=authorized)
            assert invalid_period.status_code == 422
        report = await client.get(path, params=period, headers=authorized)
        exported = await client.get(f"{path}.csv", params=period, headers=authorized)

    assert report.status_code == exported.status_code == 200
    assert report.headers["cache-control"] == exported.headers["cache-control"] == "no-store"
    assert exported.headers["content-disposition"] == 'attachment; filename="tax-ledger-2030-01-01-2030-01-01.csv"'
    assert exported.content.startswith(b"\xef\xbb\xbf")
    assert exported.headers["content-type"] == "text/csv; charset=utf-8"
    fields = ["tax_type", "membership_type", "sales_channel", "sales_amount", "tax_amount", "order_count"]
    expected_rows = [["tax_exempt", "member", "regular", 100, 0, 1], ["taxable", "nonmember", "regular", 200, 9, 1]]
    assert report.json() == {**period, "rows": [dict(zip(fields, row)) for row in expected_rows]}
    assert list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig")))) == [
        ["稅別", "身分", "銷售通路", "銷售額", "稅額", "訂單筆數"],
        *[[str(value) for value in row] for row in expected_rows],
    ]


@pytest.mark.asyncio
async def test_membership_sales_view_is_admin_only_read_only_and_reuses_ledger_scope(cooperative_context):
    c = cooperative_context
    client, session, admin = c["client"], c["session"], c["admin"]
    url = "/v1/admin/finance/sales-by-membership"
    params = {"starts_on": "2020-01-01", "ends_on": "2030-12-31"}
    expired = {"Authorization": f"Bearer {create_token(admin, 'access', -timedelta(minutes=5))}"}
    for headers, expected in (({}, 401), (expired, 401), (auth_headers(c["customer"]), 403)):
        assert (await client.get(url, params=params, headers=headers)).status_code == expected
    invalid = await client.get(url, params={"starts_on": "2030-01-01", "ends_on": "2029-12-31"}, headers=auth_headers(admin))
    assert invalid.status_code == 422

    for _ in range(2):
        response = await client.get(url, params=params, headers=auth_headers(admin))
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        report = response.json()
        assert report == {
            **params, "total_revenue": 600, "monitoring_threshold_reached": 0.30,
            "sales_breakdown": [
                {"membership_type": "nonmember", "revenue": 200, "ratio": pytest.approx(1 / 3)},
                {"membership_type": "trainee", "revenue": 0, "ratio": 0},
                {"membership_type": "member", "revenue": 400, "ratio": pytest.approx(2 / 3)},
            ],
        }
    assert await session.scalar(select(func.count(Notification.id))) == 0
    assert await session.scalar(select(func.count(AdminAudit.id))) == 0
    tax = await client.get("/v1/admin/finance/tax-ledger", params=params, headers=auth_headers(admin))
    assert sum(row["sales_amount"] for row in tax.json()["rows"]) == report["total_revenue"]
    old = await client.get("/v1/admin/finance/nonmember-sales", params=params, headers=auth_headers(admin))
    assert old.json()["sales_breakdown"] == report["sales_breakdown"]
    notice = (await session.scalars(select(Notification))).one()
    assert notice.event_type == "nonmember_sales_limit" and notice.user_id == admin.id

    empty = await client.get(url, params={"starts_on": "2001-01-01", "ends_on": "2001-01-01"}, headers=auth_headers(admin))
    assert empty.status_code == 200
    assert empty.json()["total_revenue"] == 0 and empty.json()["monitoring_threshold_reached"] is None


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
    product = Product(slug="wish-launched-product", name="願望成案商品", category="測試", unit="份", member_price=100, nonmember_price=100, stock_quantity=10)
    cooperative_context["session"].add(product)
    await cooperative_context["session"].flush()
    launched = await client.put(f"/v1/admin/wishes/{wish_id}/status", json={"status": "launched", "launched_product_id": product.id, "proposer_points": 120}, headers=auth_headers(cooperative_context["admin"]))
    assert launched.status_code == 200
    points = await client.get("/v1/me/points", headers=auth_headers(member))
    assert points.json()["balance"] == 120


@pytest.mark.parametrize("note", [{}, {"reason": ""}, {"reason": " \t "}])
@pytest.mark.asyncio
async def test_admin_point_adjustment_optional_note_preserves_actor_and_amount(cooperative_context, note):
    c = cooperative_context
    body = {"user_id": c["member_a"].id, "amount": 5, **note}
    path = "/v1/admin/points/adjustments"
    assert (await c["client"].post(path, json=body, headers=auth_headers(c["customer"]))).status_code == 403
    assert (await c["client"].post(path, json={**body, "amount": 0}, headers=auth_headers(c["admin"]))).status_code == 422
    result = await c["client"].post(path, json=body, headers=auth_headers(c["admin"]))
    assert result.status_code == 201, result.text
    transaction = await c["session"].get(PointTransaction, result.json()["id"])
    assert transaction.amount == 5 and transaction.note == "" and transaction.created_by_id == c["admin"].id
    audit = await c["session"].scalar(select(AdminAudit).where(AdminAudit.action == "points.adjusted"))
    assert audit.reason == "" and audit.aggregate_id == transaction.id
    assert await c["session"].scalar(select(func.count(PointTransaction.id))) == 1
