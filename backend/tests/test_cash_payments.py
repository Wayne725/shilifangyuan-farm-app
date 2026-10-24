from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import time_machine
from sqlalchemy import func, select

from app.models import (
    AdminAudit, InvoiceStatus, Meal, MealEvent, MealEventOffering, MealEventStatus,
    MembershipType, Order, OutboxEvent, PaymentAttempt, PaymentStatus, PointTransaction,
    Refund, RefundStatus, User, UserRole,
)
from app.routers.meals import meals_router
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from app.jobs import _reconcile_expired_cash_orders, _reconcile_expired_payments
from app.meal_finance import MealSalesFilters, _receipts
from tests.support import api_test_context, auth_headers, make_test_settings, tomorrow_meal_timeline


@pytest.fixture
async def cash_context(database_session):
    session = database_session
    admin = User(email="onsite-admin@example.com", display_name="收款員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="onsite-buyer@example.com", display_name="買家", password_hash="test")
    meal = Meal(slug="onsite-lunch", name="現場付款便當", description="測試", price=120)
    session.add_all([admin, buyer, meal])
    await session.flush()
    event = MealEvent(title="現場付款測試", location="合作社", allow_cash_payment=True,
        **tomorrow_meal_timeline(datetime.now(timezone.utc)),
        status=MealEventStatus.PUBLISHED, created_by_id=admin.id,
        offerings=[MealEventOffering(meal=meal, price=120, capacity=2)])
    session.add(event)
    await session.commit()
    settings = make_test_settings()
    async with api_test_context(session, [meals_router, orders_router, payments_router], settings=settings) as client:
        yield SimpleNamespace(client=client, session=session, admin=admin, buyer=buyer, event=event, settings=settings)


async def reload(c):
    for entity in (c.admin, c.buyer, c.event):
        await c.session.refresh(entity)
    await c.session.refresh(c.event, ["offerings"])


async def create_cash(c, **extra):
    await reload(c)
    response = await c.client.post(f"/v1/meal-events/{c.event.id}/orders", headers=auth_headers(c.buyer), json={
        "items": [{"offering_id": c.event.offerings[0].id, "quantity": 1}],
        "contact_email": c.buyer.email, "payment_method": "cash_on_pickup", **extra,
    })
    await reload(c)
    return response


async def action(c, order_id, operation="cash-payment", actor=None, **extra):
    await reload(c)
    response = await c.client.post(f"/v1/orders/{order_id}/admin/{operation}", headers=auth_headers(actor or c.admin), json={"amount": 120, "reason": "已核對款項", **extra})
    await reload(c)
    return response


async def count(c, model):
    return await c.session.scalar(select(func.count(model.id)))


async def received_total(c):
    day = datetime.now(timezone.utc).date()
    receipts = _receipts(MealSalesFilters(starts_on=day, ends_on=day, sales_channel="all", order_category="all"))
    return await c.session.scalar(select(func.coalesce(func.sum(receipts.c.amount), 0)))


async def approve_cancellation(c, order_id):
    approved = await c.client.post(f"/v1/admin/meal-orders/{order_id}/cancel", headers=auth_headers(c.admin), json={"reason": "電話取消預訂"})
    assert approved.status_code == 200, approved.text


@pytest.mark.asyncio
async def test_pending_order_reserves_meal_without_revenue_or_online_payment(cash_context):
    c = cash_context
    response = await create_cash(c)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["payment_method"] == "cash_on_pickup" and order["payment_status"] == "pending"
    assert order["invoice_status"] == "external" and order["pickup_code"] is None
    assert "pay" not in order["available_actions"]
    assert c.event.offerings[0].reserved_quantity == 1 and c.event.offerings[0].paid_quantity == 0
    assert await count(c, PaymentAttempt) == 0 and await received_total(c) == 0
    assert (await c.client.post(f"/v1/orders/{order['id']}/payment-attempts", headers=auth_headers(c.buyer))).status_code == 409
    await reload(c)
    for path in [f"/v1/admin/meal-events/{c.event.id}/orders", "/v1/admin/meal-orders"]:
        response = await c.client.get(path, headers=auth_headers(c.admin))
        rows = response.json() if isinstance(response.json(), list) else [row["order"] for row in response.json()["items"]]
        assert "confirm_cash_payment" in rows[0]["available_actions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["cash", "pos", "qr"])
async def test_confirm_payment_is_idempotent_and_pos_owns_invoice(cash_context, method):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    first = await action(c, order_id, collection_method=method, reference="POS-123")
    assert first.status_code == 200, first.text
    assert first.json()["payment_status"] == "paid" and first.json()["invoice_status"] == "external"
    duplicate = await action(c, order_id)
    assert duplicate.status_code == 200 and duplicate.json()["paid_at"] == first.json()["paid_at"]
    attempt = await c.session.scalar(select(PaymentAttempt))
    assert attempt.provider == "onsite" and attempt.provider_response["collection_method"] == method
    assert attempt.provider_response["reference"] == "POS-123"
    listing = await c.client.get("/v1/admin/meal-orders", headers=auth_headers(c.admin))
    assert listing.json()["items"][0]["order"]["onsite_payment"]["reference"] == "POS-123"
    assert await count(c, PaymentAttempt) == 1 and await count(c, AdminAudit) == 1
    assert await received_total(c) == 120
    assert c.event.offerings[0].paid_quantity == 1 and c.event.offerings[0].reserved_quantity == 0
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type.startswith("invoice."))) == 0
    assert (await c.client.get(f"/v1/meal-orders/{order_id}/pickup-credential", headers=auth_headers(c.buyer))).status_code == 200
    assert await _reconcile_expired_payments(c.session, c.settings, c.event.pickup_ends_at, 100) == 0
    from app.integrations.invoice_service import enqueue_invoice_issue, issue_paid_order_invoice, InvoiceApplicationError, invoice_adapter_from_settings
    order = await c.session.get(Order, order_id)
    assert not enqueue_invoice_issue(c.session, order, trigger="retry")
    with pytest.raises(InvoiceApplicationError, match="POS"):
        await issue_paid_order_invoice(c.session, order_id, object())


