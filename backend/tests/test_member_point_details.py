from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select

from app.models import (
    BadgeDefinition,
    MemberBadge,
    Membership,
    MembershipApplication,
    MembershipCharge,
    MembershipChargeKind,
    MembershipFeeSchedule,
    MembershipStatus,
    MembershipType,
    Order,
    OrderKind,
    PaymentStatus,
    PointAccount,
    PointSourceType,
    PointTransaction,
    Refund,
    RefundStatus,
    SalesChannel,
    User,
    UserRole,
)
from app.routers.cooperative import cooperative_router
from tests.support import api_test_context, auth_headers, fast_password_hash


@pytest.fixture
async def point_context(database_session):
    now = datetime.now(timezone.utc)
    users = {}
    for name, membership_status, role in [
        ("member", MembershipStatus.ACTIVE, UserRole.CUSTOMER),
        ("other_member", MembershipStatus.ACTIVE, UserRole.CUSTOMER),
        ("active_admin", MembershipStatus.ACTIVE, UserRole.ADMIN),
        ("customer", None, UserRole.CUSTOMER),
        ("trainee", MembershipStatus.TRAINEE, UserRole.CUSTOMER),
        ("suspended", MembershipStatus.SUSPENDED, UserRole.CUSTOMER),
        ("resigned", MembershipStatus.RESIGNED, UserRole.CUSTOMER),
        ("admin_without_membership", None, UserRole.ADMIN),
    ]:
        user = User(
            email=f"{name}@points.example.test",
            display_name=name,
            password_hash=fast_password_hash("fixture-pass-123"),
            email_verified_at=now,
            user_role=role,
        )
        if membership_status is not None:
            user.membership = Membership(status=membership_status)
        users[name] = user
        database_session.add(user)
    await database_session.flush()
    accounts = {
        name: PointAccount(user_id=user.id)
        for name, user in users.items()
        if name in {"member", "other_member", "active_admin"}
    }
    database_session.add_all(accounts.values())
    await database_session.commit()
    async with api_test_context(database_session, [cooperative_router]) as client:
        yield {
            "client": client,
            "session": database_session,
            "users": users,
            "accounts": accounts,
        }


async def add_order(context, user_name, channel, number):
    user = context["users"][user_name]
    order = Order(
        order_number=number,
        order_kind=OrderKind.GROUP if channel == SalesChannel.GROUP else OrderKind.REGULAR,
        sales_channel=channel,
        user_id=user.id,
        membership_type_snapshot=MembershipType.MEMBER,
        amount_total=2000,
        contact_email=user.email,
        payment_status=PaymentStatus.PAID,
    )
    context["session"].add(order)
    await context["session"].flush()
    return order


async def add_refund(context, order):
    refund = Refund(
        order_id=order.id,
        amount=400,
        status=RefundStatus.COMPLETED,
        reason="測試退款",
        requested_by_id=context["users"]["active_admin"].id,
    )
    context["session"].add(refund)
    await context["session"].flush()
    return refund


def add_points(context, amount, source_type, reference_id=None, *, owner="member", **kwargs):
    transaction = PointTransaction(
        account_id=context["accounts"][owner].id,
        amount=amount,
        source_type=source_type,
        reference_id=reference_id,
        **kwargs,
    )
    context["session"].add(transaction)
    return transaction


@pytest.mark.parametrize("channel", list(SalesChannel))
@pytest.mark.asyncio
async def test_purchase_and_refund_link_to_owned_order_and_preserve_ledger(point_context, channel):
    context = point_context
    session = context["session"]
    order = await add_order(context, "member", channel, "MY-ORDER")
    refund = await add_refund(context, order)
    now = datetime.now(timezone.utc)
    purchase = add_points(
        context, 20, PointSourceType.PURCHASE, order.id,
        note="消費累積", created_at=now - timedelta(days=1),
    )
    deduction = add_points(
        context, -4, PointSourceType.REFUND, refund.id,
        note="退款扣回", created_at=now,
    )
    add_points(context, 900, PointSourceType.ADMIN_ADJUSTMENT, owner="other_member")
    await session.commit()

    response = await context["client"].get(
        "/v1/me/points", headers=auth_headers(context["users"]["member"])
    )

    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == 16
    assert [item["id"] for item in body["transactions"]] == [deduction.id, purchase.id]
    source_order = {"id": order.id, "order_number": "MY-ORDER", "sales_channel": channel.value}
    for item, expected in zip(body["transactions"], [deduction, purchase]):
        assert item["source_order"] == source_order
        assert item["amount"] == expected.amount
        assert item["source_type"] == expected.source_type.value
        assert item["reference_id"] == expected.reference_id
        assert item["note"] == expected.note
        assert datetime.fromisoformat(item["created_at"]).replace(tzinfo=None) == expected.created_at.replace(tzinfo=None)
    assert await session.scalar(select(func.count(PointTransaction.id))) == 3


