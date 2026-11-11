import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.database import Base, create_engine, get_session
from app.integrations.invoice_service import invoice_request_from_order
from app.member_claims import roster_aad
from app.models import (
    AdminAudit, FulfillmentMethod, InventoryReservation, Meal, MemberProfile, MemberRosterEntry, Membership,
    MembershipStatus, Order, OrderItem, OrderKind, OutboxEvent, PaymentAttempt, PaymentStatus, PointAccount,
    PointTransaction, Product, TaxType, User, UserRole, WalkInRequestGuard, WalkInSale,
)
from app.routers import walk_in_sales
from app.routers.cooperative import cooperative_router
from app.routers.orders import load_order
from tests.support import auth_headers, make_test_settings
from tests.test_receipt_context import configure_test_cipher
from tests.test_walk_in_sales import payload, post, walk_in_context


PHONE = "0900123456"


async def seed_member(session, user, settings, status=MembershipStatus.ACTIVE, *, with_profile=True):
    membership = Membership(
        user_id=user.id, status=status,
        member_number="SLF-001" if status == MembershipStatus.ACTIVE else None,
        trainee_number="TR-001" if status == MembershipStatus.TRAINEE else None,
    )
    session.add(membership)
    cipher = configure_test_cipher(settings)
    if with_profile:
        session.add(MemberProfile(
            user_id=user.id, phone_encrypted=cipher.encrypt_text(PHONE, associated_data=f"member-profile:{user.id}"),
            legal_name_encrypted="unused", birth_date_encrypted="unused", address_encrypted="unused",
            emergency_contact_encrypted="unused", consent_version="test", consented_at=datetime.now(timezone.utc),
        ))
    product = Product(
        name="同名品項", slug=f"counter-{uuid4().hex}", category="合成分類", unit="盒",
        member_price=101, nonmember_price=150, stock_quantity=9, tax_type=TaxType.TAXABLE,
    )
    session.add(product)
    await session.commit()
    return membership, product


def quote_payload(membership, item, *, sale_category="product", invoice_buyer_type="personal"):
    return {
        "member": {"member_number": membership.member_number or membership.trainee_number, "phone": PHONE},
        "sale_category": sale_category,
        "catalog_id": item.id,
        "invoice_buyer_type": invoice_buyer_type,
    }


def collection_payload(quote_request, quote):
    preferences = {"invoice_buyer_type": quote_request["invoice_buyer_type"]}
    if preferences["invoice_buyer_type"] == "company":
        preferences.update(invoice_buyer_tax_id="15989995", invoice_buyer_name="合成公司")
    return payload(
        member=quote_request["member"], sale_category=quote_request["sale_category"],
        catalog_id=quote_request["catalog_id"], preferences=preferences, quantity=1,
        product_name=quote["product_name"], unit_price=quote["unit_price"], tax_type=quote["tax_type"],
    )


async def read_quote(c, body, actor=None):
    return await c.client.post("/v1/admin/walk-in-sales/member-quote", json=body, headers=auth_headers(actor or c.admin))


