import pytest

from app.routers.platform import platform_router
from tests.support import auth_headers
from tests.test_meal_booking_policy import booking_context  # noqa: F401


@pytest.mark.asyncio
async def test_meal_order_notice_is_published_only_when_enabled_and_survives_other_edits(booking_context):
    client, _session, _buyer, admin, _event, _body = booking_context
    client._transport.app.include_router(platform_router)
    headers = auth_headers(admin)
    assert (await client.get("/v1/platform")).json()["meal_order_notice"] is None
    assert (await client.get("/v1/admin/platform", headers=headers)).json()["meal_order_notice"] == {
        "enabled": False, "title": "訂購須知", "message": ""}

    notice = {"enabled": True, "title": " 訂購須知 ", "message": "本週新品八折\n颱風天請留意停餐公告"}
    saved = await client.put("/v1/admin/platform", headers=headers, json={
        "expected_version": 0, "meal_order_notice": notice, "reason": "設定訂購須知"})
    assert saved.status_code == 200, saved.text
    assert (await client.get("/v1/platform")).json()["meal_order_notice"] == {
        "title": "訂購須知", "message": "本週新品八折\n颱風天請留意停餐公告"}

    other = await client.put("/v1/admin/platform", headers=headers, json={
        "expected_version": 1, "orders_open": True, "reason": "舊版頁面只改接單"})
    assert other.status_code == 200, other.text
    assert other.json()["meal_order_notice"]["enabled"] is True

    too_long = await client.put("/v1/admin/platform", headers=headers, json={
        "expected_version": 2, "meal_order_notice": {**notice, "message": "字" * 1001}, "reason": "太長"})
    assert too_long.status_code == 422
    off = await client.put("/v1/admin/platform", headers=headers, json={
        "expected_version": 2, "meal_order_notice": {**notice, "enabled": False}, "reason": "關閉"})
    assert off.status_code == 200, off.text
    assert (await client.get("/v1/platform")).json()["meal_order_notice"] is None
