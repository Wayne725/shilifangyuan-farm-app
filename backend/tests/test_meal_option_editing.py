from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import OrderItem, OrderItemOption
from tests.support import auth_headers, tomorrow_meal_timeline
from tests.test_v2_routers import v2_context


async def create_meal(context, name="主食餐盒"):
    response = await context["client"].post("/v1/admin/meals", headers=auth_headers(context["admin"]), json={
        "name": name, "price": 130, "option_groups": [{"name": "主食", "min_selections": 1, "options": [
            {"name": "飯"}, {"name": "壽司", "price_delta": 10},
        ]}],
    })
    assert response.status_code == 201, response.text
    return response.json()


def options_payload(meal):
    return {"expected_updated_at": meal["updated_at"], "option_groups": [{
        key: value for key, value in group.items() if key != "position"
    } for group in meal["option_groups"]]}


def clean_payload(meal):
    payload = options_payload(meal)
    for group in payload["option_groups"]:
        group["options"] = [{k: v for k, v in option.items() if k not in {"position", "image_url"}} for option in group["options"]]
    return payload


@pytest.mark.asyncio
async def test_meal_edit_version_keeps_utc_after_database_reload(v2_context):
    client, admin = v2_context["client"], auth_headers(v2_context["admin"])
    meal = await create_meal(v2_context)
    v2_context["session"].expire_all()
    meals = (await client.get("/v1/admin/meals", headers=admin)).json()
    reloaded = next(item for item in meals if item["id"] == meal["id"])
    assert reloaded["updated_at"] == meal["updated_at"]
    assert reloaded["updated_at"].endswith("Z")


@pytest.mark.asyncio
async def test_edits_update_live_choices_and_prices_but_preserve_order_snapshots(v2_context):
    client, admin = v2_context["client"], auth_headers(v2_context["admin"])
    meal = await create_meal(v2_context)
    option_id = meal["option_groups"][0]["options"][1]["id"]
    event = await client.post("/v1/admin/meal-events", headers=admin, json={
        "title": "主食編輯驗證", "location": "清華大學",
        **{key: value.isoformat() for key, value in tomorrow_meal_timeline(datetime.now(timezone.utc)).items()},
        "offerings": [{"meal_id": meal["id"], "price": 130, "capacity": 20}],
    })
    assert event.status_code == 201, event.text
    event_id, offering_id = event.json()["id"], event.json()["offerings"][0]["id"]
    assert (await client.post(f"/v1/admin/meal-events/{event_id}/publish", headers=admin)).status_code == 200
    order_payload = {"items": [{"offering_id": offering_id, "quantity": 1, "option_ids": [option_id]}], "contact_phone": "0912345678", "contact_email": "applicant@example.com"}
    order = await client.post(f"/v1/meal-events/{event_id}/orders", headers=auth_headers(v2_context["applicant"]), json=order_payload)
    assert order.status_code == 201, order.text
    assert order.json()["amount_total"] == 140
    edit = clean_payload(meal)
    edit["option_groups"][0]["options"][1].update(name="豆皮壽司", price_delta=25)
    edit["option_groups"][0]["options"].append({"name": "麵包", "price_delta": 5, "is_active": True})
    response = await client.put(f"/v1/admin/meals/{meal['id']}/options", headers=admin, json=edit)
    assert response.status_code == 200, response.text
    assert response.json()["option_groups"][0]["options"][1]["id"] == option_id
    quote = await client.post(f"/v1/meal-events/{event_id}/quote", json=order_payload)
    assert quote.status_code == 200, quote.text
    assert quote.json()["amount_total"] == 155
    snapshots = list(await v2_context["session"].scalars(select(OrderItemOption).join(OrderItem).where(OrderItem.order_id == order.json()["id"])))
    assert [(item.option_name, item.price_delta) for item in snapshots] == [("壽司", 10)]
    edit = clean_payload(response.json())
    edit["option_groups"][0]["options"][1]["is_active"] = False
    assert (await client.put(f"/v1/admin/meals/{meal['id']}/options", headers=admin, json=edit)).status_code == 200
    assert (await client.post(f"/v1/meal-events/{event_id}/quote", json=order_payload)).status_code == 422
    stale = await client.put(f"/v1/admin/meals/{meal['id']}/options", headers=admin, json=edit)
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_option_edits_validate_ids_rules_names_and_admin_permissions(v2_context):
    client, admin = v2_context["client"], auth_headers(v2_context["admin"])
    meal, other = await create_meal(v2_context), await create_meal(v2_context, "另一餐盒")
    payload = clean_payload(meal)
    path = f"/v1/admin/meals/{meal['id']}/options"
    assert (await client.put(path, json=payload)).status_code == 401
    assert (await client.put(path, headers=auth_headers(v2_context["applicant"]), json=payload)).status_code == 403
    invalids = []
    for field, value in [("max_selections", 3), ("min_selections", 2)]:
        draft = deepcopy(payload); draft["option_groups"][0][field] = value; invalids.append(draft)
    draft = deepcopy(payload); draft["option_groups"][0]["options"][0]["name"] = "  "; invalids.append(draft)
    draft = deepcopy(payload); draft["option_groups"][0]["options"][1]["name"] = "飯"; invalids.append(draft)
    draft = deepcopy(payload); draft["option_groups"][0]["id"] = other["option_groups"][0]["id"]; invalids.append(draft)
    draft = deepcopy(payload); draft["option_groups"][0]["options"][0]["id"] = other["option_groups"][0]["options"][0]["id"]; invalids.append(draft)
    draft = deepcopy(payload); draft["option_groups"][0]["options"].pop(); invalids.append(draft)
    for draft in invalids:
        response = await client.put(path, headers=admin, json=draft)
        assert response.status_code == 422, response.text
    meals = (await client.get("/v1/admin/meals", headers=admin)).json()
    assert next(item for item in meals if item["id"] == meal["id"]) == meal
    payload["option_groups"][0]["options"][0]["name"] = "壽司"
    payload["option_groups"][0]["options"][1]["name"] = "飯"
    response = await client.put(path, headers=admin, json=payload)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_empty_meal_can_add_and_disable_groups(v2_context):
    client, admin = v2_context["client"], auth_headers(v2_context["admin"])
    meal = (await client.post("/v1/admin/meals", headers=admin, json={"name": "新餐盒", "price": 120})).json()
    path = f"/v1/admin/meals/{meal['id']}/options"
    response = await client.put(path, headers=admin, json={"expected_updated_at": meal["updated_at"], "option_groups": [{
        "name": "主食", "min_selections": 1, "max_selections": 1, "options": [{"name": "飯"}],
    }]})
    assert response.status_code == 200, response.text
    edit = clean_payload(response.json())
    edit["option_groups"][0]["is_active"] = False
    edit["option_groups"][0]["options"][0]["is_active"] = False
    response = await client.put(path, headers=admin, json=edit)
    assert response.status_code == 200, response.text