@pytest.mark.parametrize("reader", ["member", "active_admin"])
@pytest.mark.asyncio
async def test_source_order_never_exposes_another_users_order_even_to_admin(point_context, reader):
    context = point_context
    own_order = await add_order(context, reader, SalesChannel.REGULAR, "OWN-ORDER")
    foreign_order = await add_order(context, "other_member", SalesChannel.MEAL_PREORDER, "PRIVATE-MEAL-ORDER")
    foreign_refund = await add_refund(context, foreign_order)
    own = add_points(context, 20, PointSourceType.PURCHASE, own_order.id, owner=reader)
    foreign_purchase = add_points(context, 11, PointSourceType.PURCHASE, foreign_order.id, owner=reader)
    foreign_deduction = add_points(context, -3, PointSourceType.REFUND, foreign_refund.id, owner=reader)
    await context["session"].commit()

    response = await context["client"].get(
        "/v1/me/points", headers=auth_headers(context["users"][reader])
    )

    assert response.status_code == 200
    body = response.json()
    transactions = {item["id"]: item for item in body["transactions"]}
    assert body["balance"] == 28
    assert transactions[own.id]["source_order"]["id"] == own_order.id
    assert transactions[foreign_purchase.id]["source_order"] is None
    assert transactions[foreign_deduction.id]["source_order"] is None
    assert foreign_order.order_number not in response.text


