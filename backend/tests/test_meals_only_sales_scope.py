from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.auth import create_token
from app.config import get_settings
from app.integrations.payment_service import (
    PaymentApplicationError,
    create_membership_payment_attempt,
    create_payment_attempt,
    ensure_order_payment_runtime_enabled,
    ensure_payment_runtime_enabled,
)
from app.integrations.invoice_service import invoice_context_from_settings
from app.models import (
    FulfillmentMethod,
    Meal,
    MealEvent,
    MealEventStatus,
    MealEventOffering,
    Membership,
    MembershipApplication,
    MembershipApplicationStatus,
    MembershipCharge,
    MembershipChargeKind,
    MembershipDocument,
    MembershipDocumentStatus,
    MembershipDocumentType,
    MembershipFeeSchedule,
    MembershipType,
    Order,
    OrderItem,
    OrderFulfillment,
    OrderKind,
    PaymentAttempt,
    PaymentStatus,
    SalesChannel,
    TaxType,
    User,
    UserRole,
)
from app.routers import groups, logistics, membership, orders
from app.routers.payments import payments_router
from app.sales_scope import MEALS_ONLY_MESSAGE, SalesScopeError, ensure_sales_scope_allows
from tests.support import api_test_context, auth_headers, make_test_settings
from tests.test_payment_return import payment_return_context, payment_settings, successful_result
from tests.test_raygate import raygate_callback_context
from tests.test_meals_production_configuration import meal_production_settings


def meal_scope_settings(**overrides):
    return make_test_settings(sales_scope="meals_only", **overrides)


def meal_context(**overrides):
    return {
        "sales_channel": SalesChannel.MEAL_PREORDER,
        "fulfillment_method": FulfillmentMethod.EVENT_PICKUP,
        "meal_event_id": "real-meal-event",
        **overrides,
    }


def test_all_scope_keeps_existing_sales_behavior():
    ensure_sales_scope_allows(make_test_settings())


def test_meals_only_allows_complete_meal_context():
    ensure_sales_scope_allows(meal_scope_settings(), **meal_context())
    ensure_payment_runtime_enabled(meal_scope_settings(), **meal_context())


@pytest.mark.parametrize("context", [
    {},
    meal_context(sales_channel=SalesChannel.REGULAR),
    meal_context(sales_channel=SalesChannel.GROUP),
    meal_context(fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP),
    meal_context(fulfillment_method=FulfillmentMethod.ECPAY_LOGISTICS),
    meal_context(meal_event_id=None),
    meal_context(meal_event_id=""),
    meal_context(meal_event_id="meal-event-preorder-demo"),
    meal_context(meal_event_id="meal-event-dinner-demo"),
    meal_context(meal_event_id="meal-event-pickup-demo"),
])
def test_meals_only_rejects_nonmeal_or_incomplete_context(context):
    with pytest.raises(SalesScopeError, match="僅開放便當"):
        ensure_sales_scope_allows(meal_scope_settings(), **context)
    with pytest.raises(PaymentApplicationError, match="僅開放便當"):
        ensure_payment_runtime_enabled(meal_scope_settings(), **context)


