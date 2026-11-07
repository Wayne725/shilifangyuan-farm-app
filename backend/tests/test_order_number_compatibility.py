from datetime import datetime, timezone
import re
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select

from app.integrations.ecpay import create_merchant_trade_no
from app.integrations.ecpay_logistics import MERCHANT_TRADE_NO_PATTERN
from app.integrations.invoice_service import invoice_relate_number
from app.models import FulfillmentMethod, MembershipType, Order, OrderFulfillment, OrderItem, OrderKind, Product, TaxType, User
from app.order_numbers import _is_order_number_collision, allocate_order_number, insert_order_with_unique_number, make_order_number
from app.routers.groups import make_order_number as group_order_number
from app.routers.orders import make_order_number as regular_order_number


def test_shared_order_number_preserves_invoice_payment_and_logistics_contracts():
    number = make_order_number()
    assert re.fullmatch(r"SLF[0-9]{12}[0-9A-F]{4}", number)
    assert regular_order_number is group_order_number is make_order_number
    assert invoice_relate_number(number) == f"INV{number}"
    assert MERCHANT_TRADE_NO_PATTERN.fullmatch(number)
    trade_number = create_merchant_trade_no(number, 1, datetime.now(timezone.utc))
    assert re.fullmatch(r"[A-Za-z0-9]{20}", trade_number)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["M", "O"])
async def test_daily_order_number_preserves_provider_contracts(database_session, prefix):
    fixed = datetime(2026, 11, 6, 4, tzinfo=timezone.utc)
    number = await allocate_order_number(database_session, prefix, fixed)
    assert number == f"{prefix}2026110601"
    assert invoice_relate_number(number) == f"INV{number}"
    assert MERCHANT_TRADE_NO_PATTERN.fullmatch(number)
    assert re.fullmatch(r"[A-Za-z0-9]{20}", create_merchant_trade_no(number, 1, fixed))


@pytest.mark.parametrize("sqlstate,constraint,expected", [
    ("23505", "ix_orders_order_number", True),
    ("23505", "pk_orders", False),
    ("23514", "ix_orders_order_number", False),
])
def test_postgres_collision_classifier_is_constraint_and_unique_specific(sqlstate, constraint, expected):
    original = SimpleNamespace(sqlstate=sqlstate, __cause__=SimpleNamespace(constraint_name=constraint))
    error = IntegrityError("INSERT", {}, original)
    assert _is_order_number_collision(error, "postgresql") is expected


@pytest.mark.parametrize("message,expected", [
    ("UNIQUE constraint failed: orders.order_number", True),
    ("UNIQUE constraint failed: orders.id", False),
])
def test_sqlite_collision_classifier_does_not_swallow_other_unique_failures(message, expected):
    error = IntegrityError("INSERT", {}, sqlite3.IntegrityError(message))
    assert _is_order_number_collision(error, "sqlite") is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_write", [False, True])
@pytest.mark.parametrize("collision", [False, True])
@pytest.mark.parametrize("async_factory", [False, True])
async def test_sqlite_successful_insert_and_retry_stay_inside_callers_rollback(database_session, existing_write, collision, async_factory):
    session = database_session
    session.autoflush = False
    buyer = User(email="sqlite-number@example.com", notification_email="sqlite-number@example.com", display_name="買家", password_hash="test")
    product = Product(slug="sqlite-number", name="商品", category="測試", unit="份", member_price=100, nonmember_price=100, stock_quantity=100)
    session.add_all([buyer, product])
    await session.flush()
    buyer_id, product_id = buyer.id, product.id
    occupied_number = "SLF301001040000ABCD"
    available_number = "SLF301001040000EF01"

    def new_order(number):
        return Order(order_number=number, order_kind=OrderKind.REGULAR, membership_type_snapshot=MembershipType.NONMEMBER,
                     user_id=buyer_id, amount_total=100, contact_email=buyer.email,
                     items=[OrderItem(source_product_id=product_id, product_name="商品", unit_label="份", quantity=1, unit_price=100, subtotal=100, tax_type=TaxType.TAXABLE)],
                     fulfillment=OrderFulfillment(method=FulfillmentMethod.COOPERATIVE_PICKUP))

    original = new_order(occupied_number)
    session.add(original)
    await session.commit()
    original_id = original.id
    await session.execute(select(Product).where(Product.id == product_id))
    buyer.notification_email = buyer.email  # A repeat buyer does not cause a pre-savepoint UPDATE.
    if existing_write:
        product.stock_quantity = 99
        await session.flush()
    pending = new_order(occupied_number if collision else available_number)
    async def allocate_next():
        return available_number
    await insert_order_with_unique_number(session, pending, allocate_next if async_factory else lambda: available_number)
    assert await session.scalar(select(func.count(Order.id))) == 2
    await session.rollback()
    assert await session.scalar(select(func.count(Order.id))) == 1
    assert await session.scalar(select(Order.id)) == original_id
    assert await session.scalar(select(func.count(OrderItem.id))) == 1
    assert await session.scalar(select(func.count(OrderFulfillment.id))) == 1
    assert (await session.get(Product, product_id)).stock_quantity == 100
