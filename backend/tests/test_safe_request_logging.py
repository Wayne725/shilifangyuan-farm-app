import logging
import os
from pathlib import Path
import subprocess
import sys

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app import main
from tests.support import make_test_settings


@pytest.fixture(autouse=True)
def observe_application_logger(monkeypatch):
    # Earlier Alembic fileConfig calls can disable already imported app loggers.
    monkeypatch.setattr(logging.getLogger("app.main"), "disabled", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [200, 404, 410, 500])
async def test_application_logs_redact_capability_paths_on_all_responses(monkeypatch, caplog, status_code):
    monkeypatch.setattr(main, "get_settings", lambda: make_test_settings())
    application = main.create_app()

    @application.get("/logistics/{token}/log-probe")
    async def probe(token: str):
        if status_code == 500:
            raise RuntimeError("synthetic failure")
        if status_code != 200:
            raise HTTPException(status_code, "synthetic rejection")
        return {"status": "ok"}

    token = "synthetic-private-capability-credential"
    async with AsyncClient(transport=ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
        with caplog.at_level(logging.INFO, logger="app.main"):
            response = await client.get(f"/logistics/{token}/log-probe?token=synthetic-query-credential",
                                        headers={"X-Request-ID": "security-probe-request"})
    assert response.status_code == status_code
    records = [record.getMessage() for record in caplog.records if record.name == "app.main"]
    assert records and "request_id=security-probe-request" in records[-1]
    assert token not in " ".join(records)
    assert "synthetic-query-credential" not in " ".join(records)
    assert "{token}" in " ".join(records)


@pytest.mark.asyncio
async def test_unmatched_capability_path_is_redacted_without_a_route_template(monkeypatch, caplog):
    monkeypatch.setattr(main, "get_settings", lambda: make_test_settings())
    application = main.create_app()
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        with caplog.at_level(logging.INFO, logger="app.main"):
            response = await client.get("/logistics/private-unmatched-token/missing")
    assert response.status_code == 404
    assert "private-unmatched-token" not in caplog.text
    assert "/logistics/{token}/missing" in caplog.text


@pytest.mark.parametrize("target,expected,status_code", [
    ("/logistics/private-credential/select?token=query-credential", "/logistics/{token}/select", 200),
    ("/logistics/private-credential/select", "/logistics/{token}/select", 404),
    ("/logistics/private-credential/select", "/logistics/{token}/select", 410),
    ("/logistics/private-credential/select", "/logistics/{token}/select", 500),
    ("/logistics/selection-result?token=query-credential", "/logistics/selection-result", 200),
    ("/payments/private-credential/checkout?token=query-credential", "/payments/{attempt_id}/checkout", 200),
    ("/reset-password?token=query-credential", "/reset-password", 404),
    ("/verify-email?token=query-credential", "/verify-email", 404),
])
def test_uvicorn_default_access_logger_redacts_path_and_query_credentials(target, expected, status_code):
    script = """
import logging
import logging.config
import sys
from uvicorn.config import LOGGING_CONFIG
import app.config as config

logging.config.dictConfig(LOGGING_CONFIG)
config.get_settings = lambda: config.Settings(_env_file=None, environment="test")
import app.main
logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d',
    "127.0.0.1:1234", "GET", sys.argv[1], "1.1", int(sys.argv[2]))
"""
    result = subprocess.run([sys.executable, "-c", script, target, str(status_code)],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, "APP_ENV": "test", "DATABASE_URL": "sqlite+aiosqlite:///:memory:"},
                            capture_output=True, text=True, check=True)
    output = result.stdout + result.stderr
    assert "private-credential" not in output and "query-credential" not in output
    assert f'"GET {expected} HTTP/1.1" {status_code}' in output