@pytest.mark.parametrize(
    "status,buyer_type,sale_category,unit_price,tax_type,tax_amount,points",
    [
        (MembershipStatus.ACTIVE, "personal", "product", 101, "tax_exempt", 0, 1),
        (MembershipStatus.ACTIVE, "company", "product", 101, "taxable", 5, 1),
        (MembershipStatus.TRAINEE, "personal", "product", 101, "taxable", 4, 0),
        (MembershipStatus.ACTIVE, "personal", "meal", 129, "tax_exempt", 0, 1),
    ],
)
async def test_verified_sale_uses_current_price_and_tax_without_creating_website_order(
    walk_in_context, status, buyer_type, sale_category, unit_price, tax_type, tax_amount, points,
):
    c = walk_in_context
    membership, product = await seed_member(c.session, c.customer, c.settings, status)
    item = product
    if sale_category == "meal":
        item = Meal(name="同名品項", slug="counter-meal", price=129, tax_type=TaxType.TAXABLE)
        c.session.add(item)
        await c.session.commit()
    request = quote_payload(membership, item, sale_category=sale_category, invoice_buyer_type=buyer_type)
    request["member"]["phone"] = "+886 900-123-456"
    response = await read_quote(c, request)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    quote = response.json()
    expected_membership = "member" if status == MembershipStatus.ACTIVE else "trainee"
    assert quote == {
        "product_name": item.name, "unit_price": unit_price, "tax_type": tax_type,
        "member_tax_exempt": tax_type == "tax_exempt", "membership_type": expected_membership,
    }
    assert await c.session.scalar(select(func.count(Order.id))) == 0

    body = collection_payload(request, quote)
    result = await post(c, body)
    assert result.status_code == 201, result.text
    order = await c.session.get(Order, result.json()["order_id"])
    line = await c.session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
    assert order.user_id is None and order.customer_kind == "walk_in"
    assert order.verified_counter_membership_id == membership.id
    assert order.membership_type_snapshot.value == expected_membership
    assert order.member_tax_exempt_snapshot is quote["member_tax_exempt"]
    assert order.amount_total == unit_price and order.tax_amount == tax_amount
    assert line.unit_price == unit_price and line.tax_type.value == tax_type
    assert line.source_product_id is None and line.source_meal_offering_id is None
    assert product.stock_quantity == 9
    assert await c.session.scalar(select(func.count(InventoryReservation.id))) == 0
    assert await c.session.scalar(select(func.count(OutboxEvent.id))) == 1
    invoice_request = invoice_request_from_order(await load_order(c.session, order.id), "TEST-COUNTER")
    assert invoice_request.items[0].tax_type == ("3" if tax_type == "tax_exempt" else "1")
    assert invoice_request.customer_email == body["contact_email"]
    assert invoice_request.customer_id == order.id.replace("-", "")[:20]
    assert (await c.client.get("/v1/orders", headers=auth_headers(c.customer))).json() == []
    assert (await c.client.get(f"/v1/orders/{order.id}", headers=auth_headers(c.customer))).status_code == 404
    audit = await c.session.scalar(select(AdminAudit).where(AdminAudit.action == "walk_in_sale.collected"))
    assert audit.data["catalog_id"] == item.id and audit.data["verified_membership_id"] == membership.id
    assert PHONE not in str(audit.data) and not any("phone" in name for name in audit.data)

    ledger = (await c.session.execute(select(PointAccount.user_id, PointTransaction.amount)
        .join(PointTransaction, PointTransaction.account_id == PointAccount.id))).all()
    assert ledger == ([(c.customer.id, points)] if points else [])

    membership.status = MembershipStatus.SUSPENDED
    await c.session.commit()
    retry = await post(c, {**body, "member": {**body["member"], "phone": "0911111111"}})
    assert retry.status_code == 200 and retry.json() == result.json()
    conflict = await post(c, {**body, "unit_price": unit_price + 1})
    assert conflict.status_code == 409 and "x-error-code" not in conflict.headers
    assert await c.session.scalar(select(func.count(PaymentAttempt.id))) == 1


async def test_member_quote_rejects_stale_identity_and_same_name_catalog_changes_before_collection(walk_in_context):
    c = walk_in_context
    membership, product = await seed_member(c.session, c.customer, c.settings)
    request = quote_payload(membership, product)
    assert (await read_quote(c, request, c.customer)).status_code == 403
    quote = (await read_quote(c, request)).json()
    body = collection_payload(request, quote)
    for changed in [
        {"member": {**body["member"], "phone": "0911111111"}},
        {"catalog_id": str(uuid4())},
        {"product_name": "其他名稱"},
        {"tax_type": "taxable"},
    ]:
        response = await post(c, {**body, **changed, "client_request_id": str(uuid4())})
        assert response.status_code == 409 and response.headers["x-error-code"] == "walk_in_member_quote_stale"
    c.session.add(Product(
        name=product.name, slug="different-counter-product", category="合成分類", unit="包",
        member_price=119, nonmember_price=150, stock_quantity=2, tax_type=TaxType.TAXABLE,
    ))
    await c.session.commit()
    different = await c.session.scalar(select(Product).where(Product.slug == "different-counter-product"))
    response = await post(c, {**body, "catalog_id": different.id, "client_request_id": str(uuid4())})
    assert response.status_code == 409 and response.headers["x-error-code"] == "walk_in_member_quote_stale"
    assert (await read_quote(c, {**request, "catalog_id": different.id})).json()["unit_price"] == 119

    product.member_price += 1
    await c.session.commit()
    response = await post(c, body)
    assert response.status_code == 409 and response.headers["x-error-code"] == "walk_in_member_quote_stale"
    membership.status = MembershipStatus.SUSPENDED
    await c.session.commit()
    response = await post(c, {**body, "unit_price": product.member_price, "client_request_id": str(uuid4())})
    assert response.status_code == 409 and response.headers["x-error-code"] == "walk_in_member_quote_stale"
    for model in (Order, PaymentAttempt, PointTransaction, WalkInSale, OutboxEvent, AdminAudit):
        assert await c.session.scalar(select(func.count()).select_from(model)) == 0


