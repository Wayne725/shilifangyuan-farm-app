import importlib.util
from pathlib import Path
import socket
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "order_load_test", Path(__file__).resolve().parents[1] / "scripts" / "order_load_test.py"
)
load_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load_test)


@pytest.mark.parametrize("url", [
    "postgresql://u:p@127.0.0.1:55441/shilifangyuan_load_test",
    "postgresql+asyncpg://u:p@localhost:5432/shilifangyuan_test",
    "postgresql://u:p@[::1]:55441/shilifangyuan_test",
])
def test_load_database_accepts_only_named_local_databases(url):
    assert load_test.validate_database_url(url).startswith("postgresql+asyncpg://")


@pytest.mark.parametrize("url", [
    "postgresql://u:p@production.example.com:5432/shilifangyuan_load_test",
    "postgresql://u:p@127.0.0.1:5432/shilifangyuan",
    "postgresql://u:p@127.0.0.1:5432/postgres",
    "postgresql://u:p@127.0.0.1/shilifangyuan_test",
    "postgresql://u:p@127.0.0.1:5432/shilifangyuan_test?host=production.example.com",
    "postgresql://u:p@127.0.0.1:5432/shilifangyuan_test#anything",
    "postgresql://u:p@127.0.0.1.evil:5432/shilifangyuan_test",
    "postgresql://u:p@127.1:5432/shilifangyuan_test",
    "postgresql://u:p@0.0.0.0:5432/shilifangyuan_test",
    "sqlite+aiosqlite:///test.db",
])
def test_load_database_rejects_external_or_unscoped_targets(url):
    with pytest.raises(ValueError):
        load_test.validate_database_url(url)


@pytest.mark.parametrize("schema", ["public", "load_x", "load_" + "a" * 32 + '";DROP SCHEMA public CASCADE;--'])
def test_load_schema_cannot_address_existing_or_inject_sql(schema):
    with pytest.raises(ValueError):
        load_test.validate_schema(schema)


def test_load_generated_schema_is_accepted():
    assert load_test.validate_schema("load_" + "a" * 32) == "load_" + "a" * 32


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8765", "http://production.example.com:8765",
    "http://127.0.0.1.evil:8765", "http://localhost:8765", "http://127.0.0.1",
    "http://127.0.0.1:8765/v1", "http://u:p@127.0.0.1:8765", "http://127.0.0.1:8765?target=remote",
])
def test_load_http_rejects_any_non_owned_origin(url):
    with pytest.raises(ValueError):
        load_test.validate_http_target(url)


def test_load_http_accepts_explicit_numeric_loopback_port():
    assert load_test.validate_http_target("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"


def test_external_connection_and_dns_fail_closed(monkeypatch):
    # monkeypatch tracks the originals so the socket restrictions cannot leak to other tests.
    for attribute in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, attribute, getattr(socket.socket, attribute))
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    load_test.block_external_connections()
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match="non-loopback"):
            connection.connect(("1.1.1.1", 443))
        with pytest.raises(RuntimeError, match="non-loopback"):
            connection.connect_ex(("smtp.example.com", 465))
    with pytest.raises(RuntimeError, match="non-loopback DNS"):
        socket.getaddrinfo("pay.example.com", 443)


def test_load_settings_ignore_environment_and_local_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINTER_ENABLED", "true")
    monkeypatch.setenv("RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("RAYGATE_PAYMENT_KEY_HEX", "real-secret-must-not-be-used")
    (tmp_path / ".env").write_text("PRINTER_ENABLED=true\nRECONCILIATION_ENABLED=true\nRESEND_API_KEY=real-secret\n")
    monkeypatch.chdir(tmp_path)
    import app.config as config
    monkeypatch.setattr(config, "get_settings", config.get_settings)
    settings = load_test.configure_runtime("postgresql+asyncpg://u:p@127.0.0.1:55441/shilifangyuan_load_test")
    assert settings.printer_enabled is False
    assert settings.printer_worker_enabled is False
    assert settings.reconciliation_enabled is False
    assert settings.raygate_payment_key_hex == ""
    assert settings.resend_api_key == ""
    assert settings.database_pool_size == settings.database_max_overflow == 5


def test_actual_server_entrypoint_uses_guarded_asyncio_loop():
    probe = """
import runpy
from unittest.mock import patch
module = runpy.run_path('scripts/order_load_test.py', run_name='load_guard_probe')
with patch('uvicorn.run') as run:
    module['serve']('postgresql+asyncpg://u:p@127.0.0.1:55441/shilifangyuan_load_test', 'load_' + 'a' * 32, 8765)
    assert run.call_args.kwargs['loop'] == 'asyncio'
    assert run.call_args.kwargs['host'] == '127.0.0.1'
    assert run.call_args.kwargs['workers'] == 1
"""
    completed = subprocess.run([sys.executable, "-c", probe], cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
