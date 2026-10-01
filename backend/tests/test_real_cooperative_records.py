from __future__ import annotations

import base64
import json
from datetime import date

import pytest
from sqlalchemy import select

from app.models import Supplier, User, UserRole
from app.routers.catalog import catalog_router
from app.routers.operations import operations_router
from app.schemas import FulfillmentUpdate
from tests.support import (
    api_test_context,
    auth_headers,
    make_test_settings,
)


def real_data_settings():
    return make_test_settings(
        pii_encryption_keys_json=json.dumps(
            {
                "v1": base64.b64encode(b"r" * 32).decode("ascii"),
            }
        ),
    )


def test_pickup_window_requires_a_complete_ordered_range() -> None:
    with pytest.raises(ValueError, match="同時提供"):
        FulfillmentUpdate(
            status="ready_for_pickup",
            pickup_starts_at="2026-08-17T10:00:00+08:00",
        )
    with pytest.raises(ValueError, match="晚於開始時間"):
        FulfillmentUpdate(
            status="ready_for_pickup",
            pickup_starts_at="2026-08-17T11:00:00+08:00",
            pickup_ends_at="2026-08-17T10:00:00+08:00",
        )


@pytest.fixture
async def real_data_context(database_session):
    admin = User(
        email="operations-admin@example.test",
        display_name="營運管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    customer = User(
        email="operations-customer@example.test",
        display_name="一般顧客",
        password_hash="test",
        user_role=UserRole.CUSTOMER,
    )
    database_session.add_all([admin, customer])
    await database_session.commit()

    async with api_test_context(
        database_session,
        [operations_router, catalog_router],
        settings=real_data_settings(),
    ) as client:
        yield {
            "client": client,
            "session": database_session,
            "admin": admin,
            "customer": customer,
        }


@pytest.mark.asyncio
async def test_admin_manages_pickup_locations(real_data_context) -> None:
    client = real_data_context["client"]
    admin = real_data_context["admin"]
    customer = real_data_context["customer"]
    payload = {
        "code": "library-gate",
        "name": "圖書館大門",
        "address": "校本部圖書館",
        "instructions": "週三 16:00–18:00",
        "sort_order": 60,
    }

    forbidden = await client.post(
        "/v1/admin/pickup-locations",
        json=payload,
        headers=auth_headers(customer),
    )
    created = await client.post(
        "/v1/admin/pickup-locations",
        json=payload,
        headers=auth_headers(admin),
    )
    public_locations = await client.get("/v1/pickup-locations")

    assert forbidden.status_code == 403
    assert created.status_code == 201
    assert created.json()["name"] == "圖書館大門"
    assert [item["code"] for item in public_locations.json()] == [
        "library-gate"
    ]


@pytest.mark.asyncio
async def test_supplier_accreditation_and_product_binding(
    real_data_context,
) -> None:
    client = real_data_context["client"]
    session = real_data_context["session"]
    admin_headers = auth_headers(real_data_context["admin"])
    supplier_payload = {
        "business_name": "好田農產行",
        "tax_id": "12345678",
        "responsible_person": "林小農",
        "contact_person": "林小農",
        "phone": "0912345678",
        "email": "farm@example.com",
        "line_id": "good-farm",
        "settlement_terms": "每月 10 日結算，次月 5 日匯款。",
        "bank_account": "808-123456789012",
    }

    created = await client.post(
        "/v1/admin/suppliers",
        json=supplier_payload,
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    supplier_id = created.json()["id"]
    stored_supplier = await session.scalar(
        select(Supplier).where(Supplier.id == supplier_id)
    )

    assert created.json()["responsible_person"] == "林小農"
    assert stored_supplier.responsible_person_encrypted != "林小農"
    assert stored_supplier.bank_account_encrypted != "808-123456789012"

    approved = await client.post(
        f"/v1/admin/suppliers/{supplier_id}/accreditations",
        json={
            "reviewed_on": date(2026, 8, 16).isoformat(),
            "process_notes": "文件與現場訪查完成。",
            "status": "approved",
            "result_notes": "通過。",
        },
        headers=admin_headers,
    )

    assert approved.status_code == 201
    assert approved.json()["supplier_number"] == "SUP-2026-0001"
    assert approved.json()["is_active"] is True

    product_payload = {
        "product_number": "P-0001",
        "sku": "RICE-2KG",
        "supplier_id": supplier_id,
        "name": "友善栽培白米 2 公斤",
        "description": "合作農友定期供應。",
        "category": "米・雜糧",
        "unit": "包",
        "member_price": 260,
        "nonmember_price": 290,
        "stock_quantity": 30,
        "tax_type": "tax_exempt",
    }
    product = await client.post(
        "/v1/products",
        json=product_payload,
        headers=admin_headers,
    )
    duplicate = await client.post(
        "/v1/products",
        json={**product_payload, "name": "重複 SKU 商品"},
        headers=admin_headers,
    )

    assert product.status_code == 201
    assert product.json()["supplier_name"] == "好田農產行"
    assert product.json()["sku"] == "RICE-2KG"
    assert duplicate.status_code == 409
