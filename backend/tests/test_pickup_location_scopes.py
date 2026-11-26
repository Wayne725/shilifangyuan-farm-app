from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from app.models import Order, PickupLocation
from tests.support import auth_headers
from tests.test_group_checkout_contract import group_checkout, join, join_body  # noqa: F401
from tests.test_meal_booking_policy import booking_context  # noqa: F401
from tests.test_real_cooperative_records import real_data_context  # noqa: F401


async def add_scoped_locations(session):
    store = PickupLocation(code="store", name="合作社門市")
    building = PickupLocation(code="science", name="綜合三館", retail_pickup=False)
    other = PickupLocation(code="other", name="校內其他地點", requires_pickup_note=True, retail_pickup=False)
    session.add_all([store, building, other])
    await session.commit()
    return store, building, other


async def test_regular_meals_use_meal_points_and_require_note_where_set(booking_context):
    client, session, buyer, _admin, event, body = booking_context
    _store, building, other = await add_scoped_locations(session)
    base = f"/v1/meal-events/{event.id}"
    headers = auth_headers(buyer)
    meal_point = await client.post(f"{base}/quote", json={**body, "pickup_location_id": building.id}, headers=headers)
    assert meal_point.status_code == 200, meal_point.text
    missing_note = await client.post(f"{base}/quote", json={**body, "pickup_location_id": other.id}, headers=headers)
    assert missing_note.status_code == 422 and "實際取餐位置" in missing_note.json()["detail"], missing_note.text
    noted = {**body, "pickup_location_id": other.id, "kitchen_note": "工科館一樓", "client_request_id": str(uuid4())}
    created = await client.post(f"{base}/orders", json=noted, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["venue_name"] == "校內其他地點"
    assert created.json()["kitchen_note"] == "工科館一樓"


async def test_daily_items_and_group_buys_only_take_retail_points(group_checkout):
    c = group_checkout
    store, building, other = await add_scoped_locations(c.session)
    order_body = {
        "items": [{"product_id": c.product_ids[1], "quantity": 1}],
        "contact_email": "daily-buyer@example.com", "fulfillment_method": "cooperative_pickup",
    }
    for location in (building, other):
        daily = await c.client.post("/v1/orders", headers=c.buyer_headers[0], json={**order_body, "pickup_location_id": location.id})
        group = await join(c, join_body(pickup_location_id=location.id))
        assert [daily.status_code, group.status_code] == [422, 422], (daily.text, group.text)
    assert await c.session.scalar(select(Order.id)) is None
    limited = await c.client.patch(f"/v1/group-campaigns/{c.campaign_id}", headers=c.admin_headers,
                                   json={"pickup_location_ids": [building.id]})
    assert limited.status_code == 422, limited.text
    daily = await c.client.post("/v1/orders", headers=c.buyer_headers[0], json={**order_body, "pickup_location_id": store.id})
    group = await join(c, join_body(pickup_location_id=store.id))
    assert [daily.status_code, group.status_code] == [201, 201], (daily.text, group.text)


async def test_admin_keeps_note_points_out_of_retail(real_data_context):
    client = real_data_context["client"]
    admin = auth_headers(real_data_context["admin"])
    base = "/v1/admin/pickup-locations"
    created = await client.post(base, json={"code": "other-spot", "name": "校內其他地點測試", "requires_pickup_note": True}, headers=admin)
    assert created.status_code == 201, created.text
    assert created.json()["business_meal_only"] is False and created.json()["retail_pickup"] is False
    plain = await client.post(base, json={"code": "plain-spot", "name": "一般取貨點測試"}, headers=admin)
    assert plain.json()["retail_pickup"] is True
    meals_only = await client.patch(f"{base}/{plain.json()['id']}", json={"retail_pickup": False}, headers=admin)
    assert meals_only.status_code == 200 and meals_only.json()["retail_pickup"] is False
    business = await client.patch(f"{base}/{created.json()['id']}", json={"business_meal_only": True}, headers=admin)
    assert business.status_code == 200 and business.json()["retail_pickup"] is False
    reopened = await client.patch(f"{base}/{plain.json()['id']}", json={"retail_pickup": True}, headers=admin)
    assert reopened.status_code == 200 and reopened.json()["retail_pickup"] is True
    note = await client.patch(f"{base}/{plain.json()['id']}", json={"requires_pickup_note": True}, headers=admin)
    assert note.status_code == 200 and note.json()["retail_pickup"] is False
    conflict = await client.patch(f"{base}/{plain.json()['id']}", json={"retail_pickup": True}, headers=admin)
    assert conflict.status_code == 422, conflict.text
    meal_codes = {row["code"] for row in (await client.get("/v1/pickup-locations")).json()}
    retail_codes = {row["code"] for row in (await client.get("/v1/pickup-locations", params={"retail": True})).json()}
    assert "plain-spot" in meal_codes and "plain-spot" not in retail_codes
    assert "other-spot" not in meal_codes


def test_scope_migration_keeps_current_checkouts_and_rolls_back_meal_points_to_business(tmp_path, monkeypatch):
    from app.config import Settings

    path = tmp_path / "pickup-scopes.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{path}")
    previous = "0074_pickup_location_limits"
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO pickup_locations (id, code, name, address, instructions, sort_order, is_active,
                    business_meal_only, requires_pickup_note, created_at, updated_at)
                VALUES ('store', 'store', '門市', '', '', 1, true, false, false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                       ('hall', 'hall', '綜合三館', '', '', 2, true, true, false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                       ('other', 'other', '校內其他地點', '', '', 3, true, true, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """))
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.execute(text(
                "SELECT id, retail_pickup FROM pickup_locations WHERE id IN ('store', 'hall', 'other') ORDER BY sort_order"
            )).all() == [
                ("store", 1), ("hall", 0), ("other", 0)]
            connection.execute(text("UPDATE pickup_locations SET business_meal_only = false WHERE id IN ('hall', 'other')"))
        command.downgrade(config, previous)
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT id, business_meal_only, requires_pickup_note FROM pickup_locations"
                " WHERE id IN ('store', 'hall', 'other') ORDER BY sort_order"
            )).all() == [("store", 0, 0), ("hall", 1, 0), ("other", 1, 1)]
    finally:
        engine.dispose()