def test_preview_acceptance_allowlist_cannot_bypass_meal_scope():
    settings = meal_scope_settings(
        environment="preview",
        raygate_payment_acceptance_order_id="existing-nonmeal",
    )
    with pytest.raises(PaymentApplicationError, match="僅開放便當"):
        ensure_payment_runtime_enabled(
            settings,
            order_id="existing-nonmeal",
            amount=10,
            fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,kwargs", [
    (orders.create_order, {"body": None}),
    (groups.join_campaign, {"campaign_id": "existing", "body": None}),
    (membership.save_my_application, {"body": None}),
    (membership.submit_my_application, {}),
    (membership.resubmit_supplement, {}),
    (membership.create_document_upload_url, {"body": None, "request": None, "storage": Mock()}),
    (membership.confirm_document_upload, {"document_id": "existing", "body": None, "storage": Mock()}),
    (logistics.create_logistics_selection, {"order_id": "existing", "body": None}),
    (logistics.reissue_logistics_selection_link, {"order_id": "existing"}),
])
async def test_blocked_write_routes_stop_before_database_or_provider(endpoint, kwargs):
    session = AsyncMock()
    with pytest.raises(HTTPException) as caught:
        await endpoint(
            **kwargs, user=Mock(), session=session, settings=meal_scope_settings()
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == MEALS_ONLY_MESSAGE
    assert session.mock_calls == []
    if "storage" in kwargs:
        assert kwargs["storage"].mock_calls == []


@pytest.mark.asyncio
async def test_stale_logistics_selection_page_cannot_create_provider_order():
    session = AsyncMock()
    with pytest.raises(HTTPException, match="僅開放便當"):
        await logistics.open_logistics_selection_page(
            token="existing-link", session=session, settings=meal_scope_settings()
        )
    assert session.mock_calls == []


@pytest.mark.asyncio
async def test_new_formal_logistics_order_stops_before_provider(monkeypatch):
    session = AsyncMock()
    order = SimpleNamespace(
        payment_status=PaymentStatus.PAID,
        order_kind=OrderKind.REGULAR,
        fulfillment_method=FulfillmentMethod.ECPAY_LOGISTICS,
        fulfillment=SimpleNamespace(
            method=FulfillmentMethod.ECPAY_LOGISTICS,
            shipment=SimpleNamespace(ecpay_logistics_id=None),
        ),
    )
    monkeypatch.setattr(logistics, "_load_order", AsyncMock(return_value=order))
    provider = Mock()
    monkeypatch.setattr(logistics, "ecpay_logistics_adapter_from_settings", provider)
    with pytest.raises(HTTPException, match="僅開放便當"):
        await logistics.create_formal_logistics_order(
            order_id="existing-paid-order", admin=Mock(),
            session=session, settings=meal_scope_settings(),
        )
    assert provider.mock_calls == []
    assert session.mock_calls == []


@pytest.mark.asyncio
async def test_membership_new_payment_stops_before_database_or_provider():
    session = AsyncMock()
    with pytest.raises(PaymentApplicationError, match="僅開放便當"):
        await create_membership_payment_attempt(
            session, "existing-charge", Mock(), meal_scope_settings()
        )
    assert session.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", [SalesChannel.REGULAR, SalesChannel.GROUP])
async def test_existing_nonmeal_order_cannot_create_new_payment(channel, database_session):
    buyer = User(email="scope@example.test", display_name="測試", password_hash="test")
    order = Order(
        order_number="EXISTING-NONMEAL",
        user=buyer,
        order_kind=OrderKind.GROUP if channel == SalesChannel.GROUP else OrderKind.REGULAR,
        sales_channel=channel,
        fulfillment_method=FulfillmentMethod.COOPERATIVE_PICKUP,
        membership_type_snapshot=MembershipType.NONMEMBER,
        amount_total=10,
        contact_email=buyer.email,
        items=[OrderItem(product_name="測試", unit_label="份", quantity=1,
                         unit_price=10, subtotal=10, tax_type=TaxType.TAXABLE)],
    )
    database_session.add(order)
    await database_session.commit()
    with pytest.raises(PaymentApplicationError, match="僅開放便當"):
        await create_payment_attempt(
            database_session, order.id, buyer, meal_scope_settings()
        )
    assert await database_session.scalar(select(func.count(PaymentAttempt.id))) == 0
    assert order.payment_status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_old_nonmeal_checkout_blocked_but_status_read_retained(payment_return_context):
    client, session, order, attempt = payment_return_context
    settings = payment_settings().model_copy(update={"sales_scope": "meals_only"})
    client._transport.app.dependency_overrides[get_settings] = lambda: settings
    checkout = await client.get(f"/payments/{attempt.id}/checkout")
    assert checkout.status_code == 409
    assert checkout.json()["detail"] == MEALS_ONLY_MESSAGE
    status = await client.get(
        f"/v1/payment-attempts/{attempt.id}", headers=auth_headers(order.user)
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "pending"
    assert attempt.status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_old_nonmeal_callback_and_self_cancel_refund_still_work(payment_return_context):
    client, session, order, attempt = payment_return_context
    settings = payment_settings().model_copy(update={"sales_scope": "meals_only", "environment": "production"})
    client._transport.app.dependency_overrides[get_settings] = lambda: settings
    client._transport.app.include_router(orders.orders_router)
    order.contact_email = "buyer@example.com"
    await session.commit()
    order_id = order.id
    headers = auth_headers(order.user)
    callback = await client.post("/webhooks/ecpay/payment", data=successful_result(attempt))
    assert callback.status_code == 200
    assert callback.text == "1|OK"
    await session.refresh(order)
    assert order.payment_status == PaymentStatus.PAID
    status = await client.get(f"/v1/orders/{order_id}", headers=headers)
    assert status.status_code == 200, status.text
    refund = await client.post(
        f"/v1/orders/{order_id}/cancel", headers=headers, json={"reason": "取消舊訂單"}
    )
    assert refund.status_code == 200, refund.text
    assert refund.json()["payment_status"] == "refund_pending"


@pytest.mark.asyncio
async def test_old_nonmeal_raygate_refresh_still_confirms_provider_query_result(raygate_callback_context):
    client, session, order, attempt, _adapter, _query_result = raygate_callback_context
    settings = client._transport.app.dependency_overrides[get_settings]().model_copy(
        update={"sales_scope": "meals_only", "environment": "production"}
    )
    client._transport.app.dependency_overrides[get_settings] = lambda: settings
    response = await client.post(
        f"/v1/payment-attempts/{attempt.id}/refresh", headers=auth_headers(order.user)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paid"
    await session.refresh(order)
    assert order.payment_status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_private_document_intake_blocked_without_initializing_disabled_storage(database_session):
    async with api_test_context(
        database_session, [membership.membership_router], settings=meal_scope_settings()
    ) as client:
        upload = await client.post("/v1/membership/documents/upload-url", json={
            "document_type": "id_front", "content_type": "application/pdf",
            "size_bytes": 100, "checksum_sha256": "a" * 64,
        })
        confirm = await client.post("/v1/membership/documents/existing/confirm", json={
            "checksum_sha256": "a" * 64,
        })
    assert upload.status_code == confirm.status_code == 409
    assert upload.json()["detail"] == confirm.json()["detail"] == MEALS_ONLY_MESSAGE


@pytest.fixture
async def complete_meal_order(database_session):
    now = datetime.now(timezone.utc)
    buyer = User(email="meal@example.com", display_name="便當測試", password_hash="test")
    meal = Meal(slug="scope-meal", name="測試便當", price=100)
    event = MealEvent(
        title="正式場次", location="合作社", created_by=buyer,
        status=MealEventStatus.PUBLISHED,
        ordering_starts_at=now, ordering_ends_at=now + timedelta(hours=1),
        pickup_starts_at=now + timedelta(hours=2), pickup_ends_at=now + timedelta(hours=3),
    )
    offering = MealEventOffering(event=event, meal=meal, price=100, capacity=10)
    database_session.add(offering)
    await database_session.flush()
    order = Order(
        order_number="MEAL-SCOPE-VALID", user=buyer, meal_event=event,
        invoice_provider_context=invoice_context_from_settings(meal_production_settings()),
        order_kind=OrderKind.REGULAR, sales_channel=SalesChannel.MEAL_PREORDER,
        fulfillment_method=FulfillmentMethod.EVENT_PICKUP,
        membership_type_snapshot=MembershipType.NONMEMBER, amount_total=100,
        contact_email=buyer.email,
        fulfillment=OrderFulfillment(method=FulfillmentMethod.EVENT_PICKUP),
        items=[OrderItem(
            source_meal_offering_id=offering.id, product_name="測試便當", unit_label="份",
            quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE,
        )],
    )
    database_session.add(order)
    await database_session.commit()
    return order


@pytest.mark.asyncio
async def test_valid_meal_order_db_context_can_start_payment(database_session, complete_meal_order):
    await ensure_order_payment_runtime_enabled(
        database_session, meal_scope_settings(), complete_meal_order
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", [
    "group_kind", "group_campaign", "no_items", "missing_offering", "product_source",
    "bundle_source", "missing_event", "cross_event_offering", "missing_pickup", "wrong_pickup",
])
async def test_forged_or_incomplete_meal_order_cannot_start_payment(
    database_session, complete_meal_order, corruption
):
    order = complete_meal_order
    if corruption == "group_kind":
        order.order_kind = OrderKind.GROUP
    elif corruption == "group_campaign":
        order.group_campaign_id = "unrelated-group"
    elif corruption == "no_items":
        order.items = []
    elif corruption == "missing_offering":
        order.items[0].source_meal_offering_id = None
    elif corruption == "product_source":
        order.items[0].source_product_id = "unrelated-product"
    elif corruption == "bundle_source":
        order.items[0].source_bundle_id = "unrelated-bundle"
    elif corruption == "missing_event":
        order.meal_event_id = "nonexistent-event"
    elif corruption == "cross_event_offering":
        now = datetime.now(timezone.utc)
        other = MealEvent(
            title="其他場次", location="其他地點", created_by_id=order.user_id,
            ordering_starts_at=now, ordering_ends_at=now + timedelta(hours=1),
            pickup_starts_at=now + timedelta(hours=2), pickup_ends_at=now + timedelta(hours=3),
        )
        database_session.add(other)
        await database_session.flush()
        order.meal_event_id = other.id
    elif corruption == "missing_pickup":
        order.fulfillment = None
    elif corruption == "wrong_pickup":
        order.fulfillment.method = FulfillmentMethod.ECPAY_LOGISTICS
    await database_session.flush()
    with pytest.raises(PaymentApplicationError, match="資料不完整"):
        await ensure_order_payment_runtime_enabled(database_session, meal_scope_settings(), order)
    assert await database_session.scalar(select(func.count(PaymentAttempt.id))) == 0


@pytest.mark.asyncio
async def test_production_matching_invoice_context_allows_attempt_and_checkout(
    database_session, complete_meal_order
):
    order = complete_meal_order
    settings = meal_production_settings()
    attempt = await create_payment_attempt(database_session, order.id, order.user, settings)
    assert attempt.status == PaymentStatus.PENDING
    assert order.invoice_provider_context == invoice_context_from_settings(settings)
    async with api_test_context(database_session, [payments_router], settings=settings) as client:
        checkout = await client.get(f"/payments/{attempt.id}/checkout")
    assert checkout.status_code == 303
    assert checkout.headers["location"].startswith("https://pay.example.test/calc/pay_encrypt/")


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [
    None, {},
    {"base_url": "https://webtest.einvoice.com.tw/einv"},
    {"base_url": "https://web2.einvoice.com.tw/einv"},
    {"company_id": "87654321"}, {"seller_id": "87654321"}, {"user_id": "OTHERADMIN"},
    {"provider": "ecpay"}, {"version": 2},
])
@pytest.mark.parametrize("entrypoint", ["new_attempt", "existing_checkout"])
async def test_production_stale_invoice_context_blocks_new_charge_without_rebinding(
    database_session, complete_meal_order, mismatch, entrypoint, monkeypatch
):
    import app.integrations.payment_service as payment_service

    order = complete_meal_order
    settings = meal_production_settings()
    stored_context = (
        {**invoice_context_from_settings(settings), **mismatch} if mismatch else mismatch
    )
    order.invoice_provider_context = stored_context
    order_id = order.id
    provider = Mock(side_effect=AssertionError("blocked order must not prepare payment"))
    monkeypatch.setattr(payment_service, "payment_adapter_from_settings", provider)
    await database_session.commit()
    if entrypoint == "new_attempt":
        with pytest.raises(PaymentApplicationError, match="請重新訂購便當"):
            await create_payment_attempt(database_session, order.id, order.user, settings)
        assert await database_session.scalar(select(func.count(PaymentAttempt.id))) == 0
    else:
        attempt = PaymentAttempt(
            order=order, provider="raygate", merchant_trade_no="OLD-MEAL-PAYMENT",
            amount=100, status=PaymentStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            checkout_payload={"redirect_url": "https://pay.example.test/calc/pay_encrypt/old"},
        )
        database_session.add(attempt)
        await database_session.commit()
        async with api_test_context(database_session, [payments_router], settings=settings) as client:
            checkout = await client.get(f"/payments/{attempt.id}/checkout")
        assert checkout.status_code == 409
        assert "請重新訂購便當" in checkout.json()["detail"]
        assert attempt.status == PaymentStatus.PENDING
    await database_session.refresh(order)
    assert order.id == order_id
    assert order.invoice_provider_context == stored_context
    assert order.payment_status == PaymentStatus.PENDING
    assert provider.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["meals_only", "all"])
