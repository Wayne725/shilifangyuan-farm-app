import re

import pytest
import app.routers.catalog as catalog_module
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import make_token_pair
from app.database import Base, get_session
from app.models import Product, TaxType, User, UserRole
from app.routers.catalog import (
    catalog_router,
    create_product,
    list_products,
    update_product,
)
from app.schemas import ProductCreate, ProductUpdate


@pytest.fixture
async def database_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def http_client(database_session):
    application = FastAPI()
    application.include_router(catalog_router)

    async def override_get_session():
        yield database_session

    application.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture
async def users(database_session):
    admin = User(
        email="admin@example.test",
        display_name="管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    customer = User(
        email="customer@example.test",
        display_name="顧客",
        password_hash="test",
        user_role=UserRole.CUSTOMER,
    )
    database_session.add_all([admin, customer])
    await database_session.commit()
    return admin, customer


def auth_headers(user: User) -> dict[str, str]:
    token = make_token_pair(user)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def product_payload(**overrides) -> dict:
    payload = {
        "name": "新上架農產",
        "category": "當季蔬果",
        "unit": "袋",
        "member_price": 90,
        "nonmember_price": 110,
        "stock_quantity": 12,
        "tax_type": "taxable",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_public_only_sees_active_products_but_admin_sees_all(
    database_session,
) -> None:
    active = Product(
        slug="active-rice",
        name="上架白米",
        category="米・雜糧",
        unit="包",
        member_price=180,
        nonmember_price=210,
        stock_quantity=5,
        tax_type=TaxType.TAX_EXEMPT,
        is_active=True,
    )
    inactive = Product(
        slug="inactive-rice",
        name="下架白米",
        category="米・雜糧",
        unit="包",
        member_price=170,
        nonmember_price=200,
        stock_quantity=0,
        tax_type=TaxType.TAX_EXEMPT,
        is_active=False,
    )
    admin = User(
        email="admin@example.test",
        display_name="管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    database_session.add_all([active, inactive, admin])
    await database_session.commit()

    public_products = await list_products(None, database_session)
    admin_products = await list_products(admin, database_session)

    assert [product.slug for product in public_products] == ["active-rice"]
    assert {product.slug for product in admin_products} == {
        "active-rice",
        "inactive-rice",
    }


@pytest.mark.asyncio
async def test_admin_can_create_and_reactivate_product(database_session) -> None:
    admin = User(
        email="admin@example.test",
        display_name="管理員",
        password_hash="test",
        user_role=UserRole.ADMIN,
    )
    database_session.add(admin)
    await database_session.commit()

    product = await create_product(
        ProductCreate(
            name="新上架農產",
            category="當季蔬果",
            unit="袋",
            member_price=90,
            nonmember_price=110,
            stock_quantity=12,
            tax_type=TaxType.TAXABLE,
        ),
        admin,
        database_session,
    )
    await update_product(
        product.id,
        ProductUpdate(is_active=False),
        admin,
        database_session,
    )
    reactivated = await update_product(
        product.id,
        ProductUpdate(is_active=True),
        admin,
        database_session,
    )

    assert re.fullmatch(r"product-[a-f0-9]{12}", product.slug)
    assert reactivated.is_active is True


@pytest.mark.asyncio
async def test_product_write_requires_admin_over_http(
    http_client, users
) -> None:
    admin, customer = users

    unauthenticated = await http_client.post(
        "/v1/products", json=product_payload()
    )
    forbidden = await http_client.post(
        "/v1/products",
        json=product_payload(),
        headers=auth_headers(customer),
    )
    created = await http_client.post(
        "/v1/products",
        json=product_payload(),
        headers=auth_headers(admin),
    )
    product_id = created.json()["id"]
    unauthenticated_update = await http_client.patch(
        f"/v1/products/{product_id}", json={"is_active": False}
    )
    forbidden_update = await http_client.patch(
        f"/v1/products/{product_id}",
        json={"is_active": False},
        headers=auth_headers(customer),
    )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert created.status_code == 201
    assert unauthenticated_update.status_code == 401
    assert forbidden_update.status_code == 403


@pytest.mark.asyncio
async def test_product_create_validates_category_and_price_over_http(
    http_client, users
) -> None:
    admin, _customer = users
    headers = auth_headers(admin)

    invalid_category = await http_client.post(
        "/v1/products",
        json=product_payload(category="其他"),
        headers=headers,
    )
    invalid_price = await http_client.post(
        "/v1/products",
        json=product_payload(member_price=120, nonmember_price=110),
        headers=headers,
    )
    created = await http_client.post(
        "/v1/products", json=product_payload(), headers=headers
    )
    invalid_update_category = await http_client.patch(
        f"/v1/products/{created.json()['id']}",
        json={"category": "其他"},
        headers=headers,
    )

    assert invalid_category.status_code == 422
    assert invalid_price.status_code == 422
    assert invalid_update_category.status_code == 422


@pytest.mark.asyncio
async def test_product_update_validates_effective_price_over_http(
    http_client, users
) -> None:
    admin, _customer = users
    headers = auth_headers(admin)
    created = await http_client.post(
        "/v1/products", json=product_payload(), headers=headers
    )
    product_id = created.json()["id"]

    member_too_high = await http_client.patch(
        f"/v1/products/{product_id}",
        json={"member_price": 120},
        headers=headers,
    )
    nonmember_too_low = await http_client.patch(
        f"/v1/products/{product_id}",
        json={"nonmember_price": 80},
        headers=headers,
    )

    assert member_too_high.status_code == 422
    assert nonmember_too_low.status_code == 422


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "description",
        "category",
        "unit",
        "member_price",
        "nonmember_price",
        "stock_quantity",
        "tax_type",
        "is_active",
    ],
)
@pytest.mark.asyncio
async def test_product_update_rejects_null_required_fields_over_http(
    http_client, users, field
) -> None:
    admin, _customer = users
    headers = auth_headers(admin)
    created = await http_client.post(
        "/v1/products", json=product_payload(), headers=headers
    )

    response = await http_client.patch(
        f"/v1/products/{created.json()['id']}",
        json={field: None},
        headers=headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_server_generates_unique_slugs_for_same_name(
    http_client, users
) -> None:
    admin, _customer = users
    headers = auth_headers(admin)

    first = await http_client.post(
        "/v1/products", json=product_payload(), headers=headers
    )
    second = await http_client.post(
        "/v1/products", json=product_payload(), headers=headers
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["slug"] != second.json()["slug"]
    assert re.fullmatch(r"product-[a-f0-9]{12}", first.json()["slug"])


@pytest.mark.asyncio
async def test_product_create_retries_a_slug_conflict(
    http_client, users, database_session, monkeypatch
) -> None:
    admin, _customer = users
    database_session.add(
        Product(
            slug="collision-slug",
            name="既有商品",
            category="當季蔬果",
            unit="袋",
            member_price=80,
            nonmember_price=100,
            stock_quantity=1,
        )
    )
    await database_session.commit()
    candidates = iter(["collision-slug", "resolved-slug"])
    monkeypatch.setattr(
        "app.routers.catalog.make_product_slug",
        lambda _name: next(candidates),
    )

    response = await http_client.post(
        "/v1/products",
        json=product_payload(),
        headers=auth_headers(admin),
    )

    assert response.status_code == 201
    assert response.json()["slug"] == "resolved-slug"


@pytest.mark.asyncio
async def test_demo_reset_requires_admin_and_confirmation(
    http_client, users, monkeypatch
) -> None:
    admin, customer = users

    async def fake_reset_demo_data(_session):
        return {"products": 12, "campaigns": 1}

    monkeypatch.setattr(
        catalog_module, "reset_demo_data", fake_reset_demo_data
    )
    unauthenticated = await http_client.post(
        "/v1/admin/demo/reset", json={"confirmation": "RESET"}
    )
    forbidden = await http_client.post(
        "/v1/admin/demo/reset",
        json={"confirmation": "RESET"},
        headers=auth_headers(customer),
    )
    wrong_confirmation = await http_client.post(
        "/v1/admin/demo/reset",
        json={"confirmation": "NOPE"},
        headers=auth_headers(admin),
    )
    reset = await http_client.post(
        "/v1/admin/demo/reset",
        json={"confirmation": "RESET"},
        headers=auth_headers(admin),
    )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert wrong_confirmation.status_code == 403
    assert reset.status_code == 200
