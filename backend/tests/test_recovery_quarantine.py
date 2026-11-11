from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import database, jobs, main
import app.routers.payments as payments
from app.config import get_settings
from app.integrations.payment_service import SQLAlchemyPaymentCallbackRepository, create_payment_attempt
from app.integrations.raygate import RAYGATE_IDENTIFIER, RayGateAdapter, hash_digest
from app.models import AdminAudit, ExternalEvent, OutboxEvent, OutboxStatus, PrintJob, RecoveryHold, User, UserRole
from app.recovery_operator import export_inventory
from app.recovery_quarantine import (
    Item, Manifest, RecoveryHoldActive, Resolution, arm_cutover, begin_cutover, has_active_hold,
    payment_targets, provider_payment_reference, resolve_hold,
)
from tests.support import api_test_context, make_test_settings
from tests.test_integrations import make_regular_order, payment_settings
from tests.test_raygate import payment_result, raygate_settings

CHECKPOINT = "a" * 64
PROOF = "b" * 64
RESOLUTION = "c" * 64


def decision(scope, reference, disposition="hold"):
    return {"target": {"scope": scope, "reference": reference}, "disposition": disposition, "evidence_sha256": PROOF}


def manifest(cutover_id, *decisions):
    return Manifest(cutover_id=cutover_id, checkpoint_sha256=CHECKPOINT, decisions=list(decisions))


async def admin_user(session):
    user = User(email="restore-admin@example.com", display_name="隔離管理員", password_hash="test", user_role=UserRole.ADMIN)
    session.add(user)
    await session.commit()
    return user


async def test_unarmed_cutover_stays_read_only_when_flag_is_removed(database_session, monkeypatch):
    actor = await admin_user(database_session)
    cutover = uuid4()
    await begin_cutover(database_session, actor, cutover, CHECKPOINT)
    await database_session.commit()
    monkeypatch.setattr(database, "SessionLocal", async_sessionmaker(database_session.bind, expire_on_commit=False))
    settings = make_test_settings(recovery_mode=False, reconciliation_enabled=True)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    seed, worker = AsyncMock(), Mock()
    monkeypatch.setattr(main, "seed_demo_data", seed)
    monkeypatch.setattr(main, "periodic_reconciliation", worker)
    app = main.create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ready = await client.get("/ready")
            assert ready.status_code == 503 and ready.json()["database"] == "available"
            assert (await client.post("/internal/reconcile")).status_code == 503
    assert await has_active_hold(database_session)
    seed.assert_not_awaited()
    worker.assert_not_called()
    await arm_cutover(database_session, actor, manifest(cutover))
    await database_session.commit()
    assert not await database.check_database_connection()


