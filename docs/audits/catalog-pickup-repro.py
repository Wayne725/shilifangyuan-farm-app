"""Replay a public catalog against an in-memory API; never follows payment redirects."""

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import create_token
from app.database import Base
from app.models import Product, ProductCategory, ShippingTemperature, TaxType, User
from app.routers.orders import orders_router
from app.routers.payments import payments_router
from tests.support import api_test_context
from tests.test_secondary_test_product_payments import two_product_settings


async def main():
    catalog = json.loads(Path(sys.argv[1]).read_text())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    results = []
    settings = two_product_settings(sales_scope="pickup")
    settings.validate_runtime_secrets()
    async with factory() as session:
        buyer = User(email="catalog-replay@example.com", display_name="隔離驗證買家", password_hash="test")
        session.add(buyer)
        for row in catalog:
            session.add(Product(
                id=row["id"], sku=row["sku"], slug=row["slug"], name=row["name"],
                category=ProductCategory(row["category"]), unit=row["unit"],
                member_price=row["member_price"], nonmember_price=row["nonmember_price"],
                stock_quantity=row["stock_quantity"], tax_type=TaxType(row["tax_type"]),
                can_ship=row["can_ship"], is_active=row["is_active"],
                shipping_temperature=ShippingTemperature(row["shipping_temperature"]) if row["shipping_temperature"] else None,
                allowed_shipping_channels=row["allowed_shipping_channels"],
            ))
        await session.commit()
        headers = {"Authorization": f"Bearer {create_token(buyer, 'access', timedelta(minutes=5), settings=settings)}"}
        async with api_test_context(session, [orders_router, payments_router], settings=settings) as client:
            for row in catalog:
                items = [{"product_id": row["id"], "quantity": 1}]
                quote = await client.post("/v1/orders/quote", headers=headers, json={"items": items})
                assert quote.status_code == 200, (row["name"], quote.text)
                order = await client.post("/v1/orders", headers=headers, json={
                    "items": items, "contact_email": "catalog-replay@example.com",
                    "fulfillment_method": "cooperative_pickup",
                })
                assert order.status_code == 201, (row["name"], order.text)
                payment = await client.post(f"/v1/orders/{order.json()['id']}/payment-attempts", headers=headers)
                assert payment.status_code == 201, (row["name"], payment.text)
                session.expunge_all()
                checkout = await client.get(f"/payments/{payment.json()['id']}/checkout")
                assert checkout.status_code == 303, (row["name"], checkout.text)
                assert checkout.headers["location"].startswith("https://pay.example.test/")
                results.append({"name": row["name"], "quote": quote.status_code, "order": order.status_code,
                                "payment": payment.status_code, "checkout": checkout.status_code})
    await engine.dispose()
    print(json.dumps({"scope": "pickup", "isolated": True, "products_passed": len(results), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