@pytest.mark.asyncio
async def test_correction_balances_capacity_revenue_points_and_manual_refund(cash_context):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    order = await c.session.get(Order, order_id)
    order.membership_type_snapshot = MembershipType.MEMBER
    await c.session.commit()
    assert (await action(c, order_id)).status_code == 200
    for _ in range(2):
        corrected = await action(c, order_id, "cash-unpaid")
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["payment_status"] == "pending" and corrected.json()["paid_at"] is None
        assert await received_total(c) == 0
        assert c.event.offerings[0].reserved_quantity == 1 and c.event.offerings[0].paid_quantity == 0
        assert await c.session.scalar(select(func.sum(PointTransaction.amount))) == 0
        assert (await c.client.get(f"/v1/meal-orders/{order_id}/pickup-credential", headers=auth_headers(c.buyer))).status_code == 409
        assert (await action(c, order_id)).status_code == 200
        assert await received_total(c) == 120 and await c.session.scalar(select(func.sum(PointTransaction.amount))) == 1
    assert await count(c, PaymentAttempt) == 3
    assert await c.session.scalar(select(func.count(AdminAudit.id)).where(AdminAudit.action == "order.cash_payment_unpaid")) == 2
    await approve_cancellation(c, order_id)
    assert order.payment_status == PaymentStatus.REFUND_PENDING
    returned = await action(c, order_id, "cash-refund")
    assert returned.status_code == 200, returned.text
    assert order.payment_status == PaymentStatus.REFUNDED and await c.session.scalar(select(func.sum(PointTransaction.amount))) == 0
    assert (await c.session.scalar(select(Refund))).status == RefundStatus.COMPLETED
    assert await c.session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "refund.requested")) == 0
    assert order.invoice_status == InvoiceStatus.EXTERNAL


@pytest.mark.asyncio
async def test_switch_off_and_expiry_preserve_unpaid_order_without_income(cash_context):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    disabled = await c.client.patch(f"/v1/admin/meal-events/{c.event.id}/payment-options", headers=auth_headers(c.admin), json={"allow_cash_payment": False})
    assert disabled.status_code == 200 and (await create_cash(c)).status_code == 422
    assert (await action(c, order_id)).status_code == 200
    await action(c, order_id, "cash-unpaid")
    await _reconcile_expired_cash_orders(c.session, c.event.pickup_ends_at + timedelta(seconds=1), 100)
    order = await c.session.get(Order, order_id)
    assert order.payment_status == PaymentStatus.EXPIRED and order.invoice_status == InvoiceStatus.EXTERNAL
    assert c.event.offerings[0].reserved_quantity == c.event.offerings[0].paid_quantity == 0
    assert await received_total(c) == 0


@pytest.mark.asyncio
async def test_permission_amount_cancellation_and_payment_method_guards(cash_context):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    assert (await action(c, order_id, actor=c.buyer)).status_code == 403
    assert (await action(c, order_id, amount=1)).status_code == 409
    assert (await action(c, order_id, collection_method="unknown")).status_code == 422
    await approve_cancellation(c, order_id)
    assert (await action(c, order_id)).status_code == 409
    assert await count(c, PaymentAttempt) == await count(c, Refund) == 0
    assert c.event.offerings[0].reserved_quantity == 0
    assert (await create_cash(c, payment_method="cash_on_delivery")).status_code == 422


@pytest.mark.asyncio
async def test_redeemed_order_cannot_be_marked_unpaid(cash_context):
    c = cash_context
    order_id = (await create_cash(c)).json()["id"]
    assert (await action(c, order_id)).status_code == 200
    credential = (await c.client.get(f"/v1/meal-orders/{order_id}/pickup-credential", headers=auth_headers(c.buyer))).json()
    with time_machine.travel(c.event.pickup_starts_at + timedelta(minutes=1), tick=False):
        assert (await c.client.post(f"/v1/admin/meal-events/{c.event.id}/open-pickup", headers=auth_headers(c.admin))).status_code == 200
        assert (await c.client.post(f"/v1/admin/meal-events/{c.event.id}/redeem", headers=auth_headers(c.admin), json={"qr_token": credential["qr_token"]})).status_code == 200
        assert (await action(c, order_id, "cash-unpaid")).status_code == 409
        assert c.event.offerings[0].paid_quantity == 1
        assert not any(row.event_type.startswith("invoice.") for row in await c.session.scalars(select(OutboxEvent)))