async def test_complete_manifest_persists_holds_and_requires_safe_resolution(database_session, tmp_path, monkeypatch):
    owner, _, order = await make_regular_order(database_session)
    actor = await admin_user(database_session)
    with pytest.raises(PermissionError):
        await begin_cutover(database_session, owner, uuid4(), CHECKPOINT)
    job = OutboxEvent(event_type="invoice.issue_requested", aggregate_type="order", aggregate_id=order.id)
    ticket = PrintJob(event_key="isolated-print", order_id=order.id, kind="order", mode="live",
                      printer_fingerprint="0" * 64, snapshot={"manual": True})
    database_session.add_all([job, ticket])
    await database_session.commit()
    cutover = uuid4()
    assert await begin_cutover(database_session, actor, cutover, CHECKPOINT)
    assert not await begin_cutover(database_session, actor, cutover, CHECKPOINT)
    await database_session.commit()
    path = tmp_path / "inventory.json"
    assert await export_inventory(database_session, actor, cutover, path) == 2
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        await export_inventory(database_session, actor, cutover, path)
    with pytest.raises(ValueError, match="未納入"):
        await arm_cutover(database_session, actor, manifest(cutover, decision("outbox", job.id)))
    assert await database_session.scalar(select(func.count()).select_from(RecoveryHold)) == 0
    trade = "AFTER-CUTOFF-SYNTHETIC"
    orphan = provider_payment_reference("raygate", trade)
    plan = manifest(cutover, decision("outbox", job.id), decision("print_job", ticket.id), decision("provider_payment", orphan))
    assert await arm_cutover(database_session, actor, plan) == 5
    await database_session.commit()
    assert await arm_cutover(database_session, actor, manifest(cutover, *reversed(plan.decisions))) == 0
    assert not await has_active_hold(database_session, ("order", str(uuid4())))
    assert await has_active_hold(database_session, ("order", order.id))
    assert await has_active_hold(database_session, ("print_lineage", f"order:{order.id}"))
    repo = SQLAlchemyPaymentCallbackRepository(database_session)
    with pytest.raises(RecoveryHoldActive):
        await repo.apply_payment_callback("raygate", "signed-orphan", {"MerchantTradeNo": trade})
    adapter = RayGateAdapter(raygate_settings())
    provider_read = AsyncMock(side_effect=AssertionError("held orphan reached provider"))
    monkeypatch.setattr(adapter, "query_order", provider_read)
    monkeypatch.setattr(payments, "raygate_payment_adapter_from_settings", lambda _: adapter)
    data = adapter.encrypt_transaction(payment_result(pos_order_number=trade))
    async with api_test_context(database_session, [payments.payments_router], settings=make_test_settings()) as client:
        reply = await client.post("/webhooks/raygate/payment", headers={"X-ePay-Identifier": RAYGATE_IDENTIFIER},
                                  json={"TransactionData": data, "HashDigest": hash_digest(data)})
        assert (reply.status_code, reply.text, reply.headers["Retry-After"]) == (503, "RETRY", "30")
    provider_read.assert_not_awaited()
    assert await database_session.scalar(select(func.count()).select_from(ExternalEvent)) == 0

    def resolve(scope, reference, outcome="confirmed_unsent"):
        return Resolution(cutover_id=cutover, target=Item(scope=scope, reference=reference), outcome=outcome,
                          evidence_sha256=RESOLUTION)

    with pytest.raises(ValueError, match="關聯"):
        await resolve_hold(database_session, actor, resolve("order", order.id))
    with pytest.raises(ValueError, match="尚未登錄"):
        await resolve_hold(database_session, actor, resolve("outbox", job.id, "reconciled"))
    for scope, identifier in (("outbox", job.id), ("print_job", ticket.id), ("order", order.id)):
        assert await resolve_hold(database_session, actor, resolve(scope, identifier))
    assert not await resolve_hold(database_session, actor, resolve("order", order.id))
    await database_session.commit()
    assert not await has_active_hold(database_session, ("order", order.id))
    assert await has_active_hold(database_session, ("provider_payment", orphan))
    assert await database_session.scalar(select(func.count()).select_from(AdminAudit)) == 5


async def test_completed_but_ambiguous_job_keeps_its_order_hold(database_session):
    _, _, order = await make_regular_order(database_session)
    actor = await admin_user(database_session)
    job = OutboxEvent(event_type="invoice.issue_requested", aggregate_type="order", aggregate_id=order.id,
                      status=OutboxStatus.COMPLETED)
    database_session.add(job)
    await database_session.commit()
    cutover = uuid4()
    await begin_cutover(database_session, actor, cutover, CHECKPOINT)
    await database_session.commit()
    with pytest.raises(ValueError, match="工作識別不存在"):
        await arm_cutover(database_session, actor, manifest(cutover, decision("outbox", str(uuid4()))))
    assert await arm_cutover(database_session, actor, manifest(cutover, decision("outbox", job.id))) == 2
    await database_session.commit()
    assert await has_active_hold(database_session, ("order", order.id))


async def test_payment_poll_excludes_held_attempt_before_claim(database_session, monkeypatch):
    user, _, order = await make_regular_order(database_session)
    settings = payment_settings()
    now = datetime.now(timezone.utc)
    attempt = await create_payment_attempt(database_session, order.id, user, settings, now=now - timedelta(hours=1))
    actor = await admin_user(database_session)
    cutover = uuid4()
    await begin_cutover(database_session, actor, cutover, CHECKPOINT)
    await arm_cutover(database_session, actor, manifest(cutover, decision("payment_attempt", attempt.id)))
    await database_session.commit()
    assert await has_active_hold(database_session, *payment_targets(attempt))
    provider = Mock()
    monkeypatch.setattr(jobs, "payment_adapter_from_settings", provider)
    assert list(await database_session.scalars(jobs.expired_payment_candidates(settings, now))) == []
    assert await jobs._reconcile_expired_payments(database_session, settings, now, limit=1) == 0
    provider.assert_not_called()
    await database_session.refresh(attempt)
    assert attempt.next_reconcile_at is None


async def test_summary_hold_checks_large_batches(database_session):
    actor = await admin_user(database_session)
    targets = [("print_lineage", f"summary-order:{uuid4()}") for _ in range(1200)]
    assert not await has_active_hold(database_session, *targets)
    cutover = uuid4()
    await begin_cutover(database_session, actor, cutover, CHECKPOINT)
    assert await arm_cutover(database_session, actor, manifest(cutover, decision(*targets[-1]))) == 1
    await database_session.commit()
    assert not await has_active_hold(database_session, *targets[:-1])
    assert await has_active_hold(database_session, *targets)
