import pytest
from sqlalchemy import func, select

from app.models import AdminAudit, SystemSetting
from app.platform_settings import PLATFORM_KEY
from app.routers.platform import platform_router
from tests.support import auth_headers
from tests.test_meal_booking_policy import booking_context


@pytest.mark.asyncio
async def test_stale_admin_cannot_overwrite_newer_platform_settings(booking_context):
    client, session, _, admin, _, _ = booking_context
    client._transport.app.include_router(platform_router)
    headers = auth_headers(admin)
    original = await client.get("/v1/admin/platform", headers=headers)
    assert original.status_code == 200
    assert original.json()["version"] == 0
    first = await client.put("/v1/admin/platform", headers=headers, json={
        "expected_version": 0, "support_phone": "0223456789", "reason": "設定聯絡電話",
    })
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1
    stale = await client.put("/v1/admin/platform", headers=headers, json={
        "expected_version": 0, "orders_open": False, "reason": "尚未重新讀取的管理員",
    })
    assert stale.status_code == 409
    current = await client.get("/v1/admin/platform", headers=headers)
    assert current.json()["orders_open"] is True
    assert current.json()["support_phone"] == "0223456789"
    assert current.json()["version"] == 1
    assert await session.scalar(select(func.count(AdminAudit.id)).where(
        AdminAudit.action == "platform.settings_updated",
    )) == 1


@pytest.mark.asyncio
async def test_existing_legacy_settings_and_legacy_updates_advance_version(booking_context):
    client, session, _, admin, _, _ = booking_context
    client._transport.app.include_router(platform_router)
    session.add(SystemSetting(key=PLATFORM_KEY, value={"orders_open": False, "support_phone": "0223456789"}))
    await session.commit()
    headers = auth_headers(admin)
    assert (await client.get("/v1/admin/platform", headers=headers)).json()["version"] == 0
    updated = await client.put("/v1/admin/platform", headers=headers, json={
        "orders_open": True, "reason": "舊版管理頁仍可恢復接單",
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 1
    assert updated.json()["support_phone"] == "0223456789"
    public = await client.get("/v1/platform")
    assert public.json()["orders_open"] is True
    assert "_version" not in public.json()
    assert "expected_version" not in (await session.get(SystemSetting, PLATFORM_KEY)).value
    stale = await client.put("/v1/admin/platform", headers=headers, json={
        "expected_version": 0, "orders_open": False, "reason": "舊草稿",
    })
    assert stale.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [-1, "invalid"])
async def test_invalid_version_never_creates_settings(booking_context, version):
    client, session, _, admin, _, _ = booking_context
    client._transport.app.include_router(platform_router)
    response = await client.put("/v1/admin/platform", headers=auth_headers(admin), json={
        "expected_version": version, "reason": "無效版本",
    })
    assert response.status_code == 422
    assert await session.get(SystemSetting, PLATFORM_KEY) is None
