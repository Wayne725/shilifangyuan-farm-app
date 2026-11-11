from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import Invoice, InvoiceStatus, MealEventOffering, Order, UserRole
from app.routers.invoice_reports import invoice_reports_router
from app.routers.operations import operations_router
from tests.support import api_test_context, auth_headers
from tests.test_invoice_tax_summary import fanyu
from tests.test_member_invoice_tax import ROUTERS, checkout_context, quote_and_create, stored_order, tax_settings


@pytest.mark.parametrize("channel", ["regular", "meal"])
@pytest.mark.parametrize("case", ["company", "personal", "pending", "voided", "unknown"])
async def test_saved_invoice_tax_is_safe_and_consistent_across_order_apis(database_session, channel, case):
    personal = case == "personal"
    context = await checkout_context(database_session, channel, membership_state="nonmember",
        buyer_type="personal" if personal else "company")
    if channel == "regular":
        context.sources[0].member_price = context.sources[0].nonmember_price = 120
    else:
        context.sources[0].price = 120
        offering = await database_session.scalar(select(MealEventOffering))
        offering.price = 120
    await database_session.commit()
    async with api_test_context(database_session, [*ROUTERS, operations_router, invoice_reports_router], settings=tax_settings()) as client:
        _, created = await quote_and_create(client, context)
        assert created["tax_amount"] == (5 if personal else 6) and created["amount_total"] == 120
        assert created["invoice_tax"] is None
        order = await stored_order(database_session, created["id"])
        # Preserve a historical estimate even when the actual invoice differs.
        order.tax_amount = 5
        invoice = None
        if case != "pending":
            fixture = fanyu(personal=personal)
            if not personal:
                fixture.sales_amount, fixture.tax_amount, fixture.total_amount = 114, 6, 120
                fixture.provider_request.update(salesAmount="114", taxAmount="6", totalAmount="120")
            if case == "voided":
                fixture.status = "voided"
            if case == "unknown":
                fixture.provider_request = {}
            invoice = Invoice(order_id=order.id, relate_number="INV" + order.id.replace("-", "")[:27],
                invoice_date=datetime.now(timezone.utc), **vars(fixture))
            database_session.add(invoice)
            order.invoice_status = InvoiceStatus(fixture.status)
        else:
            order.invoice_status = InvoiceStatus.PENDING
        await database_session.commit()
        saved_order = (order.amount_total, order.tax_amount, order.member_tax_exempt_snapshot)
        saved_invoice = deepcopy(invoice.provider_request) if invoice else None
        expected = None if case == "pending" else {
            "status": "voided" if case == "voided" else "issued",
            "tax_type": "unknown" if case == "unknown" else "taxable",
            "amount_semantics": "unknown" if case == "unknown" else "included_not_separately_stated" if personal else "separately_stated",
            "tax_amount": None if personal or case == "unknown" else 6,
        }
        paths = [f"/v1/orders/{order.id}"]
        if channel == "meal":
            paths.append(f"/v1/meal-orders/{order.id}")
        for path in paths:
            response = await client.get(path, headers=auth_headers(context.user))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["invoice_tax"] == expected
            assert body["tax_amount"] == 5 and body["amount_total"] == 120
            assert "private-line" not in response.text and "private-tax-id" not in response.text
        context.user.user_role = UserRole.ADMIN
        await database_session.commit()
        registered = await client.get("/v1/admin/invoice-register", headers=auth_headers(context.user))
        assert registered.status_code == 200, registered.text
        row = next(row for row in registered.json()["items"] if row["order_id"] == order.id)
        assert row["invoice_tax"] == expected and row["estimated_tax_amount"] == 5
        assert row["tax_amount"] == (invoice.tax_amount if invoice else 5)
        if channel == "meal":
            response = await client.get(f"/v1/admin/meal-events/{order.meal_event_id}/orders", headers=auth_headers(context.user))
            assert response.status_code == 200, response.text
            assert next(row for row in response.json() if row["id"] == order.id)["invoice_tax"] == expected
        summary = await client.get("/v1/admin/order-search", headers=auth_headers(context.user))
        assert summary.status_code == 200, summary.text
        assert next(row for row in summary.json()["items"] if row["id"] == order.id)["invoice_tax"] == expected
        actual = (await database_session.execute(select(Order.amount_total, Order.tax_amount,
            Order.member_tax_exempt_snapshot).where(Order.id == order.id))).one()
        assert tuple(actual) == saved_order
        if invoice:
            assert await database_session.scalar(select(Invoice.provider_request).where(Invoice.id == invoice.id)) == saved_invoice
