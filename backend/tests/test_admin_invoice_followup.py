from datetime import datetime, timedelta, timezone

import pytest
import time_machine

from app.models import (
    Invoice, InvoiceStatus, MembershipType, Order, OrderKind, OutboxEvent, OutboxStatus,
    PaymentMethod, PaymentStatus, SalesChannel, User, UserRole, WalkInSale, new_id,
)
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers, make_test_settings


async def check_admin_invoice_followup(session):
    now = datetime(2030, 1, 3, 3, tzinfo=timezone.utc)
    admin = User(email="invoice-admin@example.com", display_name="開票管理員", password_hash="test", user_role=UserRole.ADMIN)
    buyer = User(email="invoice-buyer@example.com", display_name="測試客人", password_hash="test")
    session.add_all([admin, buyer])
    await session.flush()

    def order(name, *, status=InvoiceStatus.PENDING, sale=SalesChannel.REGULAR, walk_in=False, **fields):
        row = Order(
            id=new_id(), order_number=name, order_kind=OrderKind.REGULAR, sales_channel=sale,
            user_id=None if walk_in else buyer.id, customer_kind="walk_in" if walk_in else "account",
            membership_type_snapshot=MembershipType.NONMEMBER, amount_total=120,
            contact_email=buyer.email, invoice_status=status,
            payment_status=fields.pop("payment_status", PaymentStatus.PAID), **fields,
        )
        session.add(row)
        return row

    def invoice(row, *, status, provider="fanyu", **fields):
        session.add(Invoice(order=row, relate_number=row.order_number, status=status, provider=provider, **fields))

    def job(row, status, *, kind="invoice.issue_requested", legacy=False, **payload):
        session.add(OutboxEvent(
            aggregate_type="order" if legacy else "invoice",
            aggregate_id=row.id if legacy else new_id(), event_type=kind,
            status=status, payload=payload if legacy else {"order_id": row.id, **payload},
            available_at=now + timedelta(minutes=30) if status == OutboxStatus.PENDING else now,
        ))

    regular = order("REGULAR-FAILED")
    job(regular, OutboxStatus.FAILED)
    job(regular, OutboxStatus.FAILED, kind="invoice.issue", legacy=True)
    session.add(OutboxEvent(
        aggregate_type="invoice", aggregate_id=new_id(), event_type="invoice.issue",
        status=OutboxStatus.PENDING, payload={}, available_at=now,
    ))
    meal = order(
        "MEAL-REJECTED", sale=SalesChannel.MEAL_PREORDER, status=InvoiceStatus.FAILED,
        invoice_handling="pickup_day", invoice_issue_after=now,
    )
    invoice(meal, status=InvoiceStatus.FAILED, provider_status="3")
    job(meal, OutboxStatus.COMPLETED)
    walk_in = order("COUNTER-FAILED", walk_in=True, payment_method=PaymentMethod.CASH_ON_PICKUP, invoice_handling="on_payment")
    job(walk_in, OutboxStatus.FAILED)
    group = order("GROUP-VOID-FAILED", sale=SalesChannel.GROUP, status=InvoiceStatus.VOID_PENDING, payment_status=PaymentStatus.REFUNDED)
    invoice(group, status=InvoiceStatus.VOID_PENDING, void_source="refund")
    job(group, OutboxStatus.FAILED, kind="invoice.adjustment_required", auto_void=True, adjustment="void")
    manual_void = order("LEGACY-MANUAL-VOID", status=InvoiceStatus.VOID_PENDING, payment_status=PaymentStatus.REFUNDED)
    invoice(manual_void, status=InvoiceStatus.VOID_PENDING, provider="ecpay", void_source="refund")
    job(manual_void, OutboxStatus.COMPLETED, kind="invoice.adjustment_required", auto_void=False, adjustment="void")
    uncertain_void = order("VOID-NEEDS-QUERY", status=InvoiceStatus.VOID_PENDING, payment_status=PaymentStatus.REFUNDED)
    invoice(uncertain_void, status=InvoiceStatus.VOID_PENDING, void_source="api_submission_started", error_message="作廢結果仍待核對")

    for number, status in [("RETRY-SCHEDULED", OutboxStatus.PENDING), ("RETRY-RUNNING", OutboxStatus.PROCESSING)]:
        retry = order(number, status=InvoiceStatus.FAILED)
        invoice(retry, status=InvoiceStatus.FAILED, error_message="暫時無法開票")
        job(retry, OutboxStatus.FAILED)
        job(retry, status)
    future = order("FUTURE-PICKUP", invoice_handling="pickup_day", invoice_issue_after=now + timedelta(days=1))
    undated = order("UNSCHEDULED-PICKUP", invoice_handling="pickup_day")
    legacy_external = order("LEGACY-EXTERNAL", payment_method=PaymentMethod.CASH_ON_PICKUP)
    external = order("EXPLICIT-EXTERNAL", invoice_handling="external")
    unpaid = order("UNPAID", payment_status=PaymentStatus.PENDING)
    cancelled = order("CANCELLED", cancelled_at=now)
    for row in [future, undated, legacy_external, external, unpaid, cancelled]:
        job(row, OutboxStatus.FAILED)
    order("AWAITING-PROVIDER")
    issued = order("ISSUED", status=InvoiceStatus.ISSUED)
    invoice(issued, status=InvoiceStatus.ISSUED)
    job(issued, OutboxStatus.FAILED)
    provider_issued = order("PROVIDER-ISSUED", status=InvoiceStatus.FAILED)
    invoice(provider_issued, status=InvoiceStatus.ISSUED)
    job(provider_issued, OutboxStatus.FAILED)
    auto_void = order("AUTO-VOID-RUNNING", status=InvoiceStatus.VOID_PENDING, payment_status=PaymentStatus.REFUNDED)
    invoice(auto_void, status=InvoiceStatus.VOID_PENDING, void_source="api_submission_started", error_message="尚待查詢")
    job(auto_void, OutboxStatus.FAILED, kind="invoice.adjustment_required", auto_void=True, adjustment="void")
    job(auto_void, OutboxStatus.PENDING, kind="invoice.adjustment_required", auto_void=True, adjustment="void")
    unsupported = order("VOID-WITHOUT-EVIDENCE", status=InvoiceStatus.VOID_PENDING, payment_status=PaymentStatus.REFUNDED)
    invoice(unsupported, status=InvoiceStatus.VOID_PENDING, void_source="refund")
    voided = order("VOIDED", status=InvoiceStatus.VOIDED, payment_status=PaymentStatus.REFUNDED)
    invoice(voided, status=InvoiceStatus.VOIDED)
    job(voided, OutboxStatus.FAILED, kind="invoice.adjustment_required", auto_void=True, adjustment="void")
    job(issued, OutboxStatus.COMPLETED, kind="invoice.adjustment_required", auto_void=False, adjustment="allowance")

    await session.flush()
    session.add(WalkInSale(order_id=walk_in.id, client_request_id=new_id(), request_fingerprint="fixture", operator_id=admin.id, sale_category="product"))
    await session.commit()

    path = "/v1/admin/order-search?invoice_followup=true"
    with time_machine.travel(now, tick=False):
        async with api_test_context(session, [operations_router], settings=make_test_settings()) as client:
            headers = auth_headers(admin)
            today = await client.get("/v1/admin/today", headers=headers)
            queue = await client.get(path, headers=headers)
            assert today.status_code == queue.status_code == 200
            assert queue.headers["Cache-Control"] == "no-store"
            assert (await client.get(path, headers=auth_headers(buyer))).status_code == 403
            rows = queue.json()["items"]
            assert {row["id"]: row["invoice_followup_reason"] for row in rows} == {
                regular.id: "issuance", meal.id: "issuance", walk_in.id: "issuance",
                group.id: "void", manual_void.id: "void", uncertain_void.id: "void",
            }
            assert today.json()["invoice_followups"] == queue.json()["total"] == len(rows) == 6
            assert all(row["invoice_followup_required"] is False for row in rows)
            ordinary = await client.get("/v1/admin/order-search?q=REGULAR-FAILED", headers=headers)
            assert ordinary.status_code == 200
            assert [row["id"] for row in ordinary.json()["items"]] == [regular.id]
            assert "invoice_followup_reason" not in ordinary.json()["items"][0]
            general = await client.get(path + "&order_category=general", headers=headers)
            assert general.json()["total"] == 5
            meal_only = await client.get(path + "&order_category=meal", headers=headers)
            assert [row["id"] for row in meal_only.json()["items"]] == [meal.id]


@pytest.mark.asyncio
async def test_admin_invoice_followup_uses_current_actionable_orders(database_session):
    await check_admin_invoice_followup(database_session)