async def test_validation_errors_exclude_raw_phone_and_body_context(walk_in_context):
    c = walk_in_context
    membership, product = await seed_member(c.session, c.customer, c.settings)
    request = quote_payload(membership, product)
    private_input = "private-invalid-phone"
    invalid = await read_quote(c, {**request, "member": {**request["member"], "phone": private_input}})
    quote = (await read_quote(c, request)).json()
    invalid_collection = await post(c, {**collection_payload(request, quote), "confirm_received": False})
    for response in (invalid, invalid_collection):
        assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
        assert PHONE not in response.text and private_input not in response.text
        assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    assert await c.session.scalar(select(func.count(Order.id))) == 0


async def test_quote_uses_claimed_roster_only_when_member_profile_is_missing(walk_in_context):
    c = walk_in_context
    membership, product = await seed_member(c.session, c.customer, c.settings, with_profile=False)
    request = quote_payload(membership, product)
    cipher = configure_test_cipher(c.settings)
    roster_id = str(uuid4())
    roster = MemberRosterEntry(
        id=roster_id, member_number=membership.member_number, phone_encrypted=cipher.encrypt_text(PHONE, associated_data=roster_aad(roster_id)),
        legal_name_encrypted="unused", email_encrypted="unused",
    )
    c.session.add(roster)
    await c.session.commit()
    assert (await read_quote(c, request)).status_code == 409
    roster.claimed_user_id = c.customer.id
    await c.session.commit()
    assert (await read_quote(c, request)).status_code == 200
    roster.is_active = False
    await c.session.commit()
    assert (await read_quote(c, request)).status_code == 409


async def test_counter_member_surplus_and_point_reversal_use_verified_owner(walk_in_context):
    c = walk_in_context
    membership, product = await seed_member(c.session, c.customer, c.settings)
    c.client._transport.app.include_router(cooperative_router)
    request = quote_payload(membership, product)
    body = collection_payload(request, (await read_quote(c, request)).json())
    result = await post(c, body)
    order_id = result.json()["order_id"]
    day = datetime.now(timezone.utc).date().isoformat()
    finance = {"label": "合成現場", "starts_on": day, "ends_on": day, "total_cost": 0}
    c.session.add(Order(
        order_number="SYNTHETIC-ONLINE", user_id=c.customer.id, order_kind=OrderKind.REGULAR,
        membership_type_snapshot="member", fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        amount_total=100, contact_email=c.customer.email, payment_status=PaymentStatus.PAID, paid_at=datetime.now(timezone.utc),
    ))
    await c.session.commit()

    point_response = await c.client.get("/v1/me/points", headers=auth_headers(c.customer))
    assert point_response.status_code == 200, point_response.text
    assert point_response.json()["balance"] == 1
    assert point_response.json()["transactions"][0]["source_order"] is None
    dry_run = await c.client.post("/v1/admin/surplus/dry-run", json=finance, headers=auth_headers(c.admin))
    assert dry_run.status_code == 200, dry_run.text
    assert [(row["member_id"], row["contribution_amount"]) for row in dry_run.json()["distributions"]] == [(c.customer.id, 201)]

    refund = {"amount": 101, "confirm_returned": True}
    for _ in range(2):
        response = await c.client.post(f"/v1/admin/walk-in-sales/{order_id}/refund", json=refund, headers=auth_headers(c.admin))
        assert response.status_code == 200 and response.json()["payment_status"] == "refunded"
    point_response = await c.client.get("/v1/me/points", headers=auth_headers(c.customer))
    assert point_response.json()["balance"] == 0
    assert sorted(item["amount"] for item in point_response.json()["transactions"]) == [-1, 1]
    assert all(item["source_order"] is None for item in point_response.json()["transactions"])
    after = await c.client.post("/v1/admin/surplus/dry-run", json=finance, headers=auth_headers(c.admin))
    assert after.json()["distributions"][0]["contribution_amount"] == 100

    for changes in [
        {"user_id": c.customer.id},
        {"verified_counter_membership_id": None},
        {"member_tax_exempt_snapshot": False},
    ]:
        with pytest.raises(IntegrityError):
            async with c.session.begin_nested():
                await c.session.execute(update(Order).where(Order.id == order_id).values(**changes))