async def test_membership_approval_cannot_create_new_charges_during_meal_launch(database_session, scope):
    admin = User(email="admin-scope@example.com", display_name="管理員", password_hash="test", user_role=UserRole.ADMIN)
    applicant = User(email="applicant-scope@example.com", display_name="申請人", password_hash="test")
    application = MembershipApplication(
        user=applicant, status=MembershipApplicationStatus.SUBMITTED,
        documents=[MembershipDocument(
            document_type=kind, status=MembershipDocumentStatus.CONFIRMED,
            object_key=f"scope-test/{kind.value}", content_type="application/pdf", size_bytes=100,
        ) for kind in MembershipDocumentType],
    )
    database_session.add_all([admin, application])
    database_session.add_all([
        MembershipFeeSchedule(charge_kind=kind, amount=10, effective_from=date(2020, 1, 1))
        for kind in MembershipChargeKind
    ])
    await database_session.commit()
    settings = meal_production_settings() if scope == "meals_only" else make_test_settings()
    token = create_token(admin, "access", timedelta(minutes=5), settings=settings)
    async with api_test_context(database_session, [membership.membership_router], settings=settings) as client:
        result = await client.post(
            f"/v1/admin/membership-applications/{application.id}/approve",
            headers={"Authorization": f"Bearer {token}"}, json={"reason": "離線核准驗證"},
        )
    await database_session.refresh(application)
    charge_count = await database_session.scalar(select(func.count(MembershipCharge.id)))
    membership_count = await database_session.scalar(select(func.count(Membership.id)))
    if scope == "meals_only":
        assert result.status_code == 409
        assert result.json()["detail"] == MEALS_ONLY_MESSAGE
        assert charge_count == membership_count == 0
        assert application.status == MembershipApplicationStatus.SUBMITTED
        assert application.reviewed_at is None
        assert application.reviewed_by_id is None
    else:
        assert result.status_code == 200, result.text
        assert charge_count == 2
        assert membership_count == 1
        assert application.status == MembershipApplicationStatus.APPROVED