@pytest.mark.asyncio
async def test_missing_mismatched_and_nonpurchase_references_have_no_order_link(point_context):
    context = point_context
    order = await add_order(context, "member", SalesChannel.REGULAR, "DO-NOT-INFER")
    refund = await add_refund(context, order)
    for source, reference in [
        (PointSourceType.PURCHASE, None),
        (PointSourceType.PURCHASE, "missing-order"),
        (PointSourceType.PURCHASE, refund.id),
        (PointSourceType.REFUND, None),
        (PointSourceType.REFUND, "missing-refund"),
        (PointSourceType.REFUND, order.id),
        (PointSourceType.ADMIN_ADJUSTMENT, order.id),
        (PointSourceType.WISH_LAUNCHED, order.id),
        (PointSourceType.ACTIVITY, order.id),
        (PointSourceType.VOTE, order.id),
    ]:
        add_points(context, 1, source, reference, note=f"相關訂單 {order.order_number}")
    await context["session"].commit()

    response = await context["client"].get(
        "/v1/me/points", headers=auth_headers(context["users"]["member"])
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 10
    assert len(response.json()["transactions"]) == 10
    assert all(item["source_order"] is None for item in response.json()["transactions"])


@pytest.mark.asyncio
async def test_membership_refund_has_no_order_link(point_context):
    context = point_context
    session = context["session"]
    member = context["users"]["member"]
    application = MembershipApplication(user_id=member.id)
    schedule = MembershipFeeSchedule(
        charge_kind=MembershipChargeKind.ADMISSION_FEE,
        amount=1000,
        effective_from=date(2026, 1, 1),
    )
    session.add_all([application, schedule])
    await session.flush()
    charge = MembershipCharge(
        user_id=member.id,
        application_id=application.id,
        membership_id=member.membership.id,
        fee_schedule_id=schedule.id,
        charge_kind=MembershipChargeKind.ADMISSION_FEE,
        amount=1000,
    )
    session.add(charge)
    await session.flush()
    refund = Refund(
        membership_charge_id=charge.id,
        amount=1000,
        reason="入社費退款",
        requested_by_id=context["users"]["active_admin"].id,
    )
    session.add(refund)
    await session.flush()
    add_points(context, -10, PointSourceType.REFUND, refund.id)
    await session.commit()

    response = await context["client"].get("/v1/me/points", headers=auth_headers(member))

    assert response.status_code == 200
    assert response.json()["balance"] == -10
    assert response.json()["transactions"][0]["source_order"] is None


@pytest.mark.asyncio
async def test_order_sources_are_batched_without_multiplying_points(point_context):
    context = point_context
    for index in range(25):
        order = await add_order(context, "member", SalesChannel.REGULAR, f"BATCH-{index}")
        refund = await add_refund(context, order)
        add_points(context, 20, PointSourceType.PURCHASE, order.id)
        add_points(context, -4, PointSourceType.REFUND, refund.id)
    await context["session"].commit()
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select") and ("from orders" in normalized or "from refunds" in normalized):
            statements.append(statement)

    engine = context["session"].bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await context["client"].get(
            "/v1/me/points", headers=auth_headers(context["users"]["member"])
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200
    assert response.json()["balance"] == 400
    assert len(response.json()["transactions"]) == 50
    assert all(item["source_order"] is not None for item in response.json()["transactions"])
    assert len(statements) == 2


@pytest.mark.parametrize("endpoint", ["points", "badges"])
@pytest.mark.asyncio
async def test_member_details_require_login(point_context, endpoint):
    response = await point_context["client"].get(f"/v1/me/{endpoint}")
    assert response.status_code == 401


@pytest.mark.parametrize("endpoint", ["points", "badges"])
@pytest.mark.parametrize("reader", ["customer", "trainee", "suspended", "resigned", "admin_without_membership"])
@pytest.mark.asyncio
async def test_member_details_require_active_membership(point_context, endpoint, reader):
    response = await point_context["client"].get(
        f"/v1/me/{endpoint}", headers=auth_headers(point_context["users"][reader])
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_empty_member_details_preserve_empty_contract(point_context):
    context = point_context
    headers = auth_headers(context["users"]["member"])

    points = await context["client"].get("/v1/me/points", headers=headers)
    badges = await context["client"].get("/v1/me/badges", headers=headers)

    assert points.status_code == 200
    assert points.json() == {"balance": 0, "transactions": []}
    assert badges.status_code == 200
    assert badges.json() == []


@pytest.mark.asyncio
async def test_repeated_badge_read_keeps_existing_grant_date_and_uniqueness(point_context):
    context = point_context
    add_points(context, 120, PointSourceType.ADMIN_ADJUSTMENT)
    add_points(context, 120, PointSourceType.ADMIN_ADJUSTMENT, owner="other_member")
    await context["session"].commit()
    headers = auth_headers(context["users"]["member"])

    first = await context["client"].get("/v1/me/badges", headers=headers)
    second = await context["client"].get("/v1/me/badges", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()) == 1
    badge = first.json()[0]
    assert badge["key"] == "points_100"
    assert badge["name"] == "合作新芽"
    assert badge["description"] == "累積 100 點"
    assert datetime.fromisoformat(badge["granted_at"])
    assert await context["session"].scalar(select(func.count(MemberBadge.id))) == 1


@pytest.mark.asyncio
async def test_database_naive_utc_dates_have_explicit_offset_in_api(point_context):
    context = point_context
    session = context["session"]
    naive_utc = datetime(2026, 10, 25, 0, 30)
    add_points(context, 120, PointSourceType.ADMIN_ADJUSTMENT, created_at=naive_utc)
    badge = BadgeDefinition(key="points_100", name="合作新芽", description="累積 100 點")
    session.add(badge)
    await session.flush()
    session.add(MemberBadge(
        user_id=context["users"]["member"].id, badge_id=badge.id, granted_at=naive_utc
    ))
    await session.commit()
    headers = auth_headers(context["users"]["member"])
    session.expire_all()

    points = await context["client"].get("/v1/me/points", headers=headers)
    badges = await context["client"].get("/v1/me/badges", headers=headers)

    assert points.status_code == badges.status_code == 200
    expected = naive_utc.replace(tzinfo=timezone.utc)
    assert datetime.fromisoformat(points.json()["transactions"][0]["created_at"]) == expected
    assert datetime.fromisoformat(badges.json()[0]["granted_at"]) == expected
