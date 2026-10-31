import httpx
import pytest
from fastapi import HTTPException

import app.main as main_module
from tests.support import make_test_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["quote_changed", "idempotency_conflict"])
async def test_trusted_web_origin_can_read_checkout_error_code(monkeypatch, code):
    settings = make_test_settings(web_base_url="https://shop.example.test")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "should_reconcile_now", lambda: False)
    application = main_module.create_app()

    @application.post("/v1/isolated-error-code")
    async def error_code():
        raise HTTPException(409, "訂單條件已更新", headers={"X-Error-Code": code})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver") as client:
        response = await client.post("/v1/isolated-error-code", headers={"Origin": "https://shop.example.test"})
        assert response.status_code == 409
        assert response.headers["X-Error-Code"] == code
        assert response.headers["Access-Control-Allow-Origin"] == "https://shop.example.test"
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
        exposed = {value.strip().lower() for value in response.headers.get("Access-Control-Expose-Headers", "").split(",")}
        assert "x-error-code" in exposed
        denied = await client.post("/v1/isolated-error-code", headers={"Origin": "https://untrusted.example.test"})
        assert "Access-Control-Allow-Origin" not in denied.headers
