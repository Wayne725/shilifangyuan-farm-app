from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import hash_password, make_token_pair
from app.database import Base, get_session
from app.models import (
    EducationLecture,
    EducationQuestion,
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


def headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token_pair(user)['access_token']}"}


@pytest.fixture
async def cooperative_context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        app = FastAPI()
        app.include_router(cooperative_router)

        async def session_override():
            yield session

        app.dependency_overrides[get_session] = session_override
        now = datetime.now(timezone.utc)
        admin = User(email="finance-admin@example.com", display_name="財務管理員", password_hash=hash_password("admin-pass-123"), user_role=UserRole.ADMIN, email_verified_at=now)
        member_a = User(email="core-a@example.com", display_name="社員甲", password_hash=hash_password("member-pass-123"), email_verified_at=now, membership=Membership(status=MembershipStatus.ACTIVE, member_number="CORE-1", activated_at=now))
        member_b = User(email="core-b@example.com", display_name="社員乙", password_hash=hash_password("member-pass-123"), email_verified_at=now, membership=Membership(status=MembershipStatus.ACTIVE, member_number="CORE-2", activated_at=now))
        customer = User(email="core-c@example.com", display_name="非社員", password_hash=hash_password("member-pass-123"), email_verified_at=now)
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
        lectures = []
        for position in range(1, 4):
            lecture = EducationLecture(title=f"講義 {position}", body="合作教育", position=position)
            session.add(lecture)
            await session.flush()
            session.add(EducationQuestion(lecture_id=lecture.id, prompt=f"題目 {position}", options=["錯", "對"], correct_option=1))
            lectures.append(lecture)
        await session.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {"client": client, "admin": admin, "member_a": member_a, "member_b": member_b}
    await engine.dispose()


@pytest.mark.asyncio
async def test_finance_monitor_tax_and_irreversible_surplus(cooperative_context) -> None:
    client = cooperative_context["client"]
    admin_headers = headers(cooperative_context["admin"])
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
    mine = await client.get("/v1/me/surplus-distributions", headers=headers(cooperative_context["member_a"]))
    assert mine.status_code == 200
    assert mine.json()[0]["distribution_amount"] == 188


@pytest.mark.asyncio
async def test_education_points_and_wish_launch(cooperative_context) -> None:
    client = cooperative_context["client"]
    member = cooperative_context["member_a"]
    started = await client.post("/v1/education/attempts", headers=headers(member))
    assert started.status_code == 201
    payload = started.json()
    answers = {question["id"]: 1 for question in payload["questions"]}
    submitted = await client.post(f"/v1/education/attempts/{payload['attempt_id']}/submit", json={"answers": answers}, headers=headers(member))
    assert submitted.json()["passed"] is True

    wish = await client.post("/v1/wishes", json={"name": "新作物", "description": "希望共同採購"}, headers=headers(member))
    assert wish.status_code == 201
    wish_id = wish.json()["id"]
    launched = await client.put(f"/v1/admin/wishes/{wish_id}/status", json={"status": "launched", "launched_product_id": "product-demo", "proposer_points": 120}, headers=headers(cooperative_context["admin"]))
    assert launched.status_code == 200
    points = await client.get("/v1/me/points", headers=headers(member))
    assert points.json()["balance"] == 120
