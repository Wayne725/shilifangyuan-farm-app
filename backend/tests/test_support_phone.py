import pytest
from sqlalchemy import select

from app.models import AdminAudit, SystemSetting
from app.platform_settings import PLATFORM_KEY
from app.routers.platform import platform_router
from tests.support import auth_headers
from tests.test_meal_booking_policy import booking_context


@pytest.mark.asyncio
async def test_support_phone_public_display_admin_only_edit_and_legacy_preservation(booking_context):
    client, session, buyer, admin, _, _ = booking_context
    client._transport.app.include_router(platform_router)
    public = await client.get("/v1/platform")
    assert public.json()["support_phone"] == ""
    assert public.headers["cache-control"] == "no-store"

    payload = {"support_phone": "  +886 (2) 2345-6789  ", "reason": "設定退單聯絡電話"}
    forbidden = await client.put("/v1/admin/platform", headers=auth_headers(buyer), json=payload)
    assert forbidden.status_code == 403
    assert await session.get(SystemSetting, PLATFORM_KEY) is None

    updated = await client.put("/v1/admin/platform", headers=auth_headers(admin), json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["support_phone"] == "+886 (2) 2345-6789"
    assert (await client.get("/v1/platform")).json()["support_phone"] == "+886 (2) 2345-6789"
    legacy_update = await client.put("/v1/admin/platform", headers=auth_headers(admin), json={"orders_open": False, "reason": "舊介面暫停接單"})
    assert legacy_update.status_code == 200
    assert legacy_update.json()["support_phone"] == "+886 (2) 2345-6789"

    cleared = await client.put("/v1/admin/platform", headers=auth_headers(admin), json={"support_phone": "", "reason": "移除舊號碼"})
    assert cleared.status_code == 200
    assert (await client.get("/v1/platform")).json()["support_phone"] == ""
    audits = (await session.scalars(select(AdminAudit).where(AdminAudit.action == "platform.settings_updated"))).all()
    assert len(audits) == 3
    assert audits[-1].data["previous"]["support_phone"] == "+886 (2) 2345-6789"
    assert audits[-1].data["current"]["support_phone"] == ""


@pytest.mark.parametrize("phone", ["javascript:alert(1)", "tel:0223456789", "02/23456789", "123", "1234567890123456", "02\n23456789", "02+23456789"])
@pytest.mark.asyncio
async def test_invalid_support_phone_is_rejected_without_saving(booking_context, phone):
    client, session, _, admin, _, _ = booking_context
    client._transport.app.include_router(platform_router)
    response = await client.put("/v1/admin/platform", headers=auth_headers(admin), json={"support_phone": phone, "reason": "格式驗證"})
    assert response.status_code == 422
    assert await session.get(SystemSetting, PLATFORM_KEY) is None
