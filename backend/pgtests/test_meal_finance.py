from datetime import datetime, timedelta, timezone

import pytest

from app.meal_finance import MealSalesFilters, meal_sales_report
from app.models import PaymentAttempt, PaymentStatus, Refund, RefundStatus
from pgtests.test_transaction_concurrency import postgres_sessions
from tests.test_meal_cancellation_review import seed_cancellation_order


@pytest.mark.asyncio
async def test_postgres_cash_basis_uses_taipei_midnight_and_never_multiplies_refunds(postgres_sessions):
    async with postgres_sessions() as session:
        c = await seed_cancellation_order(session)
        midnight = datetime(2030, 1, 1, 16, tzinfo=timezone.utc)
        c.order.created_at = midnight - timedelta(days=1)
        c.order.paid_at = midnight
        c.order.payment_status = PaymentStatus.REFUND_PENDING
        session.add_all([
            PaymentAttempt(order=c.order, merchant_trade_no="FINANCE-PG-FIRST", amount=200, status=PaymentStatus.REFUND_PENDING, paid_at=midnight, expires_at=midnight + timedelta(hours=1)),
            PaymentAttempt(order=c.order, merchant_trade_no="FINANCE-PG-DUPLICATE", amount=200, status=PaymentStatus.REFUNDED, paid_at=midnight + timedelta(minutes=30), expires_at=midnight + timedelta(hours=1)),
            Refund(order=c.order, amount=200, status=RefundStatus.COMPLETED, reason="重複付款", requested_by_id=c.admin.id, completed_at=midnight + timedelta(hours=1), created_at=midnight),
            Refund(order=c.order, amount=200, status=RefundStatus.PENDING, reason="仍在退款中", requested_by_id=c.admin.id, created_at=midnight),
        ])
        await session.commit()
        result = await meal_sales_report(session, MealSalesFilters(starts_on="2030-01-02", ends_on="2030-01-02"))
        assert result["summary"]["received"] == 400
        assert result["summary"]["refunded"] == 200
        assert result["summary"]["net"] == 200
        assert result["summary"]["pending_refund_amount"] == 200
        assert result["daily"][0]["period"] == "2030-01-02"
        assert result["products"][0]["quantity"] == 2
        assert result["products"][0]["sales_amount"] == 200


@pytest.mark.asyncio
async def test_postgres_all_sales_channels_share_receipt_and_refund_scope(postgres_sessions):
    from app.models import Order, OrderItem, OrderKind, SalesChannel, TaxType

    async with postgres_sessions() as session:
        c = await seed_cancellation_order(session)
        midnight = datetime(2030, 1, 1, 16, tzinfo=timezone.utc)
        c.order.paid_at = midnight
        ordinary = Order(order_number='PG-REGULAR-SALE', order_kind=OrderKind.REGULAR,
                         sales_channel=SalesChannel.REGULAR, user=c.buyer, amount_total=600,
                         contact_email=c.buyer.email, membership_type_snapshot=c.order.membership_type_snapshot,
                         created_at=midnight, paid_at=midnight, payment_status=PaymentStatus.PAID,
                         items=[OrderItem(product_name='一般選品', unit_label='袋', quantity=3,
                                          unit_price=200, subtotal=600, tax_type=TaxType.TAX_EXEMPT)])
        session.add_all([ordinary, Refund(order=ordinary, amount=200, status=RefundStatus.COMPLETED,
                                         requested_by_id=c.admin.id, reason='跨類型退款', created_at=midnight,
                                         completed_at=midnight + timedelta(hours=1))])
        await session.commit()
        filters = MealSalesFilters(starts_on='2030-01-02', ends_on='2030-01-02', sales_channel='all')
        result = await meal_sales_report(session, filters)
        assert result['summary']['received'] == 800
        assert result['summary']['refunded'] == 200
        assert result['summary']['net'] == 600
        regular = await meal_sales_report(session, filters.model_copy(update={'sales_channel': 'regular'}))
        assert regular['summary']['net'] == 400
        assert regular['orders']['total'] == 1
        assert regular['products'][0]['quantity'] == 3
