from copy import deepcopy

import pytest
from sqlalchemy import select

from app.models import AdminAudit
from tests.support import auth_headers
from tests.test_v2_routers import v2_context


@pytest.mark.asyncio
async def test_admin_can_update_and_remove_photos_without_changing_meal_choices(v2_context):
    client = v2_context["client"]
    admin = auth_headers(v2_context["admin"])
    created = await client.post("/v1/admin/meals", headers=admin, json={
        "name": "照片餐盒", "price": 130, "image_url": "https://images.example.test/meal.jpg",
        "option_groups": [{"name": "附餐", "options": [
            {"name": "飲品", "price_delta": 20, "image_url": "https://images.example.test/drink.jpg"},
            {"name": "湯品", "price_delta": 30},
        ]}],
    })
    assert created.status_code == 201, created.text
    meal = created.json()
    option = meal["option_groups"][0]["options"][0]
    assert option["image_url"] == "https://images.example.test/drink.jpg"
    path = f"/v1/admin/meals/{meal['id']}/images"

    denied = await client.patch(path, headers=auth_headers(v2_context["applicant"]), json={"image_url": None})
    assert denied.status_code == 403
    assert (await client.patch(path, json={"image_url": None})).status_code == 401

    updated = await client.patch(path, headers=admin, json={
        "image_url": "https://images.example.test/new-meal.jpg",
        "options": [{"id": option["id"], "image_url": "https://images.example.test/new-drink.jpg"}],
    })
    assert updated.status_code == 200, updated.text
    expected = deepcopy(meal)
    expected["image_url"] = "https://images.example.test/new-meal.jpg"
    expected["option_groups"][0]["options"][0]["image_url"] = "https://images.example.test/new-drink.jpg"
    assert updated.json() == expected

    removed = await client.patch(path, headers=admin, json={"options": [{"id": option["id"], "image_url": None}]})
    expected["option_groups"][0]["options"][0]["image_url"] = None
    assert removed.json() == expected
    audits = list(await v2_context["session"].scalars(select(AdminAudit).where(AdminAudit.action == "meal.images_update")))
    assert len(audits) == 2
    assert all(audit.actor_id == v2_context["admin"].id for audit in audits)


@pytest.mark.asyncio
async def test_invalid_option_photo_batch_does_not_partially_change_meal(v2_context):
    client = v2_context["client"]
    admin = auth_headers(v2_context["admin"])

    async def create(name):
        response = await client.post("/v1/admin/meals", headers=admin, json={
            "name": name, "price": 100, "option_groups": [{"name": "附餐", "options": [{"name": "湯"}]}],
        })
        assert response.status_code == 201
        return response.json()

    meal = await create("第一餐盒")
    other = await create("另一餐盒")
    option_id = meal["option_groups"][0]["options"][0]["id"]
    other_id = other["option_groups"][0]["options"][0]["id"]
    path = f"/v1/admin/meals/{meal['id']}/images"
    for ids in [[option_id, other_id], [option_id, option_id]]:
        response = await client.patch(path, headers=admin, json={
            "image_url": "https://images.example.test/should-not-save.jpg",
            "options": [{"id": value, "image_url": "https://images.example.test/soup.jpg"} for value in ids],
        })
        assert response.status_code == 422
        meals = (await client.get("/v1/admin/meals", headers=admin)).json()
        assert next(item for item in meals if item["id"] == meal["id"]) == meal
