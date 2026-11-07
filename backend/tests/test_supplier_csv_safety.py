import csv
from datetime import datetime, timezone
import io

import pytest

from app.models import MembershipType, Order, OrderItem, OrderKind, PaymentStatus, TaxType, User, UserRole
from app.routers.cooperative import cooperative_router
from tests.support import api_test_context, auth_headers, make_test_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("name,note,expected_name,expected_note", [
    ("=商品公式", "=1+1", "'=商品公式", "'=1+1"),
    ("白米", " \t=1+1", "白米", "' \t=1+1"),
    ("+商品", "+SUM(1,2)", "'+商品", "'+SUM(1,2)"),
    ("-商品", "-1+2", "'-商品", "'-1+2"),
    ("@商品", "@SUM(1,2)", "'@商品", "'@SUM(1,2)"),
    ('白米,"有機"\n小包', '備註,"冷藏"\n到貨', '白米,"有機"\n小包', '備註,"冷藏"\n到貨'),
])
async def test_supplier_csv_neutralizes_formula_text_and_preserves_numeric_quantity(database_session, name, note, expected_name, expected_note):
    admin = User(email="supplier-admin@example.test", display_name="管理員", password_hash="unused", user_role=UserRole.ADMIN)
    database_session.add(admin)
    await database_session.flush()
    order = Order(order_number="SEC-SUPPLIER", order_kind=OrderKind.REGULAR, user_id=admin.id,
                  membership_type_snapshot=MembershipType.NONMEMBER, amount_total=200, contact_email=admin.email,
                  payment_status=PaymentStatus.PAID, paid_at=datetime.now(timezone.utc),
                  items=[OrderItem(product_name=name, unit_label="份", quantity=2, unit_price=100,
                                   subtotal=200, tax_type=TaxType.TAXABLE)])
    database_session.add(order)
    await database_session.commit()
    async with api_test_context(database_session, [cooperative_router], settings=make_test_settings()) as client:
        response = await client.get("/v1/admin/supplier-orders.csv", headers=auth_headers(admin), params={
            "starts_on": "2020-01-01", "ends_on": "2030-12-31", "expected_arrival_on": "2030-12-31", "note": note,
        })
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert rows[0] == ["商品", "數量", "單位", "溫層", "預計到貨日", "備註"]
    assert rows[1] == [expected_name, "2", "份", "未設定", "2030-12-31", expected_note]
    assert response.headers["Cache-Control"] == "no-store"
