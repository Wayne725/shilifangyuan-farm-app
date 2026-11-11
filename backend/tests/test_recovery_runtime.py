import base64
from datetime import timedelta
import json
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

import app.jobs as jobs
import app.main as main_module
import app.printer_worker as printing
import app.startup as startup
from app.auth import create_token
from app.config import get_settings
from app.database import get_session
from app.models import Order, OutboxEvent, PrintJob, User, UserRole
from tests.support import make_test_settings
from tests.test_integrations import make_regular_order


@pytest.mark.parametrize("environment", ["preview", "production"])
def test_remote_recovery_starts_without_provider_credentials(environment, monkeypatch):
    settings = make_test_settings(
        environment=environment,
        recovery_mode=True,
        database_url="postgresql+asyncpg://localhost/recovery",
        jwt_secret="j" * 32,
        internal_reconcile_secret="r" * 32,
        demo_admin_password="isolated-admin-password",
        demo_member_password="isolated-member-password",
        demo_nonmember_password="isolated-customer-password",
        pii_encryption_keys_json=json.dumps({"v1": base64.b64encode(b"p" * 32).decode()}),
        pii_encryption_current_version="v1",
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    assert main_module.create_app().state.recovery_read_only

    unsafe_auth = settings.model_copy(update={"jwt_secret": "short", "web_base_url": "http://localhost"})
    with pytest.raises(RuntimeError, match="JWT_SECRET.*WEB_BASE_URL"):
        unsafe_auth.validate_runtime_secrets()

    if environment == "production":
        unsafe_database = settings.model_copy(update={
            "database_url": "sqlite+aiosqlite:///:memory:",
            "pii_encryption_keys_json": "{}",
        })
        with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEYS_JSON.*DATABASE_URL"):
            unsafe_database.validate_runtime_secrets()

    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        settings.model_copy(update={"recovery_mode": False}).validate_runtime_secrets()


@pytest.mark.asyncio
async def test_recovery_mode_keeps_order_reads_private_and_blocks_dispatch(database_session, monkeypatch):
    monkeypatch.setenv("RECOVERY_MODE", "true")
    settings = make_test_settings(reconciliation_enabled=True, printer_enabled=True, printer_worker_enabled=True)
    assert settings.recovery_mode is True
    owner, _, order = await make_regular_order(database_session)
    order.contact_email = "buyer@example.com"
    another = User(email="different@example.com", display_name="另一位買家", password_hash="test")
    admin = User(email="admin@example.com", display_name="測試管理員", password_hash="test", user_role=UserRole.ADMIN)
    database_session.add_all([another, admin])
    await database_session.commit()

    def auth(user):
        token = create_token(user, "access", timedelta(minutes=5), settings)
        return {"Authorization": f"Bearer {token}"}

    async def snapshot():
        return [await database_session.scalar(select(func.count()).select_from(model))
                for model in (Order, OutboxEvent, PrintJob)]

    async def ready(_timeout):
        assert await database_session.scalar(text("SELECT 1")) == 1

    before = await snapshot()
    seed = AsyncMock()
    reconcile_worker = Mock()
    printer_worker = Mock()
    request_scheduler = Mock()
    reconcile = AsyncMock()
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "check_database_connection", ready)
    monkeypatch.setattr(main_module, "seed_demo_data", seed)
    monkeypatch.setattr(main_module, "periodic_reconciliation", reconcile_worker)
    monkeypatch.setattr(main_module, "periodic_printing", printer_worker)
    monkeypatch.setattr(main_module, "schedule_background_reconcile", request_scheduler)
    monkeypatch.setattr(jobs, "reconcile_once", reconcile)
    app = main_module.create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: database_session
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/health", "/v1/platform"):
                assert (await client.get(path)).status_code == 200
            readiness = await client.get("/ready")
            assert readiness.status_code == 503
            assert readiness.headers["Cache-Control"] == "no-store"
            assert readiness.json() == {"status": "not_ready", "database": "available", "reason": "recovery_mode"}
            detail = f"/v1/orders/{order.id}"
            assert (await client.get(detail)).status_code == 401
            assert (await client.get(detail, headers=auth(another))).status_code == 404
            assert (await client.get(detail, headers=auth(owner))).json()["id"] == order.id
            assert (await client.get("/v1/orders", headers=auth(owner))).status_code == 200
            reconcile_status = "/v1/admin/reconciliation-status"
            assert (await client.get(reconcile_status, headers=auth(owner))).status_code == 403
            assert (await client.get(reconcile_status, headers=auth(admin))).status_code == 200
            for method, path in (
                ("POST", "/internal/reconcile"),
                ("POST", "/payments/result"),
                ("POST", "/webhooks/ecpay/payment"),
                ("POST", "/webhooks/raygate/payment"),
                ("GET", "/payments/example/checkout"),
                ("GET", "/payments/raygate/result?attempt_id=example"),
                ("POST", "/v1/orders"),
                ("POST", "/v1/auth/login"),
                ("POST", f"/v1/admin/printers/orders/{order.id}/print"),
                ("GET", "/v1/orders/future-dynamic-action"),
            ):
                result = await client.request(method, path, headers={**auth(owner), "X-Reconcile-Secret": settings.internal_reconcile_secret})
                assert result.status_code == 503
                assert result.headers["X-Error-Code"] == "recovery_read_only"
                assert result.headers["Cache-Control"] == "no-store"
                assert "X-Request-ID" in result.headers
    assert await snapshot() == before
    for target in (seed, reconcile_worker, printer_worker, request_scheduler, reconcile):
        target.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_workers_stop_before_using_database_or_transport(monkeypatch):
    settings = make_test_settings(recovery_mode=True, reconciliation_enabled=True, printer_enabled=True, printer_worker_enabled=True)
    session, factory, client = Mock(), Mock(), Mock()
    with pytest.raises(RuntimeError, match="disabled in recovery mode"):
        await jobs.reconcile_once(session, settings)
    with pytest.raises(RuntimeError, match="disabled in recovery mode"):
        jobs.schedule_background_reconcile(settings)
    assert await printing.process_print_job(settings, factory=factory, client=client) is False
    scheduler = Mock(side_effect=AssertionError("worker scheduled"))
    monkeypatch.setattr(jobs.asyncio, "create_task", scheduler)
    async with jobs.periodic_reconciliation(settings), printing.periodic_printing(settings):
        pass
    monkeypatch.setattr(printing, "get_settings", lambda: settings)
    await printing.main()
    assert not session.mock_calls
    assert not factory.mock_calls
    assert not client.mock_calls
    scheduler.assert_not_called()


@pytest.mark.parametrize(
    "recovery_mode, command, stages",
    [(True, "prepare", []), (True, "migrate", ["migrate"]), (False, "prepare", ["migrate", "seed"])],
)
def test_recovery_prepare_skips_implicit_migration_and_seeding(monkeypatch, recovery_mode, command, stages):
    settings = make_test_settings(recovery_mode=recovery_mode).model_copy(update={"environment": "preview"})
    monkeypatch.setattr(startup, "get_settings", lambda: settings)
    monkeypatch.setattr(startup, "check_database_connection", AsyncMock(return_value=False))
    monkeypatch.setattr(type(settings), "validate_runtime_secrets", lambda self: None)
    monkeypatch.setattr("sys.argv", ["startup", command])
    called = []
    monkeypatch.setattr(startup, "migrate_database", lambda: called.append("migrate"))
    monkeypatch.setattr(startup, "seed_preview_database", lambda: called.append("seed"))
    startup.main()
    assert called == stages