async def check_concurrent_requote_fence(sessions, monkeypatch, stale_first):
    settings = make_test_settings()
    async with sessions() as session:
        admin = User(email="counter-guard-admin@example.com", display_name="合成管理員", password_hash="test", user_role=UserRole.ADMIN)
        member = User(email="counter-guard-member@example.com", display_name="合成社員", password_hash="test")
        session.add_all([admin, member])
        await session.flush()
        membership, product = await seed_member(session, member, settings)
        body = collection_payload(quote_payload(membership, product), {
            "product_name": product.name, "unit_price": product.member_price, "tax_type": "tax_exempt",
        })
        headers = auth_headers(admin)

    entered_verification = asyncio.Event()
    release_first = asyncio.Event()
    second_lock_attempt = asyncio.Event()
    original_quote, original_lock = walk_in_sales.counter_member_quote, walk_in_sales._lock_request
    lock_count = 0

    async def hold_first_quote(*args, **kwargs):
        if not entered_verification.is_set():
            entered_verification.set()
            await release_first.wait()
        return await original_quote(*args, **kwargs)

    async def observe_lock(session, request_id):
        nonlocal lock_count
        lock_count += 1
        if lock_count == 2:
            second_lock_attempt.set()
        return await original_lock(session, request_id)

    monkeypatch.setattr(walk_in_sales, "counter_member_quote", hold_first_quote)
    monkeypatch.setattr(walk_in_sales, "_lock_request", observe_lock)
    app = FastAPI()
    app.include_router(walk_in_sales.walk_in_sales_router)

    async def request_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_settings] = lambda: settings
    stale = {**body, "unit_price": body["unit_price"] + 1}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def submit(data):
            return await client.post("/v1/admin/walk-in-sales", json=data, headers=headers)

        first = asyncio.create_task(submit(stale if stale_first else body))
        try:
            await asyncio.wait_for(entered_verification.wait(), 10)
            second = asyncio.create_task(submit(body if stale_first else stale))
            await asyncio.wait_for(second_lock_attempt.wait(), 10)
        finally:
            release_first.set()
        result, contender = await asyncio.wait_for(asyncio.gather(first, second), 15)
        if stale_first:
            for response in (result, contender):
                assert response.status_code == 409 and response.headers["x-error-code"] == "walk_in_member_quote_stale"
            guest = await submit(payload(client_request_id=body["client_request_id"]))
            assert guest.status_code == 409 and guest.headers["x-error-code"] == "walk_in_member_quote_stale"
            async with sessions() as session:
                assert await session.scalar(select(func.count(Order.id))) == 0
            assert (await submit({**body, "client_request_id": str(uuid4())})).status_code == 201
        else:
            assert result.status_code == 201
            assert contender.status_code == 409 and "x-error-code" not in contender.headers
            assert (await submit(body)).json() == result.json()
    async with sessions() as session:
        for model in (Order, WalkInSale, PaymentAttempt, OutboxEvent, PointTransaction):
            assert await session.scalar(select(func.count()).select_from(model)) == 1
        guards = (await session.scalars(select(WalkInRequestGuard))).all()
        assert [(row.request_id, row.retired) for row in guards] == ([(body["client_request_id"], True)] if stale_first else [])


@pytest.mark.parametrize("stale_first", [False, True], ids=["order-wins", "stale-quote-wins"])
async def test_concurrent_requote_fence_prevents_later_collection(tmp_path, monkeypatch, stale_first):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'counter-race.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await check_concurrent_requote_fence(async_sessionmaker(engine, expire_on_commit=False), monkeypatch, stale_first)
    finally:
        await engine.dispose()
