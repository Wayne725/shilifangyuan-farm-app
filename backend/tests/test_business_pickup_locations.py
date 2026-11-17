from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.models import AdminAudit, Order, PickupLocation
from tests.support import auth_headers
from tests.test_business_meals import business_body
from tests.test_group_checkout_contract import group_checkout, join, join_body
from tests.test_meal_booking_policy import booking_context
from tests.test_real_cooperative_records import real_data_context


async def test_admin_configures_business_pickup_and_public_list_filters_scope(real_data_context):
    client = real_data_context["client"]
    session = real_data_context["session"]
    admin = auth_headers(real_data_context["admin"])
    buyer = auth_headers(real_data_context["customer"])
    base = "/v1/admin/pickup-locations"
    public = "/v1/pickup-locations"

    general = await client.post(base, json={"code": "gate", "name": "校門口"}, headers=admin)
    invalid = await client.post(base, json={"code": "other", "name": "校內其他地點", "requires_pickup_note": True}, headers=admin)
    restricted = await client.post(base, json={
        "code": "other", "name": "校內其他地點", "business_meal_only": True, "requires_pickup_note": True,
    }, headers=admin)
    inactive = await client.post(base, json={
        "code": "hall", "name": "校內會堂", "is_active": False, "business_meal_only": True,
    }, headers=admin)
    assert [result.status_code for result in (general, invalid, restricted, inactive)] == [201, 422, 201, 201]
    assert general.json()["business_meal_only"] is general.json()["requires_pickup_note"] is False
    assert {row["code"] for row in (await client.get(public)).json()} == {"gate"}
    business_locations = {row["code"]: row for row in (await client.get(public, params={"business_meal": True})).json()}
    assert set(business_locations) == {"gate", "other"}
    assert business_locations["other"]["requires_pickup_note"] is True

    path = f"{base}/{restricted.json()['id']}"
    assert (await client.patch(path, json={"is_active": False}, headers=buyer)).status_code == 403
    assert (await client.patch(path, json={"business_meal_only": False}, headers=admin)).status_code == 422
    assert (await client.patch(path, json={"requires_pickup_note": None}, headers=admin)).status_code == 422
    changed = await client.patch(path, json={"business_meal_only": False, "requires_pickup_note": False}, headers=admin)
    assert changed.status_code == 200, changed.text
    assert {row["code"] for row in (await client.get(public)).json()} == {"gate", "other"}
    audit = await session.scalar(select(AdminAudit).where(
        AdminAudit.aggregate_id == changed.json()["id"], AdminAudit.action == "pickup_location.updated",
    ))
    assert audit.data["before"] == {"business_meal_only": True, "requires_pickup_note": True}
    assert audit.data["after"] == {"business_meal_only": False, "requires_pickup_note": False}


@pytest.mark.parametrize("requires_note", [False, True])
async def test_business_meal_validates_scope_and_saves_pickup_note_with_order(booking_context, requires_note):
    client, session, buyer, _admin, event, body = booking_context
    location = PickupLocation(
        code="business-room", name="校內其他地點" if requires_note else "校內會堂",
        business_meal_only=True, requires_pickup_note=requires_note,
    )
    session.add(location)
    await session.commit()
    base = f"/v1/meal-events/{event.id}"
    headers = auth_headers(buyer)
    options = {"pickup_location_id": location.id, "client_request_id": str(uuid4())}
    for action in ("quote", "orders"):
        regular = await client.post(f"{base}/{action}", json={**body, **options}, headers=headers)
        assert regular.status_code == 422 and "找不到可用的取餐點" in regular.json()["detail"], regular.text
        if requires_note:
            no_note = await client.post(f"{base}/{action}", json=business_body(body, **options, kitchen_note="  "), headers=headers)
            assert no_note.status_code == 422 and "實際取餐位置" in no_note.json()["detail"], no_note.text
    pickup_note = "工學院一樓前台" if requires_note else None
    business = business_body(body, **options, kitchen_note=pickup_note)
    quote = await client.post(f"{base}/quote", json=business)
    created = await client.post(f"{base}/orders", json=business, headers=headers)
    assert quote.status_code == 200, quote.text
    assert created.status_code == 201, created.text
    assert quote.json()["pickup"]["location_id"] == location.id
    assert created.json()["venue_name"] == location.name
    assert created.json()["kitchen_note"] == pickup_note
    location.is_active = False
    location.name = "更新後地點"
    await session.commit()
    replay = await client.post(f"{base}/orders", json=business, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == created.json()["id"]
    assert replay.json()["venue_name"] == created.json()["venue_name"]
    assert replay.json()["kitchen_note"] == pickup_note


async def test_daily_and_group_checkout_cannot_select_business_location(group_checkout):
    context = group_checkout
    location = PickupLocation(code="meeting-room", name="會餐場地", business_meal_only=True)
    common = PickupLocation(code="shared-gate", name="共同取貨櫃檯")
    context.session.add_all([location, common])
    await context.session.commit()
    regular_body = {
        "items": [{"product_id": context.product_ids[1], "quantity": 1}],
        "contact_email": "daily-buyer@example.com",
        "fulfillment_method": "cooperative_pickup",
    }
    regular = await context.client.post(
        "/v1/orders", headers=context.buyer_headers[0], json={**regular_body, "pickup_location_id": location.id},
    )
    group = await join(context, join_body(pickup_location_id=location.id))
    assert [regular.status_code, group.status_code] == [422, 422]
    assert await context.session.scalar(select(Order.id)) is None
    ordinary = await context.client.post(
        "/v1/orders", headers=context.buyer_headers[0], json={**regular_body, "pickup_location_id": common.id},
    )
    grouped = await join(context, join_body(pickup_location_id=common.id))
    assert [ordinary.status_code, grouped.status_code] == [201, 201]


def test_pickup_scope_migration_preserves_existing_locations_and_hides_restricted_points_on_rollback(tmp_path, monkeypatch):
    from app.config import Settings

    path = tmp_path / "pickup-scope.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{path}")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite:///{path}")
    previous = "0070_meal_schedule_weekend"
    try:
        command.upgrade(config, previous)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO pickup_locations (id, code, name, address, instructions, sort_order, is_active, created_at, updated_at)
                VALUES ('regular', 'gate', '校門口', '', '', 1, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                       ('business', 'room', '會議室', '', '', 2, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """))
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.execute(text("""
                SELECT id, is_active, business_meal_only, requires_pickup_note
                FROM pickup_locations WHERE id IN ('regular', 'business') ORDER BY sort_order
            """)).all() == [("regular", 1, 0, 0), ("business", 1, 0, 0)]
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("UPDATE pickup_locations SET requires_pickup_note = true WHERE id = 'regular'"))
            connection.execute(text("""
                UPDATE pickup_locations SET business_meal_only = true, requires_pickup_note = true WHERE id = 'business'
            """))
        command.downgrade(config, previous)
        with engine.connect() as connection:
            assert connection.execute(text("""
                SELECT id, is_active FROM pickup_locations WHERE id IN ('regular', 'business') ORDER BY sort_order
            """)).all() == [("regular", 1), ("business", 0)]
    finally:
        engine.dispose()
